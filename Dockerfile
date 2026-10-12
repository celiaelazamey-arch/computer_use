# Override with an approved immutable digest for reproducible production builds.
ARG PYTHON_IMAGE=python:3.11-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build

# Build a real src-layout wheel, not an uninstalled source tree. sqlite-vec
# supplies the native extension in its wheel; no compiler is needed at runtime.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip wheel --no-cache-dir --wheel-dir=/wheels '.[server]' \
    && python -m pip install --no-cache-dir --no-index --find-links=/wheels \
       --ignore-installed --prefix=/install 'cela-sh[server]'

FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /app

COPY --from=builder /install /usr/local
RUN groupadd --gid 10001 cela \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin cela \
    && mkdir -p /app/data \
    && chown cela:cela /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CELA_MEMORY_BACKEND=sqlite-vec \
    CELA_SQLITE_VEC_PATH=/app/data/cela_memory.db

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]

CMD ["uvicorn", "cela_sh.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--limit-concurrency", "32"]
