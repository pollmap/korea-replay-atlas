#!/bin/sh
# Pages staging/restoration primitive. For a complete publication transaction use
# run-pages-publication.sh so failed publication leaves retention blocked.
set -eu
ROOT=${KOREA_REPLAY_PROJECT_ROOT:-/srv/services/korea-replay/workspaces/matdongsan}
SERVICE=${KOREA_REPLAY_SERVICE_ROOT:-/srv/services/korea-replay}
cd "$ROOT"
exec /usr/bin/python3 -m pipeline.generated_work --root "$ROOT" --service-root "$SERVICE" -- "$@"
