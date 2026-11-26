# 旅行站明文备份与回退方案

适用于 2026 年 11 月 25 日已完成的第一次新版切换。备份留在原机 root 私有目录。本方案使用 SQLite 一致性备份和旧版文件副本。窗口内停写后导出的明文备份已通过校验，实际命令及输出见执行记录。[上线执行记录](../docs/verification/travel-plain-cutover-2026-11-25.md)

## 旧版保全

| 对象 | 原机当前路径 | 窗口内保全方式 |
| --- | --- | --- |
| 旧版应用 | `/opt/travel/app/backend/app` | `cp -a` 到 `/var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy-app`，逐文件比较。原部署目录未包含 `.git`。 |
| 旧版运行环境 | `/opt/travel/venv` | 原位保留。旧 unit 指向该绝对路径。新版使用独立的版本化环境。 |
| 旧版静态文件 | `/opt/continew/web/travel` | `cp -a` 到同一回退目录的 `legacy-static`。发布器另外保存原首页和 manifest。原静态目录只读核对时有 25 个文件。 |
| 原有效后端 unit | `/etc/systemd/system/travel.service` | 保存原文件字节到回退目录的 `legacy-travel.service` 并固定 SHA-256。 |
| 原共享 nginx 配置 | `/opt/continew/nginx.conf` | 跳转守卫先保存原文件到本窗口根私有目录，再将这份文件另存为 `legacy-shared-nginx.conf`。恢复通过守卫写回原挂载 inode。 |
| 业务 SQLite | `/opt/travel/app/backend/data/app.db` | 停写后用 SQLite Backup API 导出为 `legacy.sqlite3`，校验完整性、外键、权限与 SHA-256。 |

生产数据库只读核对时为 335,872 字节，未打开其内容。明文目标目录 `/var/lib/travel-cutover/rollback-20261125-5f6fbd51` 为 `root:root 0700`，数据库为 `root:root 0600`。报表仅记录路径、权限、大小、摘要和检查结果。旧签名配置保留在原部署路径。

## 停写后导出

本次操作包暂存回执给出的 `$WINDOW` 为 `/var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d`。确认 HTTPS 维护、共享 HTTP API 拒绝及旧写入进程退出后执行。脚本检查有效服务状态、8000 监听和 SQLite 文件打开者。

```bash
sudo -n /bin/bash "$WINDOW/plain-travel-rollback.sh" backup "$WINDOW"
```

脚本先把已停写的主库和存在的 WAL 或 journal 复制到回退目录中的私有临时目录，再在临时副本上执行以下导出。SQLite 自行重建临时 SHM。这可避免备份进程在生产数据目录创建 root 所有的边车文件。临时副本在明文备份通过校验后清理。回退文件同名已存在时停止。

```bash
APP=/opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281
BACKUP=/var/lib/travel-cutover/rollback-20261125-5f6fbd51/legacy.sqlite3
/usr/bin/python3 -I -B "$APP/backend/tools/backup_db.py" \
  --source "$SOURCE_COPY/app.db" --output "$BACKUP" --timeout 30
/usr/bin/python3 -I -B "$APP/backend/tools/backup_db.py" --source "$BACKUP" --verify-only --timeout 15
```

## 新版在对外开放前失败

仍处于维护、共享 HTTP 写入口关闭且本窗口写入开放记录不存在时，可执行完整回退。要求同时核对实际代理身份和门禁。已经尝试启动新版时，脚本先在生产数据目录的 root 私有子目录保全失败库和 SQLite 边车文件，再以本次导出的快照制作新文件，通过同一文件系统的原子替换恢复旧 `app.db`。已发布的首页和 manifest 由发布器恢复，原静态文件逐项对比。脚本只撤销本次安装且规则内容匹配的 `travel_guard` 表，恢复旧 unit，并等待旧后端健康后恢复共享 HTTP。

```bash
sudo -n /bin/bash "$WINDOW/plain-travel-rollback.sh" restore-before-opening "$WINDOW"
```

从回退命令启动到公网复验预计 **2–5 分钟**。这个区间是按停服务、SQLite 校验、等待后端就绪和两次外部检查估算，完整生产回退耗时待实际执行测量。

## 新版开放后失败

先恢复 HTTPS 维护。以已审阅的专用代理脚本执行，暂时停止续期定时器，并确认续期服务已经退出。使用当时实际登记目标的 SHA-256。

```bash
sudo -n /usr/bin/systemctl stop travel-ip-renew.timer
test "$(systemctl is-active travel-ip-renew.service || true)" = inactive
PIN_SHA=$(sudo -n /usr/bin/sha256sum /etc/travel-tls/edge-target.json | cut -d ' ' -f 1)
sudo -n /usr/bin/python3 -I -B /usr/local/libexec/switch_travel_https_mode.py \
  --mode maintenance --configuration "$WINDOW/config/nginx-edge-maintenance.conf" \
  --maintenance-configuration "$WINDOW/config/nginx-edge-maintenance.conf" \
  --previous-target-sha256 "$PIN_SHA" --apply
```

独立核对 HTTPS `503`、证书与目标登记后恢复续期定时器。保持 HTTP 写入关闭，保全当前库和新写入，优先修复新版。回维护预计 **1–3 分钟**，业务恢复时间取决于故障原因与切换后写入。是否整体降级依据对新写入的独立核对和另行恢复决定。

需要停后端调查时，确认本窗口的 HTTPS 和 HTTP 写入口均已关闭，再保全最新库。读取生产 SQLite 前核对后端进程、8000 监听及数据库文件打开者均已退出。最后一个命令的目标使用一个新文件名。

```bash
APP=/opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281
sudo -n /usr/bin/systemctl stop travel.service
sudo -n /usr/bin/ss -H -ltn 'sport = :8000'
sudo -n /usr/bin/fuser -v /opt/travel/app/backend/data/app.db
sudo -n /usr/bin/install -d -o root -g root -m 0700 "$WINDOW/failure-after-opening"
sudo -n /usr/bin/python3 -I -B "$APP/backend/tools/backup_db.py" \
  --source /opt/travel/app/backend/data/app.db \
  --output "$WINDOW/failure-after-opening/current.sqlite3" --timeout 30
```

## 本次切换时间与执行命令

北京时间 **2026 年 11 月 25 日 12:30** 开始切换，12:32:53 记录开放写入。关闭写入期间完成旧版保全、明文备份、新版安装和专用代理入站验收。公网外部复验门禁后运行 `open`，再通过 `resume-renewal` 恢复续期任务。浏览器冒烟测试最终通过。本次无需用户手动输入口令或运行命令。

```bash
WINDOW=/var/lib/travel-cutover/cutover-c69ec00afa6c4324ce93ca6d
sudo -n /bin/bash "$WINDOW/apply-travel-cutover.sh" prepare "$WINDOW"
# 从外部复验维护状态后，执行以下命令。
sudo -n /bin/bash "$WINDOW/apply-travel-cutover.sh" open "$WINDOW"
# 从外部复验 HTTPS 和指纹后，恢复续期任务。
sudo -n /bin/bash "$WINDOW/apply-travel-cutover.sh" resume-renewal "$WINDOW"
```

浏览器冒烟测试从本机执行，使用合成账号和私有测试旅行。

```powershell
node deploy/live-travel-smoke.cjs --live
```
