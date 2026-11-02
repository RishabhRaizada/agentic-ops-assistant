import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from app import clock, worker  # noqa: E402
from app.api import admin, approvals, auth, cases, misc  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.orchestrator.states import IllegalTransition  # noqa: E402

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        from app.models import User
        from app.services import runtime_settings

        if db.query(User).count() == 0:
            from app.seed.run import seed

            seed(db)
            db.commit()
        clock.set_offset(float(runtime_settings.get(db, "clock_offset_seconds") or 0))
    if get_settings().run_background_workers:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="OpsPilot API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(IllegalTransition)
async def _illegal(_: Request, exc: IllegalTransition):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


for r in (auth.router, cases.router, cases.comm_router, approvals.router, misc.router, admin.router, admin.public):
    app.include_router(r)
