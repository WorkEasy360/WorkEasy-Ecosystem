#!/bin/sh
# Writes the pooler's credentials from the environment into a tmpfs-backed
# file at start-up, so no password is baked into an image or a mounted file.
set -eu
: "${W360_DB_APP_PASSWORD:?W360_DB_APP_PASSWORD is required}"
mkdir -p /tmp/pgbouncer
umask 077
printf '"w360_app" "%s"\n' "$W360_DB_APP_PASSWORD" > /tmp/pgbouncer/userlist.txt
exec pgbouncer /deploy/pgbouncer/pgbouncer.ini
