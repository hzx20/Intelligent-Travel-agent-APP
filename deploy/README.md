# 部署说明（腾讯云轻量 · 2 核 4G · Ubuntu 24.04）

最新进展（2026-11-17，active）：用户恢复整体目标后，真实临时systemd隔离启动/主进程中断自动重启、原JWT和同次创建重放通过；25项相关回归通过，最终34.23秒。各轮自建unit/端口/合成work独立复核已清理，生产配置及主PID未变。旧冻结计划不含最新地图前端，正式目录/有效unit仍未安装，production_ready=false。完成范围、失败修正及恢复见[本次运行交接](../docs/交接归档-systemd隔离启动与自动重启.md)。

本文保留现役布局及早期部署说明；拟发布服务以统一私有计划的ops/travel.service为准，不直接激活下方旧base unit或旧drop-in候选。正式操作仍需最终授权。

线上地址：**http://134.175.137.18/travel/**

这台服务器同时跑着另一个项目（AI 工单系统，占用 80 端口根路径）。旅行规划 Agent
挂在 `/travel/` 子路径下，两个站点共用一个 nginx 容器，互不影响。

## 架构

```
浏览器
  │  http://134.175.137.18/travel/
  ▼
continew-web（nginx 1.27 容器，80 端口）
  ├─ /travel/             → /usr/share/nginx/html/travel/（静态文件，容器内）
  └─ /travel/api/         → http://172.18.0.1:8000/api/（宿主机上的 FastAPI）
                                   │
                                   ├─ SQLite  /opt/travel/app/backend/data/app.db
                                   ├─ 智谱 GLM-4-Flash（行程生成）
                                   └─ 高德开放平台（POI 核实 / 天气 / 周边酒店）
```

## 目录与进程

| 项 | 位置 |
|---|---|
| 后端代码 | `/opt/travel/app/backend` |
| Python 虚拟环境 | `/opt/travel/venv` |
| 环境变量 | `/opt/travel/app/.env`（600 权限，不入库） |
| 数据库 | `/opt/travel/app/backend/data/app.db` |
| 前端静态文件 | `/opt/continew/web/travel`（容器挂载为 `/usr/share/nginx/html/travel`） |
| 后端服务 | `systemctl status travel`（uvicorn，0.0.0.0:8000） |
| 反向代理 | `continew-web` 容器内的 nginx，配置在宿主机 `/opt/continew/nginx.conf` |

## 首次部署

```bash
# 1. 代码与依赖
sudo mkdir -p /opt/travel/app /opt/travel/web
sudo chown -R $USER:$USER /opt/travel
# 把仓库内容解压到 /opt/travel/app（不含 .git）
python3 -m venv /opt/travel/venv
/opt/travel/venv/bin/pip install -r /opt/travel/app/backend/requirements.txt

# 2. 环境变量（四个钥匙 + JWT 密钥）
cat > /opt/travel/app/.env <<'EOF'
ZHIPU_API_KEY=...
AMAP_API_KEY=...
AMAP_JS_KEY=...
AMAP_JS_SECURITY_CODE=...
JWT_SECRET=...
EOF
chmod 600 /opt/travel/app/.env

# 3. 采集景点数据（5 城 500 条，约 2 分钟）
cd /opt/travel/app/backend
/opt/travel/venv/bin/python scripts/fetch_pois.py --all

# 4. 后端服务
sudo cp deploy/travel.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now travel

# 5. 前端（构建到 /travel/ 子路径）
cd /opt/travel/app/frontend && npm ci && npm run build:travel
cd /opt/travel/app
mkdir -p /opt/travel/releases
/opt/travel/venv/bin/python backend/tools/frontend_release.py prepare \
  --source frontend/dist --output /opt/travel/releases/frontend-001
/opt/travel/venv/bin/python backend/tools/frontend_release.py verify \
  --candidate /opt/travel/releases/frontend-001

# 6. nginx（只加 location 块，不动原有站点）
#    把 deploy/nginx-travel.conf 里的内容合并进 /opt/continew/nginx.conf 的 server 块
sudo docker exec continew-web nginx -t && sudo docker exec continew-web nginx -s reload

# 7. 配置验证后发布候选；记录命令输出的64位发布编号
/opt/travel/venv/bin/python backend/tools/frontend_release.py publish \
  --candidate /opt/travel/releases/frontend-001 --target /opt/continew/web/travel
```

## 日常更新

```bash
# 后端：先按下方备份/副本演练步骤验证，再重启发布（默认单进程）
sudo systemctl restart travel

# 前端
cd /opt/travel/app/frontend && npm run build:travel
cd /opt/travel/app
# 按下方流程使用全新候选目录，验证后再发布；保留旧哈希资源。
```

### 前端候选、发布与回退

以下是部署者核对目标目录和上线条件后执行的操作示例，本机本轮只在临时目录演练。先完成后端兼容升级、备份及恢复预检；合并并实际验证 nginx 配置，包含 `.vite/` 与 `.travel-deploy/` 的拒绝访问规则。候选必须放在公开站点之外，不能用整目录删除同步替代这里的操作。

```bash
# 仓库根目录；每次输出目录必须全新，frontend-002仅为示例。
/opt/travel/venv/bin/python backend/tools/frontend_release.py prepare \
  --source frontend/dist --output /opt/travel/releases/frontend-002
/opt/travel/venv/bin/python backend/tools/frontend_release.py verify \
  --candidate /opt/travel/releases/frontend-002
/opt/travel/venv/bin/python backend/tools/frontend_release.py publish \
  --candidate /opt/travel/releases/frontend-002 --target /opt/continew/web/travel

# 把RELEASE_ID替换为上一步输出的64位编号；status只读。
/opt/travel/venv/bin/python backend/tools/frontend_release.py status \
  --target /opt/continew/web/travel --release-id RELEASE_ID
/opt/travel/venv/bin/python backend/tools/frontend_release.py rollback \
  --target /opt/continew/web/travel --release-id RELEASE_ID
```

候选只收录入口、Vite清单及其列出的资源，逐文件SHA256验证，不携带 `.env`、数据库或未引用旧文件。发布先创建新资源，同名不同内容拒绝覆盖，再写服务器清单，最后原子替换 `index.html`；旧标签需要的旧哈希资源保留。回退恢复该次保存的入口与清单，保留两版资源。首次发布若无旧入口，回退会移除本次入口，站点将没有可用前端。此工具不回退后端、数据库、配置或供应商操作。

