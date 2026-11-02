FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
ENV PYTHONPATH=/app PYDANTIC_AI_NO_BANNER=1 DATABASE_URL=sqlite:////data/opspilot.db
VOLUME /data
EXPOSE 8100
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
