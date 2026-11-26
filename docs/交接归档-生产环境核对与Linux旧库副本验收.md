# 生产环境核对与Linux旧库副本验收

2026-11-16，Asia/Shanghai。用户最新目标active，整体七阶段尚未完成。本轮已有真实生产环境只读核对和明确授权的Linux隔离演练进展，不是生产发布；原服务、原库、nginx和生产venv未切换。

## 实际环境与发布差异

通过已有SSH连接ubuntu@134.175.137.18，严格使用已知主机指纹，未接受新指纹或输入密码。实际Python3.12.3、Linux x86_64/glibc2.39。travel服务active/running，MainPID1465894，WorkingDirectory=/opt/travel/app/backend，ubuntu运行，0.0.0.0:8000、workers1、--proxy-headers；没有forwarded-allow-ips参数或FORWARDED_ALLOW_IPS环境。TRAVEL_DATA_DIR和三个共享开关也未设置。

nginx实际为容器continew-web内1.27.0，IP172.18.0.5；普通docker访问无权限，已有sudo -n可执行只读nginx -v/-t，实际配置语法通过。当前缺assets专用404/缓存、.vite/.travel-deploy隐藏和入口no-store规则。实际GET缺失.js和.vite/manifest.json均返回200 text/html，首页无Cache-Control。IP的HTTPS探测SSL EOF，不能据此断言未来具域名/SNI的HTTPS一定不可用，实际域名/证书尚未验收。

生产venv的11项已有直接依赖与仓库一致，Pillow缺失；pip check通过只是旧依赖集合无破损，不证明新照片功能可运行。.env为0600，JWT_SECRET/高德/智谱配置项均存在，只检查布尔值，未输出密钥或核对服务权益。原业务库/opt/travel/app/backend/data/app.db为335872字节、0644，仍是8张旧表，完整性ok、外键违规0；未执行原库迁移。实际数据/签名路径、文件权限、可信代理及新nginx规则必须在最终发布方案中明确。

只读观察摘要：docs/verification/production-readonly-2026-11-16.json；不含业务行/凭证。表结构核对本身只读；随后对受保护副本的数据核对另获授权。

## 授权及执行范围

自动审批最初拒绝上传候选源码并联网安装临时依赖，原因是未明确授权向该具体远程目的地发送私有代码；被拒命令没有执行，未绕过。随后用户明确回答“允许本次隔离演练”，授权向ubuntu@134.175.137.18独立/tmp/travel-release-rehearsal-*目录传送已校验代码，在该目录安装Pillow13.0.0，并用生产库只读快照副本升级/重启，结束删除业务副本，仅取回无业务行报告。

此授权已用于本次演练，不需要为同一收尾反复确认；不能扩大为生产部署、修改系统服务/现有venv/原库、真实供应商或个人图片上传授权。记录见docs/verification/linux-rehearsal-request-2026-11-16.json，当前状态rehearsal_passed，远程源码和依赖操作均在用户确认后执行。