同目标目录使用排他锁，必须由一个部署账号串行操作，禁止同时运行其他同步工具。遇到锁不自动抢占；进程崩溃后先核对锁记录及精确进程，确认原发布已结束后再由部署者处理，不能盲删锁。资源写入需要支持同卷硬链接的本地文件系统，失败时不切换入口；目录不得经符号链接或junction。发布工具在Linux上新建公开目录0755、文件0644，控制目录0700、记录0600；已有挂载目录属主、父目录可读性、Windows ACL需独立核对。

重复发布同一候选可核对丢失回执，保存的旧入口不会被重取；`status`报告记录阶段及当前文件是否匹配。回退可继续中断操作，旧编号不能覆盖不同的后续版本，已回退编号不能再次激活。入口和清单不是跨文件事务；异常时依据记录核对/重试，不承诺断电持久化或不遵守锁的外部写入安全。工具不清理旧资源和记录，需另定保留及磁盘容量策略，本轮未实施资源回收。

### 后端与前端绑定的代码候选

`backend/tools/release_candidate.py` 可将当前app Python源码、固定的四个运维工具、景点采集脚本、已锁直接依赖清单和部署说明/模板，与一份已校验的前端候选绑定。它不携带.env、JWT签名文件、业务库、运行额度/缓存库、虚拟环境、测试或其他工具，不安装依赖、不采集景点、不迁移数据、不部署。业务密钥不得硬编码到源码；文件白名单不能替代密钥审查。

```bash
# 仓库根目录；输入前端需先完成prepare/verify，输出目录必须全新。
/opt/travel/venv/bin/python backend/tools/release_candidate.py prepare \
  --source . --frontend /opt/travel/releases/frontend-002 \
  --output /opt/travel/releases/app-002
/opt/travel/venv/bin/python backend/tools/release_candidate.py verify \
  --candidate /opt/travel/releases/app-002

# 在预定运行环境检查直接依赖版本和Python主/次版本；不安装或升级包。
/opt/travel/venv/bin/python /opt/travel/releases/app-002/backend/tools/release_candidate.py runtime \
  --candidate /opt/travel/releases/app-002
```

候选release.json绑定全部文件哈希、前端发布编号和打包时Python版本；校验拒绝文件篡改/增删和前端编号不一致。哈希证明内容一致，不证明来源签名或生产可用。`runtime`只检查requirements中直接锁版本及Python主/次版本；uvicorn extras、传递依赖、Linux动态库仍需在目标环境安装后独立验收，不能将本机venv复制到Linux。当前本机Python为3.13.14，不推断Ubuntu 24.04上的实际Python版本；不同版本需在对应环境重新准备和验证候选，不能忽略不匹配报告。

版本目录运行前，先核对并保留数据和密钥路径。应用默认从代码根.env、backend/data读取；可通过进程环境的 `TRAVEL_ENV_FILE` 选择已有绝对环境文件，显式路径为空、相对、缺失或目录会在配置初始化时失败，未设置仍沿用默认。`TRAVEL_DATA_DIR`独立指定已有数据目录，.env中的同名字段不会自动导入进程环境。服务候选采用 `Environment=TRAVEL_ENV_FILE=/opt/travel/app/.env` 和 `Environment=TRAVEL_DATA_DIR=/opt/travel/app/backend/data`，保留原JWT/供应商配置和dotenv解析；进程已有的配置值继续优先。新文件选择必须配套包含该支持的新代码包，旧104文件冻结包不识别该变量。

`deploy/travel-release.conf.template`是审阅模板，需用已验证候选编号、刚核对的nginx容器IP及宿主bridge地址替换所有@...@。当前审阅值为PROXY_IP=172.18.0.5、BACKEND_BIND_IP=172.18.0.1；新后端绑定私网bridge地址，避免以0.0.0.0提供额外入口，已有Linux整链演练验证该绑定可从nginx访问，未修改现役服务。代码拟放`/opt/travel/releases/<编号>`，虚拟环境拟放独立的`/opt/travel/runtime-envs/<编号>`，数据/.env保持原稳定路径；不要在严格校验代码包内安装venv。模板清空继承EnvironmentFile列表，交给应用按TRAVEL_ENV_FILE读dotenv，清理PYTHONPATH/PYTHONHOME、禁止pyc、保持workers1及生产三个共享开关关闭；用户/组ubuntu、UMask0077。实际依赖目录未创建或验收前不能应用该服务配置。已有app.db的0644权限不会因新UMask自动收紧，发布维护时须另行核对。

