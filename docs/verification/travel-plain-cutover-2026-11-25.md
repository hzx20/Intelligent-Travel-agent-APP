# 2026-11-25 明文回退与切换执行记录

使用北京时间。2026-11-25 12:30 为公布的停写开始时间。以下是执行命令和回执，生产命令按实际进度记录。

## 第 2 步原机只读核对

```powershell
$probe = @'
set -eu
systemctl show -p ActiveState,SubState,MainPID,NeedDaemonReload,DropInPaths,FragmentPath,WorkingDirectory travel.service
for path in /opt/travel/app/backend/app /opt/travel/venv /opt/travel/venv/bin/python /opt/travel/app/backend/data /opt/travel/app/backend/data/app.db /opt/continew/web/travel /opt/continew/web/travel/index.html /opt/continew/web/travel/assets /var/lib/travel-cutover; do
  sudo -n /usr/bin/stat -c '%n %F %a %U:%G %s' "$path"
done
printf 'legacy_checkout_has_git='
if test -e /opt/travel/app/.git; then printf 'yes\n'; else printf 'no\n'; fi
printf 'legacy_public_static_files='
find /opt/continew/web/travel -type f -printf . | wc -c
printf 'legacy_public_static_bytes='
du -sb /opt/continew/web/travel | cut -f 1
printf 'filesystem_free_bytes='
df -PB1 /var/lib/travel-cutover | awk 'NR==2 {print $4}'
printf 'static_mountpoint='
if mountpoint -q /opt/continew/web/travel; then printf 'yes\n'; else printf 'no\n'; fi
sudo -n /usr/bin/sha256sum /etc/systemd/system/travel.service /opt/continew/nginx.conf /etc/travel-tls/edge-nginx.conf
printf 'travel_guard_table='
if sudo -n /usr/sbin/nft list table inet travel_guard >/dev/null 2>&1; then printf 'present\n'; else printf 'absent\n'; fi
printf 'fuser_path='
command -v fuser
# end
'@
$probe.Replace([string][char]13, '') | ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 /bin/sh -s
```

```text
MainPID=1247175
WorkingDirectory=/opt/travel/app/backend
ActiveState=active
SubState=running
FragmentPath=/etc/systemd/system/travel.service
DropInPaths=
NeedDaemonReload=no
/opt/travel/app/backend/app directory 755 ubuntu:ubuntu 4096
/opt/travel/venv directory 775 ubuntu:ubuntu 4096
/opt/travel/venv/bin/python symbolic link 777 ubuntu:ubuntu 7
/opt/travel/app/backend/data directory 755 ubuntu:ubuntu 4096
/opt/travel/app/backend/data/app.db regular file 644 ubuntu:ubuntu 335872
/opt/continew/web/travel directory 755 ubuntu:ubuntu 4096
/opt/continew/web/travel/index.html regular file 644 ubuntu:ubuntu 453
/opt/continew/web/travel/assets directory 755 ubuntu:ubuntu 4096
/var/lib/travel-cutover directory 700 root:root 4096
legacy_checkout_has_git=no
legacy_public_static_files=25
legacy_public_static_bytes=230675
filesystem_free_bytes=43837816832
static_mountpoint=no
7ff4dc5f4c925f607f1b36ddf5b0fef6f228c22eb5c4dc0f5c8e2d3824496c07  /etc/systemd/system/travel.service
300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73  /opt/continew/nginx.conf
e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e  /etc/travel-tls/edge-nginx.conf
travel_guard_table=absent
fuser_path=/usr/bin/fuser
```

```powershell
..\.venv-analysis\Scripts\python.exe -m pytest -q backend/tests/test_database_backup.py backend/tests/test_frontend_release.py backend/tests/test_switch_travel_cleartext.py backend/tests/test_guard_travel_write_opening.py
```

```text
..........................................                               [100%]
42 passed in 1.33s
```

## 第 3 步暂存与预检

