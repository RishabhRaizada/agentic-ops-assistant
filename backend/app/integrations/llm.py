"""LLM access. One place builds the model chain: Gemini primary -> Gemini Flash-Lite -> Groq (one
backend per key). PydanticAI's FallbackModel moves down the chain on API errors, rate limits and
timeouts; if every backend fails, callers drop to the deterministic degraded path. Chaos flags swap
in failing models so outages can be demonstrated on demand."""

import threading
import time
from collections import deque

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import runtime_settings

# Tests (and the offline demo) inject a model pair here instead of calling Gemini.
_test_models: tuple[Model, Model] | None = None
_rate_lock = threading.Lock()
_calls: deque[float] = deque()


def set_test_models(primary: Model | None, fallback: Model | None = None) -> None:
    global _test_models
    _test_models = (primary, fallback or primary) if primary else None


def _down(name: str) -> FunctionModel:
    def fail(messages, info: AgentInfo):
        raise ModelAPIError(name, "503 UNAVAILABLE: model is overloaded (chaos flag)")

    return FunctionModel(fail, model_name=name)


def _fallbacks() -> list[str]:
    return [m.strip() for m in get_settings().llm_fallback_model.split(",") if m.strip()]


def _groq_keys() -> list[str]:
    return [k.strip() for k in get_settings().groq_api_keys.split(",") if k.strip()]


def model_names() -> tuple[str, str]:
    """(primary, first fallback) - used for display and fallback detection."""
    chain = chain_names()
    return chain[0], chain[1] if len(chain) > 1 else chain[0]


def chain_names() -> list[str]:
    s = get_settings()
    if _test_models:
        return [_test_models[0].model_name, _test_models[1].model_name]
    if s.llm_fake and not s.google_api_key:
        return ["fake-llm", "fake-llm-fallback"]
    names = [s.llm_primary_model, *_fallbacks()] if s.google_api_key else []
    names += [s.groq_model] * len(_groq_keys())
    return names


def build_model(db: Session) -> Model | None:
    """Return the model chain to use right now, or None when no LLM is configured at all."""
    s = get_settings()
    chaos = runtime_settings.chaos(db)
    names = chain_names()
    if not names:
        return None
    if chaos["llm_all_down"]:
        return FallbackModel(*[_down(n) for n in names])
    if _test_models:
        models: list[Model] = list(_test_models)
    elif s.google_api_key or _groq_keys():
        models = []
        if s.google_api_key:
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            provider = GoogleProvider(api_key=s.google_api_key)
            # Each Gemini model has its own quota pool, so a chain of models multiplies capacity.
            models += [GoogleModel(n, provider=provider) for n in [s.llm_primary_model, *_fallbacks()]]
        for key in _groq_keys():
            from pydantic_ai.models.groq import GroqModel
            from pydantic_ai.providers.groq import GroqProvider

            models.append(GroqModel(s.groq_model, provider=GroqProvider(api_key=key)))
    elif s.llm_fake:
        from app.integrations import fake_llm

        models = [fake_llm.make(), fake_llm.make(name="fake-llm-fallback")]
    else:
        return None
    if chaos["llm_primary_down"]:
        models[0] = _down(names[0])
    return FallbackModel(*models)


def throttle(db: Session) -> None:
    """Keep under the provider's requests-per-minute quota; the pipeline is single-threaded,
    so waiting here is cheaper than eating 429s. Also applies the chaos latency knob."""
    limit = get_settings().llm_max_runs_per_min
    with _rate_lock:
        t = time.monotonic()
        while _calls and t - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= limit and not _test_models:
            time.sleep(max(0.0, 60 - (t - _calls[0])) + 0.1)
        _calls.append(time.monotonic())
    extra = runtime_settings.chaos(db).get("llm_latency_ms") or 0
    if extra:
        time.sleep(min(extra, 30000) / 1000)
