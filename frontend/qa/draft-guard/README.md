# 完整游客页面的草稿保护验收

在 frontend 目录运行：

```powershell
npm ci
npm run test:e2e:drafts
```

默认使用本机已安装的 Microsoft Edge，启动独立测试进程与全新浏览器上下文，不连接用户浏览器资料、账号或现有标签。测试不自动安装浏览器；没有 Edge 的环境可预先安装 Playwright Chromium，并设置 `TRAVEL_E2E_CHANNEL=chromium` 后运行。开发依赖和版本已记录在 package.json/package-lock.json。

Playwright 自动启动并在结束时关闭 127.0.0.1:5263 的 Vite 服务，运行真实完整前端及 `/travel/` 子路径。该端口若被其他服务占用则失败，不复用未知服务。配置禁用 .env 文件，所有 /api 请求直接返回 503，没有后端代理；测试再阻止非本机来源的网络请求。只创建合成游客旅行，不调用模型、地图或 OCR。

桌面 1280×900 与手机视口 390×844 各七项，共 14 项：

1. 安排编辑的 Esc 取消/确认、重开内容及关闭后的焦点恢复。
2. 当天主题编辑的同一流程。
3. 旅行信息编辑的同一流程。
4. 子组件 AI 需求草稿下的路由离开取消/确认；取消保留、离开后返回清空未保存需求。
5. 正文草稿下的浏览器刷新取消/确认；取消保留正文，确认后只有已保存旅行保留。
6. 保存安排后重载保留内容，没有未保存提示。
7. 关闭测试标签的取消/确认；取消保留输入，确认关闭。

监听实际 confirm/beforeunload 并处理，不替换 window.confirm，也不模拟返回值。[Playwright 文档](https://playwright.dev/docs/dialogs)说明原生确认框需在触发操作期间处理。取消刷新不会发生导航，因此测试调用浏览器实际 location.reload() 后检查保留的 DOM，避免等待不存在的新页面加载；确认刷新仍使用 page.reload()。

失败时保存 trace 和截图；路由取消场景也保存成功截图。输出在 `frontend/playwright-results/drafts`，已加入 Git 忽略，每次复验会重新生成；与账号套件的 accounts 输出分开。

这是 Edge 中的真实浏览器和手机视口测试，不证明实际 iOS/Android、其他浏览器、真实登录/切换账号、云端分享协作或网络异常全部通过。没有修改产品业务逻辑。
