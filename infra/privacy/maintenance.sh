#!/usr/bin/env bash
# No production mutation until a fresh dump has actually restored in an isolated server.
set -euo pipefail
umask 077
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
cd -- "$project_root"
exec 9>"$project_root/.privacy-maintenance.lock"
flock -n 9 || exit 0
backup_dir=${PRIVACY_BACKUP_DIR:-"$project_root/.privacy-backups"}
[[ $backup_dir = /* && $backup_dir != / ]] || exit 1
mkdir -p -- "$backup_dir"
backup_dir=$(cd -- "$backup_dir" && pwd -P)
chmod 700 -- "$backup_dir"
export PRIVACY_BACKUP_DIR="$backup_dir"
if [[ ${1:---check} != --apply ]]; then
  exec docker compose --profile privacy run --rm --no-deps privacy-worker --dry-run
fi
: "${PRIVACY_BACKUP_RETENTION_DAYS:?Set the operator-approved backup retention in days}"
[[ $PRIVACY_BACKUP_RETENTION_DAYS =~ ^[1-9][0-9]*$ ]] || exit 1
stamp=$(date -u +%Y%m%dT%H%M%SZ)
file="horizon-privacy-$stamp.dump"
[[ ! -e $backup_dir/$file ]] || exit 1
if ! docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/$file.partial" 2>/dev/null; then
  echo 'Privacy maintenance stopped: backup failed.' >&2; exit 1
fi
mv -- "$backup_dir/$file.partial" "$backup_dir/$file"
check_container="horizon-privacy-restore-$(cat /proc/sys/kernel/random/uuid)"
cleanup() {
  [[ $check_container == horizon-privacy-restore-* ]] && docker rm -f "$check_container" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# No network and no persistent PostgreSQL volume. Trust is confined to local docker exec.
docker run -d --name "$check_container" --network none \
  --tmpfs /var/lib/postgresql/data --tmpfs /var/run/postgresql \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=horizon_restore postgres:16-alpine >/dev/null
ready=false
for ((n=0;n<60;n++)); do
  if docker exec "$check_container" pg_isready -U postgres -d horizon_restore >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[[ $ready == true ]] || exit 1
docker exec "$check_container" psql -U postgres -d horizon_restore -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE horizon_auth; CREATE ROLE horizon_finance;' >/dev/null
if ! docker exec -i "$check_container" pg_restore -U postgres -d horizon_restore --no-owner --no-privileges --exit-on-error < "$backup_dir/$file" >/dev/null 2>&1; then
  echo 'Privacy maintenance stopped: restore verification failed.' >&2; exit 1
fi
bad=$(docker exec "$check_container" psql -U postgres -d horizon_restore -At -v ON_ERROR_STOP=1 \
  -c 'SELECT count(*) FROM finance.reconciliation WHERE balance<>ledger_balance OR held<>active_holds;')
[[ $bad == 0 ]] || exit 1
versions=$(docker exec "$check_container" psql -U postgres -d horizon_restore -At -v ON_ERROR_STOP=1 \
  -c "SELECT (SELECT version_num FROM finance.alembic_version)||':'||(SELECT version_num FROM auth.alembic_version);")
[[ $versions == finance_0012:auth_0013 ]] || exit 1
digest=$(sha256sum "$backup_dir/$file"); digest=${digest%% *}
verified=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"backup_file":"%s","sha256":"%s","verified_at":"%s","restore_checked":true,"reconciliation_zero":true}\n' \
  "$file" "$digest" "$verified" > "$backup_dir/verified.json.partial"
mv -- "$backup_dir/verified.json.partial" "$backup_dir/verified.json"
docker compose --profile privacy run --rm --no-deps --user "$(id -u):$(id -g)" privacy-worker --once
# Delete only this job's own dated backups, after a successful verified run.
find "$backup_dir" -maxdepth 1 -type f -name 'horizon-privacy-*.dump' -mtime "+$((PRIVACY_BACKUP_RETENTION_DAYS-1))" -delete
printf '{"event":"privacy_verified_maintenance_completed"}\n'
