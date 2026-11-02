"""Shared runner for all agents. It persists every invocation as an AgentRun (input hash, prompt
version, model actually used, fallback flag, validation retries, tool calls, tokens, latency) and
writes an AI audit event. Failures never raise into the pipeline: callers get `output=None` and
take the deterministic degraded path."""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import UsageLimits
from sqlalchemy.orm import Session

from app.clock import now
from app.config import get_settings
from app.integrations import llm
from app.models import AgentRun
from app.services import audit

T = TypeVar("T", bound=BaseModel)
PROMPT_DIR = Path(__file__).parent / "prompts"


@dataclass
class Prompt:
    name: str
    version: str
    text: str


def load_prompt(name: str) -> Prompt:
    raw = (PROMPT_DIR / f"{name}.md").read_text()
    first, _, rest = raw.partition("\n")
    version = first.split(":", 1)[1].strip() if first.lower().startswith("version:") else "0"
    return Prompt(name=name, version=version, text=rest.strip())


@dataclass
class Outcome(Generic[T]):
    output: T | None
    run: AgentRun

    @property
    def ok(self) -> bool:
        return self.output is not None


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _extract_trace(messages: list) -> tuple[str, int, list[dict[str, Any]]]:
    model_used = ""
    retries = 0
    final_calls = 0
    calls: dict[str, dict[str, Any]] = {}
    for m in messages:
        if isinstance(m, ModelResponse):
            model_used = m.model_name or model_used
            for p in m.parts:
                if isinstance(p, ToolCallPart) and p.tool_name == "final_result":
                    final_calls += 1
                elif isinstance(p, ToolCallPart):
                    calls[p.tool_call_id] = {"tool": p.tool_name, "args": p.args_as_dict(), "result": None}
        elif isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, RetryPromptPart):
                    retries += 1
                if isinstance(p, ToolReturnPart) and p.tool_call_id in calls:
                    content = p.content
                    calls[p.tool_call_id]["result"] = content if isinstance(content, dict | list | str) else str(content)
    # Each rejected final answer (schema or validator failure) is followed by another attempt.
    return model_used, retries + max(0, final_calls - 1), list(calls.values())


def _is_fallback(model_used: str, chain: list[str]) -> bool:
    """Was the answer produced by anything other than the primary? Longest-prefix match, because
    provider ids carry suffixes and a fallback id can itself start with the primary's id."""
    if not model_used or not chain:
        return False
    matches = [n for n in dict.fromkeys(chain) if model_used.startswith(n) or n.endswith(model_used)]
    if not matches:
        return True
    return max(matches, key=len) != chain[0]


def run_agent(
    db: Session,
    *,
    agent: Agent[Any, T],
    name: str,
    step: str,
    prompt: Prompt,
    user_prompt: str,
    input_payload: Any,
    case_id: int | None,
    ledger_version: int = 1,
    deps: Any = None,
    thinking: bool | str = False,
    confidence_of=lambda o: getattr(o, "confidence", None),
    request_limit: int = 12,
) -> Outcome[T]:
    run = AgentRun(
        case_id=case_id,
        agent=name,
        step=step,
        prompt_version=f"{prompt.name}@{prompt.version}",
        ledger_version=ledger_version,
        input_hash=_hash(input_payload),
        input_json=json.loads(json.dumps(input_payload, default=str)),
        started_at=now(),
    )
    output: T | None = None
    model = llm.build_model(db)
    primary_name, fallback_name = llm.model_names()
    t0 = time.perf_counter()
    if model is None:
        run.status, run.degraded, run.error = "degraded", True, "No LLM configured (GOOGLE_API_KEY missing)"
    else:
        # Gemini 3.x takes thinking_level (thinking_budget=0 is rejected by the Flash-Lite tier).
        settings: dict[str, Any] = {
            "temperature": 0.2,
            "timeout": get_settings().llm_timeout_s,
            "google_thinking_config": {"thinking_level": thinking if isinstance(thinking, str) else ("medium" if thinking else "minimal")},
        }
        try:
            llm.throttle(db)
            result = agent.run_sync(
                user_prompt,
                model=model,
                deps=deps,
                instructions=prompt.text,
                model_settings=settings,
                usage_limits=UsageLimits(request_limit=request_limit),
            )
            output = result.output
            model_used, retries, calls = _extract_trace(result.all_messages())
            run.model = model_used or primary_name
            run.fallback_used = _is_fallback(model_used, llm.chain_names())
            run.validation_retries = retries
            run.tool_calls = json.loads(json.dumps(calls, default=str))
            run.output_json = output.model_dump(mode="json")
            run.tokens_in = result.usage.input_tokens or 0
            run.tokens_out = result.usage.output_tokens or 0
            run.confidence = confidence_of(output)
            run.status = "ok"
        except (ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded, AgentRunError, ExceptionGroup) as e:
            run.status, run.degraded = "failed", True
            detail = "; ".join(f"{type(x).__name__}: {str(x)[:160]}" for x in getattr(e, "exceptions", [])) or str(e)
            run.error = f"{type(e).__name__}: {detail}"[:1200]
            run.model = primary_name
        except Exception as e:  # network stacks raise a zoo of types; never crash the pipeline
            run.status, run.degraded = "failed", True
            run.error = f"{type(e).__name__}: {e}"[:800]
            run.model = primary_name
    run.latency_ms = int((time.perf_counter() - t0) * 1000)
    db.add(run)
    db.flush()
    status_txt = {
        "ok": f"completed{' via FALLBACK model' if run.fallback_used else ''}",
        "failed": "failed - switching to deterministic degraded mode",
        "degraded": "skipped (no LLM) - deterministic degraded mode",
    }[run.status]
    audit.record(
        db,
        action=f"agent.{name}",
        summary=f"{name} agent {status_txt}" + (f" (confidence {run.confidence:.2f})" if run.confidence is not None else ""),
        actor_type="ai",
        actor_id=f"{name}-agent",
        case_id=case_id,
        entity="agent_run",
        entity_id=run.id,
        meta={
            "model": run.model,
            "prompt_version": run.prompt_version,
            "input_hash": run.input_hash,
            "fallback_used": run.fallback_used,
            "validation_retries": run.validation_retries,
            "latency_ms": run.latency_ms,
            "error": run.error,
        },
    )
    return Outcome(output=output, run=run)
