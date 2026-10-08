#!/bin/sh
# INVENTORY may be created by COMMAND after the remote verification succeeded.
set -eu
if [ "$#" -lt 2 ]; then
  printf '%s\n' 'Usage: run-pages-publication.sh PRIVATE_INVENTORY COMMAND [ARG ...]' >&2
  exit 2
fi
INVENTORY=$1
shift
ROOT=${KOREA_REPLAY_PROJECT_ROOT:-/srv/services/korea-replay/workspaces/matdongsan}
SERVICE=${KOREA_REPLAY_SERVICE_ROOT:-/srv/services/korea-replay}
cd "$ROOT"
exec /usr/bin/python3 -m pipeline.generated_work --root "$ROOT" --service-root "$SERVICE" \
  --inventory "$INVENTORY" --policy "$SERVICE/shared/retention-policy.json" -- "$@"
