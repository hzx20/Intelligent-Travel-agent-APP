# 预算版新版 Linux 负载及发布候选交接

2026-11-19，Asia/Shanghai。旅行详情缓存修复已在原服务器的独立 Linux 环境原生重建并通过有上限的合成负载。停在隔离验收及计划重新绑定，未装正式 unit、未改线上入口。该测试不依赖正式域名；[本机 HTTPS 交接](交接归档-预算版HTTPS与负载.md)记录已知 IP 的 TLS 握手未通过和本机证书测试。

- 经核对的冻结源码只调整 `backend/app/routers/trips.py`，应用文件数保持 117、前端编号不变。用原机 CPython 3.12.3 和既有 63 项固定依赖生成 Linux 候选 `553a85ca32f4a4d339baaa618b8c9fc97e756b96d713dbcbbcd45ffe974ae363`；所有构建输入均与先前回执核对。应用只在临时单元的独立网络命名空间监听回环，使用合成账号、配置和 SQLite 库。
- `travel-budget-load-179bf612c7602ecf.service` 验证 0.5 CPU、384 MiB、64 任务和禁用 swap。8 并发下 160 次读取无失败，P95 200.64 ms、最大 808.63 ms。用两组账号核对私有详情无缓存、互相不可访问；两笔同时提交的预算写入返回 200/409，账本未被自动记入。
- 同轮通过实际 `/api/plan/stream` 路由、后台任务和 SSE 事件流，模型调用替换为一条合成响应。收到 4 个事件，约 1.61 秒完成，无真实供应商尝试。cgroup 峰值约 148 MiB（包括负载进程），无 OOM，记录到 CPU 节流。这是带资源上限的直接 Uvicorn 验收，无 nginx、TLS 或外网流量；耗时不代表生产性能。
- 两次未启动成功的临时 unit 均确认已回收。首轮返回 203/EXEC；按已有 systemd 验收方案补足私有 `/mnt` 内的 `ExecSearchPath`，只读绑定已验收的两处输入后成功。成功轮也确认后端退出、临时目录与临时服务回收。没有读生产库，也未安装依赖或改变线上服务。
- 用已验证的原生编号重新绑定完整私有计划 `81ee1674adc6b9267e4b526ffdbf5313cae0b8094c5039ca220c528ef500f5b5`（197 文件），包含新版 `travel.service` 的版本路径。静态资源、nginx 候选及依赖锁保持原样；旧方案和以前回执保留。新计划仍是现有 HTTP 代理规则的审阅件，不是正式 TLS 配置。

[脱敏机器回执](verification/budget-linux-load-2026-11-19.json)保存候选、范围、失败记录、统计和私有原始回执哈希。私有目录 `.release-candidates/budget-https-load-2026-11-19/` 保留控制器、计划和回执；计划包含共享 nginx，勿对外发送。`python -B -s .release-candidates/budget-https-load-2026-11-19/run_linux_budget_load.py` 默认只检查本机载荷与命令，不接触服务器。复核计划用 `python -B -s backend/tools/release_plan.py verify --plan .release-candidates/budget-https-load-2026-11-19/private-plan --expected-id 81ee1674adc6b9267e4b526ffdbf5313cae0b8094c5039ca220c528ef500f5b5`。原机手工执行不属于复核步骤；不要为验证记录重跑。

下一次从本归档开始：确认用于 `/travel/` 的正式域名或经确认的 HTTPS 地址、证书与代理终止点；完成新版在真实 TLS 链路的导航、账号和物理设备验收。正式备份、目录与权限整改、切换及写入门禁仍在后续维护范围。此次未做生产负载、未改变 DNS 或 nginx、未上线，`production_ready=false`。
