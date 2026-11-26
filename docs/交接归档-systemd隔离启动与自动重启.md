# systemd隔离启动、自动重启与清理交接

后续统一打包已完成，当前候选与编号见[地图新版统一候选](交接归档-地图新版统一候选.md)。本文件的effb/2b3服务运行证据保持原范围；不能将其改写成新936a/b4c5完整计划的实际Linux组合运行结果。

2026-11-17，Asia/Shanghai。用户明确允许本次真实临时服务隔离验收，并随后将整体目标恢复为active。该最新状态覆盖上一模块的暂停；本轮完成一个运行保障事项，整体七阶段目标尚未完成，`production_ready=false`。

## 完成结果

在原服务器既有私有临时根下，使用既有63包独立venv和冻结Linux统一计划，真实创建临时systemd服务运行Uvicorn。最终成功轮remote exit0，34.23秒（不含上传）；本机规格/状态回归25 passed、0.10秒。

- 保持正式逻辑code/venv/.env/data路径，在该服务自己的命名空间以tmpfs覆盖`/opt/travel`，只读绑定代码、独立venv、合成.env及探针；唯一业务数据绑定指向自建合成data。预检查先校验inode，再读取合成.env，未读取生产.env或业务库。
- 两次实际启动均核对进程UID1000/GID1001、NoNewPrivileges、UMask0077、PrivateTmp哨兵不可见、4个只读绑定和1个合成可写绑定，CPU配额0.25、memory.max402653184、pids.max64。两个采样memory.current约103/102MiB，只是采样，不是峰值或负载证明。
- 只监听回环随机高端口。自建私网监听在外层连通；服务预检查对同一监听连接被限制，errno11、约0.601秒，监听未接收额外连接。没有向真实供应商发送探测请求。
- 合成注册201、登录200、可靠创建201；只向该临时服务主进程发一次SIGKILL，自动重启PID由2139312变2139661、NRestarts由0变1。原JWT访问资料成功，同一request_id创建重放200并返回原旅行，旅行仍可读。
- 显式stop后等待超过RestartSec，unit回收、无cgroup进程/fragment/PrivateTmp目录，监听和自建私网sink停止，合成work/.env/库全部删除。

服务限制为384MB、25%CPU、64任务、每次最长120秒、300秒内最多两次启动。不是生产1200MB配置、真实业务流量、TLS或真实地图/模型的验收。命名空间中的逻辑正式路径可用，不表示宿主正式目录已建立；测试前、中、后宿主目标路径都不存在。

## 实际失败及修正

所有失败证据保留，未把失败退出码改成成功。各轮严格顺序执行，确认前轮清理后才新建下一轮，从未同时运行两个本次临时unit。

1. review1：上传5文件9,941字节后，systemd-run在创建前按宿主文件系统预解析解释器，路径只存在于服务命名空间，因此退出1，未创建unit。清理及独立复核成功。
2. review2：增加仅用于QA的ExecSearchPath，绝对ExecStart保持原样。实际服务启动，但systemctl对空EnvironmentFiles数组不输出字段，检查器KeyError，原自动清理中止。通过同一源哈希检查器修正空字段解析，重新核对完整归属后显式stop并清理合成work。诊断期间120秒RuntimeMax实际触发一次自动重启；这轮不是计划的崩溃/凭证恢复通过。
3. review3：首次启动/合成登录创建成功；默认systemctl kill给主进程发送SIGKILL后，对辅助进程返回Invalid argument，因此命令整体非0。失败清理还遇到auto-restart阶段PID0/cgroup暂空的合法过渡态，被原守卫拒绝。等待其恢复运行后，严格核对该随机unit身份、命令和限制，显式stop并删除确切work。日志证明发生重启，但未完成重放验收，仍记exit1。
4. review4：明确使用`--kill-whom=main`，保存中断命令日志；检查器接受仅PID0、空cgroup、activating/auto-restart的过渡态，其他陌生cgroup或运行态空cgroup继续拒绝。空EnvironmentFiles正规化不吞掉非空值。最终启动→中断→重启→JWT/创建重放→自动清理完整通过。