服务器直连官方PyPI下载wheel60秒超时，子进程由subprocess.run终止并退出，未安装成功、未开始业务副本演练。确认该独立目录状态后改为本机从官方PyPI元数据取得同一cp312 Linux wheel，核对SHA256，传入原授权目录离线安装，不改生产venv。来源：[Pillow13.0.0官方PyPI](https://pypi.org/project/pillow/13.0.0/)。文件pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl，7144542字节，SHA256=42d63be3af4f9b951b199b0a9d23365d37ab14687e6d77b74940432c8515c4ca。哈希是与官方元数据比对，不冒充额外签名验证。

## 保存的Linux候选与工具

原Windows候选9e98f19b25a1b5f79376a272d44653728c3f9ea679582f3f7910147f11939c5d的104文件全部保留字节，仅由服务器Python3.12.3重新prepare，生成独立Linux候选：

- 编号：a2bd8b01fcf847f04a03256522b62924288d14427bd0efc0feffa61be6973684
- 本机持久副本：C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP\.release-candidates\linux-app-2026-11-16-a2bd8b01
- 服务器临时根：/tmp/travel-release-rehearsal-20261116-3d8b3a0a，候选在candidate，Pillow仅在deps，helper为rehearse.py。
- 前端编号仍b13f55b253f90ebef299a6c3cae142ece51c43dc4e9a2008e58b6183da5b67fc；Linux12项直接依赖匹配。
- 准备/来源/本机路径报告：docs/verification/linux-candidate-preparation-2026-11-16.json。

新增backend/tools/rehearse_release.py：仅接受全新独立工作目录，候选/源库/工作目录不重叠；源库mode=ro在线快照，再生成恢复副本。含持久任务行的快照会拒绝启动，避免重放复制任务；不删任务来绕过检查。启动前验证候选/运行版本，使用外部临时数据和合成JWT，清空供应商钥匙，对socket加本机限制。两次启动均断言实际app路径和数据路径；健康200、未认证旅行401，读预检检查实际结构与前端。

工具对原列按有序行逐值作内部摘要，只输出表名、列/行数和是否保留，不输出业务行/密码。结束只删除自己全新工作目录下的快照、恢复目录及WAL/SHM，保留报告和私有日志；不清理未知目录或进程。候选不写pyc/data。此辅助工具不在原104文件候选白名单内，以单独rehearse.py传入；生产运行不依赖它。

## 验收结论及边界

真实生产快照包含users2行、spots500行、ai_plans2行，其余5张表0行。Linux候选从恢复副本两次真实Uvicorn启动成功，8张原表所有既有列和值保留，新表/补列通过预检，第二次启动全库内容稳定；候选和基线快照哈希未变。源文件inode/大小/mtime在演练期间观察未变，源连接始终mode=ro；这不承诺其他正常用户永不写库。真实攻略表0行，因此不能声称核对了真实攻略内容迁移；已有合成旧攻略/旅行等测试为另外证据。

报告：docs/verification/production-copy-rehearsal-2026-11-16.json。结束后远程再次核对snapshot.db和data均不存在，原服务PID仍1465894、健康ok、原库仍8旧表、生产venv仍缺Pillow、原nginx -t仍通过，见production-postcheck-2026-11-16.json。只取回JSON和候选代码元数据，没有下载业务库/照片/凭证。

另在隔离Pillow环境内存生成32×32合成PNG/JPEG并编码/解码成功，证明两个基础codec可用；不使用个人图片、不向API或OCR上传。它不替代完整Linux照片权限/缩放和真实OCR验收。

本机新增6项安全/失败清理测试，与候选工具16项最终22 passed、1.65秒：新列允许但原值变更被发现、两类持久任务拒绝、路径重叠/已有目录拒绝、启动失败/任务拒绝均删除自建副本且不报告私有行。初轮4项0.06秒为较早子集，不累加。授权Linux演练为单独实测，内含两次启动，不把它当作另两项pytest；未重跑完整后端248、前端179、nginx两视口或此前49项全部套件。

## 未完成与下一步

本轮落实当前真实旧库副本迁移和Linux基础运行证据，不是生产切换、旧代码回退或负载许可。候选是冻结快照，本轮新增交接/辅助工具不回写旧候选；最终nginx等整合后应重新准备统一候选并复验。

下一优先准备与实际共享nginx配置合并的可审阅候选，在实际Linux nginx只验语法/隔离运行，不直接覆盖或reload；明确实际代理IP信任、稳定数据/.env和新版本依赖环境，保护另一根站点。生产依赖安装/配置切换仍需最终发布授权。还需真实旧代码回退兼容、灾备方案、HTTPS域名/证书、权限/负载、旧资源保留策略和实际设备。

整体约五类未完范围仍保留：生产发布/灾备与配置；真实环境/资源/HTTPS；真实地图复杂AI/OCR权益费用准确性；授权攻略来源/供应商；真实账号设备与整体视觉/复杂协作。已完成当前库副本核对不能替代未来业务状态、外部权益或最终上线。真实OCR此前欠费，sample.png外发授权仍只限本机5264/18963合成验收，勿扩大。

## 恢复

仓库C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP，main/HEAD f5bd176，全部混合未提交/未跟踪改动保留，无提交/reset/clean。Python C:\Users\i\Desktop\travel\.venv-analysis\Scripts\python.exe。先读本归档、矩阵/路线、SESSION_LOG末尾及上述5份JSON，核对最新用户目标状态和实际服务。

Linux候选本机verify可用，但本机Python3.13运行runtime会因主次版本不同拒绝，这是预期；实际Linux测试使用3.12.3加隔离deps。先从preparation报告取路径并核对候选哈希，不假定服务器/tmp持久存在。若需重演，同一helper传--candidate、--source、--workdir全新目录，运行时PYTHONPATH只指隔离依赖，环境PYTHONDONTWRITEBYTECODE=1，目标Python3.12.3；原服务数据和JWT不得被测试值替换。生产发布不能使用临时目录或这个合成JWT。

上传归档在本机.release-candidates/linux-rehearsal-request-2026-11-16.tar.gz（106成员=104文件+候选根清单+helper，309737字节），SHA256=7210c6d1707db880cbe276e9414c9be2ad3ae2ad088680613e397190f9da140a；代码/依赖与无业务行报告在服务器0700临时根内保留，业务副本已删除。此前3个浏览器临时目录仍未核实归属/占用而保留。本轮只结束自建演练进程，不宣称枚举清理全部系统进程或所有临时文件。
