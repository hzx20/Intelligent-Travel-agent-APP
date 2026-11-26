# 旅行工作区验收

在 frontend 目录执行 `npm run test:e2e:workspace`。使用 Edge、Playwright 1.65.1，桌面1280×900、手机390×844，另检查320×740；这是浏览器视口验收，不能替代真实手机。

配置沿用 accounts 隔离服务：5264 前端、18963 后端；与账号、网络、分享、发现等套件串行执行，禁止复用业务服务。setup 创建 host TEMP 下 travel-browser-accounts-* 新目录，清理只处理本次精确路径。workspace-backend.py 拒绝已有库或外部地图/模型钥匙，只有模型传输替换为合成修改建议；鉴权、SQLite、持久任务、预览校验、版本应用和记录接口都是真实本机实现。浏览器阻止非本机请求，无上传、个人资料或供应商调用。

8个场景×2种视口：游客创建/收集/排期/预订与刷新；景点库加入后打开收集区；AI任务切区继续、预览不写、显式应用；跨区草稿与足迹切日保护；503/错误旅行回执保留缓存和草稿；预订/记账/足迹真实保存与同行入口；工作区/日期URL刷新与攻略深链接；地图禁钥匙降级/重试及窄屏菜单焦点/Esc/外部点击。

结果、初次失败及修复见 docs/交接归档-旅行工作区暂停.md。截图/失败trace在 frontend/playwright-results 下，含合成账号数据的输出不可公开。指定 `--output playwright-results/workspace-verified` 可与其他套件输出分开。

组件测试另外用模拟SDK检查隐藏时不初始化、显示只构造一次、保留用户视角、隐藏时换选择再定位、销毁等待与错误重试；没有实际高德SDK拖动验收。resizeEnable 使用官方地图选项，参考 https://developer.amap.com/api/maps-javascript-api/reference/amap-map/map 。

本次修改了旧 drafts/accounts/network/sharing 脚本，显式进入对应工作区；原数据保护和权限断言保留。撤权后UI锁定写入，另直接请求验证服务端404；隐藏区域的表单先切回可见区再核对。其他旧套件只有此前证据，不能将其旧通过数冒充本轮全量回归。
