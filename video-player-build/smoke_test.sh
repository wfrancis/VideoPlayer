#!/bin/zsh
set -euo pipefail

APP_PATH="${1:-/Applications/Video Player.app}"
MEDIA_PATH="${2:-}"
BASE_URL="${VIDEO_PLAYER_BASE_URL:-http://127.0.0.1:9876}"

if [[ -n "$MEDIA_PATH" && ! -f "$MEDIA_PATH" ]]; then
  echo "Missing media file: $MEDIA_PATH" >&2
  exit 1
fi

/usr/bin/pkill -f "${APP_PATH}/Contents/MacOS/" >/dev/null 2>&1 || true

if [[ -n "$MEDIA_PATH" ]]; then
  open -na "$APP_PATH" --args "$MEDIA_PATH"
else
  open -na "$APP_PATH"
fi

for _ in {1..30}; do
  if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "Health:"
curl -fsS "$BASE_URL/health"
echo
echo "State:"
curl -fsS "$BASE_URL/state"
echo
