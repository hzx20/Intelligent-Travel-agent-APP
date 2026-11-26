# 共享nginx合并候选与语法验收

后续状态（2026-11-17）：本归档保留历史证据；当前代理协议头修复、最新候选与暂停状态见[模块暂停归档](交接归档-代理协议头与模块暂停.md)，恢复时以该归档及最新用户指令为准。

2026-11-16开始，2026-11-17收尾（Asia/Shanghai）。整体目标active，原七阶段未全部完成。本轮完成共享配置的可审阅合并、实际Linux语法检查和本机HTTP验证；未覆盖线上配置、reload、重启服务或正式发布。

## 本轮改动及预期效果

新增backend/tools/nginx_merge.py，prepare只读来源/模板，输出全新私有目录中的nginx-candidate.conf、nginx.diff和report.json，不提供发布或reload命令。按词法/结构解析注释、引号、转义及${variable}，只替换一个明确旅行server内的旅行locations及对应http限流zone/map。其他站点内联指令保持原结构和原文本；新增旅行资源404/immutable、入口no-store和隐藏.vite/.travel-deploy规则，API仍剥/travel前缀并覆盖客户端转发头。

同名旅行zone/map若被其他路由引用、定义重复、旅行server不唯一、regex/named旅行location歧义、未知include或托管区夹入无关指令均拒绝自动合并。仅允许标准mime.types include，不展开任意外部配置并冒称已保留。全局map_hash_bucket_size至少满足模板128，已有更大值不降低；此全局设置列入明确修改范围。使用BEGIN/END TRAVEL HTTP/SERVER标记，重复运行同一模板字节一致，源文件和已存在输出不覆盖。

新增tests/test_nginx_merge.py（25项）和test_nginx_merge_runtime.py（1项）。本轮无前端业务/数据库改动，也未修改原旅行模板内容。合并工具是本机运维辅助，不在既有104文件运行候选白名单内；完整生产配置含另一项目，始终存放Git忽略的私有审阅目录，不放入公开代码包。

## 实际候选及证据

- 私有根：C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP\.release-candidates\nginx-review-2026-11-16-bb57aa1d
- 原文件：production-before.conf，4491字节；来源/opt/continew/nginx.conf，SHA256=300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73。
- 最终候选和差异：上述根下merged-final/nginx-candidate.conf、nginx.diff；候选7770字节，SHA256=02f7fae48c9e6a5b210f43bddb3b69db2e1a361e97f6cffad87de644b3e9dfe7。
- 模板SHA256=afdc08a1d25380857da6a0a4b72a57fd9969df7848d4ab40a97f320d19fd7d49；原4个旅行location替换为8个，5个zone各一份。全局map桶从未显式设置变128。
- 非旅行内联指令结构摘要c362c6fb8c56f1ed4422e145183d337dbb2d6a771c7b934c8b4eb81cfb6dcdd0保持一致；此为受限解析范围证明，不是未知include或整个站点所有行为的证明。
- 汇总：docs/verification/nginx-merge-2026-11-16.json，completed_date=2026-11-17，状态review_candidate_validated_not_deployed。保存工具哈希、源码/候选/差异路径、语法各次结果、挂载观察和最终26项结果，不含完整其他站点配置或凭证。

## 授权及Linux检查

最初自动审批拒绝把本份共享配置送回原容器，原因是此前代码候选演练授权未明确覆盖这份配置传送。被拒命令未执行，未绕过。用户随后明确答复“允许本次只读语法检查”，范围为ubuntu@134.175.137.18的continew-web容器，仅检查本候选，不保存或覆盖生产配置、不reload或切服务；没有扩大为发布授权。

首次-c /dev/stdin未读取有效完整配置，虽出现syntax is ok，最终仍报no events并失败，不能记通过。改用容器/dev/shm的0600临时检查文件后，原配置相对include mime.types因路径改变失败；检查副本已删。最终在0700内存临时目录中建立到原/etc/nginx/mime.types的只读用途符号链接，保持候选字节不变，以实际nginx1.27.0执行-t成功。临时文件、链接和目录均删除并验证不存在，不落/etc/nginx或宿主机生产配置。前两个失败是验证输入/相对资源夹具问题，不是放宽模板或把失败计通过。

检查前后宿主机配置哈希都为300ba…589f73，后端MainPID都1465894；容器当前/etc/nginx/nginx.conf哈希也与宿主机一致。新候选仅被检查器解析，运行中的nginx没有加载它；线上资源缺失仍可能回SPA，不能宣称新规则已生效。加未知include保护后重新生成最终候选，字节仍02f7…b3e9dfe7，与Linux实际检查完全一致。

