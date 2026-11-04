FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY src ./src
CMD ["uvicorn", "app.adapters.inbound.http.main:app", "--host", "0.0.0.0", "--port", "8000"]
