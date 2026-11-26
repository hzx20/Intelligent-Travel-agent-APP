# 航班新版 Linux 与 HTTPS 绑定

## 2026-11-25 正式上线和验收

- **已跳过项**　取消上线前的加密快照解密验收。维护窗口内先完成 SQLite 明文导出及完整性和外键检查，开放后执行浏览器冒烟测试。
- **风险接受**　用户确认 OpenPGP 恢复口令无法找回，接受既有加密快照作废。执行记录以本次停写点的备份为数据库回退依据。
- **回退路径**　原机 `/var/lib/travel-cutover/rollback-20261125-5f6fbd51/` 内保留旧版应用、静态文件、unit、原共享 nginx 配置及 `legacy.sqlite3`。SQLite 为 335,872 字节，`root:root 0600`，目录为 `root:root 0700`，摘要 `b192321a2d9a97c54feb093a5b0677323bf6686b4146984d886b6bdb151398ea`。开放写入后发生问题时先回到维护并保全当前库，再按故障类型决定修复路径。[明文备份及回退方案](../deploy/PLAIN_ROLLBACK.md)
- **冒烟测试结果**　12:30 开始维护，12:32:53 记录开放写入。HTTPS 首页及健康接口 `200`，TLS 1.3 证书有效且 Edge 无告警。六个主要页面 `200`。合成账号登录、个人中心均 `200`，创建旅行 `201`，刷新后记录仍在。36 项站内静态资源无 `404`，控制台错误与页面异常均为 0。[执行命令和实际输出](verification/travel-plain-cutover-2026-11-25.md)

入站验收发现 unit 摘要在两个工具内录入不完整。冻结清单、原机 unit 及其摘要一致，更正工具后通过入站接入及外网复验。首轮 Edge 冒烟发现默认图标路径返回 `404`。原子发布图标修复后，完整复验通过。两次浏览器冒烟分别创建一组测试账号和私有旅行。

首次上线后端版本为 `28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281`，前端版本为 `15fe8379a9e2b19370a7cf67b4344cfc942b9f270b76f1ea88357600725d56fa`。HTTP 旅行页转向 HTTPS，明文 API 返回 `403`，公网 8000 直连被阻断，共享站点首页仍为 `200`。续期定时器已恢复，原机复核时下次预计北京时间 18:15:23。[执行命令和实际输出](verification/travel-plain-cutover-2026-11-25.md)