```powershell
python .release-candidates/supplier-city-2026-11-20/stage_cutover_operations.py --include-legacy-restore --stage-reviewed-plan 00d9d923db78f725f101ad955ddd16b97209d7be481958f8eeeaa605b30d9d83
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'sudo -n /bin/bash /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d/apply-travel-cutover.sh preflight /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d'
python deploy/check-travel-public-edge.py --mode baseline --interface-index 18
```

```text
{"state":"staged_and_verified","operations_id":"cc20126ea565ff67579527f98b3e6a4e63ddcd3093fcf966a22b568041247cbf","members":12,"attempt":"c69ec00afa6c4324ce93ca6d","origin_private_directory":"/var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d"}
{"state":"plain_cutover_scripts_staged","window":"/var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d","hashes":{"apply-travel-cutover.sh":"86b8e0dfbb3de425c295678f032e51422d39e34ef7cb9604e229b8e627ca595e","plain-travel-rollback.sh":"765160983697e2271d7855f2b6ddb8e0ee43f9513dc72757d85718d90cfecc04"},"bash_syntax":"passed"}
No broken requirements found.
{"action": "verify", "result": "7c620cde98827c6704b818f28f14ab49ddc594f44d188cff28f87c0797304aff"}
Production preflight: old service, frozen frontend, dependency and ingress syntax verified
{"mode": "baseline", "certificate_trusted_and_ip_verified": true, "leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_8000_blocked": true, "direct_probe_interface": 18, "statuses": {"shared_http_root": 200, "travel_http_page": 200, "travel_http_api": 200, "travel_https_page": 503, "travel_https_api": 503}}
```

案头 Bash 解析和服务实际启动分开记录。默认网络对 8000 报出 TCP 握手，但无 HTTP 响应。WLAN 18 直连验证通过，作为本次对照。

停写前确认导出只打开受限目录中的 SQLite 副本。合成 WAL 夹具通过，原机私有脚本更新时核对前后指纹，再做 Bash 语法检查。隔离 Edge 已以默认信任访问实际维护页。

```text
Private source copy verified; committed WAL data preserved; original test DB and WAL unchanged.
{"state": "updated_private_rollback_copy", "sha256": "e5ac009754eb356cc20fc960d6b77091a314887cf441cb3e03fdb1f8c33e3ae3", "bash_syntax": "passed"}
{"browser":"Microsoft Edge","pre_cutover_maintenance_http":503,"tls_protocol":"TLS 1.3","certificate_warning":false}
```

北京时间 12:23 的定时检查完成后复核公网及证书。

```powershell
python backend/tools/observe_travel_edge_load.py --inspect-origin --interface 18
```

```text
{"state": "scheduled_hook_verified", "timer_trigger_utc": "2026-11-25T04:23:45.125205+00:00", "hook_outcome": "unchanged", "reload_sent": false, "saved_selected_and_public_leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_maintenance_and_legacy_http_verified": true}
```

## 第 3 步实际切换

```powershell
$start=[DateTimeOffset]::Parse('2026-11-25T12:30:00+08:00')
if ([DateTimeOffset]::UtcNow -lt $start) { throw '维护窗口尚未到达' }
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'sudo -n /bin/bash /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d/apply-travel-cutover.sh prepare /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d'
```

主要输出如下。等待检查重复输出 `ingress_unconfirmed`，退出码为 1。此时公网仍在维护。

