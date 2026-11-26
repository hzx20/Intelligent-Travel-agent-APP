# 原规划可靠创建与停止验收

后续规划历史保护见`../plan-history/README.md`，该套及trip-home/discovery也共用5264/18963，须串行。最新状态见`docs/交接归档-发现页恢复.md`，目标active。本套“回看旧历史”场景先调用实际store的startFresh建立独立空会话，使初始active查询失效，再创建历史夹具，避免夹具生成期间被页面当作待恢复任务接回；等待单个历史气泡后才断言正文，保留最终服务器原聊天不变的断言。发现页模块修改共享目的地控件后，本套完整30项51.9秒回归通过。

frontend运行`npm run test:e2e:plan-creation`。桌面1280×900、手机视口390×844各15项，共30项。默认已安装Edge、上级`.venv-analysis`，可设TRAVEL_E2E_PYTHON；实际手机/其他浏览器/真实账号模型仍待验证。

与plan-recovery/accounts/profile/guide-detail/guide-editor/sharing/network/creation/spot-collection/spot-writes共用5264/18963，必须串行，drafts用5263。复用planFixture：仅生成的travel-browser-accounts-*目录下全新数据库，拒绝已有库及外部钥匙，只替换图输出，保留真实认证、HTTP、SQLite和调度器。合成账号，无坐标/图片，阻止非本机请求，无个人上传或业务库操作。

## 页面覆盖

1. 已提交后丢响应，原UUID和原内容找回同任务，只有一条历史。
2. 刷新恢复固定快照，不换UUID/正文。
3. 游客随机凭证随本标签恢复，新标签不带入。
4. 存储不可写时保留输入，不POST。
5. 旧能力或专用接口405不回退无标识创建。
6. 错UUID成功回执不清快照，真回执才接回。
7. 停止先到，迟到创建410不启动；之后新需求独立生成。
8. 创建已保存而201迟到，停止后旧回执不恢复生成界面。
9. 创建和停止都丢响应，刷新保留cancel模式，继续只停止不再创建。
10. 换号不串待确认提交，回原账号可停止同次任务。
11. 放弃确认可取消；接受仅清本标签，不删已生成历史。
12. 新需求链接不覆盖待确认内容，不自动另建。
13. 损坏快照锁定提交，明确放弃前不POST。
14. 已知任务过期仍有可见入口，同UUID410不新建，停止确认expired后结束恢复。
15. 回看历史不改冻结快照，找回后回原对话，旧历史聊天原文不变。

截图creation-pending/creation-recovered/cancel-pending各桌面和手机，检查无横向溢出、放弃入口位于固定导航上方。手机未知区自然增高，按钮最小44px，未确认时日期/目的地组inert锁定键盘。取景滚动只是测试动作。输出在忽略的playwright-results/plan-creation，可能含合成凭证，不发布；定向运行重建本套输出。最后历史选择器及控件锁定定向复核使用独立plan-creation-final目录，保留全套截图；该目录最后只有锁定两项的图片，不宣称仍有前次全部定向产物。

测试脚本独有/api/__qa/plan/expire/{jid}只修改临时库已完成任务时间，验证页面过期，不是等待真实30分钟；生产app没有该接口。实际进程替换/创建取消映射证据见后端test_plan_restart.py。原plan-recovery22项另外回归了新协议下的恢复/取消/账号流程。

## 协议及边界

先GET jobs/capabilities，再按账号在sessionStorage保存UUID、深冻结请求、create/cancel模式；游客另有随机guest_key。只有支持reliable_creation和scoped_guests才POST jobs/create，201首次/200找回，回执须匹配UUID、owner_id和合法任务编号，不降级旧POST。存储失败不首次POST，坏回执保留标识。

服务器按账号或游客凭证摘要+UUID唯一映射，内容变409；任务/映射同事务。jobs/create/cancel可先于创建留下取消记录，迟到创建410；已创建则撤销租约，已完成返回实际状态，不谎称撤销历史。客户端先保存cancel模式，刷新不会改回创建；已知编号取消未知仍GET核对，无编号时须显式继续。

任务30分钟过期或硬删除后最小映射继续保留，410不复活；清理先解除外键。映射无原正文或原游客凭证，仅摘要、请求标识、关联/取消标记。旧无UUID的/jobs和SSE兼容创建不保证去重，游客须保留原guest_key与UUID。关闭标签/清数据/明确放弃会失去恢复保障，放弃不停止后台/删除历史；同任务重启仍可能重新调用模型，不保证外部计费一次。

test_plan_creation.py7项覆盖并发、作用域、取消竞态、过期/硬删、外键、原子回滚及调度启动失败；test_plan_restart.py新增实际进程验证映射、游客及预先取消跨重启和过期不复活。前端快照/会话测试覆盖深冻结、存储、坏回执、历史和取消模式。完整结果、失败记录、生产迁移/代理边界见docs/交接归档-规划可靠创建.md。
