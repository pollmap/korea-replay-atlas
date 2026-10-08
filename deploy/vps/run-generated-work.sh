#!/bin/sh
# All Pages staging, promotion and restoration must use this same lock before
# automatic retention is enabled. The collector already uses bulk-work.lock.
set -eu
if [ "$#" -eq 0 ]; then
  printf '%s\n' 'Usage: run-generated-work.sh COMMAND [ARG ...]' >&2
  exit 2
fi
LOCK=/srv/services/korea-replay/shared/data/bulk-work.lock
exec /usr/bin/flock --exclusive --nonblock "$LOCK" "$@"