```text
No broken requirements found.
{"action": "verify", "result": "7c620cde98827c6704b818f28f14ab49ddc594f44d188cff28f87c0797304aff"}
Production preflight: old service, frozen frontend, dependency and ingress syntax verified
{"state": "redirect_active_and_verified", "config_sha256": "8e489e8d949d0d8df92d8ae832a1f8b10a547e20d6ed5174b2c36941ff0648fc", "original_config_backup_sha256": "300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73", "shared_root_unchanged": true}
SQLite file openers: none
Verified SQLite snapshot: /var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy.sqlite3 (335872 bytes)
Verified SQLite integrity and foreign keys (read-only)
rollback_directory=/var/lib/travel-cutover/rollback-20261125-5f6fbd51
verified_backup_sha256=b192321a2d9a97c54feb093a5b0677323bf6686b4146984d886b6bdb151398ea
{"action": "publish", "result": "7c620cde98827c6704b818f28f14ab49ddc594f44d188cff28f87c0797304aff"}
active
{"state": "ingress_unconfirmed", "error_type": "ValueError"}
Preparation failed at verify_closed_ingress (status 124)
Rollback operation stopped at line 124. Keep maintenance and inspect the private files.
Recovery needs inspection. Leave routes gated and preserve state.
```

```powershell
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'sudo -n /usr/bin/sha256sum /opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281/release.json /etc/systemd/system/travel.service /etc/systemd/system/travel-backend-ingress.service /etc/travel-tls/backend-ingress.nft'
```

```text
63514d2d03011ba0404ce1de3bd3b65155532136cfa61f8424299ab4bba9a3fb  /opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281/release.json
ec03ed87a56ea67acf870c71e6722fd3cd2e041af79e712b310cb3be4d36649e  /etc/systemd/system/travel.service
e63ce1ddd6f79bc1be6f50aa5a318c15c9c489cfbe90edc6db9c2489c811a92e  /etc/systemd/system/travel-backend-ingress.service
707816e51b6926eea6814c275d8420f131407d07337a54b66ad9ee274c5c3b89  /etc/travel-tls/backend-ingress.nft
```

原机 unit 与冻结包中的文件及清单一致。两处工具内的同一摘要录入时缺少字符。通过现有 SSH 使用受限 Python 更正两处校验文本，保存原工具并核对语法及落盘摘要。

```text
{"state": "corrected_two_reviewed_unit_pins", "unit_sha256": "ec03ed87a56ea67acf870c71e6722fd3cd2e041af79e712b310cb3be4d36649e", "tool_sha256": {"/usr/local/libexec/verify_travel_cutover_ready.py": "14f4819b1bba90d2f75513264725fe5aae09860657e597aeed956667420dfb47", "/var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d/plain-travel-rollback.sh": "18d7b700e501ea2bb6fbb9aef4bc369f04f5b9d871c9b2b836caec0f6850b18c"}}
```

```powershell
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'sudo -n /usr/bin/python3 -I -B /usr/local/libexec/verify_travel_cutover_ready.py --inspect-live'
python deploy/check-travel-public-edge.py --mode closed --interface-index 18 --expected-leaf 19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'sudo -n /bin/bash /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d/apply-travel-cutover.sh open /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d'
python deploy/check-travel-public-edge.py --mode open --interface-index 18 --expected-leaf 19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'sudo -n /bin/bash /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d/apply-travel-cutover.sh resume-renewal /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d'
```

```text
{"state": "maintenance_ingress_verified", "application_id": "28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281", "dedicated_proxy_reached_backend": true, "anonymous_trip_read_http": 401, "nonproxy_host_connection_dropped": true, "https_travel_page_remained_maintenance": true}
{"mode": "closed", "certificate_trusted_and_ip_verified": true, "leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_8000_blocked": true, "direct_probe_interface": 18, "statuses": {"shared_http_root": 200, "travel_http_page": 308, "travel_http_api": 403, "travel_https_page": 503, "travel_https_api": 503}}
{"container_id": "efe4bb2d1d7a5d75962e84be3c8c03b28e837a45a88f49118af735a56ed3dba9", "previous_target_sha256": "74b7165d6be745c2b86446f5ac1142dca7c1b2002204dd48d5ee2126876955df", "target_sha256": "b19cf88e7d916b48c859be6092cbe260aefbbb111e2ced3f531397ab7cbf6610", "public_leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "state": "normal_edge_verified", "renewal_timer_still_stopped": true}
{"mode": "open", "certificate_trusted_and_ip_verified": true, "leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_8000_blocked": true, "direct_probe_interface": 18, "statuses": {"shared_http_root": 200, "travel_http_page": 308, "travel_http_api": 403, "travel_https_page": 200, "travel_https_api": 200}}
NextElapseUSecRealtime=Wed 2026-11-25 18:15:23 CST
ActiveState=active
SubState=waiting
```

