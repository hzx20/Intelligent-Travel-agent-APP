# 首页恢复与可靠转换验收

在frontend运行`npm run test:e2e:trip-home`，默认Edge，桌面1280×900、手机视口390×844各15场景，共30项。复用planFixture、真实HTTP/认证/SQLite/调度器，仅替换规划图输出，用合成账号和新生成的travel-browser-accounts-*临时目录；拒绝已有库，外部钥匙禁用，浏览器阻止非本机请求。无真实模型、个人上传、业务库或真实供应商操作。

本套与accounts/profile/guide-detail/guide-editor/sharing/network/creation/spot-collection/spot-writes/plan-recovery/plan-creation/plan-history共用5264/18963，必须串行。drafts使用5263。可设置TRAVEL_E2E_PYTHON和TRAVEL_E2E_CHANNEL。运行会重建本套输出，定向复验可另设output目录。

## 页面证据

1. 键盘转换历史，显式打开可编辑旅行，原聊天不变。
2. 已提交转换丢响应，GET核对找回其他页面已编辑的原旅行，不再POST。
3. 未送达转换，核对后原版本重试，只一份结果。
4. 原历史版本冲突，核对/继续不偷偷提升原版本。
5. 本机复制已提交丢响应，刷新保留原快照；修改本机原件后继续仍用首次正文，不复制后续内容。
6. 只读核对复制结果，本机标记失败仍明确显示账号已确认，不把成功说成未保存。
7. 账号旅行503保留旧卡片，单来源重试不重读历史。
8. 历史坏200保留列表，不作为空数据；单来源恢复。
9. 本机读取故障保留卡片，不挡账号旅行与历史。
10. 取消路由离开、刷新、退出保留待确认；接受离开后返回可继续。
11. 接受换号后迟到成功不跳页，不影响新账号；回原账号可核对。
12. 本标签存储不可写不首次POST；旧能力不回退普通转换接口。
13. 错误归属200不清原次，真实重复返回同旅行。
14. 损坏/串号恢复信息锁定提交，放弃可取消或接受，不删除账号旅行。
15. 本机预订和订单号明确复制，原件保留；只验证手动记录，不涉及真实预订。

截图home-pending/home-confirmed各两视口，检查无横向溢出，相关操作入口在顶栏及手机固定导航之间。已观察手机标题不被按钮挤成单字换行，已确认面板用绿色区分待确认状态，面板获焦并进入视口。截图前的滚动是测试取景，产品自身也在面板出现/确认时定位焦点，不声称任何尺寸都已证明。

首轮28项中27通过、1手机失败：测试点击了隐藏桌面链接，未触发确认，超时不算通过。改用可见手机导航，增加预订复制场景及标题布局后最终30 passed39.5秒。原账号协作22 passed47秒为本轮回归。前端142项、后端完整离线210 passed/2 deselected、68.56秒及build:travel通过；之后not_created的already_imported标记小修定向11 passed2.64秒，未重跑全套。真实进程重启和旧结构恢复副本另有后端证据。详见`docs/交接归档-首页恢复与转换.md`。

## 协议

历史转换先检查`GET /api/trips/plan-import-capabilities`，再POST `/from-plan/{id}/convert`（原version/revision）。按账号+稳定方案revision唯一映射；GET同前缀`/conversion?revision=...`只读。首次201、找回200，回执含owner_id/plan_id/plan_revision及当前trip。首次陈旧版本409；已转换后重试返回当前旅行，不覆盖之后编辑；删除结果后410不重建。原`/from-plan/{id}`保留201/409兼容，新前端不回退。

本机复制先要求guest-import-capabilities的reservation_records/idempotent_import/status_lookup为true，POST原import-guest，POST import-guest/check只核对相同完整快照，不写入。服务端绑定账号、本机id、版本、规范化正文摘要；改同版本内容409。回执增加owner_id/guest_id/guest_version/state，旧调用仍兼容。

sessionStorage键`travel.home-transfer.v1.<owner_id>`，首次网络写前保存深冻结正文。刷新只恢复入口，不自动POST。列表刷新/核对不更新原次正文和版本；另起尝试须先处理或明确放弃原次。已确认结果由用户打开，不用迟到响应自动跳转。关闭标签、清数据或放弃可能失去入口；放弃不取消请求、不删云端或本机旅行。

日志、截图与trace在忽略的playwright-results目录，可能有合成凭证，不公开。当前未部署，生产表升级、真实账号/移动设备及外部服务仍待验收。
