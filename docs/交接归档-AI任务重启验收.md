# 交接归档：旅行 AI 任务实际进程重启验收

后续用户已重新激活整体目标，并完成完整游客页面草稿保护验收。最新恢复入口为 `交接归档-草稿浏览器验收.md`，当前目标 active；本文件保留上次暂停记录。

## 停止点与用户范围

用户要求“做完这个模块先暂停”。当前模块已完成并停止，不继续后续板块；整体改造尚未完成。只有用户重新要求继续后再恢复工作。

项目：`C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP`。分支 `main`，HEAD `f5bd176`。工作树有大量此前未提交及未跟踪改动，全部保留；不要 reset、clean、覆盖或擅自提交。此次没有部署，也没有替换业务数据库。

## 本模块完成改动

- `backend/tests/test_service_restart.py` 的启动夹具支持测试专用引导代码，新增旅行 AI 任务的三进程验收。
- 第一个真实 Uvicorn 进程通过 HTTP 注册、登录、创建旅行并提交建议任务；模型传输模拟函数开始等待后，确认磁盘任务为 running、执行次数为 1、尚无建议，再停止进程。
- 只在已停止服务的临时 SQLite 库中将领取期限设为过去，模拟过期；不等待生产 180 秒，也不修改生产调度设置。
- 第二个真实进程使用同一临时库和登录凭证自动恢复任务；验证第二次执行、单份建议、清空任务快照、未登录查询 401、预览前后行程不变。
- 第三个真实进程仍能找回相同预览；确认后才写入一条安排并增加版本，重复应用返回 409，没有再次调用模型。
- 只替换 `trip_ai.chat` 外部模型传输；保留真实应用生命周期、调度器、领取机制、JSON 与行程校验、权限和持久化路径。
- 同步更新 `deploy/README.md`、`阶段验收矩阵.md`、`行程产品改造路线.md`、`SESSION_LOG.md`，旧归档增加最新入口。

本模块没有修改产品业务逻辑。此前已完成旅行、关联预订、私有照片重启读取及 TRAVEL_DATA_DIR 隔离，详见 `交接归档-服务重启验收.md`。

## 验证与当前状态

- 针对性：`tests/test_service_restart.py tests/test_trip_ai.py`，**14 passed，16.54 秒**。
- 完整离线后端：**158 passed、2 deselected，38.62 秒**，另忽略 `test_m1.py`、`test_m2.py`。
- 前端本轮未改，未重复运行；最近记录为 **42 项测试与 build:travel 通过**。
- 测试所有子进程在 finally 中结束，没有留下常驻验收服务；使用临时磁盘库、合成账号与模拟模型，没有调用外部模型、地图或 OCR，没有上传个人文件。
- 这不是生产 systemd、真实供应商故障、真实账号或原规划页面内存任务的恢复证据。

## 剩余事项：约五类收尾板块

这是对剩余工作的归并，不代表仅剩五个小任务；原七阶段的明细与证据以 `阶段验收矩阵.md` 为准。

1. **完整页面与多设备验收**：未保存修改的 Esc/路由/关闭确认、真实账号切换、复杂分享协作和并发、移动浏览器、网络中断恢复及最终视觉核对。
2. **真实地图、AI 与 OCR**：游客真实路线和模型、复杂规划与地点核实、OCR 浏览器选文件与真实识别；OCR 曾受账户欠费影响，应先核对状态。
3. **外部授权能力**：攻略链接需授权来源；真实房态、价格、支付和订单需供应商接入。当前手动预订记录不产生真实订单。
4. **部署与数据可靠性**：真实历史库副本迁移和备份恢复、systemd/容器重启、nginx/可信代理、HTTPS、生产子路径、多进程共享限流与缓存。原规划页仍有内存任务，扩容前需另行解决。
5. **最终整体验收与发布**：将已完成页面、外部服务及部署结果统一回归，核对降级、权限、历史兼容和发布条件；实际发布需明确环境及授权。

照片浏览器授权仅限此前生成的 32×32、117 字节纯色 `sample.png` 上传至本机 `http://127.0.0.1:5262`，不能扩大至个人文件或外部地址。可复现夹具见 `frontend/qa/photo-upload/README.md`。

## 下次恢复所需信息

先确认用户要求继续，再检查 `git status --short`，阅读本文件、验收矩阵和日志末尾；依据用户选定范围推进，不把归档视为整体完成。

在项目 `backend` 目录执行针对性复验：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
..\..\.venv-analysis\Scripts\python.exe -u -m pytest tests/test_service_restart.py tests/test_trip_ai.py -q
```

完整离线命令：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
..\..\.venv-analysis\Scripts\python.exe -u -m pytest tests -q --ignore=tests/test_m1.py --ignore=tests/test_m2.py -k 'not test_plan_stream_saves_history_when_logged_in and not test_graph'
```

此前本机沙箱内 TestClient/套接字存在等待问题，这些测试按工具审批在沙箱外完成；不要擅自绕过审批。新隔离服务必须使用新的临时绝对 `TRAVEL_DATA_DIR`，不能指向工作区业务库。真实供应商、生产库替换和发布先形成可审查方案，再按已有授权边界执行。