## 第 4 步冒烟、图标修复和复验

首次执行 `node deploy/live-travel-smoke.cjs --live`，首页、证书、主要页面、登录及表单通过。控制台记录一条 `404`，退出码为 1。只读浏览器复查将错误定位到 `/favicon.ico`。修复后构建只增加 PNG，并修改 HTML 及 manifest。已有 JS、CSS 文件均与冻结版逐文件一致。

```powershell
npm run build:travel
python backend/tools/frontend_release.py prepare --source frontend/dist --output .release-candidates/supplier-city-2026-11-20/frontend-favicon-20261125
python .release-candidates/supplier-city-2026-11-20/publish_favicon_patch.py --publish-reviewed-patch
python deploy/check-travel-public-edge.py --mode open --interface-index 18 --expected-leaf 19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c --frontend-manifest .release-candidates/supplier-city-2026-11-20/frontend-favicon-20261125/release.json
node deploy/live-travel-smoke.cjs --live
```

构建命令在 `frontend` 下执行，其余命令在仓库根目录执行。主要输出如下。

```text
✓ 136 modules transformed.
✓ built in 1.20s
{"action": "prepare", "result": "15fe8379a9e2b19370a7cf67b4344cfc942b9f270b76f1ea88357600725d56fa"}
{"state": "favicon_frontend_published_and_verified", "frontend_id": "15fe8379a9e2b19370a7cf67b4344cfc942b9f270b76f1ea88357600725d56fa", "candidate_path": "/opt/travel/frontend-releases/15fe8379a9e2b19370a7cf67b4344cfc942b9f270b76f1ea88357600725d56fa", "changed_frontend_files": [".vite/manifest.json", "assets/travel-favicon-Clb1qSVb.png", "index.html"]}
{"mode": "open", "certificate_trusted_and_ip_verified": true, "leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_8000_blocked": true, "direct_probe_interface": 18, "statuses": {"shared_http_root": 200, "travel_http_page": 308, "travel_http_api": 403, "travel_https_page": 200, "travel_https_api": 200}}
{"checks":[{"name":"首页","passed":true,"http":200},{"name":"HTTPS 证书与浏览器信任","passed":true,"issuer":"YE2","protocol":"TLS 1.3","expires_utc":"2026-11-29T13:21:39.000Z","certificate_warning":false},{"name":"网站图标","passed":true,"http":200},{"name":"页面 灵感","passed":true,"http":200},{"name":"页面 天气与航班","passed":true,"http":200},{"name":"页面 景点","passed":true,"http":200},{"name":"页面 攻略","passed":true,"http":200},{"name":"页面 AI 规划","passed":true,"http":200},{"name":"页面 创建旅行","passed":true,"http":200},{"name":"登录与个人中心","passed":true,"login_http":200,"profile_http":200},{"name":"核心表单","passed":true,"create_http":201,"persisted_after_reload":true},{"name":"站内静态资源","passed":true,"observed":36,"missing":[]},{"name":"浏览器控制台与页面异常","passed":true,"count":0,"errors":[]}],"test_account_created":true,"test_trip_created":true}
```

两次真实浏览器冒烟各产生一个合成账号和一条私有测试旅行。

## 第 5 步归档前回读

