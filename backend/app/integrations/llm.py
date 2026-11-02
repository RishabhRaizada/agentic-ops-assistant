"""LLM access. One place builds the model chain: Gemini primary -> Gemini fallback (PydanticAI
FallbackModel switches on API errors and timeouts). If both fail, callers drop to the deterministic
degraded path. Chaos flags swap in failing models so outages can be demonstrated on demand."""

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


def model_names() -> tuple[str, str]:
    s = get_settings()
    if _test_models:
        return _test_models[0].model_name, _test_models[1].model_name
    if s.llm_fake and not s.google_api_key:
        return "fake-llm", "fake-llm-fallback"
    return s.llm_primary_model, s.llm_fallback_model


def build_model(db: Session) -> Model | None:
    """Return the model chain to use right now, or None when no LLM is configured at all."""
    s = get_settings()
    chaos = runtime_settings.chaos(db)
    primary_name, fallback_name = model_names()
    if chaos["llm_all_down"]:
        return FallbackModel(_down(primary_name), _down(fallback_name))
    if _test_models:
        primary, fallback = _test_models
    elif s.google_api_key:
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        provider = GoogleProvider(api_key=s.google_api_key)
        primary = GoogleModel(s.llm_primary_model, provider=provider)
        fallback = GoogleModel(s.llm_fallback_model, provider=provider)
    elif s.llm_fake:
        from app.integrations import fake_llm

        primary, fallback = fake_llm.make(), fake_llm.make(name="fake-llm-fallback")
    else:
        return None
    if chaos["llm_primary_down"]:
        primary = _down(primary_name)
    return FallbackModel(primary, fallback)


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
