FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.8.8 /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (layer cache), then the project.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY nexus ./nexus
COPY benchmarks ./benchmarks
RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH"
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=12 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "nexus.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
