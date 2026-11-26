#!/usr/bin/env bash
# Apply the reviewed travel-only release in two stages around external verification.
set -Eeuo pipefail
umask 077

ACTION=${1:?use prepare, open, or resume-renewal}
WINDOW=${2:?use the staged root-private window path}
[[ $# == 2 && $EUID == 0 && $WINDOW =~ ^/var/lib/travel-cutover/cutover-[0-9a-f]{24}$ ]]
[[ $(realpath -e -- "$WINDOW") == "$WINDOW" && $(stat -c '%a %u %g' -- "$WINDOW") == '700 0 0' ]]

APP=/opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281
VENV=/opt/travel/runtime-envs/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281
DATA=/opt/travel/app/backend/data
FRONT=/opt/continew/web/travel
RULES=/etc/travel-tls/backend-ingress.nft
UNIT=/etc/systemd/system/travel.service
INGRESS=/etc/systemd/system/travel-backend-ingress.service
PIN=/etc/travel-tls/edge-target.json
LIB=/usr/local/libexec
ROLLBACK=/var/lib/travel-cutover/rollback-20261125-5f6fbd51
SHARED_ID=c7a06b34d8e5f7e3a6d52f18f11ba9ac54ae6df453910e833b66ac43a892cbcc
SHARED_IMAGE=sha256:98f8ec75657d21b924fe4f69b6b9bff2f6550ea48838af479d8894a852000e40
OLD_UNIT_SHA=7ff4dc5f4c925f607f1b36ddf5b0fef6f228c22eb5c4dc0f5c8e2d3824496c07

sha() { /usr/bin/sha256sum -- "$1" | /usr/bin/cut -d ' ' -f 1; }
verify_ingress() { /usr/bin/python3 -I -B "$LIB/verify_travel_cutover_ready.py" --inspect-live; }

restore_early() {
  /usr/bin/python3 -I -B "$WINDOW/tools/switch_travel_cleartext.py" --restore-before-upgrade \
    --candidate "$WINDOW/shared-nginx-before.conf" --shared-container "$SHARED_ID" --shared-image "$SHARED_IMAGE" \
    --backup "$WINDOW/shared-nginx-redirect-before-resume.conf" --apply
}

preflight() {
  local tool
  [[ $(sha "$UNIT") == "$OLD_UNIT_SHA" && ! -e $INGRESS && ! -e $RULES && ! -e $ROLLBACK ]]
  [[ $(/usr/bin/systemctl show -p ActiveState,NeedDaemonReload,DropInPaths --value travel.service | sort | tr '\n' '|') == '|active|no|' ]]
  [[ $(sha "$APP/release.json") == 63514d2d03011ba0404ce1de3bd3b65155532136cfa61f8424299ab4bba9a3fb ]]
  [[ $(stat -c '%a %u %g' -- "$DATA") == '755 1000 1001' && $(stat -c '%a %u %g' -- "$DATA/app.db") == '644 1000 1001' ]]
  [[ $(realpath -e -- "$DATA") == "$DATA" && ! -L "$DATA/app.db" && $(realpath -e -- "$FRONT") == "$FRONT" ]]
  [[ $(stat -c '%a %u %g' -- /opt/travel/app/.env) == '600 1000 1001' ]]
  [[ $(stat -c '%a %u %g' -- "$FRONT") == '755 1000 1001' ]]
  [[ ! -e $WINDOW/write-opening.json && ! -e $WINDOW/shared-nginx-before.conf ]]
  for tool in switch_travel_https_mode.py verify_travel_cutover_ready.py guard_travel_write_opening.py register_travel_tls_edge.py; do
    [[ ! -e $LIB/$tool && ! -L $LIB/$tool ]]
  done
  /usr/sbin/nft --check --file "$WINDOW/config/backend-ingress.nft"
  /usr/sbin/runuser -u ubuntu -- "$VENV/bin/python" -I -B -m pip check
  /usr/sbin/runuser -u ubuntu -- /usr/bin/python3 -I -B "$APP/backend/tools/frontend_release.py" verify --candidate "$APP/frontend"
  printf 'Production preflight: old service, frozen frontend, dependency and ingress syntax verified\n'
}

phase=preflight
redirected=0
has_backup=0
failed_prepare() {
  local status=$1
  trap - ERR HUP INT TERM
  printf >&2 'Preparation failed at %s (status %s)\n' "$phase" "$status"
  if [[ $redirected == 1 ]]; then
    if [[ $has_backup == 1 ]]; then
      if /bin/bash "$WINDOW/plain-travel-rollback.sh" restore-before-opening "$WINDOW"; then
        printf 'Recovery after failure: legacy backend and HTTP restored\n'
      else
        printf >&2 'Recovery needs inspection. Leave routes gated and preserve state.\n'
      fi
    elif restore_early; then
      printf 'Recovery after failure: legacy backend and HTTP restored\n'
    else
      printf >&2 'Early recovery needs inspection. Leave routes gated and preserve state.\n'
    fi
  fi
  exit "$status"
}

prepare() {
  local tool name
  trap 'failed_prepare "$?"' ERR
  trap 'failed_prepare 130' HUP INT TERM
  preflight
  phase=close_http
  /usr/bin/python3 -I -B "$WINDOW/tools/switch_travel_cleartext.py" \
    --candidate "$WINDOW/config/nginx-http-after-tls.conf" --shared-container "$SHARED_ID" --shared-image "$SHARED_IMAGE" \
    --backup "$WINDOW/shared-nginx-before.conf" --apply
  redirected=1
  phase=stop_old_backend
  /usr/bin/systemctl stop travel.service
  phase=backup
  /bin/bash "$WINDOW/plain-travel-rollback.sh" backup "$WINDOW"
  has_backup=1

  phase=publish_frontend
  /usr/sbin/runuser -u ubuntu -- /usr/bin/python3 -I -B "$APP/backend/tools/frontend_release.py" publish --candidate "$APP/frontend" --target "$FRONT"
  phase=install_units
  for tool in switch_travel_https_mode.py verify_travel_cutover_ready.py guard_travel_write_opening.py register_travel_tls_edge.py; do
    /usr/bin/install -o root -g root -m 0644 -T -- "$WINDOW/tools/$tool" "$LIB/$tool"
  done
  /usr/bin/install -o root -g root -m 0600 -T -- "$WINDOW/config/backend-ingress.nft" "$RULES"
  /usr/bin/install -o root -g root -m 0644 -T -- "$WINDOW/config/travel-backend-ingress.service" "$INGRESS"
  /usr/bin/install -o root -g root -m 0644 -T -- "$WINDOW/config/travel.service" "$UNIT"
  for name in app.db app.db-wal app.db-shm app.db-journal; do
    if [[ -e "$DATA/$name" || -L "$DATA/$name" ]]; then
      [[ -f "$DATA/$name" && ! -L "$DATA/$name" && $(stat -c '%u %g' -- "$DATA/$name") == '1000 1001' ]]
      /usr/bin/chmod 0600 -- "$DATA/$name"
    fi
  done
  /usr/bin/chmod 0700 -- "$DATA"
  phase=enable_ingress
  /usr/bin/systemctl daemon-reload
  /usr/bin/systemctl start travel-backend-ingress.service
  /usr/bin/systemctl is-active travel-backend-ingress.service
  phase=start_new_backend
  (set -o noclobber; /usr/bin/date --utc --iso-8601=seconds > "$WINDOW/backend-start-attempted")
  /usr/bin/sync -f -- "$WINDOW"
  /usr/bin/systemctl start travel.service
  phase=verify_closed_ingress
  /usr/bin/timeout 40s /bin/sh -c 'until /usr/bin/python3 -I -B /usr/local/libexec/verify_travel_cutover_ready.py --inspect-live; do sleep 1; done'
  printf 'state=prepared_with_public_write_gate_closed\n'
  trap - ERR HUP INT TERM
}

open() {
  local digest pin_sha
  [[ $(cat "$ROLLBACK/window-path") == "$WINDOW" && -f $WINDOW/backend-start-attempted ]]
  digest=$(sha "$ROLLBACK/legacy.sqlite3")
  [[ $digest == "$(cat "$ROLLBACK/legacy.sqlite3.sha256")" ]]
  verify_ingress
  /usr/bin/systemctl is-active --quiet travel-ip-renew.timer
  /usr/bin/systemctl stop travel-ip-renew.timer
  /usr/bin/timeout 55s /bin/sh -c 'until [ "$(systemctl is-active travel-ip-renew.service || true)" = inactive ]; do sleep 1; done'
  pin_sha=$(sha "$PIN")
  /usr/bin/python3 -I -B "$LIB/switch_travel_https_mode.py" --mode normal \
    --configuration "$WINDOW/config/nginx-edge.conf" --maintenance-configuration "$WINDOW/config/nginx-edge-maintenance.conf" \
    --previous-target-sha256 "$pin_sha" --backup-sha256 "$digest" --opening-record "$WINDOW/write-opening.json" --apply
}

resume_renewal() {
  /usr/bin/python3 -I -B -c 'import sys; sys.path.insert(0,"/usr/local/libexec"); import travel_tls_edge as e; spec=e.parse_target(e.read_root_file(e.PIN,4096)); e.Edge(spec).peer()'
  /usr/bin/systemctl start travel-ip-renew.timer
  /usr/bin/systemctl show --no-pager -p ActiveState,SubState,NextElapseUSecRealtime travel-ip-renew.timer
}

case "$ACTION" in
  preflight) preflight ;;
  prepare) prepare ;;
  open) open ;;
  resume-renewal) resume_renewal ;;
  *) printf >&2 'Invalid action\n'; exit 2 ;;
esac
