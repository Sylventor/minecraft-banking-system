#!/usr/bin/env sh
set -eu
: "${BACKUP_DIR:?BACKUP_DIR is required}"
: "${BACKUP_PGSERVICE:?A dedicated PostgreSQL backup service configuration is required}"
retention="${BACKUP_RETENTION_DAYS:-7}"
case "$retention" in ''|0|*[!0-9]*) echo 'invalid retention' >&2; exit 1;; esac
case "$BACKUP_DIR" in /*) ;; *) echo 'BACKUP_DIR must be absolute' >&2; exit 1;; esac
umask 077
mkdir -p "$BACKUP_DIR"
resolved=$(cd "$BACKUP_DIR" && pwd -P)
if [ "$resolved" = / ]; then echo 'root is not a backup directory' >&2; exit 1; fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="horizon-$stamp.dump"
target="$resolved/$name"
tmp="$target.partial"
if [ -e "$target" ]; then echo 'backup name collision' >&2; exit 1; fi
trap 'rm -f "$tmp"' EXIT
# pg_service.conf and .pgpass keep credentials out of process arguments.
PGSERVICE="$BACKUP_PGSERVICE" pg_dump --format=custom --file "$tmp"
pg_restore --list "$tmp" >/dev/null
mv "$tmp" "$target"
(cd "$resolved" && sha256sum "$name" > "$name.sha256")
find "$resolved" -maxdepth 1 -type f \( -name 'horizon-*.dump' -o -name 'horizon-*.dump.sha256' \) -mtime "+$((retention-1))" -delete
printf '{"event":"backup_completed","timestamp":"%s"}\n' "$stamp"