```powershell
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'systemctl show -p ActiveState,SubState,WorkingDirectory,NeedDaemonReload travel.service; systemctl show -p ActiveState,SubState travel-backend-ingress.service; systemctl show -p ActiveState,SubState,NextElapseUSecRealtime travel-ip-renew.timer; sudo -n /usr/bin/stat -c "%n %a %U:%G %s" /var/lib/travel-cutover/rollback-20261125-5f6fbd51 /var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy.sqlite3 /var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy-app /var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy-static; sudo -n /usr/bin/sha256sum /var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy.sqlite3; sudo -n /usr/bin/stat -c "%y" /var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d/write-opening.json'
```

```text
WorkingDirectory=/opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281/backend
ActiveState=active
SubState=running
NeedDaemonReload=no
ActiveState=active
SubState=exited
NextElapseUSecRealtime=Wed 2026-11-25 18:15:23 CST
ActiveState=active
SubState=waiting
/var/lib/travel-cutover/rollback-20261125-5f6fbd51 700 root:root 4096
/var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy.sqlite3 600 root:root 335872
/var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy-app 755 ubuntu:ubuntu 4096
/var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy-static 755 ubuntu:ubuntu 4096
b192321a2d9a97c54feb093a5b0677323bf6686b4146984d886b6bdb151398ea  /var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy.sqlite3
2026-11-25 12:32:53.806014112 +0800
```

## 上线后证书验收适配

验收脚本使用原机登记并核对过的代理模式，实际校验新版 HTTPS 首页和健康接口、HTTP 页面跳转及明文 API 拒绝。维护模式按对应的公网响应检查。

```powershell
..\.venv-analysis\Scripts\python.exe -m pytest -q backend/tests/test_observe_travel_edge_load.py
python -c "import json; import sys; sys.path.insert(0, 'backend'); from tools.observe_travel_edge_load import verify_public; print(json.dumps(verify_public(18)))"
python backend/tools/observe_travel_edge_load.py --inspect-origin --interface 18
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes ubuntu@134.175.137.18 'systemctl show --no-pager -p ActiveState,SubState,NextElapseUSecRealtime travel-ip-renew.timer'
```

```text
10 passed in 0.06s
{"saved_selected_and_public_leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_edge_mode": "normal", "public_http_mode": "https_redirect", "public_routes_verified": true}
{"state": "waiting_for_first_hook_execution", "timer_trigger_utc": "2026-11-25T04:23:45.125205+00:00"}
NextElapseUSecRealtime=Wed 2026-11-25 18:15:23 CST
ActiveState=active
SubState=waiting
```

定时器恢复后的第一次实际运行已于北京时间 18:19:55 验收。任务结果为 `success`，自动装载回执为 `unchanged`。保存证书、代理选中证书和公网证书指纹一致，跳过 nginx 重载，站点及明文路由复核通过。下一次检查预计为 11 月 26 日 00:22:19，实际更换仍以新公网指纹和装载回执为准。

```powershell
python backend/tools/observe_travel_edge_load.py --inspect-origin --interface 18
ssh.exe -T -oBatchMode=yes -oStrictHostKeyChecking=yes -oConnectTimeout=8 -oClearAllForwardings=yes -oForwardAgent=no ubuntu@134.175.137.18 'systemctl show --no-pager -p ActiveState,SubState,LastTriggerUSec,NextElapseUSecRealtime travel-ip-renew.timer; systemctl show --no-pager -p Result,ExecMainStatus travel-ip-renew.service'
```

```text
{"state": "scheduled_hook_verified", "timer_trigger_utc": "2026-11-25T10:19:55.298994+00:00", "hook_outcome": "unchanged", "reload_sent": false, "saved_selected_and_public_leaf_sha256": "19c0f0f01f07f295794d5cf58764a689d32fe8a2acec69e1d91aef606f27c44c", "public_edge_mode": "normal", "public_http_mode": "https_redirect", "public_routes_verified": true}
NextElapseUSecRealtime=Thu 2026-11-26 00:22:19 CST
LastTriggerUSec=Wed 2026-11-25 18:19:55 CST
ActiveState=active
SubState=waiting
Result=success
ExecMainStatus=0
```

