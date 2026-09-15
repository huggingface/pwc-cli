FROM python:3.12.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY standalone_cli /src/standalone_cli
COPY mcp_server /src/mcp_server
RUN uv sync --project /src/mcp_server --frozen --no-dev --no-editable

USER app
ENV PATH="/src/mcp_server/.venv/bin:$PATH"
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=4)"

CMD ["pwc-mcp"]
