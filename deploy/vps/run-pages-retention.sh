#!/bin/sh
# Example runner. Installation and the first --apply require release-owner review.
set -eu
ROOT=/srv/services/korea-replay/workspaces/matdongsan
CONFIG=/srv/services/korea-replay/shared/retention-policy.json
cd "$ROOT"
exec /usr/bin/python3 -m pipeline.pages_retention --root "$ROOT" --config "$CONFIG" "$@"