## 上线后移动视口验收

使用 Edge 和 Chrome 的隔离触摸视口访问公网新版。点击顶部折叠菜单进入天气与航班，再从底部导航创建游客旅行，将手动收集的地点安排到第 2 天。刷新后核对地点和当前日期。检查首页、天气与航班、创建页和旅行详情的横向溢出，并统计各视口的资源请求、控制台和 API 写入。游客数据仅保存在临时浏览器会话中。

```powershell
node .release-candidates/supplier-city-2026-11-20/verify-live-mobile.cjs
node .release-candidates/supplier-city-2026-11-20/verify-live-mobile.cjs chrome
```

```text
{"viewport":{"width":390,"height":844},"homepage_http":200,"touch_navigation":true,"guest_plan_persisted_after_reload":true,"horizontal_overflow":false,"assets_observed":20,"asset_failures":0,"console_errors":0,"production_write_attempts":0}
{"viewport":{"width":320,"height":667},"homepage_http":200,"touch_navigation":true,"guest_plan_persisted_after_reload":true,"horizontal_overflow":false,"assets_observed":20,"asset_failures":0,"console_errors":0,"production_write_attempts":0}
{"browser":"chrome","viewport":{"width":390,"height":844},"homepage_http":200,"touch_navigation":true,"guest_plan_persisted_after_reload":true,"horizontal_overflow":false,"assets_observed":20,"asset_failures":0,"console_errors":0,"production_write_attempts":0}
{"browser":"chrome","viewport":{"width":320,"height":667},"homepage_http":200,"touch_navigation":true,"guest_plan_persisted_after_reload":true,"horizontal_overflow":false,"assets_observed":20,"asset_failures":0,"console_errors":0,"production_write_attempts":0}
```

## 上线后生产 SQLite 结构验收

先校验冻结清单和模型源码，提取已上线版本要求的 SQLite 结构。在原机以服务账号使用 SQLite `mode=ro` 和 `query_only` 读取结构元数据。引用的生产服务在核对前后保持同一版本和进程。未查询业务行。

```powershell
..\.venv-analysis\Scripts\python.exe -B .release-candidates/supplier-city-2026-11-20/verify_live_schema.py
..\.venv-analysis\Scripts\python.exe -B .release-candidates/supplier-city-2026-11-20/verify_live_schema.py --inspect-origin
```

```text
{"state": "review_only", "release": "28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281", "tables": 30}
{"release": "28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281", "inspection": "sqlite_schema_metadata", "launch_read_only": true, "business_rows_queried": false, "counts_verified": {"tables": 30, "columns": 258, "primary_keys": 30, "unique_constraints": 18, "indexes": 47, "foreign_keys": 42}, "missing": []}
```

## 上线后生产分享与撤权验收

在生产站创建两个合成测试账号和一条私有旅行，记录模拟预订和费用。匿名链接只返回公开行程，重复激活返回同一链接。未登录接受邀请返回 `401`，只读核对不授予成员权限。用 Edge 显式接受邀请后，协作者编辑成功且所有者可回读。撤销邀请、移除成员后，协作者的读取与重新接受都返回 `404`。撤销只读链接后，浏览器刷新清除公开行程，重放原激活请求返回 `409`。两个测试链接均已撤销，成员数为零。两个合成账号及其私有测试记录仍在。

```powershell
node .release-candidates/supplier-city-2026-11-20/verify-live-sharing.cjs --live
```

```text
{"anonymous_view_http":200,"public_privacy_fields_hidden":true,"activation_retry_http":200,"invitation_requires_login_http":401,"readback_before_acceptance_members":0,"explicit_browser_acceptance":true,"editor_write_http":200,"editor_access_after_removal_http":404,"view_after_revocation_http":404,"activation_after_revocation_http":409,"test_links_revoked":2,"active_test_members":0,"synthetic_accounts_created":2,"synthetic_private_trips_created":1}
```

