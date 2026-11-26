# 真实nginx与生产静态包验收

先frontend执行npm run build:travel。需要官方便携nginx Windows测试二进制，默认项目.tools/nginx-1.30.6/nginx.exe；可用TRAVEL_TEST_NGINX_EXE指定已有同类二进制（当前夹具按Windows目录组织）。.tools已Git忽略，不是发布依赖或系统服务。

backend设置PYTEST_DISABLE_PLUGIN_AUTOLOAD=1和TRAVEL_E2E_PRODUCTION_SMOKE=1，再用项目Python执行 -u -m pytest tests/test_release_runtime.py tests/test_deployment_check.py -q。Edge/Node/Playwright需已安装；TRAVEL_E2E_CHANNEL可选择已安装通道。nginx或构建缺失时runtime测试明确skip，不能把skip计作通过。不开烟测环境变量时仍做nginx/HTTP/后端组合，但不运行桌面手机浏览器。

夹具启动两个真实Uvicorn，共享新临时数据目录且三个开关全开，只替供应商传输；使用deploy/nginx-travel.conf完整http map/limit及location，替换临时监听/root/后端地址并加隔离root站点。执行nginx -t后仅监听127.0.0.1，不注册服务。校验三控制组合、缓存/请求额度/槽位释放、/travel重定向、SPA刷新、全部manifest资源的状态/MIME/缓存、缺失资源404、隐藏清单、API剥前缀和未知API JSON404，root演示站点保持。

Node生产包烟测通过真正静态JS/CSS及nginx代理，桌面1280×900和手机390×844各创建本机旅行、收集/排期、刷新恢复，断言无横向溢出和未处理脚本错误。没有Vite开发服务、个人数据或外部调用。结果/截图在playwright-results/production-bundle，不是实际手机/生产Linux部署证明。

前端候选模块新增真实nginx切换与回退测试：在dist临时副本仅改变入口注释，检查深链读取新入口、控制记录404、回退原入口和资源仍可读取。最终完整模块命令在上述pytest参数增加tests/test_frontend_release.py，共23 passed（发布15、预检6、运行2），内含两视口烟测，不重复加计。本机候选摘要在docs/verification/frontend-candidate-2026-11-16.json，最新归档docs/交接归档-前端发布候选与模块收尾.md。本回合不推进后续，无线上发布。
