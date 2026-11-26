# 首次大版本发布与回退手册

本手册记录正式切换的顺序与停止条件。执行前先取得本次维护窗口及具体生产变更的授权。

## 2026-11-25 上线验收调整

用户确认 OpenPGP 恢复口令已无法找回，取消上线前的加密快照解密验收，并确认接受因此失去既有加密快照恢复能力的风险。旧加密快照作废，排除出本次回退依据。发布验证改为上线后冒烟测试。正式切换前先审定旧版保全和 SQLite 明文回退备份方案。[发布交接](../docs/交接归档-航班新版Linux与HTTPS绑定.md#2026-11-25-用户决定调整上线验收)

北京时间 12:30 已执行切换，12:32:53 记录开放写入，明文备份及上线后冒烟均通过。受限备份目录、导出命令和按开放时点区分的回退命令见独立方案。[明文备份及回退方案](PLAIN_ROLLBACK.md) · [执行记录](../docs/verification/travel-plain-cutover-2026-11-25.md)

## 2026-11-20 生效的发布基线

- 发布应用为 `28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281`，冻结计划为 `3546a422a4ce7b43bdde35b184c39f1d4ab886e33eb6df151dcf43c7346fdb80`，专用 HTTPS 配置包为 `f85a32dea9051cb7379b34ddba704f26ed4f990b0e728e1d61b4eef22ded70bd`。材料在私有目录 `.release-candidates/supplier-city-2026-11-20/` 的 `private-plan/` 和 `private-https-companion/`。冻结计划内的手册副本保留审阅时的字节，切换采用本节列出的配套配置与步骤。[发布交接](../docs/交接归档-航班新版Linux与HTTPS绑定.md)
- 正式代码与 63 项离线依赖已预置。两份候选 unit 和入站规则通过原机静态预检，正式路径通过隔离启动与重启，专用代理来源的 8000 入站规则通过隔离报文验收。现役服务仍为旧版，旧共享代理从 `172.18.0.5` 访问 `172.18.0.1:8000`。新版使用 `br-travel-edge`，后端绑定 `172.31.240.1:8000`，专用 nginx 为 `172.31.240.2`。[原机预检](../docs/verification/travel-https-actual-host-preflight-2026-11-20.json) · [入站规则隔离回执](../docs/verification/travel-staged-backend-ingress-2026-11-20.json)
- 2026-11-24 公网 HTTPS 维护代理已试发并保留。公网旅行页与 API 返回 503，原 HTTP 根路径与旅行页仍为 200，现役旧版健康。自动装载已接入专用续期服务，换代后需复核公网叶证书指纹。重建独立代理前，在切换窗口内先暂停续期任务并确认退出，重新登记完整目标且验证代理后再恢复定时器。[公网维护入口](../docs/verification/budget-public-maintenance-retained-2026-11-24.json) · [自动装载回执](../docs/verification/budget-edge-renewal-activation-2026-11-24.json)

## 已证实的限制

与生产逐文件SHA256相同的旧源码在本机合成库中经历旧→新→旧→新启动。升级后的库可被旧版读取，所测写入没有SQL结构错误，新旅行和新增表数据保留。但旧版修改资料/攻略不推进新版版本号；返回新版后，旧版本号的过时请求仍会200并覆盖中间修改。因此禁止将“旧版启动成功/数据库完整”作为透明可写回退通过条件。这是Windows/Python3.13的源码级行为验收，不是完整Linux回退证明。

必须区分两种时点：

| 时点 | 允许考虑的恢复路径 | 必须停止的情况 |
|---|---|---|
| 新版尚未开放、未接受任何业务写入 | 保持维护，停止新版，保全失败现场；恢复经校验的发布前备份与原代码/原前端，核验后才开放 | 无法证明没有新写入、备份不完整、有效配置/挂载状态不明 |
| 已接受新版业务写入 | 保持维护，停止写入，保存当前库及关联照片的可恢复快照；优先修复向前或回到已验证兼容的新架构版本 | 只有原旧版可用时，不自动恢复旧快照、不开放旧版写入，不丢弃已确认写入 |

首次新版发布尚无“上一份兼容新架构版本”，后者不能被写成自动可用选项。需要明确人工恢复决定。数据库中恢复业务记录不等同重放可靠写入收据；不得拼接两份库、删除新表或只回滚部分新增列来尝试降级。

## 正式操作前必须具备

- 按发布基线复核同版代码、前端、服务及 nginx 配置，确认实际应用目录权限、117 个应用文件摘要、63 项依赖与 `pip check`。使用已在正式路径创建的 venv。[正式预置回执](../docs/verification/travel-formal-runtime-staged-2026-11-20.json)
- 在应用切换窗口前，从外部确认正式证书的信任链和 IP、维护入口的 `503` 响应及原 HTTP 可用性。只在这一验收通过后，才可以规划 HTTP 旅行路径的后续切换。[公网维护入口](../docs/交接归档-预算版公网维护入口回退.md)
- 原服务有效unit、进程、数据/.env实际路径及权限、Docker IP/网关/挂载均重新核对。现役后端仍0.0.0.0:8000，不能只开启nginx维护就宣称全部写入已停止；备份/迁移前必须停止原写入进程并排除其他写入入口。
- 发布前按审定的回退方案保全旧版代码、前端、服务与代理配置，并在停写点导出 SQLite 明文备份。备份存于受限目录，核对完整性、外键、关联数据和摘要。报告只记录路径、权限和校验结果。
- 原共享nginx为只读单文件bind；宿主原子重命名不等于容器已读新文件。此问题已在同镜像隔离容器中复现，保留inode写入/校验/失败恢复也已实测，见下节。实际生产仍须维护窗口和独占写入条件，不能把临时演练当作生产提升授权。
- 具体、可审阅的最终操作得到用户授权。本次临时演练授权不能扩大成正式安装、reload/restart或切换授权。

## 数据库恢复的验收顺序

冻结预算版的合成恢复演练覆盖了照片、关联预订、成员权限、报价和账本。原机 Linux 旧版依赖复建与启动另有合成演练。正式窗口以本次停写后导出的 SQLite 明文备份为数据库回退依据。[合成业务恢复](../docs/交接归档-预算版备份恢复验收.md)

1. 核对待恢复应用版本、数据目录和实际签名来源。当前候选的 `footprint_photos.data` 为 SQLite BLOB，关联外键也在同一个 `app.db`；仍需清点实际实例的外置附件与配置。`.env`、外置签名设置或旧数据目录使用的 `.jwt_secret` 都不在数据库快照内，按照当前实际来源另行保全，报告只记录位置和权限核对结果。
2. 维持写入门禁，停止所有写入者。保存原代码、前端、有效 unit/drop-in、nginx 和完整依赖的恢复入口。使用 `backend/tools/backup_db.py --source <原库> --output <私有目录中的新文件>`，记录源版本、快照大小和 SHA256；从源重做或追加保存时使用新文件名。保持原库及其已有 WAL/SHM 原样，不直接复制 SQLite 主文件当作完整备份。
3. 对快照运行 `--verify-only`（SQLite 完整性及外键检查），再用 `--source <快照> --output <隔离数据目录>/app.db` 复制恢复件。启动与快照兼容的应用，在私有入口核对旧数据、版本以及旅行、关联预订、预算、账本和足迹照片权限；核对快照在演练前后哈希相同。记录完成时间、输出体积、权限、空间余量、清理结果及异常，由值守人核对可接受的停机和数据恢复范围。
4. 任何一项不符即保持维护，勿原地覆盖应用数据或删除 WAL。开始开放新写入之后，先保全当前库再决策；不得用发布前旧快照无条件吞掉新写入。实际生产步骤仍以本文的写入时点、门禁和明确授权为准。

### 新版启动或迁移失败

1. 保持公网维护，确认 8000 入口仍受控。停止尝试启动的 `travel.service`，核对进程、监听和所有数据库文件打开者均已退出。记录 `write-opening.json` 的状态，并用同一窗口的代理身份和实际路由核对公网门禁。记录缺失只说明未找到记录，仍需独立证明未开放写入。
2. 保全失败时的数据库及 WAL、SHM、journal 等现有文件和有效配置。保留本次明文备份、当前版本编号和已执行步骤。只读取摘要、权限和存在状态作为报告内容。
3. 按待审定的回退方案，在受限目录中以本次明文备份制作新的隔离恢复件，核对摘要、完整性和外键，再用相符的旧版做隔离回读。失败现场继续保留。
4. 只有确认未接受新版写入且获准恢复时，才按恢复表决定是否采用已验收的旧库候选。若见到写入记录或证据不完整，继续保全现场并按向前修复路径处理。前端如已发布，可用同一冻结版本的 `frontend_release.py rollback` 在维护期间单独回退静态入口，使用 `$FRONT` 和该次发布回执的 `--release-id`。旧库、旧 unit、入口规则及路由全部验收后，才能恢复访问。

## 正式路径和权限的准入检查

通过 `backend/tools/inspect_release_host.py` 从既有 SSH 账号读取 `travel.service` 的生效属性、文件元数据、真实监听、nginx 容器挂载和配置哈希。脚本不读 `.env` 或数据库内容；配置文件与 systemd 单元只返回摘要或固定条件的判断值。`NeedDaemonReload`、drop-in、监听和两侧配置字节均要重新核对，不能用仓库基线代替已加载配置。磁盘上的 nginx 哈希与容器文件一致也不证明主进程已加载该版本。

2026-11-21 原机复查。现役 unit 与原记录哈希一致，无 drop-in 或待加载改动，仍是旧版 `0.0.0.0:8000`。共享 nginx 的宿主源、容器内挂载文件和冻结基线三个摘要一致。已预置的代码及 venv 均可由服务账号读取和执行，服务账号无法写入。`.env` 已为 `0600`，实际权限差异是数据目录 `0755` 和 `app.db` 的 `0644`。在获批窗口停写并核对回退快照后，分别收紧为 `0700`、`0600`，保留原服务账号属主。现场另核对 SQLite 边车和 ACL。[原机切换基线](../docs/verification/travel-cutover-origin-inventory-2026-11-21.json)

1. 正式目录按同一 application 编号使用 `/opt/travel/releases/<id>` 和 `/opt/travel/runtime-envs/<id>`。在已批准的窗口内，由管理员管理 `/opt/travel` 顶层和两个正式父目录；服务账号不得写这些父目录或版本树，否则即便子目录归 root 所有也可能被更名替换。预期目录 `root:root`、`0755`，普通代码文件 `0644`，解释器可执行，正式 venv 在目标路径新建。仅处理审定的顶层/新目录，不能递归改旧 `app`、旧 `venv` 或共享站点树。核对服务账号能遍历、读代码和执行解释器，不能写代码/venv；保留父目录原元数据供有条件恢复。
2. 现有 `/opt/travel/app/.env` 保持原路径、服务账号可读取、其他账号不应读取；应用直接读取该文件，systemd 不载入它。`/opt/travel/app/backend/data` 必须让服务账号可写、其他账号不得读取；安装前以 `0700` 数据目录、`0600` 库和已存在的同库 WAL/SHM 为审查目标。先退出旧写入者并保全文件，入窗后核对属主、类型和 ACL，再在审批范围内处理需要调整的项。安装件 `UMask=0077` 仅影响新建文件，不收紧已经存在的库。不要搬动原数据或删除 WAL。
3. 新 `travel.service` 的生效文件由管理员控制，服务用户无写入权限。核对有效 `WorkingDirectory`、`ExecStart`、单 worker、`User/Group`、EnvironmentFile/drop-in、`PrivateTmp`/`NoNewPrivileges`/`UMask` 和监听。正式 HTTPS 代理将 `/opt/continew/web/travel` 只读挂载到 `/usr/share/nginx/html/travel`。若静态发布替换了子目录 inode，重新建立该挂载并逐端核对实际资源。旧服务监听 `0.0.0.0:8000` 时，仍须独立核对旧版写入门禁。[代理挂载与证书装载](../docs/交接归档-预算版首次装载与续期接入.md)

## 统一私有发布计划

`backend/tools/release_plan.py`只生成和校验私有计划，不安装、迁移、启动服务或发布。计划包含严格校验的application运行包、63项离线wheel与哈希锁、原共享nginx字节及按application模板重新生成的正常/维护候选、服务profile和按同一application编号生成的完整travel.service。完整共享配置包含另一站点，只能保存在Git忽略私有目录，不能公开发布这个计划。

本次 HTTPS 切换的拟安装件是 `private-https-companion/travel.service`、`travel-backend-ingress.service` 和 `backend-ingress.nft`。冻结计划中 `ops/travel.service` 的地址对应旧共享 nginx。正式操作前核对实际有效 unit 和 drop-in，确认新版仅以已审阅的完整 unit 生效。专用 nginx 的维护及正常配置、旧共享 nginx 的 HTTP 配置也从同一 HTTPS 配置包取用。[实际路径预检](../docs/verification/travel-https-actual-host-preflight-2026-11-20.json)

本次窗口使用私有目录下的 `cutover-operations-v3/` 作为冻结操作包，由 6 份配套配置和 5 份现场工具组成。`manifest.json` 将操作文件绑定到当前应用、冻结计划与 HTTPS 配置包。使用 `assemble_cutover_ops.py --include-legacy-restore --verify cc20126ea565ff67579527f98b3e6a4e63ddcd3093fcf966a22b568041247cbf` 可核验该历史包。现场更正 unit 摘要校验和图标补丁的工具版本见执行记录。共享 nginx 配置包含同机站点的设置，按获批范围使用原机私有路径传输和保存。正式应用及 venv 使用各自经过核对的已有路径。[执行记录](../docs/verification/travel-plain-cutover-2026-11-25.md)

用 `stage_cutover_operations.py --include-legacy-restore` 在本机审阅上传包及接收逻辑。新窗口获批后添加 `--stage-reviewed-plan 00d9d923db78f725f101ad955ddd16b97209d7be481958f8eeeaa605b30d9d83`。原机核对包摘要、清单和全部成员，再写入新建的 root 私有目录并回读。成功回执给出本窗口 `$PRIVATE` 绝对路径，用作后续操作的输入。首次窗口的 `cutover-operations-v2/` 及摘要保留供核对。[窗口回退回执](../docs/verification/travel-cutover-maintenance-2026-11-25.json)

校验器除逐项哈希外，会重新生成nginx/服务并比较，核对直接依赖及extras被Linux锁覆盖、官方wheel位置字段、wheel内嵌名称/版本及目标tag，拒绝额外文件、空数据目录、符号链接或不同版本校验器。verify命令需要独立保存的expected-id，避免把被整体重新编号的改动误当作已审阅计划。该编号是完整性锚，不是数字签名；静态校验不证明运行路径/环境/供应商或生产授权就绪。

application 的 `prepared_python` 记录实际生成解释器。本机生成的 3.13 包不能冒称 Linux 3.12 候选；若镜像已验收的原生候选，须核对原生运行回执中的 Python、编号、成员与前端编号，同时使本机镜像逐文件通过校验。新源码须重新在实际 Linux 解释器生成和验收。临时 63 包 venv 仅用于隔离验证，不搬迁到正式目录。

## 有界维护与切换顺序

1. 核对公网 HTTPS 维护入口及已审定的明文回退方案。记录当前公开入口、共享 nginx 的挂载源和哈希、现役服务、预置版本以及获批的操作范围。
2. 进入获批维护窗口，确认 HTTPS 旅行页面和 API 均返回 `503`，带 `Cache-Control: no-store` 和 `Retry-After`。按下一节保存一份供保护收集器读取的原共享 nginx 副本，再用守卫安装 `nginx-http-after-tls.conf`，使旅行页面 `GET`/`HEAD` 转向 HTTPS 维护入口，明文 API 及旅行写入方法返回 `403`。从外部核对这些响应、同机另一站点和 80 的 ACME 校验。完成门禁复验后再停止旧服务。
3. 停止旧版服务并确认原 PID 已退出、8000 上的旧监听消失、数据库及边车文件无写入者。按审定的回退方案导出本次明文备份，记录时点、权限、摘要和 SQLite 校验结果，核对原共享配置。保持停写并进入受控升级，或按恢复表恢复旧版。
4. 复用 `releases/<id>` 和 `runtime-envs/<id>`。按专用 HTTPS 配置包安装获批的入站规则及后端 unit，保留原 `.env` 和数据目录。核对专用网桥的实际地址，按顺序确保防护单元生效后启动绑定 `172.31.240.1:8000` 的新版单 worker，仅信任 `172.31.240.2` 的转发头。三个共享开关继续按审阅值关闭。
5. 保持公网写入门禁。先用 `verify_travel_cutover_ready.py --inspect-live` 复核有效单元、监听、已加载的 8000 规则及隔离的代理读回。再核对受控账号登录后的只读路径、静态文件和有效配置摘要。从独立验证端检查相邻来源、公网直连和旧代理是否被阻断。合成业务写入用隔离库完成。任一验收失败则按上表恢复。
6. 维持写入门禁，按下节用 `switch_travel_https_mode.py` 核对静态产物、专用 nginx 和新版入站。独立代理开放前先把本次应用编号、已验证的回退备份 SHA-256 和可能开始接受写入的状态落盘。结果不明或恢复维护时保留记录，按回退表和独立门禁证据决定后续动作。只要无法排除新版已接受写入，就先保全当前库及关联数据。[开放写入守卫回执](../docs/verification/travel-write-opening-guard-2026-11-20.json)
7. 上线后逐项冒烟测试，核对首页 `200`、HTTPS 证书有效且无告警、主要页面、登录和核心表单、静态资源无 `404`、浏览器控制台无报错。同步核对 HTTP 转向、明文 API 拒绝和同机站点，观察错误率、资源、任务与锁。浏览器从 HTTP 转到 HTTPS 后，使用原账号重新登录并核对云端已保存的旅行。HTTP 下的浏览器登录状态及本机草稿不会随重定向进入 HTTPS。切换前将这点告知现有用户，已发出但结果未知的提交先核对云端记录。可取得原提交标识时以原 `request_id` 重试。异常时先恢复维护并保全当前数据。[浏览器同源定义](https://html.spec.whatwg.org/multipage/browsers.html#concept-origin) · [Web Storage 规范](https://html.spec.whatwg.org/multipage/webstorage.html)

维护转换保留原http限流/map及非旅行配置，仅替换旅行locations。它不会停止现役8000、后台任务或其他写入者，也没有提供发布命令。正常规则可以从维护候选重新合并，测试已验证恢复字节与相同模板正常候选一致；这只证明配置转换可逆，不证明业务数据可逆。

## 新版安装前退出维护

如果明文备份准备失败或决定结束窗口，并且新版启动和迁移均未开始，使用本窗口 root 私有目录中的原配置恢复旧版。守卫校验原 unit 和有效配置，拒绝已有新版入站单元或写入开启记录的场景。旧后端在回环健康检查就绪后才恢复共享 HTTP，等待超时则保留维护门禁。完成后从公网复验 HTTP 页面和健康接口为 `200`，HTTPS 维护入口为 `503`。

```sh
sudo -n /usr/bin/python3 -I -B "$PRIVATE/tools/switch_travel_cleartext.py" \
  --restore-before-upgrade --candidate "$PRIVATE/shared-nginx-before.conf" \
  --shared-container "$SHARED_CONTAINER_ID" --shared-image "$SHARED_IMAGE_ID" \
  --backup "$PRIVATE/shared-nginx-redirect-before-resume.conf" --apply
```

## 确认本次明文回退备份后安装新版

保持 HTTPS 维护和 HTTP 写入拦截，并复核旧后端仍退出、无 8000 监听及数据库文件打开者。核对本窗口 `$PRIVATE` 与操作包暂存回执一致，确认旧 unit 摘要、本次明文备份的完整性及摘要记录，再以原机 `ubuntu` 账号执行。发现任何命令失败时保持维护并核对已落盘步骤。进入新版后端启动前，明确记录尝试启动的时点并按本手册恢复表处理其后的失败。

```sh
set -eu
: "${PRIVATE:?本窗口 root 私有路径}"
APP=/opt/travel/releases/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281
DATA=/opt/travel/app/backend/data
FRONT=/opt/continew/web/travel
UNIT=/etc/systemd/system/travel.service
INGRESS=/etc/systemd/system/travel-backend-ingress.service
RULES=/etc/travel-tls/backend-ingress.nft

test "$(sha256sum "$UNIT" | cut -d ' ' -f 1)" = 7ff4dc5f4c925f607f1b36ddf5b0fef6f228c22eb5c4dc0f5c8e2d3824496c07
test "$(systemctl show -p ActiveState,MainPID,NeedDaemonReload,DropInPaths --value travel.service | sort | tr '\n' '|')" = '|0|inactive|no|'
test -z "$(ss -H -ltn 'sport = :8000')"
test "$(readlink -f "$DATA")" = "$DATA"
test -f "$DATA/app.db"
test ! -L "$DATA/app.db"
test "$(readlink -f "$FRONT")" = "$FRONT"
sudo -n /usr/bin/test ! -e "$PRIVATE/legacy-travel.service"
test ! -e "$INGRESS"
sudo -n /usr/bin/test ! -e "$RULES"
sudo -n /usr/bin/install -o root -g root -m 0600 -T "$UNIT" "$PRIVATE/legacy-travel.service"

/usr/bin/python3 -I -B "$APP/backend/tools/frontend_release.py" verify --candidate "$APP/frontend"
/usr/bin/python3 -I -B "$APP/backend/tools/frontend_release.py" publish --candidate "$APP/frontend" --target "$FRONT"
for tool in verify_travel_cutover_ready.py switch_travel_https_mode.py register_travel_tls_edge.py guard_travel_write_opening.py; do
  sudo -n /usr/bin/install -o root -g root -m 0644 -T "$PRIVATE/tools/$tool" "/usr/local/libexec/$tool"
done
sudo -n /usr/bin/install -o root -g root -m 0600 -T "$PRIVATE/config/backend-ingress.nft" "$RULES"
sudo -n /usr/bin/install -o root -g root -m 0644 -T "$PRIVATE/config/travel-backend-ingress.service" "$INGRESS"
sudo -n /usr/bin/install -o root -g root -m 0644 -T "$PRIVATE/config/travel.service" "$UNIT"

for name in app.db app.db-wal app.db-shm app.db-journal; do
  if test -e "$DATA/$name" || test -L "$DATA/$name"; then
    test -f "$DATA/$name"
    test ! -L "$DATA/$name"
    test "$(stat -c '%u %g' "$DATA/$name")" = '1000 1001'
    chmod 0600 "$DATA/$name"
  fi
done
chmod 0700 "$DATA"
sudo -n /usr/bin/systemctl daemon-reload
sudo -n /usr/bin/systemctl start travel-backend-ingress.service
sudo -n /usr/bin/systemctl is-active travel-backend-ingress.service
```

确认入口规则已装载后，启动新版后端并等待实际入站验收通过。`systemctl start` 返回后应用仍可能在启动。以下等待超过 35 秒时保持 HTTPS 维护，检查后端状态。完成下一节的其余入站验收后才进入独立代理切换。

```sh
sudo -n /usr/bin/systemctl start travel.service
timeout 35s /bin/sh -c '
  until sudo -n /usr/bin/python3 -I -B /usr/local/libexec/verify_travel_cutover_ready.py --inspect-live; do
    sleep 1
  done
'
```

## 保持维护时验证实际入站

在获批窗口将新验收工具与现有证书脚本放在同一个 root 管理目录。`verify_travel_cutover_ready.py --inspect-live` 核对正式版本摘要、两个生效单元、监听 `172.31.240.1:8000` 的同一个后端进程和内核中的 `travel_guard`。它从已登记的维护容器发出健康与匿名读请求，要求分别返回 `200` 和 `401`。随后由宿主尝试一次直连，要求连接超时且允许和丢弃计数分别增加。最后再次确认维护页、后端进程及监听。此处实际验证的是维护代理和宿主直连路径，生产账号、同桥其他容器和公网负向检查仍有独立步骤。[本机与原机基线回执](../docs/verification/travel-closed-ingress-verifier-2026-11-24.json) · [nftables JSON 格式](https://manpages.ubuntu.com/manpages/jammy/man5/libnftables-json.5.html)

## 独立代理切换后恢复证书自动装载

1. 切换前记录当前维护容器的完整 ID、配置摘要、精确挂载、目标 JSON 摘要和当前公开叶证书指纹。停止专用 `travel-ip-renew.timer` 的触发，确认 `travel-ip-renew.service` 已退出，再变更容器或配置。若续期在进行，等待完成并重新核对已保存和公网证书。保留受限证书目录与各代证书。
2. 切换正常代理或重建维护代理后，用宿主和容器内的同一份已审阅配置摘要核对实际实例。正常代理须具备只读静态目录挂载。确认完整 ID、实际镜像、`travel-edge` 网桥、`172.31.240.2`、仅 IPv4 TCP 443 的映射及有效的 `nginx -t`。从外部核对模式对应响应、证书信任与 IP，确认实际送出的指纹等于即时保存和选中的证书。
3. 在获批窗口把经审阅的现场工具放入原机 root 管理的 `/usr/local/libexec`，与证书脚本同目录。正常模式用下述命令，在无网、无公网端口的临时容器先做语法核验，校验静态产物和新版实际入站后登记写入开放时点，再重建独立旅行容器。工具按新容器完整 ID 和实际证书更新续期绑定。过程中失败时尝试恢复维护代理和续期目标，保留开放记录及业务数据。成功后仍由窗口操作者独立复核公网并恢复定时器。外部复验后需回维护时，取当前目标摘要并使用 `--mode maintenance`，两个配置参数均指向维护配置。无法识别现役目标时，依据现场回执核对端口、容器和当前目标摘要后恢复。单纯重新登记已核验的容器时可使用 `register_travel_tls_edge.py`。[本机验收与包回执](../docs/verification/travel-dedicated-edge-switch-fixture-2026-11-24.json)

   `$VERIFIED_BACKUP_SHA` 使用本次已核对的 SQLite 明文备份摘要。

   ```sh
   sudo -n /usr/bin/python3 -I -B /usr/local/libexec/switch_travel_https_mode.py \
     --mode normal --configuration "$PRIVATE/config/nginx-edge.conf" \
     --maintenance-configuration "$PRIVATE/config/nginx-edge-maintenance.conf" \
     --previous-target-sha256 "$PREVIOUS_EDGE_TARGET_SHA" \
     --backup-sha256 "$VERIFIED_BACKUP_SHA" --opening-record "$PRIVATE/write-opening.json" --apply
   ```

## 单文件bind配置提升和恢复（隔离验证过的算法）

`backend/tools/switch_travel_cleartext.py` 对已审阅的原配置和 HTTP 跳转配置固定摘要。获批后将守卫与配置保存到原机受控的私有目录，使用现场核对的共享容器完整 ID、镜像 ID，以及私有目录中的新备份文件路径。默认调用仅核对候选。带 `--apply` 时检查旧版 HTTP、独立 HTTPS 维护和共享配置挂载，先落盘原配置，再在同一 inode 上写入。信号前后检查配置可见性、共享 nginx 身份、旅行门禁和同机根路径。测试或响应异常会尝试恢复原配置。发现其他写入者改动或恢复结果不明时，保存原配置证据并停止切换。此工具用于旧后端仍健康的第 2 步，后续阶段按写入时点决定恢复路径。[守卫和原机只读回执](../docs/verification/travel-cleartext-cutover-guard-2026-11-24.json)

```sh
sudo -n /usr/bin/python3 -I -B "$PRIVATE/tools/switch_travel_cleartext.py" \
  --candidate "$PRIVATE/config/nginx-http-after-tls.conf" \
  --shared-container "$SHARED_CONTAINER_ID" --shared-image "$SHARED_IMAGE_ID" \
  --backup "$PRIVATE/shared-nginx-before.conf" --apply
```

先在私有审阅目录保留完整原配置字节、候选、差异和哈希。实际生产应用仍待具体命令/路径/恢复件最终审阅及授权；`qa_nginx_bind_switch.py`只允许临时根/自建标签容器，不是生产发布器。

1. 重新确认宿主配置与容器当前文件都等于经审阅的基线；容器/image、挂载源、有效PID和文件权限也必须匹配。若两边不一致，停止，不自动重建现役容器或猜测哪份正确。
2. 在控制其他配置写入者/自动reload的维护窗口内，打开原文件的无符号链接描述符并独占锁定；核对描述符与路径的device/inode、当前字节哈希。保存回退字节，不用rename覆盖生产单文件bind源。
3. 通过同一描述符写入完整候选、截断到确切长度并fsync，保持inode和权限/归属。再次核对文件身份和宿主/容器字节都等于候选。锁不能约束不合作的外部写入者，发现漂移就停止，不覆盖别人的更新。
4. 检查完整`nginx -t`的退出码，不能只看到首行“syntax is ok”就放行。本次实际镜像对不可绑定地址先输出该行、最终退出1，守卫已验证会拦截。
5. 仅在可见性和检查均通过后，向批准的master发加载信号。信号退出0仍需独立HTTP验证：旅行入口内容/状态、缺失资源、API语义、缓存头和另一站点只读基线。隔离试验中曾出现语法/信号均0而入口404，HTTP门槛正确发现失败。
6. 检查失败且未发送信号时，原内存配置仍服务；通过相同描述符恢复保存字节，再核对双侧哈希和完整检查。HTTP失败时先恢复经验证的维护配置、确认503，再保全当前数据库/关联文件并按写入时点决定下一步，不自动还原旧业务库。
7. 经过批准继续恢复正常规则时，仍执行双侧哈希→完整检查→加载→HTTP语义验证。临时容器已实测“正常→维护→坏配置/HTTP失败→维护→正常”，不代表生产掉电、磁盘写失败、并发配置修改或所有其他站点业务已验收。

[nginx官方控制说明](https://nginx.org/en/docs/control.html)区分重新读取配置、应用配置与旧worker退出；[Docker bind mount文档](https://docs.docker.com/engine/storage/bind-mounts/)说明挂载机制。本次具体镜像行为以保存的退出码、双侧哈希和HTTP结果为准，不能将其他版本文档的简述当作实测结果。

## 停止条件与留痕

任一备份、关联文件、正式venv、有效unit、代理信任、实际生产配置提升前提、回退时点或权限门槛未通过时，`production_ready=false`。Linux维护规则和隔离配置可见性/恢复已获得本轮证据，仍不能用它们或既有327项离线回归替代正式路径/有效unit及生产最终门槛。

每次实际操作单独保存日期、候选/源哈希、授权范围、命令退出状态、健康/资源结果、恢复路径和清理结果。记录不含业务行、密钥或token；完整共享配置保留在私有审阅目录。未知进程/目录不清理，观察超时后先核对原句柄/确切进程，不盲目重启或再次迁移。
