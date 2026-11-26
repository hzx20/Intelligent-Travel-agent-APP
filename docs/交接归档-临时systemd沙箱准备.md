# 临时systemd服务沙箱：准备完成，等待启动授权

最新后续：用户已明确授权并恢复整体目标active，真实临时服务启动/主进程中断自动重启、凭证与同次创建恢复及清理均已完成，见[systemd实际运行交接](交接归档-systemd隔离启动与自动重启.md)。下文待答复、未上传/启动及暂停说明均为准备时历史状态，不再作为当前门槛；正式生产部署仍未授权或执行。

后续停止状态：当前地图恢复模块已完成并按用户要求暂停，最新入口为[地图加载恢复与暂停](交接归档-地图加载恢复与暂停.md)。下文active为准备当时的历史状态；本服务仍未上传/启动，原冻结计划也不包含后续地图源码改动。恢复前以用户最新答复和最新交接为准。

2026-11-17，Asia/Shanghai。整体目标active。本次只完成规格/脚本准备、本机检查和服务器只读能力核对，**没有上传运行包、创建unit或启动服务**。已向用户提出本次真实临时服务授权，截至本归档尚未收到答复；不能把时间流逝或旧静态检查授权当作同意。

上一完整完成项仍为[统一Linux发布计划](交接归档-统一发布计划与服务语法.md)：计划effb123a…fc42、运行包2b3a3395…6383，生产ready=false。原七阶段及约五类剩余工作不缩减。

## 已准备的运行件

- `systemd_sandbox_fixture.py`：从冻结完整unit生成受限临时服务规格；只允许原临时根内互不重叠的来源、随机新服务名及高端口，拒绝生产目录来源、关闭PrivateTmp/NoNewPrivileges、root用户或改变worker/数据路径。
- `qa_systemd_preflight.py`：计划作为ExecStartPre，在导入真实应用前先校验.env/数据/代码/venv映射inode；正确后才读取合成.env哈希，检查UID/UMask/NoNewPrivs、PrivateTmp哨兵隔离及本机私网负控连接。任何失败都不进入应用。
- `qa_systemd_inspect.py`：以系统Python `-I -S -B`、stdlib-only运行，按nonce/Description/Transient/ExecStart归属只检查该临时unit的PID、挂载、cgroup及合成映射，不读取生产.env或业务库。
- `qa_systemd_sandbox.py`：计划创建一个临时unit，直接运行真实`-m uvicorn app.main:app`，用合成账号/库测试登录/可靠创建；仅中断该临时unit一次，验证systemd自动重启后同JWT/同request_id恢复；最后显式stop、等待超过RestartSec、确认回收和无cgroup进程后删除合成work。

正式逻辑路径保持`/opt/travel/releases/<2b3编号>`、`runtime-envs/<2b3编号>`及原data/env锚，但仅在服务自己的文件系统命名空间用16MB tmpfs覆盖`/opt/travel`；ro绑定已测临时代码、venv、合成.env与探针，rw只绑定合成data。宿主正式目录应在测试前、中、后都不存在。PrivateTmp保留，不拿上一轮仍指向生产路径的语法夹具启动。

QA差异明确列出：API改为回环随机高端口；384MB/25%CPU/64任务；额外localhost网络过滤、私网自建监听负控和预检查；每次最长120秒、300秒内至多两次启动、stop超时10秒和collect。不能据此声称生产1.2GB真实负载已测。

设计依据为systemd项目[v255命名空间文档](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.exec.xml)、[网络/cgroup控制文档](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.resource-control.xml)及[transient/collect文档](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd-run.xml)。官方站点归档抓取超时后使用项目同版本源码文档；具体运行效果仍待真实验收。

## 已验证与尚未验证

本机 **21 passed，0.09秒，退出0**，只覆盖纯规格、来源路径、profile控制和命令名守卫；不是实际Linux unit启动证明。脚本均已AST解析，尚未执行Linux特有的服务/进程检查。

只读核对服务器systemd255.4，支持collect/property/service-type/setenv，cgroupv2有cpu/memory/pids控制器；sudo策略允许systemd-run/systemctl/journalctl及系统Python。这里只运行了help/version、文件元数据和sudo-l，没有执行这些新特权动作。

私有目录：`C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP\.release-candidates\systemd-sandbox-review-2026-11-17`。包含review、example-spec、example-start-arguments、4份脚本、expected、client driver及5文件压缩包。

压缩包 **9,941字节**，SHA256 `3a569b32175ea0bf69487fa59bdc60cae19fb2b4a3ccd63deb3ad0a99ad1163b`。拟用服务器stage：

`/tmp/travel-release-rehearsal-20261116-3d8b3a0a/systemd-sandbox-20261117-review1`

汇总：`docs/verification/systemd-sandbox-preparation-2026-11-17.json`。sudo策略回执在私有sudo-policy.json。示例端口/nonce仅供审阅，执行时选择空闲高端口和新随机nonce，并将实际request/start-arguments保存到新stage。

## 待授权范围与恢复

已询问约6分钟范围：向既有tmp传10KB脚本，sudo创建一个384MB/25%CPU/64任务的临时服务，unit命名空间合成映射，回环HTTP/自建私网负控，中断该临时进程一次验证自动重启，停止回收unit并删除合成env/库；不安装依赖、不读生产env/库、不改宿主/opt或现役服务。该授权目前待答复，不能运行client driver。

收到明确允许后，先核对当前review/bundle/source哈希以及effb…计划、原服务和宿主正式路径状态，再执行同一审阅件；不重复索取已获相同授权。若答复不运行，则保留准备件并继续其他不依赖此动作的工作。

执行后必须保留实际结果、失败及清理回执，独立复查unit/cgroup/fragment/PrivateTmp/work/端口和生产配置/PID。观察超时先查确切request和unit，不另开服务盲重试。失败不关闭核心隔离来迎合测试，不把静态通过记为真实unit通过。系统journal按原系统策略保留该合成服务日志，不做全局日志删除。

工作区main/HEAD f5bd176，混合改动保留，无提交/reset/clean。当前没有该模块的运行句柄。下一会话先读本归档和SESSION_LOG末尾，确认用户是否已答复本次问题；收到批准后继续，不用本归档旧“待答复”覆盖更新的授权或最新目标状态。
