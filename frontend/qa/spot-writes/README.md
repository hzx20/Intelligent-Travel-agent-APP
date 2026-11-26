# 景点评论与收藏可靠写入验收

在frontend目录运行`npm run test:e2e:spot-writes`。默认已安装Edge，桌面1280×900和手机视口390×844各12项，共24项；实际手机、其他浏览器、生产账号和生产网络仍须单独验收。

与spot-collection/accounts/profile/guide-detail/guide-editor/sharing/network/creation共用5264/18963，必须串行。复用`playwright.spot-collection.config.mjs`的metadata.seedSpots及共用启动器：只有新建`travel-browser-accounts-*`目录下的全新SQLite库，外部钥匙为空，无坐标、无图片的两个人工景点。合成账号通过真实注册登录接口建立，浏览器阻止非本机请求，无个人文件上传。默认使用工作区上级`.venv-analysis`，可设置TRAVEL_E2E_PYTHON。

输出在Git忽略的`playwright-results/spot-writes`，可能含合成凭证，不发布。定向运行会重建本套输出目录，完整套件才生成所有截图；原collection套件使用独立输出目录。

## 覆盖

1. 评论先真实提交再丢弃响应，核对不重发；同UUID返回原记录，列表不重复插入。
2. 刷新前触发原生beforeunload；接受后恢复同一正文、评分和UUID，继续找回同条评论。
3. sessionStorage写入失败保留输入，不发送POST。
4. 景点隔离；明确放弃前可取消，放弃仅清本标签快照，不删除服务端评论。
5. 同标签退出及换号，不带入旧账号快照；切回旧账号恢复。
6. 旧服务能力缺失或专用路径405，不回退旧评论接口。
7. 服务实际保存但回执UUID错误，不清输入，原次重试取得真回执。
8. 错账号/损坏快照锁定发布，明确放弃才清除。
9. 收藏实际保存后丢响应，再次PUT保持原目标和版本，不反转状态。
10. 双标签同版本收藏只增加一次版本；较旧的相反决定409，核对后才可发新决定。
11. 收藏读取缺版本、专用PUT405时不降级无条件toggle。
12. 收藏200回执与目标相反时不显示成功，保持原尝试重试。

`comment-pending.png`、`comment-recovered.png`、`favorite-recovered.png`分别记录待恢复、恢复成功和收藏恢复；评论截图滚动至相关区域，并断言无横向溢出、待恢复的放弃入口在固定导航上方。截图滚动只属于验收取景，不宣称产品页面自动滚动。原20项collection套件复验加入旅行、原生弹窗、账号/路由/离开保护及新协议下核对行为。

## 协议与恢复边界

评论能力探测成功后先将正文、评分及UUID写入本标签sessionStorage（键包含账号和景点），再POST`/api/spots/{id}/comments/create`。成功必须同时匹配UUID、景点、正文和评分才清除。后台同账号UUID唯一映射与评论同事务保存，重试200返回原条目；修改原内容/景点409，被审核删除或硬删除410且不复活。关闭标签、清存储、显式放弃后不能保证再找回同UUID；未提交输入不持久化。其他账号及其他独立标签不自动共享快照。

收藏PUT`/api/spots/{id}/favorite-state`包含目标布尔值和加载时版本，按账号/景点原子条件更新。同目标可安全重复，相反陈旧意图409；未知操作只在本页保留，刷新后读取实际状态，显式核对会结束旧尝试。不会自动同步全部标签。原Favorite表同事务镜像，保留收藏列表、计数和推荐统计。

后端专项`tests/test_spot_reliable_writes.py`验证并发、去重、内容绑定、删除不复活、旧收藏采用及旧接口428；`test_database_migration.py`在恢复副本连续升级两次并保留原内容，`test_service_restart.py`使用真实uvicorn进程验证磁盘重启后相同请求仍恢复。生产库不由这些测试升级，生产迁移仍须备份演练。
