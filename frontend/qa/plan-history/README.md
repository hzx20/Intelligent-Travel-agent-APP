# 规划历史保护验收

在frontend目录运行`npm run test:e2e:plan-history`。桌面1280×900、手机视口390×844各16项，共32项，默认使用已安装Edge。与plan-recovery、plan-creation、accounts/profile/guide-detail/guide-editor/sharing/network/creation/spot-collection/spot-writes共用5264/18963，必须串行；drafts使用5263。

复用`e2e/account-backend.setup.js`的planFixture：仅在生成的`travel-browser-accounts-*`目录启动全新SQLite库，拒绝已有库，禁用外部钥匙；只替换图的生成输出，保留实际认证、HTTP、数据库、调度器和版本写入。合成账号、无坐标行程，浏览器阻止非本机请求，无个人文件上传。地图视角通过实际store的`setMapView`触发保存，未调用真实地图SDK拖动或真实模型。

## 覆盖范围

1. 键盘打开历史，保存视角、刷新及重新读取聊天和地图。
2. 较早详情迟到不覆盖较晚选择；错误编号的200拒绝显示。
3. 列表503保留已有行，显式重试恢复。
4. 延时视角保存固定原记录，切换历史不写进新记录。
5. 两个实际浏览器页面共享身份，另一页先提交后，原页409保留本地视角和首次版本；核对不改变提交版本，取消/接受重新载入分别验证。
6. 快照真实提交后丢响应，原版本重试409，不覆盖已保存数据。
7. 连续视角串行保存，只有确认前次版本后才提交下一次。
8. 删除确认及服务端限时撤销，聊天恢复完整。
9. 删除提交后丢响应，GET核对结束未知状态，不额外删除。
10. 删除未送达，核对后继续仍用原查询版本。
11. 删除回执迟到，不清除之后打开的另一条历史。
12. 另一页恢复后再次删除，原撤销409；核对保留原版本，结束核对确认可取消/接受。
13. 撤销过期410不复活。
14. 未确认快照取消刷新、取消换号后保留视角和对话。
15. 生成回复完整保存，不因打字动画截短；刷新保留已确认版本，后续地图仍可保存。

第2项含两个独立场景，合计16场景，每场景各跑两个视口。四类截图history-open/snapshot-conflict/history-undo/delete-unknown保存在忽略的`playwright-results/plan-history`，检查无横向溢出，相关操作入口位于顶栏和手机导航之间。截图前滚动是测试取景，不代表产品自动滚动。日志/trace可能包含合成凭证，不发布。

仅测试bootstrap注册`POST /api/__qa/plan/expire-undo/{id}`，验证登录归属且只修改临时库的已删除记录期限；不是等待真实8秒，生产app没有此接口。其他expire/recover接口同样只在测试bootstrap。实际进程替换及迁移副本的持久化证据在后端`test_plan_restart.py`和`test_database_migration.py`。

## 协议边界

读取包含version与稳定revision。快照写使用`PATCH /api/plan/{id}/snapshot/edit`，删除使用`DELETE /api/plan/{id}/content?version=...&revision=...`，恢复使用`POST /api/plan/{id}/content/restore`。修改只接受原版本，未知结果不自动用最新版本重发。状态核对只报告服务器状态；放弃本页尝试不撤销已发送请求。

撤销期限为服务端8秒，重复删除不延长；前端扣除请求耗时，过期不再提供新8秒。历史草稿/未知操作主要保存在当前应用内存，刷新有原生确认，账号切换有确认；当前实时对话缓存保存基线及未确认警告，不盲目自动重发。其他历史记录的未确认正文不跨刷新保证恢复。旧无版本写接口返回428，新客户端专用路径缺失不会退回旧写接口。实际设备、真实地图、生产账号、生产旧库迁移仍待验收。
