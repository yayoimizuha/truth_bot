FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY . .

ENV PATH="/app/.venv/bin:${PATH}"
ENV MEDIA_HOST_STORAGE_DIR=/data

EXPOSE 8010

CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