上线后复核证书续期定时器。北京时间 18:19:55 的检查成功，保存、代理选中及公网证书指纹一致，自动装载按预期跳过重载。正常模式、HTTP 跳转和明文 API 拒绝均通过。下一次检查预计北京时间 11 月 26 日 00:22:19，实际换代仍待验证。[上线后续期检查](verification/travel-plain-cutover-2026-11-25.md#上线后证书验收适配)

公网 Edge 和 Chrome 触摸视口补测通过。390×844 和 320×667 均可用折叠菜单和底部导航，游客可创建旅行并将地点排入第 2 天，刷新后保留行程和选中的日期。所测页面无横向溢出，每轮的 20 项静态资源均正常，控制台错误与生产写入尝试均为零。[移动视口验收](verification/travel-plain-cutover-2026-11-25.md#上线后移动视口验收)

按已上线模型只读核对生产 SQLite。30 张表、258 个字段、30 个主键、18 项唯一约束、47 个索引及 42 项外键定义均匹配，缺项为零。已同步验收矩阵中的结构迁移状态。[生产结构验收](verification/travel-plain-cutover-2026-11-25.md#上线后生产-sqlite-结构验收)

公网分享和邀请已用两个合成账号实测。只读分享隐藏预订和费用，重复激活返回原链接。编辑邀请经显式接受才授权，协作者写入可由所有者回读。撤销测试链接及移除成员后，相关访问返回 `404`，重放原分享激活返回 `409`。两个测试链接已撤销，测试成员数为零。[生产分享验收](verification/travel-plain-cutover-2026-11-25.md#上线后生产分享与撤权验收)

和风天气主机与密钥、飞常准密钥已同步到生产。后端重启后两个供应商均显示已配置，公网健康接口为 `200`。后续按独立授权，在公网 Edge 触摸视口实查杭州七天预报和 12 月 2 日上海至北京直飞及票价，三项均返回 `200` 并通过页面显示核对。[配置同步](verification/travel-plain-cutover-2026-11-25.md#上线后天气与航班配置同步) · [生产真实查询](verification/travel-plain-cutover-2026-11-25.md#上线后真实天气与航班查询)

北京时间 15:20 完成共享缓存后端更新。当前后端版本为 `8d2565cb6758665106c6a6193d5f9db46eef5f2859294cd5e4e0740eb6f63169`，天气、路线和周边酒店启用共享缓存。公网复验及受限明文备份通过。此小版本的回退保留当前业务库。[共享缓存发布与回退](交接归档-路线与酒店共享缓存.md#2026-11-25-生产预置切换与回退)

## 2026-11-25 用户决定调整上线验收

- **已跳过项**：上线前的加密快照解密验收。用户确认 OpenPGP 恢复口令已无法找回，取消此步骤。发布验证改为上线后冒烟测试。
- **风险接受**：旧加密快照作废，不再作为回退依据。无法依赖这些快照恢复，是用户已知并明确接受的风险。此后无需用户提供恢复口令。
- **执行顺序**　调整前置条件、整理退回旧版的办法、按维护窗口切换、上线后冒烟复验、更新交接归档。用户要求连续推进剩余步骤，实际结果见文首。[正式切换手册](../deploy/RELEASE_RUNBOOK.md)

## 2026-11-25 正式维护窗口回退

经用户授权，已在原机暂存并复核操作包，核对公网 HTTP 转向和明文写入拦截，停止旧后端并确认 8000 已关闭、SQLite 无打开者。生成编号 `112524443f54d4531d004fc9` 的 122 项 OpenPGP 回退快照，密文已取回本机独立备份目录，摘要和权限已复核。

等待本机解密验收期间未收到对应回执。窗口结束时恢复了旧后端和原共享 nginx 配置。旧后端启动后的首次探针早于就绪，随后确认进程健康、没有重启，并恢复 HTTP。北京时间 11:04 的公网复验确认旧版旅行页和健康接口均为 `200`，同机根路径为 `200`，HTTPS 继续提供 `503` 维护页。本次未发布新版前端、安装新版后端或迁移数据库。[维护与回退回执](verification/travel-cutover-maintenance-2026-11-25.json)

这次窗口已结束。后续发布按文首的新决定执行。另有真实账号和设备验收、实际换代后的证书指纹确认、QQ 告警启用授权，以及合法攻略来源和酒店交易供应商接入等事项。[正式切换手册](../deploy/RELEASE_RUNBOOK.md)

下一窗口的 `cutover-operations-v3` 已在本机打包并复核。增加了旧后端就绪等待，确认健康后再恢复原 HTTP。锁内复用原共享 nginx 的重载与回读流程。30 项相关测试和 4 项接收逻辑检查通过，新的操作包仍在本机。[就绪守卫验收](verification/travel-cutover-legacy-readiness-2026-11-25.json)

此前验证了 `check_cutover_snapshot_gate.py`，12 项定向测试通过。该工具及回执保留作历史证据。前端发布、入站规则安装、受限启动及验证的两段 shell 已通过本机语法和步骤顺序检查，现场执行仍待获批窗口。[历史快照准入验收](verification/travel-cutover-snapshot-gate-2026-11-25.json) · [安装步骤](../deploy/RELEASE_RUNBOOK.md#确认本次明文回退备份后安装新版)

用合成数据库模拟新版迁移失败，实际运行 OpenPGP 恢复命令。命令拒绝覆盖失败库，从密文恢复到新文件，验证恢复件的旧结构与数据，同时核对失败库和 WAL 摘要保持一致。包含写入标记、SQLite 备份和迁移的 20 项测试通过。发布手册补齐保持维护、保全现场和隔离回读的顺序。[失败现场保全回执](verification/travel-preopening-restore-preservation-2026-11-25.json)

## 2026-11-25 已结束窗口的预检

当时原机旧服务正常，宿主与共享 nginx 容器的配置摘要一致，正式版本清单匹配，预置环境的 `pip check` 通过。操作包 11 份文件、旧版备份 122 项清单以及当时的本机解密工具和恢复目录权限均已核对。该窗口后续回退，当前发布依据见文首。[历史窗口预检回执](verification/travel-cutover-window-preflight-2026-11-25.json)

2026-11-20 最新进展。冻结版本的正式应用目录和虚拟环境已预置，117 个文件摘要和 63 项离线依赖通过复核。使用这些路径完成了真实 systemd 隔离启动及后端入站规则隔离验收。现役服务仍运行旧版。文末记载执行范围、实际路径和回执。[正式预置回执](verification/travel-formal-runtime-staged-2026-11-20.json) · [入站规则隔离回执](verification/travel-staged-backend-ingress-2026-11-20.json)

## 2026-11-24 当前停止点与恢复顺序

1. 已确认腾讯云 TCP 443 放行。本机正式备份的 122 个成员、SQLite 完整性与外键以及旧版应用回读均通过。[云侧截图核对](verification/budget-tencent-443-user-readback-2026-11-21.json) · [正式备份本机回执](verification/budget-formal-backup-local-verified-2026-11-21.json)
2. 经单次授权，独立 HTTPS 维护入口已经通过公网验收并保留。旅行页及 API 返回 503，原 HTTP 两个入口均为 200。线上现役业务继续由旧版处理。[公网维护回执](verification/budget-public-maintenance-retained-2026-11-24.json)
3. 复用预置版本执行获批切换。维护期间取得适用于切换时点的回退快照，验证新版服务和 8000 入站门禁，并在私有入口检查受控账号登录后的只读路径。记录开放时点后再开放业务写入，完成真实账号与设备的公网验收。[实际路径预检回执](verification/travel-https-actual-host-preflight-2026-11-20.json)

切换手册已对齐专用 HTTPS 配置包，写明旧共享网桥和新版专用网桥各自的用途、正式 venv 复用、维护与明文入口控制、回退快照及开放写入记录。已复核冻结计划 197 项、HTTPS 配置包 6 项摘要和本机 35 项发布相关测试。[正式切换手册](../deploy/RELEASE_RUNBOOK.md)

11 月 24 日用该冻结应用和配套文件完成 Windows 回环演练。nginx 先后运行维护、正常、再次维护配置。HTTP 转向保留查询参数，明文写入与 API 拒绝，ACME 指定文件仍可读。正常 HTTPS 通过匿名拒绝、合成账号登录、创建及静态资源校验，回到维护后保留已提交旅行。夹具使用临时监听和测试证书，同机另一站点在夹具中使用占位响应，生产入口和入站防护仍须实际窗口验收。13 项定向测试通过，进程与临时数据已清理。[本机演练回执](verification/travel-frozen-https-cutover-fixture-2026-11-24.json)

11 月 24 日只读复查共享 nginx。实际只读单文件挂载仍匹配原配置，两端摘要一致，HTTP 根、旅行页及健康接口均返回 200，专用 HTTPS 两个旅行入口均返回 503。新增 HTTP 跳转守卫，锁内保留原 inode 和回退件，定向 18 项通过，覆盖语法错误、路由错误、部分写入和外部配置漂移。原机未部署守卫，正式使用仍需维护窗口授权。[回执](verification/travel-cleartext-cutover-guard-2026-11-24.json)

11 月 24 日新增保持维护时的 8000 验收工具，核对实际运行版本、专用代理成功访问、未登录拒绝、宿主直连阻断及防火墙计数。定向 23 项通过。只读核对原机仍为旧版通配监听，专用规则单元尚未安装。验收工具待在获批窗口随切换使用。[入站验收回执](verification/travel-closed-ingress-verifier-2026-11-24.json)

当时的操作文件冻结在私有 `cutover-operations/`，10 个文件逐项绑定到应用和配套配置。已独立重查内容及摘要，拒绝配置漂移、额外文件和错误编号。QQ 告警另等启用授权。[历史操作文件回执](verification/travel-cutover-operations-reviewed-2026-11-24.json)

11 月 21 日重新读取原机。旧 systemd unit 和共享 nginx 的宿主、容器配置均匹配原基线，正式代码与 venv 目录权限保持有效。已把数据目录及 SQLite 当前权限和窗口内拟收紧的值写入手册。[原机切换基线](verification/travel-cutover-origin-inventory-2026-11-21.json)

专用定时器服务已接入自动装载。11 月 25 日北京时间 00:19 和 06:20 两轮完成，证书一致时均跳过 nginx 重载。公网维护入口送出的同一证书到期于北京时间 11 月 29 日 21:21:39，下一次实际换代待验收。QQ 验收邮件已确认收到，自动告警待授权启用。[定时装载回执](verification/budget-edge-renewal-first-scheduled-load-2026-11-25.json) · [邮件告警交接](交接归档-预算版证书邮件告警.md)

2026-11-20，Asia/Shanghai。经用户授权完成一次原机隔离验收。358,546 字节的候选包由当前工作树重新构建并通过逐文件检查。为航班城市输入和结果正文生成的前端编号为 `7c620cde98827c6704b818f28f14ab49ddc594f44d188cff28f87c0797304aff`。[航班改动与真实响应](travel-data-suppliers.md)

## 原机验收与候选

- 在原机 CPython 3.12.3 环境复用已验收的 63 项依赖，原生生成 117 项应用候选 `28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281`。用回环 ASGI 服务逐项校验 `/travel/` 的 49 个静态响应，合成直飞和票价请求均通过真实应用鉴权及飞常准适配器，供应商网络层使用 `MockTransport`。
- 合成旧库升级、两次独立启动、预算与分段路线、旧 JWT 和可靠创建回读均通过。独立 SSH 复查确认原机本次目录只保留 `result.json`，试验进程清理完毕，现役旅行服务为 `active`，健康接口返回 200。本次验收范围为私有回环和合成数据，单次授权已消费。
- 按原生回执绑定私有计划 `3546a422a4ce7b43bdde35b184c39f1d4ab886e33eb6df151dcf43c7346fdb80`，共 197 项。67 个后端及部署源文件的哈希与前一轮一致，更新项限定于前端路径、应用清单和相应 systemd 版本路径。[机器回执](verification/travel-flight-city-input-2026-11-20.json)

## 专用 HTTPS 启动件

专用 HTTPS 配置包 `f85a32dea9051cb7379b34ddba704f26ed4f990b0e728e1d61b4eef22ded70bd` 绑定上述应用。正常 nginx 将旅行 API 转发到 `172.31.240.1:8000`，维护配置返回 503。专用 `travel.service` 使用该网桥地址监听，只接受 `172.31.240.2` 的转发头，依赖 Docker 和 `travel-backend-ingress.service`。后者加载已通过隔离报文验收的 `inet travel_guard` 规则。共享站点的 `nginx-http-after-tls.conf` 已收录，现场须先确认公众能通过 HTTPS 访问。相关本机配置和发布计划回归 43 项通过。[隔离报文验收](verification/budget-https-ingress-packets-2026-11-19.json)

## 原机 systemd 校验

原机普通账号将两份完整候选交给 systemd 255。`systemd-analyze verify --root` 在临时根中校验准确文件字节、依赖图及可执行路径，结果退出 0 且无诊断。模拟根使用合成的标准目标、Docker 单元和可执行路径。分别移除 Docker 单元和入站防护单元后，两次校验均报告必需依赖缺失。临时根已清理，原机现役旅行及 Docker 单元的 PID、启动时点和加载状态前后一致。本步骤仅做静态校验，未启动候选、执行 `nft` 或读取生产库。可在本机以 `python -B -s .release-candidates/supplier-city-2026-11-20/verify_supplier_systemd.py` 核对候选哈希，原机执行回执为同目录的 `systemd-companion-origin-verify.json`。[机器回执](verification/travel-flight-city-input-2026-11-20.json) · [systemd-analyze 文档](https://www.freedesktop.org/software/systemd/man/latest/systemd-analyze.html)

## 原机静态校验后的历史停止点

按用户要求，在完整 systemd 候选通过原机静态校验后暂停。恢复时先核对腾讯云 TCP 443 入站规则，确认实际公网 TLS 可用，再进入获批的停写窗口创建正式加密备份并取回验收。专用网桥和现役 8000 防护、候选服务实际启用、真实账号及设备、正式续期和邮件送达整体验收仍待完成。[公网入口回退](交接归档-预算版公网维护入口回退.md) · [加密备份恢复点](交接归档-预算版续期演练与加密备份准备.md)

私有文件位于 `.release-candidates/supplier-city-2026-11-20/`。候选路径为 `private-plan/` 和 `private-https-companion/`，Linux 原始回执为 `attempt-0e4630e60fc6.json`。可在仓库根用 `python -B -s backend/tools/release_plan.py verify --plan .release-candidates/supplier-city-2026-11-20/private-plan --expected-id 3546a422a4ce7b43bdde35b184c39f1d4ab886e33eb6df151dcf43c7346fdb80` 复核。`run_linux_budget_review.py` 默认只核对本机包，保存的单次执行标记用于避免重复上传。HTTPS 组合内的共享配置仅供私有审阅。

## 正式目录离线安装准备

`backend/tools/stage_travel_runtime.py` 从同一份 197 项计划复核并复制应用，按锁文件在目标版本路径新建 venv。依赖使用 `--no-index --require-hashes --only-binary=:all:` 离线安装。工具校验安装结果、代码摘要和所有者权限，拒绝覆盖同名版本，失败时回收本次创建的版本目录。正式模式要求 `/opt/travel` 及父路径已由 root 控制，安装器只准备代码和 venv。systemd、nginx、旧数据与写入门禁仍沿用发布流程。[安装器](../backend/tools/stage_travel_runtime.py)

在原机两个临时目录走完了安装流程。63 个 wheel 全部复用原机已验签文件，版本逐项吻合，`pip check`、PNG 生成及尺寸读取和运行依赖导入通过。再次写入同一版本被拒绝，临时目录已清理，现役进程状态保持一致。首次演练因脚本引用了错误的维护渲染函数名退出，修正后复验通过。44 项本机发布计划与候选测试通过。本次没有创建正式路径，正式目录属主和 `ubuntu` 的实际访问权限仍需在部署时核对。[离线安装回执](verification/travel-offline-runtime-staging-2026-11-20.json)

原机回执和演练脚本已保存在同一私有候选目录的 `offline-runtime-stage-origin.json` 与 `rehearse_runtime_staging.py`。公网 443 控制台规则仍待取得。Windows 桌面工具本次在 Edge 地址栏输入时无法取得前台窗口进程，重新选取时未能取得唯一窗口，因而未读到控制台规则。[公网入口交接](交接归档-预算版公网维护入口回退.md)

## 正式目录预置方案已通过原机预检

只读确认 `/opt/travel` 目前为 `1000:1001`、`0755`，两个正式版本父目录尚未创建。旧服务仍使用其下的原 `app` 和 `venv`，健康检查通过。预置控制器从同一个 408,001 字节包在原机根用户私有临时目录重建计划，63 个 wheel 复用原机副本，共验证 197 项。退出后临时输入已清理，正式路径及现役进程保持原样。[预置预检回执](verification/travel-formal-runtime-preview-2026-11-20.json)

执行入口为同一候选目录的 `stage_formal_release.py --stage-reviewed-plan f4b0b798f5a006749c106cef53e108a63652d24a7f84f348896170949fa67e4b`。需要用户授权将 `/opt/travel` 顶层改为 `root:root`，保留 `0755`。随后工具只创建版本化应用和虚拟环境，核对 `ubuntu` 能执行、能读取且不能写入。失败时回收本次目录并恢复原顶层属主。旧版子目录、业务库和配置保持现状。正式发布、systemd 切换和公网入口仍另行验收。[预置预检回执](verification/travel-formal-runtime-preview-2026-11-20.json)

## 正式版本目录已预置

经用户授权，`/opt/travel` 顶层已改为 `root:root`，保留 `0755`。在 `releases/<应用编号>` 与 `runtime-envs/<应用编号>` 使用冻结计划预置新版代码和 63 项离线依赖。完成后以普通 `ubuntu` 独立复核 117 个应用文件的 SHA-256、63 个依赖版本及 `pip check`。部署账号可读取代码、执行解释器，无法写入新版本及其父目录。旧版 `app` 和 `venv` 元数据、现役服务 PID 和健康检查均通过复核。[正式预置回执](verification/travel-formal-runtime-staged-2026-11-20.json)

临时输入已清理。上线时复用同一个版本编号下已预置的目录并复核，安装器会拒绝再次覆盖。专用 systemd 配置、网桥、8000 保护和 HTTPS 入口仍待在获批切换范围内验收。当前回退策略见文首。[公网入口交接](交接归档-预算版公网维护入口回退.md)

## 已预置路径的真实 systemd 隔离启动

以临时 systemd 服务和 `ubuntu` 身份直接运行原机正式版本目录中的代码和 venv。临时挂载只遮蔽该服务视图中的旧 `app` 目录，配置和数据库映射到合成文件。应用导入前检查了代码、解释器、映射文件和目录的 inode，以及 `PrivateTmp`、`NoNewPrivileges` 和私有网络。试验在独立网络命名空间监听 `127.0.0.1:8000`，资源限额保留在临时单元内。[实际启动回执](verification/travel-staged-systemd-runtime-2026-11-20.json)

匿名资料请求返回 401。合成用户注册、登录和创建旅行通过。中断临时主进程后，systemd 自动重启，同一 JWT 仍可访问资料，同一请求编号返回原旅行。临时单元和合成数据已清理，原服务进程及启动时点前后一致。公开网桥、实际 8000 入站保护、正式配置和业务库尚未进入该新版流程。[实际启动回执](verification/travel-staged-systemd-runtime-2026-11-20.json)

## 原机实际路径与正式候选预检

预置完成后，从原机私有临时目录把已冻结的旅行服务和入站保护候选交给 `systemd-analyze verify`，核对实际代码、解释器、宿主现有 Docker 单元和候选依赖。以 `ubuntu` 从正式路径实际运行解释器，版本为 3.12.3。两份 unit 的检查及宿主 `nft --check` 均退出 0。唯一诊断来自现有 `tat_agent.service` 的旧式 `/var/run` PIDFile 写法。临时文件已删除，现役服务状态和宿主规则摘要保持一致。[实际路径预检回执](verification/travel-https-actual-host-preflight-2026-11-20.json)
