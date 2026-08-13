#!/usr/bin/env sh
set -eu

exec uv run uvicorn comic_enhancer.main:app \
  --app-dir service \
  --host 127.0.0.1 \
  --port "${COMIC_ENHANCER_PORT:-8765}"

