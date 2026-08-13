#!/bin/sh
set -eu

INDEX_PATH="${COMIC_ENHANCER_ADAPTER_INDEX:-/app/runtime/adapters/index.json}"
if [ ! -f "$INDEX_PATH" ]; then
  mkdir -p "$(dirname "$INDEX_PATH")"
  cp /app/adapters/index.json "$INDEX_PATH"
fi

exec "$@"
