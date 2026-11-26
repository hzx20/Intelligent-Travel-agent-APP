# 隔离照片上传页面

在 frontend 目录执行：

```powershell
npm run dev -- --config qa/photo-upload/vite.config.mjs
```

打开 `http://127.0.0.1:5262/qa/photo-upload/index.html`，仅选择本目录生成的 `sample.png`（32×32 纯色，117 字节，无个人信息）。本次用户已明确授权该图片上传至这一隔离地址；不得据此上传其他个人文件或改用外部服务。

页面使用真实 TripFootprints/FootprintPhoto 前端组件，接口是 Vite 开发中间件模拟，只监听本机。照片只保存在 Node 进程内存，刷新浏览器后仍显示，重启服务后消失；不连接实际账号、业务数据库、高德或 OCR。停止服务即可清理内存中的图片。

已通过浏览器文件选择、上传、显示及重新加载验收。该页面不证明真实后端重编码、私有权限或数据库持久化；这些需要接口测试和实际后端端到端验收。测试使用 PNG，模拟服务直接返回 PNG，不能把它当作真实 JPEG 处理结果。

这是人工验收夹具，不属于生产入口。

## 真实接口模式

先在 backend 目录使用项目 Python 环境启动 `python tools/qa_photo_server.py`，再在 frontend 执行：

```powershell
npm run dev -- --config qa/photo-upload/vite.config.mjs --mode real-backend
```

仍访问同一个本机 5262 页面，仅上传已授权的 sample.png。请求经本机代理到 18962，调用实际足迹照片路由；服务不导入 app.main，不调用生产初始化、不读取业务数据库。账号、登录签名和数据库均为临时合成，未配置外部服务调用。`/api/qa-status` 只返回测试照片格式、尺寸、EXIF 数量和行程版本，不返回图片或令牌。

本模式已验证浏览器选图、真实接口上传、鉴权读取显示、重新加载，以及数据库中 JPEG/32×32/无 EXIF 的结果。原始测试图不含 EXIF，不能单靠本次图片证明移除已有元数据；已有服务端接口测试另覆盖含 EXIF 的输入。未覆盖生产账号、磁盘数据库重启后读取或全部移动浏览器。

两个服务只监听 127.0.0.1，含自动生成测试身份的 bootstrap 接口，不得对外提供。测试结束停止两个进程，内存中的图片、记录和签名密钥随之销毁。
