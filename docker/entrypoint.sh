#!/bin/sh
# Starts as root just long enough to join the group that owns the Docker socket, then runs the server
# as uid 1000. That group's id differs per machine, so nothing outside the container has to know it.
set -eu
if [ "$(id -u)" != 0 ]; then
    exec "$@"
fi
sock=/var/run/docker.sock
if [ -S "$sock" ]; then
    groups="--groups $(stat -c %g "$sock")"
else
    groups="--clear-groups"
fi
# A data/ folder that Docker created for a first `compose up` belongs to root, which the server can't write.
data="${TOMABOTCHI_ROOT:-}/data"
if [ -n "${TOMABOTCHI_ROOT:-}" ] && [ -d "$data" ] && [ "$(stat -c %u "$data")" = 0 ]; then
    chown 1000:1000 "$data"
fi
export HOME=/home/keeper
# shellcheck disable=SC2086
exec setpriv --reuid=1000 --regid=1000 $groups "$@"