## 本机HTTP与测试

最终26 passed、10.61秒：25项合并/保护测试+1项实际nginx HTTP集成。本机使用官方Windows便携nginx、实际dist副本和合成HTTP后端，模拟共享根站点/另一API/正则静态规则，检查另一站点仍返回原结果、旅行深链读取真正index且no-store、全部manifest资源正确MIME/immutable、缺失.js404、真实存在的私有记录与清单404、API前缀/query保持、伪造X-Forwarded-For/X-Real-IP被覆盖。SSE仅验证专用路径转发，不冒称验证完整流式时序。

初轮1项默认GBK读取UTF8差异失败，测试显式UTF8后21项通过；补未知include保护后旧标记区测试先被include保护拒绝，改用普通无关指令独立验证标记区保护，未知include另有独立测试；最终26全部通过。未修改产品来适配断言，复跑不累加。此前完整后端248、前端179、49/22项等都是对应前轮证据，本轮未重跑这些完整套件，也未重跑两视口浏览器。

backend复验：设置PYTEST_DISABLE_PLUGIN_AUTOLOAD=1，用C:\Users\i\Desktop\travel\.venv-analysis\Scripts\python.exe -u -m pytest tests/test_nginx_merge.py tests/test_nginx_merge_runtime.py -q。HTTP测试需.tools/nginx-1.30.6/nginx.exe和frontend/dist/.vite/manifest.json，缺失会skip，不计通过；进程和本机端口可能需沙箱外权限。

## 已观察到的实际挂载约束

inspect确认/etc/nginx/nginx.conf是宿主机/opt/continew/nginx.conf的只读单文件bind，静态根是/opt/continew/web到/usr/share/nginx/html的只读目录bind。[Docker文档](https://docs.docker.com/engine/storage/bind-mounts/)说明只读限制的是容器写入；本轮只核对其元数据，没有改挂载。

单文件绑定会保留其文件对象，宿主机重命名替换可能使容器继续读取旧对象，见[Moby项目贡献者说明](https://github.com/moby/moby/issues/6011)。结合本次实际单文件挂载，最终提升方案须保持可见的文件对象或明确安排重建挂载，并在reload前核对宿主机/容器哈希都等于已验证候选。不要只看宿主机新哈希就推断容器已读新配置。本轮没有试写当前文件，也没有提出未经验证的自动提升命令；配置备份、失败恢复和其他站点保护须包含在最终发布方案。

## 当前状态、待办及恢复

整体继续active，完成“共享配置合并与语法/本机行为验收”。下一推进实际Linux候选的隔离HTTP验收、systemd稳定数据/.env/可信代理及生产依赖环境、旧代码回退和最终统一候选。现有生产Pillow仍缺失、可信代理未按实际容器IP配置；本轮授权不覆盖这些生产变更。还需HTTPS域名/证书、真实代理/负载/权限、真实地图复杂AI/OCR权益准确性、授权攻略/供应商、真实账号设备与整体视觉及最终发布。原七阶段/约五类完整范围保留。

工作区C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP，main/HEAD f5bd176，所有混合未提交/未跟踪改动保留，无提交/reset/clean。当前日期已进入2026-11-17，报告文件名16表示开始日期，不重命名或误当过期验证。先读本归档、矩阵/路线、SESSION_LOG末尾和上述报告，核对最新目标状态。

在受保护源码工作区执行：python -B backend/tools/nginx_merge.py --source-file <已核对私有原配置副本> --template deploy/nginx-travel.conf --output-dir <全新私有目录>。读取报告路径后复核SHA256、差异和源配置当前状态；基线300ba…若已变化，先重新获取并生成候选，不能覆盖他人更新。同一已授权语法检查收尾无须反复问，但生产应用和额外运行动作要按实际范围确认。

两种已有代码候选9e98…（Windows）和a2bd…（Linux）均为冻结快照，新辅助工具/文档不回写旧包；最终整合后重新构建和复验。完整生产配置及diff保持.release-candidates忽略，不公开trace/其他站点内容。Linux语法通过不能替代实际Linux隔离HTTP、HTTPS/代理链或生产最终核验。此前3个浏览器临时目录未核实归属/占用，仍保留；只结束本轮自建进程，不宣称清理全部系统进程/临时文件。