修正依据包括systemd项目[v255 systemd-run源码](https://raw.githubusercontent.com/systemd/systemd/v255/src/run/run.c)的命名空间路径预解析条件和[v255 systemctl源码](https://raw.githubusercontent.com/systemd/systemd/v255/src/systemctl/systemctl.c)的kill-whom支持；具体空数组和信号错误以保存的本机实际输出为准。新增4项回归分别约束ExecSearchPath/原命令、空数组解析、准确重启过渡态及仅临时主进程中断，共25项。不削弱PrivateTmp/NNP、映射、资源或业务文件隔离来通过测试。

## 独立收尾与生产状态

最终另行SSH只读复核了review1—4的确切请求/unit：均未加载、无PID、无cgroup、无transient fragment、无该unit的PrivateTmp目录、无合成work，8个自建服务/sink端口均无监听。四轮上传源码逐项哈希未变。review1没有实际服务；其余三轮实际创建并已回收，失败轮没有被隐藏。

冻结184文件计划再次独立CLI verify退出0。宿主正式code/venv目标仍不存在。生产后端MainPID仍1465894、nginx主PID仍1，原共享配置SHA256仍`300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73`。没有安装依赖、读取业务库/生产.env、改宿主`/opt`、安装正式unit或reload/restart现役服务。

保留本轮临时脚本、非业务报告和按服务器原策略保留的合成服务journal；不做全局日志/其他历史目录清理。既有独立venv和wheel仍按原授权保留。

## 运行件与恢复信息

- 成功私有本机目录：`.release-candidates/systemd-sandbox-review-2026-11-17-attempt4`，含review、5文件bundle、upload、原始stdout/stderr、result和独立postcheck。前1—3轮各自目录保留原始失败报告，2/3另有cleanup-recovery。
- 成功远端stage：`/tmp/travel-release-rehearsal-20261116-3d8b3a0a/systemd-sandbox-20261117-review4`，仅脚本/合成日志/报告保留，无运行中的服务或work。
- 成功bundle10,215字节，SHA256`dd8a7b1dff161f41364d693ff56264a1d514bb946d933ac4454bab9767685549`；随机unit`travel-qa-a9a01821d2f54ea3a55b2ad5a649f78d.service`已卸载。
- 基准计划`effb123a9119956d419ba8e52aeaa307141254a10d775953330cc0721392fc42`、application`2b3a3395e87d12ce10920642ebbace27ee7928f648d2f02d6a963a6cc7406383`，冻结不回写，仍不包含上一模块的新地图前端。
- 公开汇总`docs/verification/systemd-sandbox-runtime-2026-11-17.json`。本次改动是QA脚本/规格/检查器/25项测试和文档，业务应用及冻结正式unit未改。

工作区`C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP`，main/HEAD f5bd176，混合改动完整保留，无提交/reset/clean。93095失败exit1、30428成功exit0，本轮无活运行句柄。下一会话先读本归档、SESSION_LOG末尾与最新用户状态，不能用旧准备文档的待授权状态重复索取本次已获授权，也不能把本轮许可扩为生产部署。

## 当前状态与后续顺序

整体目标保持active，按用户最新恢复状态继续，不因旧暂停归档再次暂停。下一可做当前地图前端与运维稿的最终运行件重新构建/统一绑定；再核对正式目录、有效unit/drop-in、权限/并发写入门禁、关联备份、HTTPS及资源负载。临时运行成功不代表正式配置已安装。

仍保留约五大类：发布运行保障、真实地图/复杂AI与OCR、攻略及供应商授权来源、真实账号/设备/视觉及复杂场景、最终统一验收与发布。对应[七阶段路线](行程产品改造路线.md)和[验收矩阵](阶段验收矩阵.md)，原完整目标未缩减。正式发布与真实外部调用按具体范围另行处理，不由本轮隔离授权推定。
