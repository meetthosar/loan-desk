FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

COPY config ./config
COPY prompts ./prompts
COPY data ./data
COPY evals ./evals
# Bake the embedding model into the image so the container starts offline.
RUN python -c "from loandesk.embeddings import Embedder; Embedder('BAAI/bge-small-en-v1.5')"

RUN useradd --create-home app && mkdir -p /app/.loandesk && chown -R app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "loandesk db seed --if-empty && loandesk ingest && loandesk serve --host 0.0.0.0"]