systemd的EnvironmentFile会覆盖Environment设置、解析语义也不同；本方案用应用自己的dotenv路径选择以保持旧行为，见[systemd执行环境文档](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)。Uvicorn显式CLI的host/port/workers/forwarded-allow-ips优先于环境；候选只信任当时确认的nginx单个IP，不采用通配/整网段，容器重建后需重核，见[Uvicorn官方配置说明](https://github.com/encode/uvicorn/blob/master/docs/settings.md)。所有路径/IP都是审阅数据，当前未安装、未执行daemon-reload或restart。

先对实际历史库做受保护快照，在隔离恢复副本上运行候选并核对业务/私有照片/权限，确认旧代码与升级后结构兼容，再安排后端一致重启，最后用候选内frontend发布。切回旧代码不等于数据库回退；迁移后数据是否向后兼容须单独证明，不能直接用旧库覆盖已产生新写入的业务库。本工具只生成/校验代码包，没有自动修改systemd、切换后端或恢复数据库的能力。

隔离进程验收 `tests/test_release_candidate_runtime.py` 从候选backend启动真实Uvicorn，断言模块实际路径属于候选、数据目录在候选外；合成旧结构经快照/恢复后由包内代码升级，核对旅行/攻略/资料、可靠创建和再次重启，源库和备份哈希保持不变。子进程清空供应商钥匙并阻止外部连接。可设置 `TRAVEL_TEST_RELEASE_CANDIDATE` 为已校验本机候选目录，直接验收该保存候选；默认另建临时包。执行后再verify，确保未在候选内产生数据或pyc。此证据仍不是生产历史数据、Linux或真实供应商验收。

本机候选摘要见 `docs/verification/app-candidate-2026-11-16.json`，当前模块与恢复步骤见 `docs/交接归档-前后端绑定候选与包内升级验收.md`。整体目标按最新指示继续active，原七阶段未完成；此处只准备可审阅候选，未进行生产切换。

## 为什么这样部署

共享nginx候选可在受保护的源码工作区执行 `python -B backend/tools/nginx_merge.py --source-file <私有原配置副本> --template deploy/nginx-travel.conf --output-dir <全新私有目录>`。工具只输出候选、差异及报告，不应用或reload；只支持一个明确旅行server、标准mime.types include和无外部共享冲突的旅行zone/map，其余情况拒绝自动合并。完整共享配置/diff应保持在Git忽略的私有目录，不能进入公开仓库。准备完成的syntax_tested=false需另有明确授权、对同一候选哈希的实际检查才能更新为已验；目前已验证候选SHA和结果见docs/verification/nginx-merge-2026-11-16.json（17日收尾）。此辅助工具不在原104文件运行包中，实际应用/单文件挂载可见性、恢复及最终发布仍需核对。

- **不新开端口**：云防火墙只放行 80/443，8080 等端口从公网连不上（实测 TCP 能握手，
  HTTP 请求无响应）。所以只能共用 80 端口，用子路径区分。
- **不新起 nginx 容器**：直接复用现有的 `continew-web`，少一个进程、少一份内存。
  代价是配置文件由两个项目共享，改动前必须备份并 `nginx -t`。
- **SQLite 而不是 MySQL**：单机低并发够用，省下约 400MB 内存；这台机器还要跑
  MySQL + Redis + Spring Boot。
- **单 worker**：2 核机器上多 worker 收益有限，且进程内缓存（酒店、地图）不共享。

旅行编辑器的 AI 建议任务会在启动时创建 `trip_ai_jobs` 表，重启后重新领取超时任务，最多尝试两次；领取期限为180秒。原AI规划页面现使用独立`plan_generation_jobs`表、60秒租约和心跳，最多执行两次；它不自动修改旅行编辑器的安排。异常中断后重试可能产生额外模型费用，两套调度器及数据协议保持区分。部署前应备份数据库，并验证实际环境中的迁移和重启恢复。

个人足迹照片使用新增的 `footprint_photos` 表保存在数据库，部署需安装 `requirements.txt` 中的 Pillow 并确保数据库备份容量足够。照片接口需要鉴权，不能通过静态目录直接提供；应用限制每张原图 4 MiB，代理的请求体上限需至少支持该大小。

### 数据库备份与恢复演练

仓库提供 `backend/tools/backup_db.py`，使用 SQLite 在线备份接口读取一致快照，包含已提交的 WAL 数据；完成后执行完整性检查。必须明确指定源文件和全新的目标文件，旧备份不会被覆盖。工具不启动应用、不执行迁移，也不自动替换业务库。示例在仓库根目录执行，先准备仅服务账号可访问的备份目录，并为每次备份使用不同文件名：

```bash
/opt/travel/venv/bin/python backend/tools/backup_db.py \
  --source /opt/travel/app/backend/data/app.db \
  --output /opt/travel/backups/before-update-001.db

# 恢复演练：从备份再生成一个独立数据库，不接触正在使用的 app.db。
/opt/travel/venv/bin/python backend/tools/backup_db.py \
  --source /opt/travel/backups/before-update-001.db \
  --output /opt/travel/backups/restore-rehearsal-001.db
```

默认备份阶段等待上限为 60 秒，可用 `--timeout` 调整；失败时清除本次新建的不完整文件，已存在的文件保留。完整性检查证明 SQLite 文件可读，不能替代业务核验：演练应使用隔离服务核对旅行、预订关联、账本、成员权限和私有照片。自动测试已验证临时业务库的旅行、关联预订及照片逐表一致，并覆盖 WAL、未提交数据、超时、损坏源及拒绝覆盖。

实际恢复前应停止全部服务与数据库连接，保留当前库及其 WAL/SHM 文件用于回退，核对恢复文件、应用版本、属主及权限，再由部署者切换数据库并验证启动迁移和业务。不要在服务运行时直接用旧文件覆盖 `app.db`，也不要把仅复制主文件视为 WAL 模式下的完整备份。生产切换与恢复演练尚未执行。

开发环境可运行 `python -m pytest tests/test_database_migration.py tests/test_database_backup.py -q`（在 `backend` 目录）。升级演练合成缺少已知历史列及新增表的旧结构，在备份恢复副本上执行两次当前初始化，验证原始字段保留、补列默认值、新表创建以及行程接口继续读写。该测试覆盖当前补列逻辑，不能证明所有历史部署版本均兼容；上线前仍需在实际数据库的受保护副本中核对结构并演练。缺少旧导入摘要的记录仅补空摘要，不会凭空推断原始导入内容。

备份包含订单号、账号和私有照片，应存放在静态站点目录之外并限制访问；工具在支持的系统上以 0600 创建目标，Windows 还应核对目录 ACL。`.env` 与 JWT 配置需另行安全备份，数据库快照不包含这些文件。

### 独立数据目录与进程重启验收

后端可通过进程环境变量 `TRAVEL_DATA_DIR` 指定绝对数据目录；未设置时继续使用仓库的 `backend/data`。该变量由数据库模块直接读取，应通过 shell 或 systemd 的 `Environment=` 配置；仅写进项目 `.env` 不会生效。目录内保存 `app.db`，未显式配置 JWT_SECRET 时也会保存回退签名文件。服务账号需要该目录的读写权限；变更目录不会自动复制原数据，备份命令的源路径也需随之调整。

`tests/test_service_restart.py` 启动两个实际 Uvicorn 进程，共用一个独立临时磁盘库，经过注册登录、创建旅行/预订/足迹、上传合成照片、停止和再次启动，核对登录凭证、行程版本、预订关联及照片内容。测试禁用外部服务密钥，不连接业务库；公共 pytest 入口也在导入应用前设置临时初始化目录。此证据不等于生产 systemd、容器代理或真实账号重启验收。

同一文件另有旅行AI任务的三进程验收：第一个进程领取任务并在模拟模型响应处等待，停止后只在该临时数据库中将领取期限设为过去，再启动第二个进程验证恢复、第二次执行和单一建议；第三个进程验证已保存建议可恢复，显式确认后才改变行程且拒绝重复应用。仅替换模型传输函数，保留实际调度器、JSON校验和接口；不改生产180秒领取期限，不调用真实模型。原规划页新增独立`test_plan_restart.py`验证重启及双实际进程共享队列，仍不证明生产部署或模型供应商中断恢复。

### 原规划页持久化任务升级

init_db新建`plan_generation_jobs`，本次不为旧表加列，也不能恢复升级前已消失的内存任务。先备份并在副本演练，再重启后端调度器，最后更新前端。新旧进程混跑时，旧进程仍可能创建内存任务，不在新恢复保证内，应安排后端进程一致升级。现有部署仍保持单worker；任务表的独占领取测试不等于全系统限流、地图和酒店缓存已支持扩容。

任务状态、原请求和事件在磁盘保存。租约60秒、每约20秒续约，丢失执行者后重新领取一次；旧令牌不能写事件、历史或最终结果。取消先落库撤销令牌，其他进程在下次心跳/写入时停止；外部已发请求不保证能撤回。每次执行最多10分钟，单进程最多同时领取4个原规划任务，不是跨进程全局并发配额。历史和任务完成同事务；历史保存失败保留结果并标记saved=false。

完成/失败/取消任务可按编号恢复30分钟，之后调度器清理，停机期间物理清理会延迟。登录历史AiPlan不随任务清理。游客任务也临时落库，但没有active列表，随机任务编号等同不公开访问凭证，不应记录或公开；账号任务始终检查归属。新前端会按账号隔离本标签快照及编号，旧v1缓存只迁移到其明确归属。服务重启后通常要等原租约到期，不能承诺立即恢复；自动重新生成不等于供应商模型只计费一次。

原SSE保留为同一磁盘任务的事件转发，断开不取消任务；其无UUID兼容创建不保证去重。原规划页新客户端现使用下述可靠创建。恢复基础验收见`frontend/qa/plan-recovery/README.md`。

### 原规划可靠创建与停止协议

init_db新增`plan_creation_requests`及job_id索引，不改旧表列；先备份副本演练，后端表/能力/接口就绪后再发布前端。GET`/api/plan/jobs/capabilities`支持reliable_creation和scoped_guests时，客户端才POST`/api/plan/jobs/create`。旧POST`/api/plan/jobs`及SSE保留兼容，无标识不保证去重，新客户端不降级。

账号作用域user:id，游客作用域为单次guest_key的SHA256摘要，再与UUID组合唯一。原游客凭证不进数据库/任务输入/回执。会话、历史、槽位和清洗聊天快照的摘要固定，改内容409；任务与映射同事务，首次201/重复200找回同编号。任务过期或硬删除后410不重新生成。清理先解除job_id外键，不保留过期正文；最小摘要/取消映射当前不自动过期，应随备份保留，不应单删映射后允许同UUID重建。

POST`/api/plan/jobs/create/cancel`携带原请求与凭证，可在原创建前留下取消记录，迟到原POST不启动；已创建则同事务撤销租约并记录取消。已完成返回实际done/error，过期返回expired，不谎称撤销了历史；外部已发请求不保证可撤回。

客户端在本标签按账号保存深冻结提交快照及create/cancel模式，游客原凭证也在快照中，不存JWT。停止意图先保存再发送，刷新不变回创建；首次存储失败不POST，坏回执不清标识。关标签/清数据/明确放弃可能失去恢复入口，放弃不停止后台或删历史。未确认时新需求链接不覆盖旧尝试，处理后须重新打开新需求。

nginx示例将新create纳入原6次/分钟AI桶，cancel使用独立30次/分钟、burst5控制桶，避免创建配额阻止停止；429保留原标识。示例未在本机执行nginx -t或部署，多实例限额/真实代理/生产账号仍待验证。任务唯一映射不改变模型重启重调/计费边界。复验见`frontend/qa/plan-creation/README.md`。

### 发现页读取与查询状态

本轮无数据库新表/列；`GET /api/spots/regions`新增total为全库实际景点数，城市统计含无区县记录，区县候选仍只含有名称的区域。新前端遇旧响应没有total时隐藏全库数字，不用地区计数之和冒充；城市没有区县候选仍可选择。`GET /api/spots/for-you`返回private,no-store，部署时保留缓存头，不对私人推荐套公共缓存。足迹仍沿用既有私有no-store及权限检查。

公开景点/攻略URL保存已提交筛选和页号，失败时仍显示上次成功条件和旧结果，重试固定失败查询；未搜索输入不持久化。灵感页规划草稿在离开/刷新/换号/替换前确认，接受离开不保证恢复；明确提交仍走原可靠创建，不因本轮改变其幂等/停止协议。实际个人资料与搜索词仍按既有安全措施保护，不公开测试日志或合成凭证trace。

部署仍需完整发布前端并更新后端统计/缓存头；本轮无DDL不表示前模块的生产表迁移已执行。实际地图、模型/OCR、真实设备及生产代理尚未验收。复验见`frontend/qa/discovery/README.md`，当前恢复入口`docs/交接归档-发现页恢复.md`。

### 首页转换与本机复制恢复

新增plan_trip_conversions表，唯一(user_id,plan_revision)，记录原方案ID/版本及结果旅行关联；先备份，在旧库副本重复初始化、转换/核对/重启演练通过后再统一升级后端和发布前端。ORM删除旅行清空关联保留映射，原次410，不为清理结果而删映射。当前合成旧库与实际进程测试通过，未执行生产迁移/发布。

新前端要求GET `/api/trips/plan-import-capabilities`支持recoverable_conversion，再POST `/from-plan/{id}/convert` body原version/revision；GET `/from-plan/{id}/conversion?revision=...`只读找回。首次201、已有结果200返回当前旅行，保留之后编辑；首次陈旧409。旧普通`/from-plan/{id}`201/409保留兼容，新前端不回退。能力路由必须注册于通用`/{trip_id}`之前。

本机复制要求guest-import-capabilities增加idempotent_import/status_lookup及reservation_records；原POST import-guest增加owner_id/guest_id/guest_version/state回执，POST import-guest/check仅以相同完整快照核对，不写入。原trip/already_imported字段继续兼容。核对不是模型请求，不纳入模型调用桶；实际代理和共享限流仍待部署验证。

首次写前按账号在sessionStorage保存固定尝试，恢复不自动POST。成功由用户打开，不用迟到回执自动跳页；放弃/换号不撤销请求或删除原件。完整本机快照含已有预订订单号，只在用户明确复制时发出并保留于本标签，关闭/清数据可能失去入口。验收见`frontend/qa/trip-home/README.md`，最新整体状态见`docs/交接归档-首页恢复与转换.md`。

### 规划历史版本与限时撤销（兼容细节）

AiPlan新增version（旧记录1）、revision（稳定UUID）及restore_until。启动初始化会补三列并为旧记录补标识，重复初始化保持标识；实际生产旧库仍须备份、在副本演练迁移与读写，再统一升级后端和发布前端，不能以临时库测试代替生产验收。

历史列表/详情含version/revision，GET `/api/plan/{id}/state`仅本人可核对已删除状态。新客户端写入使用PATCH `/{id}/snapshot/edit`、DELETE `/{id}/content?version=...&revision=...`、POST `/{id}/content/restore`（body原version/revision），均相对`/api/plan`。条件更新拒绝陈旧覆盖，旧无版本snapshot/delete/restore写返回428，新路径不存在时不退回旧写。部署须确保所有实例支持新协议。

服务端撤销期8秒，重复删除不续期，旧已删除记录无期限不能获得新撤销窗口，过期410。快照丢响应后保留原版本，状态读取不等于正文提交证明，需明确重新载入或放弃；客户端不会自动套最新版本重写。生成任务保存plan_version/plan_revision为原始基线，不随历史编辑变化，旧任务缺元数据暂停自动保存。

历史待确认队列主要在本标签内存，刷新/换号有确认；实时缓存只保留基线与未确认警告，不盲目自动重发，也不保证所有历史草稿跨刷新恢复。确认离开或结束核对不撤销已发写入。隔离验收和复验见`frontend/qa/plan-history/README.md`，最新停止/恢复入口见`docs/交接归档-规划历史保护.md`。本次未部署、未执行nginx -t或真实旧库迁移。

### 可靠创建旅行的版本兼容

新前端登录创建使用 `GET /api/trips/create-capabilities` 和 `POST /api/trips/create`，携带UUID request_id；部署时先让后端及数据库支持这些接口，再发布前端。启动初始化会新增 `trip_creation_requests` 表，旧结构副本升级与重复初始化已有隔离演练；实际历史生产库仍须先备份并独立验收。

账号与request_id有唯一约束，旅行、日期和映射在同一事务提交。相同内容重试返回原旅行的当前状态，不覆盖之后的编辑；更换内容却复用标识返回409，已删除旅行不自动重建。普通旧接口 `POST /api/trips`继续支持无标识调用，这种调用不保证去重。新前端不会在接口缺失时退回旧创建路径，避免旧服务忽略未知字段而重复创建。

实际进程重启测试已验证创建映射随同临时磁盘库保留；ORM删除也会清空关联，避免SQLite复用旅行编号。此证据不等于生产发布或所有外部写入幂等。浏览器的待确认信息只存于本标签sessionStorage；关闭标签或清除浏览器数据后，需从账号旅行列表核对已有旅行。

### 分享链接安全重试的版本兼容

新前端使用POST `/api/trips/{id}/sharing/links/prepare`及`/activate`。先升级支持两接口的后端，再发布前端；接口缺失不会回退旧普通创建。准备不落库，激活用已有唯一令牌哈希去重，本模块无新增数据库表。旧POST `/sharing/links`继续兼容旧客户端，但没有此重试保证。

准备凭证以JWT_SECRET经独立用途派生的密钥签名，各进程/重启必须保持同一密钥。备份并妥善保管显式配置或数据目录中的回退密钥文件；轮换会使尚未确认的准备凭证失效，用户须核对管理列表，不自动新建。现有哈希分享链接不因这次签名密钥轮换本身失效。未使用准备凭证15分钟过期，已激活且仍有效可继续找回原结果；重试不延长有效期或复活撤销链接。

凭证与原令牌只在页面内存，刷新离开后无法找回地址；管理列表仅含元数据。生产代理、错误采集及应用日志不要记录请求正文/响应中的敏感令牌。隔离复验见`frontend/qa/share-recovery/README.md`，实际部署和生产账号仍待验收。

### 攻略可靠写入的数据库和客户端升级

启动初始化会新增`guide_creation_requests`表，为guides补`version`（旧行初始1）和`revision`（稳定UUID）。重复初始化不重置revision；旧结构恢复副本已验证，但生产历史库仍应先备份并独立演练。revision防止SQLite复用已删除编号，让旧编辑误指向另一篇。

前端先GET `/api/guides/write-capabilities`，再专用POST `/api/guides/create`，请求必须带UUID request_id。账号+UUID唯一，相同内容返回原攻略当前状态，改变内容409，原件删除410不重建；正文/图片/景点/映射同事务提交。旧POST无标识仍兼容，不保证去重；新前端不降级旧创建。

编辑PATCH及删除DELETE使用`/api/guides/{id}/content`，携带加载时version及revision（删除放查询参数，两者均非凭证）。旧根路径PATCH/DELETE也要求这两项，缺少时428；这是有意的旧写客户端升级要求，避免绕过并发保护。应先完成后端迁移和接口支持，再发布前端；升级窗口内旧编辑客户端需刷新/更新。读接口保持兼容。条件版本领取与所有内容关联变更同事务，陈旧请求409，失败全回滚。

已提交的新建快照与标识按账号保存在本标签sessionStorage，不含JWT；刷新继续原尝试，成功打开后清除。未提交或正在编辑的正文不自动持久化，关闭标签/清存储不能保证恢复创建标识，需核对账号云端。实际服务重启和副本升级已有隔离证据，生产多进程/代理/真实账号仍待验收。复验见`frontend/qa/guide-editor/README.md`。

### 个人资料版本和旧客户端升级

User新增`profile_version`，旧users表通过启动初始化补列（NOT NULL DEFAULT 1）；GET `/api/me/profile`返回`version`，身份接口返回`profile_version`。恢复副本重复升级、旧内容保留和实际进程重启已有隔离证据；生产历史库仍须先备份并独立演练。

新前端PATCH `/api/me/profile/edit`携带加载时version；条件UPDATE原子更新昵称/头像与版本，失配409不覆盖。旧PATCH `/api/me/profile`也必须带version，缺少428；旧写客户端需要更新，不能绕过新保护。新前端读不到版本或专用路径不可用时不降级旧写入。部署顺序为后端迁移/接口先就绪，再发布前端，升级窗口内旧资料编辑需刷新更新。

资料/收藏/攻略各自读取重试，读取与资料写入均no-store。昵称草稿及待确认保存不自动持久化；响应丢失后的原版本重试会409，应先核对再明确重新载入，不会因读取新资料自动提升保存版本。全局用户资料合并防止迟到身份结果回退已确认昵称，不改变JWT，不承诺多标签实时昵称同步。复验见`frontend/qa/profile-recovery/README.md`；尚未生产部署或真实设备验收。

### 攻略详情与旅行选择前端验收

攻略详情的旅行选择使用共用原生HTML dialog，加载中可关闭；当前已安装Edge的桌面与390×844视口完成焦点、Esc和关闭迟到列表验收，其他浏览器/实际设备须独立验证。此模块不改变后端协议，仍依赖前述version/revision专用删除路径和权限检查。

未知删除只保留本页展示并暂停重复操作，显式重新读取后才恢复；确认离开不取消已发删除。手机提示渲染后滚动至可见位置，恢复链接须在底部导航上方。`/me?tab=guides`应在/travel/子路径中直达个人中心攻略标签。复验见`frontend/qa/guide-detail/README.md`，没有生产部署或真实设备证明；原有数据库迁移和备份步骤仍须完成。

### 景点加入旅行前端恢复

景点加入使用共用原生dialog，并固定首次读取的旅行版本与地点快照。核对最新清单只更新展示，不换版本重新新增；依赖现有旅行写入原子版本检查，无新增表或后端接口。输入及尝试只在页内存，明确关闭或离开不撤销已发写入，之后另起尝试应核对原清单。

原景点收集的页面保护已进一步接入下述评论/收藏可靠写入。隔离复验和可选人工景点夹具约束见`frontend/qa/spot-collection/README.md`；测试夹具拒绝已有数据库，不应指向生产目录。真实设备和生产网络仍待验收。

### 景点评论与收藏协议升级

启动init_db新增`comment_creation_requests`与`spot_favorite_states`两张表，无本轮新增旧表列。先备份并在恢复副本演练，再让后端迁移和专用接口就绪，最后发布前端；已有生产库未执行此次升级。旧Favorite行保留，首次版本化写入时按真实收藏采用为版本0，随后状态与Favorite镜像在同事务更新，原个人中心/管理/推荐计数继续读取Favorite。升级后禁止旧进程或脚本直接无条件改Favorite而绕过状态表。

收藏PUT`/api/spots/{id}/favorite-state`必须带`favorited`目标和`version`；旧POST`/{id}/favorite`缺明确目标/版本返回428，有请求体也执行同样条件写。旧客户端需刷新更新，不能继续依赖toggle语义。相同目标的旧版本允许无变更恢复，陈旧相反意图409，未来版本也409；刷新/核对读取实际状态后再决定，不保证全标签实时显示。

评论GET`/api/spots/write-capabilities`后使用POST`/{id}/comments/create`，UUID、账号、景点及归一化正文/评分绑定；重复返回原评论，被删除返回410不重建，正文或景点变化409。旧`/{id}/comments`保留无UUID兼容调用，其无标识创建不保证重试去重；新前端不会降级此接口。请求映射和正文同事务，稳定数据库必需，进程重启不会清映射。旧评论没有自动生成可恢复标识。

前端只把已提交评论的正文/评分/UUID暂存在按账号和景点区分的sessionStorage，不存JWT；未提交输入仍在页内存。刷新可继续同次发布，关闭标签、清存储、明确放弃会失去恢复保障，放弃不撤销已发布评论。收藏未知尝试只在页内存。新前端遇旧服务无能力/无版本/不支持专用路径时提示更新，不退回不受保护写入。复验见`frontend/qa/spot-writes/README.md`；测试与截图使用隔离临时库，无真实账号/生产部署证明。

### 客户端 IP 与代理信任

游客 AI、攻略和路线的应用内限流按 `request.client.host` 分桶。示例服务默认只信任本机代理；nginx 在容器中时，未配置其实际源 IP 会让所有访客共用容器的限额。先确认 nginx 到后端使用的 Docker 网络及其实际源地址，再为 `travel.service` 配置 systemd drop-in，例如将下列地址替换为**已确认的 nginx 容器地址**：

```ini
# sudo systemctl edit travel
[Service]
Environment="FORWARDED_ALLOW_IPS=127.0.0.1,172.18.0.2"
```

然后执行 `sudo systemctl daemon-reload`、重启服务并检查状态。`172.18.0.2` 只是示例，容器重建后地址可能变化；应给代理安排稳定地址并核对运行时配置。仅信任实际代理，不能设为 `*` 或直接信任共享 Docker 网段；后端 8000 端口也应只允许代理访问。示例 nginx 假定其为公网入口，并用 `$remote_addr` 覆盖客户端提供的 `X-Forwarded-For`；若前面还有 CDN 或负载均衡，须先仅对可信上游配置 nginx real-ip，再验证 `$remote_addr` 确实是访客地址，不能直接信任任意来源的转发头。

上线前从两个不同客户端验证它们使用独立的游客限额，并验证未受信任连接伪造 `X-Forwarded-For` 无法改变限额归属；同时执行 `nginx -t`。应用内频率和并发限制仍为单进程，nginx 的 IP 限制也只覆盖配置它的实例；扩容前需设计共享限流。本节为部署步骤，仓库改动本身不会改变已运行服务。

## 资源控制

| 项 | 值 | 目的 |
|---|---|---|
| systemd `MemoryMax` | 1200M | 防止异常请求把机器内存打满 |
| uvicorn workers | 1 | 与进程内缓存一致，降低内存 |
| nginx `limit_req` | 6 次/分钟/IP（规划接口） | 保护模型与高德配额 |
| nginx `travel_ai` | 6 次/分钟/IP，允许 2 次突发 | 限制创建规划任务、AI 建议和攻略识别的 POST；普通读取与轮询不计入 |
| nginx `travel_plan_control` | 30 次/分钟/IP，允许 5 次突发 | 原规划停止确认的独立配额，配置须部署核对 |
| nginx `travel_place` | 20 次/分钟/IP，允许 5 次突发 | 限制主动发起的高德地点搜索；还需合并并验证示例配置 |
| nginx `proxy_read_timeout` | 300s | 行程生成最慢约 2 分钟，留余量 |
| nginx `/travel/api/` 请求体上限 | 6 MiB | 容纳 4 MiB 图片的 base64 和 JSON 包装；应用仍检查图片类型和大小 |
| nginx 普通 API 读取超时 | 180s | AI 建议或攻略识别可能进行一次格式重试 |

## 排障

```bash
systemctl status travel -l          # 服务状态
journalctl -u travel -n 100 --no-pager   # 后端日志
sudo docker exec continew-web tail -50 /var/log/nginx/error.log
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1/travel/api/health
```

常见问题：

- `/travel/api/*` 返回 404 且错误日志是 `connect() failed (111)`：后端没起来，或
  uvicorn 只监听了 127.0.0.1，容器无法通过宿主bridge地址访问。应绑定刚核对的宿主bridge地址（当前 `172.18.0.1`）；Linux隔离整链已验证这种绑定，不需要为此监听全部接口。现役旧服务的0.0.0.0是历史配置，尚未切换。
- 页面白屏但接口正常：前端构建时没带 `--mode travel`，资源路径成了 `/assets/...`。
- 地图空白、提示未配置：`.env` 里的 `AMAP_JS_KEY` 不是「Web端(JS API)」类型，
  或安全密钥不匹配。
- 图片攻略返回 413：确认已将示例配置中的 `/travel/api/` 请求体上限同步到线上 nginx，
  并在变更后执行 `nginx -t`；图片仍须为不超过 4 MiB 的 PNG/JPEG。
- 图片识别提示不可用：检查智谱账号的 OCR 服务权限和配额。当前公开样例实测提示服务账户欠费；
  可暂时粘贴攻略正文。不要在日志或反馈中粘贴 API 密钥或图片内容。

## 限制

- 纯 HTTP，没有域名和证书；登录 token 存在 localStorage，公开演示够用，不适合承载真实用户。
- 演示站点开放注册，规划接口按 IP 限流；如果被滥用，先在 nginx 层封 IP 或关闭注册。
- 数据只有 5 个城市 500 条 POI，超出范围的地点会被核实环节替换或标记未核实。

## 2026-11-16 旅行工作区暂停交接

工作区分区导航、移动端收集/助手/记录串联及读取保护已完成，本轮只修改前端业务代码和隔离QA，无后端DDL。`build:travel`通过不表示线上已更新；未部署，前模块生产迁移和真实地图/模型/OCR及HTTPS/代理等仍待验收。按用户最新要求完成当前模块后暂停，最新恢复入口 `docs/交接归档-旅行工作区暂停.md`，复验命令见 `frontend/qa/workspace/README.md`。不要依据上方历史active自动推进。

## 2026-11-16 分享接收恢复（整体已重新激活）

用户重新激活整体目标；上节暂停为历史状态。新增POST `/api/trip-sharing/invitation-status`，须与分享接收前端一起更新，无新增DDL。该接口只读核对当前账号membership；不会接受或恢复成员。未知接受结果在旧服务缺少核对接口时保持待核对，不降级重发。217项后端既定离线、175项前端、58个相关页面及子路径构建通过，未部署；真实生产代理、备份/迁移和外部服务仍待验收。最新交接 `docs/交接归档-分享接收恢复.md`。

2026-11-16 攻略图片导入：云端OCR成功回执新增version，与游客一致，客户端要求匹配原版本；请同步升级前后端。输入增加完整PNG/JPEG解码和2000万像素限制，无新增DDL，未部署。真实OCR服务账户与外部图片授权仍待核对；本机授权sample.png验收不替代供应商验证。最新恢复入口`docs/交接归档-攻略图片导入恢复.md`，整体active。

## 2026-11-16 备份关联校验及独立恢复演练

发布前先备份到不存在的目标，再验证备份、在新目录恢复副本并演练升级/读取，核对后才进行后端和前端发布。线上服务、JWT密钥及环境配置仍需另行安全保管，本轮未实际发布。

```bash
# 在backend目录；路径必须替换为已核对的实际目录，output不得已存在。
python tools/backup_db.py --source /absolute/data/app.db --output /absolute/backups/new-snapshot.db --timeout 60
python tools/backup_db.py --source /absolute/backups/new-snapshot.db --verify-only --timeout 60
python tools/backup_db.py --source /absolute/backups/new-snapshot.db --output /absolute/new-rehearsal/app.db --timeout 60
```

父目录须事先存在，工具不覆盖旧文件、不自动修复外键错误。备份验证现包含SQLite结构与外键关联，复制与验证共用截止检查；仅验证模式不迁移数据库。恢复副本应禁用真实供应商调用，并使用独立端口和明确配置，不得把业务服务直接指向未验证副本。在线快照只包含捕获点已提交状态，之后的写入不在该快照；正式切换需考虑停写窗口和回退策略。

11项专项通过，含新进程读取恢复副本、权限/私有照片与原次创建验证；不等同生产迁移或灾备完成。最新交接 docs/交接归档-备份关联校验与恢复演练.md，整体active。

## 2026-11-16 可选共享游客请求额度

后端新增TRAVEL_SHARED_LIMITS=1开关，默认关闭；开启前，所有worker必须运行当前代码、使用相同开关及同一可写TRAVEL_DATA_DIR本地目录。状态写入request-quotas.db；不是业务表迁移。systemd模板保留workers=1，增加可选开关注释和UMask=0077；实际启用应使用审阅后的drop-in，再统一重启全部worker，不能混用开启/未开启进程。

共享五个POST入口：游客路线/地点搜索各30次/60秒，游客OCR/AI预览各2次/60秒，游客攻略预览4次/60秒。按真实客户端地址+入口滑动计数，受信代理必须先把正确地址传给应用；不要信任整个公网、*或未经核对的容器网段。已有nginx及进程内限制可更严格，实际允许量取各层共同限制，不能简单相加。

额度耗尽返回429，状态库锁超时/损坏/满100000活动事件返回503，均含Retry-After；失败时不放行外部请求。大图片读取前拒绝，轮询/取消/核对/普通编辑保持可用。不同机器或不同数据目录不共享；此实现没有共享供应商缓存/并发容量或覆盖全部模型入口，不能据此直接提高worker数。系统时钟需同步，向后调整可能延长窗口。

request-quotas.db仅存短时频率，不是app.db业务备份的一部分；不要让每次服务重启删除它而重置限制。权限和目录须按服务账号核对。线上nginx -t、真实代理/负载与多进程容量仍待验证。最新交接 docs/交接归档-共享游客额度.md，整体active。

## 可选路线、周边酒店与和风天气共享缓存

TRAVEL_SHARED_CACHE=1显式开启，默认关闭；与TRAVEL_SHARED_LIMITS独立，所有worker须同配置、同TRAVEL_DATA_DIR本地共享目录。服务仍workers=1，本轮不实际改生产开关。provider-cache.db保存已校验JSON和短租约，地图路线15分钟、周边酒店30分钟；命中不续期，原查询时间保留。坐标/酒店查询结果需要受限目录权限，摘要键不等于匿名数据。

同查询跨进程协调，20秒租约、12秒等待；过期旧执行者不能覆盖较新结果。最多512条/32MiB、每值512KiB、128个租约；容量满、存储不可用或协调超时显式降级，不能绕过缓存再无约束调用。酒店空/失败不缓存，连续空结果可能重新查询。共享模式跳过旧内存缓存，避免延长旧值有效期。

覆盖路线查询、`GET /api/hotels` 周边搜索和和风天气。天气 TTL 为 15 分钟，冷查询租约 35 秒，等待约 12 秒。2026-11-25 生产单 worker 已通过 systemd drop-in 启用。住宿名匹配、静态地图、地点搜索、模型和航班采用各自的调用逻辑。并发与请求起始间隔由独立供应商预算控制。状态库独立于 `app.db` 业务快照，恢复后可冷启动。扩容前仍需验证生产负载、时钟、权限和代理。[共享缓存交接](../docs/交接归档-路线与酒店共享缓存.md)

缓存并发修正版采用只读命中路径。冷键先预留条目数名额，已有条目与活动租约达到上限时直接拒绝，不发出上游查询。字节上限在取得实际响应后检查。SQLite 临时锁冲突仅有限重试本地事务。此前生成的缓存文件兼容，修正版发布状态见[交接记录](../docs/交接归档-路线与酒店共享缓存.md)。

## 2026-11-16 可选供应商调用预算

TRAVEL_PROVIDER_BUDGET=1开启应用侧共享起始间隔/租约并发，默认关闭。所有worker必须采用同样配置和同一可写本地数据目录；仍保留workers=1，未实际扩容。状态在provider-budget.db，独立于业务备份和另外两个可选开关。

应用默认策略：amap并发2、间隔0.4秒；chat并发2、间隔0.4秒；ocr并发1、间隔1秒。可用TRAVEL_AMAP_CONCURRENCY/CHAT_CONCURRENCY/OCR_CONCURRENCY和TRAVEL_AMAP_INTERVAL/CHAT_INTERVAL/OCR_INTERVAL调整，各变量完整前缀为TRAVEL_；并发1至16，间隔0至60秒，无效值拒绝调用。这些是应用策略，不是供应商官方账号配额，实际开启须核对账号权益和负载。

涵盖高德所有现有网络出口（核实/搜索/路线/天气/酒店/静态图）以及chat/OCR两个异步组。开启时SDK隐藏重试关闭，业务层再次调用仍重新领取预算；缓存命中不耗网络槽。每次领取最多等2秒，30秒租约/10秒续租；忙碌、状态库故障不绕过控制。租约不等于物理取消上游请求，故障时不能承诺无重叠或只计费一次。

轮询/取消/核对不直接受供应商调用预算限制。短时运行库不在app.db业务快照内，不应每次重启删除；实际权限、时钟、资源、供应商服务与SDK行为仍需验收。最新交接 docs/交接归档-供应商共享调用预算.md，整体active。

## 2026-11-16 发布前只读预检

前端先build:travel，确保dist/.vite/manifest.json存在；在已备份恢复的稳定副本上运行（替换为已核对的绝对路径）：

```bash
python tools/deployment_check.py --database /absolute/rehearsal/app.db --frontend /absolute/frontend/dist --workers 1 --timeout 60
```

传入实际拟运行worker数和与服务相同的环境开关。数据库检查完整性/外键、当前模型所需表列、主键/唯一键/索引/外键声明；只读不自动迁移。前端核对/travel/入口及manifest的文件/懒加载依赖，防止漏部署资源。运行参数检查不能证明线上systemd实际采用了这些值；通过输出local_checks_passed仅表示本机三组输入通过。

还必须独立验收实际历史数据/迁移、备份恢复、nginx -t、HTTPS/子路径回退、代理、生产资源/权限、真实供应商及设备；模型列类型/NULL/默认值、业务语义不由本工具完整证明。绝不把JSON报告当生产发布批准。本机隔离报告在docs/verification/deployment-check-2026-11-16-verified.json，最新交接docs/交接归档-发布前只读预检.md，整体active，未上线。

## 2026-11-16 真实nginx与生产包隔离运行

模板已增加map_hash_bucket_size 128（合并到http时若已有该项，修改原值不重复）、/travel/assets/缺文件404和哈希资源immutable、/travel/index.html no-store、/travel/.vite/拒绝公开。实际Windows nginx解析发现长map键默认桶不足，已修；测试保留完整map/limit/location，仅替本机upstream、root、监听并加独立根站点样例。

两个真实Uvicorn同库三开关全开有组合证据；实际nginx HTTP检查及Edge从dist加载的桌面/手机创建—收集—排期—深链刷新通过，最终7项（内含2视口烟测）17.83秒。此为Windows 1.30.6便携版/本机临时目录，不替代生产Linux容器nginx -t、HTTPS证书、实际代理IP链或负载许可；没有实际部署/扩容。

复验见frontend/qa/production-bundle/README.md；需已有便携工具和build，缺失时测试skip不计通过。此段为前轮历史证据，最新状态见下方候选归档。

## 2026-11-16 前端安全发布候选收尾

前端候选工具、安全切换/回退及真实nginx复验完成，最终23项通过（15项发布工具、6项预检、2项运行集成；内含桌面/手机两视口烟测，不另加计数）。已保存本机前端候选和docs/verification/frontend-candidate-2026-11-16.json，非后端完整发布包；未上线。当前模块到此收尾，不在本回合推进后续，恢复入口docs/交接归档-前端发布候选与模块收尾.md。
