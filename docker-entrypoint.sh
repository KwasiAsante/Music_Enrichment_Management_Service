#!/bin/sh
# Drops from root to PUID:PGID before exec'ing the real command, so files the
# app writes under APP_DATA_DIR/BEETSDIR/APP_MUSIC_DIR come out owned by a
# real host user instead of root. Unset/0 (the default) skips all of this and
# runs as root, matching the pre-PUID/PGID behavior exactly.
set -e

PUID="${PUID:-0}"
PGID="${PGID:-0}"

if [ "$PUID" = "0" ] && [ "$PGID" = "0" ]; then
    exec "$@"
fi

if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -o -g "$PGID" appgroup
fi

if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -o -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin appuser
fi

# /music is the user's own library, potentially huge — leave its ownership
# alone and let the user match it to PUID/PGID on the host. /data and
# BEETSDIR are small, app-managed state, safe to fix up on every start.
chown -R "$PUID":"$PGID" "$APP_DATA_DIR" "$BEETSDIR"

exec gosu "$PUID":"$PGID" "$@"
