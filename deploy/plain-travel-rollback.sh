#!/usr/bin/env bash
# Operate only on the staged travel maintenance window and its own rollback directory.
set -Eeuo pipefail
umask 077

ACTION=${1:?use backup or restore-before-opening}
WINDOW=${2:?use the staged root-private window path}
[[ $# == 2 && $EUID == 0 && $WINDOW =~ ^/var/lib/travel-cutover/cutover-[0-9a-f]{24}$ ]]
[[ $(realpath -e -- "$WINDOW") == "$WINDOW" && $(stat -c '%a %u %g' -- "$WINDOW") == '700 0 0' ]]

ROLLBACK=/var/lib/travel-cutover/rollback-20261125-5f6fbd51
APP=/opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281
TOOLS=$APP/backend/tools
OLD_APP=/opt/travel/app/backend/app
OLD_VENV=/opt/travel/venv
FRONT=/opt/continew/web/travel
DATA=/opt/travel/app/backend/data
DB=$DATA/app.db
UNIT=/etc/systemd/system/travel.service
INGRESS=/etc/systemd/system/travel-backend-ingress.service
RULES=/etc/travel-tls/backend-ingress.nft
SHARED=/opt/continew/nginx.conf
EDGE=/etc/travel-tls/edge-nginx.conf
OLD_UNIT_SHA=7ff4dc5f4c925f607f1b36ddf5b0fef6f228c22eb5c4dc0f5c8e2d3824496c07
NEW_UNIT_SHA=ec03ed87a56ea67acf870c71e6722fd3cd2e041af79e712b310cb3be4d36649e
INGRESS_SHA=e63ce1ddd6f79bc1be6f50aa5a318c15c9c489cfbe90edc6db9c2489c811a92e
RULES_SHA=707816e51b6926eea6814c275d8420f131407d07337a54b66ad9ee274c5c3b89
OLD_SHARED_SHA=300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73
REDIRECT_SHA=8e489e8d949d0d8df92d8ae832a1f8b10a547e20d6ed5174b2c36941ff0648fc
MAINTENANCE_SHA=e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e
FRONT_ID=7c620cde98827c6704b818f28f14ab49ddc594f44d188cff28f87c0797304aff
SHARED_ID=c7a06b34d8e5f7e3a6d52f18f11ba9ac54ae6df453910e833b66ac43a892cbcc
SHARED_IMAGE=sha256:98f8ec75657d21b924fe4f69b6b9bff2f6550ea48838af479d8894a852000e40

sha() { /usr/bin/sha256sum -- "$1" | /usr/bin/cut -d ' ' -f 1; }
db_copy() { /usr/bin/python3 -I -B "$TOOLS/backup_db.py" "$@"; }
trap 'printf >&2 "Rollback operation stopped at line %s. Keep maintenance and inspect the private files.\n" "$LINENO"' ERR

require_closed_routes() {
  [[ ! -e $WINDOW/write-opening.json && ! -L $WINDOW/write-opening.json ]]
  [[ $(sha "$EDGE") == "$MAINTENANCE_SHA" ]]
  [[ $(sha "$SHARED") == "$REDIRECT_SHA" ]]
  [[ $(sha "$WINDOW/shared-nginx-before.conf") == "$OLD_SHARED_SHA" ]]
  [[ $(/usr/bin/docker exec "$SHARED_ID" /usr/bin/sha256sum /etc/nginx/nginx.conf | /usr/bin/cut -d ' ' -f 1) == "$REDIRECT_SHA" ]]
  [[ $(/usr/bin/curl -qsS --noproxy '*' --max-time 4 --connect-to 134.175.137.18:443:127.0.0.1:443 -o /dev/null -w '%{http_code}' https://134.175.137.18/travel/api/health) == 503 ]]
  [[ $(/usr/bin/curl -qsS --noproxy '*' --max-time 4 -H 'Host: 134.175.137.18' -o /dev/null -w '%{http_code}' http://127.0.0.1/travel/api/health) == 403 ]]
}

require_stopped() {
  local state listeners
  state=$(/usr/bin/systemctl show -p ActiveState,MainPID,DropInPaths --value travel.service | sort | tr '\n' '|')
  [[ $state == '|0|inactive|' || $state == '|0|failed|' ]]
  listeners=$(/usr/bin/ss -H -ltn 'sport = :8000')
  [[ -z $listeners ]]
  [[ $(realpath -e -- "$DATA") == "$DATA" && -f $DB && ! -L $DB && $(stat -c '%u %g' -- "$DB") == '1000 1001' ]]
  /usr/bin/python3 -I -B - "$DB" <<'PY'
from pathlib import Path
import stat
import sys

database = sys.argv[1]
watched = set()
for suffix in ('', '-wal', '-shm', '-journal'):
    try:
        info = Path(database + suffix).lstat()
    except FileNotFoundError:
        continue
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit('Unexpected SQLite file type')
    watched.add((info.st_dev, info.st_ino))
for process in Path('/proc').iterdir():
    if not process.name.isdecimal():
        continue
    try:
        for fd in (process / 'fd').iterdir():
            try:
                info = fd.stat()
            except FileNotFoundError:
                continue
            if (info.st_dev, info.st_ino) in watched:
                raise SystemExit('A process still has a SQLite file open')
    except (FileNotFoundError, ProcessLookupError):
        continue
print('SQLite file openers: none')
PY
}

backup() {
  local source suffix
  require_closed_routes
  require_stopped
  [[ $(sha "$UNIT") == "$OLD_UNIT_SHA" && ! -e $INGRESS && ! -e $RULES ]]
  [[ $(realpath -e -- "$OLD_APP") == "$OLD_APP" && $(realpath -e -- "$FRONT") == "$FRONT" ]]
  [[ $(df -PB1 -- "$WINDOW" | awk 'NR==2 {print $4}') -ge 1073741824 ]]
  /usr/bin/mkdir -m 0700 -- "$ROLLBACK"
  printf '%s\n' "$WINDOW" > "$ROLLBACK/window-path"
  /usr/bin/cp -a -- "$OLD_APP" "$ROLLBACK/legacy-app"
  /usr/bin/cp -a -- "$FRONT" "$ROLLBACK/legacy-static"
  /usr/bin/install -m 0600 -T -- "$UNIT" "$ROLLBACK/legacy-travel.service"
  /usr/bin/install -m 0600 -T -- "$WINDOW/shared-nginx-before.conf" "$ROLLBACK/legacy-shared-nginx.conf"
  /usr/bin/diff -qr -- "$OLD_APP" "$ROLLBACK/legacy-app"
  /usr/bin/diff -qr -- "$FRONT" "$ROLLBACK/legacy-static"
  # A read-only SQLite connection can create WAL sidecars. Open only a stopped copy here.
  source=$(/usr/bin/mktemp -d "$ROLLBACK/.sqlite-source-XXXXXXXX")
  /usr/bin/cp -- "$DB" "$source/app.db"
  for suffix in -wal -journal; do
    if [[ -e "$DB$suffix" ]]; then /usr/bin/cp -- "$DB$suffix" "$source/app.db$suffix"; fi
  done
  db_copy --source "$source/app.db" --output "$ROLLBACK/legacy.sqlite3" --timeout 30
  db_copy --source "$ROLLBACK/legacy.sqlite3" --verify-only --timeout 15
  [[ $(stat -c '%a %u %g' -- "$ROLLBACK/legacy.sqlite3") == '600 0 0' ]]
  printf '%s\n' "$(sha "$ROLLBACK/legacy.sqlite3")" > "$ROLLBACK/legacy.sqlite3.sha256"
  /usr/bin/rm -f -- "$source/app.db" "$source/app.db-wal" "$source/app.db-shm" "$source/app.db-journal"
  /usr/bin/rmdir -- "$source"
  /usr/bin/sync -f -- "$ROLLBACK"
  printf 'rollback_directory=%s\nverified_backup_sha256=%s\n' "$ROLLBACK" "$(cat "$ROLLBACK/legacy.sqlite3.sha256")"
}

restore() {
  require_closed_routes
  [[ $(cat "$ROLLBACK/window-path") == "$WINDOW" && $(stat -c '%a %u %g' -- "$ROLLBACK") == '700 0 0' ]]
  [[ $(sha "$ROLLBACK/legacy.sqlite3") == "$(cat "$ROLLBACK/legacy.sqlite3.sha256")" ]]
  [[ $(sha "$ROLLBACK/legacy-travel.service") == "$OLD_UNIT_SHA" && $(sha "$ROLLBACK/legacy-shared-nginx.conf") == "$OLD_SHARED_SHA" ]]
  [[ $(sha "$UNIT") == "$NEW_UNIT_SHA" || $(sha "$UNIT") == "$OLD_UNIT_SHA" ]]
  [[ -x $OLD_VENV/bin/python ]]
  /usr/bin/systemctl stop travel.service
  require_stopped
  /usr/bin/diff -qr --exclude='__pycache__' -- "$OLD_APP" "$ROLLBACK/legacy-app"

  if [[ -f "$FRONT/.travel-deploy/$FRONT_ID.json" ]]; then
    /usr/sbin/runuser -u ubuntu -- /usr/bin/python3 -I -B "$TOOLS/frontend_release.py" rollback --target "$FRONT" --release-id "$FRONT_ID"
  fi
  /usr/bin/python3 -I -B - "$ROLLBACK/legacy-static" "$FRONT" <<'PY'
import hashlib
from pathlib import Path
import sys

saved, live = map(Path, sys.argv[1:])
for old in saved.rglob('*'):
    relative = old.relative_to(saved)
    if relative.parts[0] == '.travel-deploy' or old.is_dir():
        continue
    installed = live / relative
    if old.is_symlink() or not installed.is_file() or installed.resolve(strict=True) != installed:
        raise SystemExit('Unexpected legacy static file type')
    for path in (old, installed):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').digest()
        if path == old:
            expected = digest
        elif digest != expected:
            raise SystemExit('Legacy static files differ')
print('Legacy static files verified')
PY

  if [[ -e $INGRESS ]]; then
    [[ $(sha "$INGRESS") == "$INGRESS_SHA" ]]
    /usr/bin/systemctl stop travel-backend-ingress.service
  fi
  if /usr/sbin/nft list table inet travel_guard >/dev/null 2>&1; then
    [[ $(sha "$RULES") == "$RULES_SHA" ]]
    /usr/bin/python3 -I -B -c 'import sys; sys.path.insert(0,"/usr/local/libexec"); from verify_travel_cutover_ready import live_counters; live_counters()'
    /usr/sbin/nft delete table inet travel_guard
  fi
  if [[ -e $RULES ]]; then
    [[ $(sha "$RULES") == "$RULES_SHA" ]]
    /usr/bin/mv -T -- "$RULES" "$ROLLBACK/disabled-backend-ingress.nft"
  fi
  if [[ -e $INGRESS ]]; then
    /usr/bin/mv -T -- "$INGRESS" "$ROLLBACK/disabled-travel-backend-ingress.service"
  fi

  if [[ -f "$WINDOW/backend-start-attempted" ]]; then
    local held file
    held=$(/usr/bin/mktemp -d "$DATA/.failed-cutover-XXXXXXXX")
    db_copy --source "$ROLLBACK/legacy.sqlite3" --output "$held/restored-app.db" --timeout 30
    /usr/bin/chown 1000:1001 -- "$held/restored-app.db"
    /usr/bin/ln -- "$DB" "$held/failed-app.db"
    for file in app.db-wal app.db-shm app.db-journal; do
      if [[ -e "$DATA/$file" ]]; then /usr/bin/mv -T -- "$DATA/$file" "$held/$file"; fi
    done
    /usr/bin/mv -T -- "$held/restored-app.db" "$DB"
    /usr/bin/sync -f -- "$DATA"
    printf 'failed_database_preserved_in=%s\n' "$held"
  fi
  /usr/bin/install -m 0644 -T -- "$ROLLBACK/legacy-travel.service" "$UNIT"
  /usr/bin/systemctl daemon-reload
  /usr/bin/python3 -I -B "$WINDOW/tools/switch_travel_cleartext.py" --restore-before-upgrade \
    --candidate "$WINDOW/shared-nginx-before.conf" --shared-container "$SHARED_ID" --shared-image "$SHARED_IMAGE" \
    --backup "$WINDOW/shared-nginx-redirect-before-resume.conf" --apply
}

[[ $(sha "$TOOLS/backup_db.py") == b00cf9f0cb778355ccaaf568bae85524edfc829f690d7bf453efaeedd58958e0 ]]
case "$ACTION" in
  backup) backup ;;
  restore-before-opening) restore ;;
  *) printf >&2 'Invalid action\n'; exit 2 ;;
esac