## 上线后天气与航班配置同步

用户授权仅同步 `QWEATHER_API_HOST`、`QWEATHER_API_KEY`、`VARIFLIGHT_API_KEY` 并重启 `travel.service`。三项配置通过 SSH 标准输入传送。原配置副本留在原机 `/var/lib/travel-cutover/supplier-env-sync-20261125/previous.env`，目录和文件权限分别为 `0700`、`0600`，由 root 保管。配置值未写入归档。

```powershell
..\.venv-analysis\Scripts\python.exe -B .release-candidates/supplier-city-2026-11-20/sync_live_supplier_config.py
..\.venv-analysis\Scripts\python.exe -B .release-candidates/supplier-city-2026-11-20/sync_live_supplier_config.py --apply-approved-sync
curl.exe -q --noproxy '*' -fsS --max-time 8 -w '\n{"health_http":%{http_code}}\n' https://134.175.137.18/travel/api/health
curl.exe -q --noproxy '*' -sS --max-time 8 -o NUL -w '{"unauthenticated_supplier_status_http":%{http_code}}\n' https://134.175.137.18/travel/api/travel-data/status
```

```text
{"state": "supplier_sync_prepared", "local_required_fields": 3, "origin_contacted": false}
{"state": "supplier_config_synced", "fields_written": 3, "new_backend_pid_verified": true, "public_health_http": 200, "protected_backup_created": true, "supplier_requests": 0}
{"status":"ok"}
{"health_http":200}
{"unauthenticated_supplier_status_http":401}
```

通过 SSH 用已部署代码只读检查生产配置，核对当前版本、启动时间及两项航班参数拒绝。运行时使用 PowerShell 将探针送入 Python 标准输入。日后可用同一探针文件复核。

```powershell
python -B .release-candidates/supplier-city-2026-11-20/verify_live_supplier_config.py
```

```text
{"deployed_version":"28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281","qweather":{"configured":true},"variflight":{"configured":true},"env_file_changed_since_service_start":false,"invalid_flight_cases_rejected_by_deployed_model":2,"supplier_requests":0}
```

本次未发起供应商请求，生产真实数据和供应商权限待验收。

## 上线后真实天气与航班查询

2026-11-25 用户另行授权线上查询。使用 Edge 390×844 触摸视口和一个新建合成账号，登录公网天气与航班页，通过页面各提交一次杭州天气、2026-12-02 上海至北京直飞、同日同航线票价。每种查询只允许发出一次。飞常准本次公示用量为 75 积分，未核对账单。测试账号仍在生产，未创建行程或订单。

```powershell
node --check .release-candidates/supplier-city-2026-11-20/verify-live-supplier-browser.cjs
node .release-candidates/supplier-city-2026-11-20/verify-live-supplier-browser.cjs
node .release-candidates/supplier-city-2026-11-20/verify-live-supplier-browser.cjs --run-approved-queries
```

```text
{"viewport":"390x844 Edge touch viewport","date":"2026-12-02","synthetic_accounts_created":1,"queries":{"weather":{"http":200,"display_verified":true,"days":7,"attribution_displayed":true},"route":{"http":200,"display_verified":true,"text_blocks":1,"structured_result":false},"prices":{"http":200,"display_verified":true,"text_blocks":1,"structured_result":false}},"blocked_queries":0,"console_errors":0,"page_errors":0,"supplier_status_http":200,"automatic_query_before_click":false,"no_horizontal_overflow":true,"clear_results_after_sign_out":true,"state":"passed","dispatched":["weather","route","prices"]}
```

和风返回七天预报和来源声明。飞常准两次均返回非空文本，页面显示日期、航线、查询类型和来源。未保存供应商原文或价格。退出后结果清除，页面无横向溢出，控制台与页面错误均为零。价格和航班随供应商变化，实际手机验收仍待补充。


