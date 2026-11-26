# 真实游客AI页面与新增字段修正交接

后续更新：原规划器已修正预算与日编号，完整复杂样例仍有语义缺口；最新源码/停止点见[原规划预算与日期校验](交接归档-原规划预算与日期校验.md)。本页游客真实页面证据保留原范围。

2026-11-17，Asia/Shanghai。整体目标active，原七阶段未全部完成。本轮已完成真实游客AI的桌面/手机视口页面链路，修复新增活动附加来源字段导致连续失败的问题。停止在源码、测试和证据归档完成，未重打包或部署，也未推进酒店交易。

## 已完成改动和效果

上一轮真实模型顺序位置修正属于实质进展。本轮把验证延伸到真实前端页面→本机HTTP代理→真实后端路由→已配置glm-4-flash→预览→明确应用→刷新保存。

首轮模型给出了正确索引，但把原安排的source字段放入新增休息活动，两次均被StrictModel额外字段校验拒绝，页面HTTP502；两个供应商响应为200不代表页面生成成功。核对原localStorage完整记录未变，未应用失败建议。

`backend/app/services/trip_ai.py`现明确新增item只允许name/kind/start_time/duration_min/note/activity，禁止模型输出source/address/lng/lat/amap_id；来源由系统标记、地点仍待核实。针对extra_forbidden加入固定的字段格式反馈，不回传任意模型字段/内容，不删除多余字段后偷偷接受。继续最多两次生成、严格编号/位置和明确应用。

新增`backend/tests/test_trip_ai_extra_field_retry.py`4项，覆盖来源、经纬度、地址三类回复经明确反馈后修正，以及两次重复额外字段仍拒绝、不静默移除；核对原快照、服务器生成的待核实来源/空坐标。此前数值/顺序反馈和权限流程保留。

当前trip_ai SHA：dc385a3c5c493cb349715e7ad244f6b2bdea986c78969e1ca40029ce86858517。

## 验证结果和适用范围

相关6个测试文件：50 passed、9.99秒、退出0，4新+46既有；命令为在backend下设置PYTEST_DISABLE_PLUGIN_AUTOLOAD=1后运行`python -B -m pytest tests/test_trip_ai_extra_field_retry.py tests/test_trip_ai_index_retry.py tests/test_trip_ai_schema_http.py tests/test_trip_ai_schema.py tests/test_trip_ai_schema_routes.py tests/test_trip_ai.py -q`。句柄96758终态，不与旧327/196或历史41/46相加。

真实浏览器最终程序24.862秒完成两个完整流程：1280×900及390×844。两次页面预览请求均成功、各一次真实模型回复（HTTP200），共两次供应商请求；没有再做位置或字段重试才通过。首轮另有两次供应商请求，故本模块总共4次，不计上一模块的11次。

每个视口均核对：

1. 真实模型提出移动博物馆到第二天、断桥60→30分钟、第一天新增30分钟休息，预览包含3项差异；确认前localStorage整份旅行不变。
2. 点击“确认应用这些修改”后版本只加1，保留所有原地点和坐标/名称/备注，第二天顺序为原安排后接博物馆，新增休息无坐标且来源标待核实。
3. 合成预订记录在本机原样保留；浏览器提交、服务端接收及实际供应商发送三处均确认无reservations或合成订单标记。没有真实预订或付款。
4. 清空需求后真正刷新页面，已应用旅行完整保存，预览不跨刷新保留，未再次发起模型生成；行程页显示断桥/休息，无横向溢出。
5. 只删除自建合成旅行条目并关闭自建标签；两种视口各预览/应用截图共4张已导出并查看，第4张超过工具建议3张的提示已保留，不当作零警告。

运行绑定为旧冻结b46aab…80bd后端基底+当前trip_ai单模块快照，前端为369742…6a77的44文件生产包，本机Python3.13.14。没有把天气/航班等额外工作区改动混入该组合，不等同整个工作区、Linux3.12、nginx生产配置、云端账号或实体手机验收。地图API/JS配置在本轮关闭，图片未选择或外发。

## 失败、清理与私有证据

首轮目录`.release-candidates/real-ai-guest-ui-2026-11-17`保留HTTP502、模型多余source原回复、失败截图及原行程未变回执。清理检查时Tabbit已空闲过期，初次落到新空白页，在localStorage读取即失败、未执行后续动作；按retainedTabId346655381接管原页核对未变后才清理。自有空白页也关闭，finish成功，无强制清理；用户原标签保留。

首轮服务supervisor退出1：新增的只读行数检查使用sqlite连接上下文，离开上下文并不关闭连接，使自己的临时app.db删除时被占用。进程结束后独立核对确切PID17344/43792及端口50292/50293无残留，再确认绝对路径仅在该轮work目录下后删除，cleanup-recovery.json保留。不将首轮记为全通过。

成功轮目录`.release-candidates/real-ai-guest-ui-fixed-2026-11-17`：已改显式close连接，supervisor60900退出0、122.81秒，owned进程停止/work删除，原本机.env哈希前后相同。浏览器执行句柄50363退出0，自己的两个localStorage条目和标签已删，Tabbit finish=true/forcedCleanup=false。4张最终截图及browser-result/provider-calls/源码快照/HTTP请求/模型提示回复/cleanup等全部保留。

成功轮清理前临时库trips/trip_proposals/trip_ai_jobs行数均0；首轮也是0，说明游客没有创建服务器旅行/建议/账号任务。独立后检4个PID、4端口及2个work目录均无残留，Tabbit全任务为空。首轮最终.env哈希回执因清理异常未生成，不能冒充该项已独立完成；首轮代码只读原配置，本轮没有改.env。

`node --check check-template.js`最初按CommonJS解析Tabbit顶层await程序而失败；随后以真实异步函数包装解析通过，浏览器实际程序已成功执行。不把第一次语法命令写成通过。

## 当前状态和恢复

公开汇总：`docs/verification/real-guest-ai-ui-and-field-retry-2026-11-17.json`。旧033971…20970计划、2394源/Windows预览仍早于本轮和上一轮AI修正，冻结字节保留，不能直接作为当前版本发布。旧unified-ai-map-review client已被superseded守卫拦在SSH之前，标记更新当前源码SHA；备份仅历史，不能执行绕过。当前没有新Linux候选编号或运行证据。

工作区`C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP`，main/HEAD f5bd176，混合未提交和未跟踪改动全部保留；其他4个供应商文件差异见此前归档，未覆盖。无commit/reset/clean、SSH/生产切换、供应商开户或实际订单。

剩余约5类收尾仍按验收矩阵：真实酒店交易渠道和适配器；原规划器更复杂真模型/真实OCR和授权外部攻略；真实账号协作/设备；当前源码候选Linux整链、正式环境/备份/HTTPS/负载与发布；最终整体一致性回归。游客真实模型这一公开三操作页面场景已完成，不再列为完全未验收；更多要求及游客预览不跨刷新等边界保留。

恢复先读本归档/公开JSON/矩阵和当前源码，核对dc385a…858517及额外工作区变化。不要重跑已有结果的模型脚本重复消费；需要新候选则新目录重建和验证，不回写旧包。酒店仍缺渠道测试权限，交通本期继续高德路线，不自行扩展票务下单。
