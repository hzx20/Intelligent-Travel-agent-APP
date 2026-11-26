# 和风天气与飞常准接入记录（2026-11-17）

2026-11-25 补充。新版已上线。和风天气和飞常准配置同步并重启后，公网健康接口返回 `200`。用合成账号在公网 Edge 触摸视口各实查一次杭州天气、12 月 2 日上海至北京直飞和票价，三项均返回 `200`。天气显示七天预报和来源。两项航班结果均为文本，页面显示航线、日期和查询类型，退出后清除结果。[生产同步回执](verification/travel-plain-cutover-2026-11-25.md#上线后天气与航班配置同步) · [生产查询回执](verification/travel-plain-cutover-2026-11-25.md#上线后真实天气与航班查询)

2026-11-20 补充。天气与航班页新增常用中文城市联想、城市码预览以及实际查询日期和航线。经授权实查一次直飞、一次票价，页面已支持保留返回文本的换行。新版 `/travel/` 构建已通过本机和原机 Linux 隔离验收。[本轮验收回执](verification/travel-flight-city-input-2026-11-20.json)

2026-11-17 的接入范围为两个供应商的本地代码，现有业务服务继续运行。

## 已完成

- 导航新增“天气与航班”，地址 `/travel-data`；部署在 `/travel/` 时为 `/travel/travel-data`。查询需要登录，页面适配桌面与手机。
- 和风天气：城市解析 GeoAPI + 最新 `/weather/v1/daily/{latitude}/{longitude}` 每日预报；返回未来 7 天天气，采用当地日期，显示来源声明。行程生成也使用同一适配器，行程页保留原有前三天摘要。
- 飞常准：直连官方 Aviation stateless JSON MCP，固定允许按航班号、出发到达城市、城市间票价三类查询。每次点击最多调用一次，不自动重试或自动分页；直飞列表最多返回前 20 条摘要。页面展示供应商查询内容，价格不是下单保证。
- key 仅保存在后端配置，通过请求标头发送；前端、配置状态 API、错误提示不返回 key。和风专属域名限制为 `*.qweatherapi.com`，拒绝重定向和任意地址。HTTP 响应限制为 1 MiB。
- 天气成功结果按城市缓存 15 分钟，最多 256 项。显式查询每账号每进程每分钟最多 6 次（天气和航班分开计数）。现有可选共享供应商预算新增 `qweather`、`variflight` 两组；未启用共享预算时此模块不提供跨进程总费用上限。
- 生产已启用和风天气共享缓存，天气页和行程生成共用。缓存按专属主机、密钥摘要和城市隔离，命中不延长 15 分钟有效期。Linux 隔离环境已验证跨进程和重启复用，生产保持单 worker。[共享缓存发布与验收](交接归档-路线与酒店共享缓存.md#2026-11-25-生产预置切换与回退)
- 尚未配置 key 时原行程天气继续使用高德；已经配置和风但调用失败时，保留失败状态，不把其他来源冒充和风数据。

## 本机配置与使用

根目录 `.env` 已由用户填写两个 key。本次仅写入用户提供的非秘密 API Host；未在归档中复制 key。

```dotenv
QWEATHER_API_HOST=kb67ch6pa7.re.qweatherapi.com
QWEATHER_API_KEY=（用户本机保管）
VARIFLIGHT_API_KEY=（用户本机保管）
```

可复用空白模板为 `deploy/travel-data.env.example`；把字段合并进现有 `.env` 或 `TRAVEL_ENV_FILE`，不要覆盖原配置。配置变更需要重启后端进程才能载入。前端重新构建后可从导航进入“天气与航班”。供应商 key 不得使用 `VITE_` 前缀。

接口全部需要 `Authorization: Bearer <登录 token>`：

| 接口 | 用途 |
| --- | --- |
| `GET /api/travel-data/status` | 仅显示本机字段配置是否完整，不代表余额或实时权限验证 |
| `GET /api/travel-data/weather?city=杭州` | 和风城市天气 |
| `POST /api/travel-data/flights/query` | 飞常准显式查询 |

航班请求示例（日期由用户选择）：

```json
{"kind":"number","date":"2026-11-17","flight_number":"MU2157"}
```

```json
{"kind":"route","date":"2026-11-17","origin":"SHA","destination":"BJS"}
```

`kind=prices` 查询票价，城市代码与 `route` 相同。两类查询的参数为 IATA 城市三字码。页面支持 13 个常用中文城市名，也可手填其他城市的三字代码。页面先显示解析出的出发和到达城市，点击提交时只发送一次查询。结果保留当次航线、日期和查询类型。飞常准数据工具消耗供应商额度，权限和余额由供应商控制。[飞常准 Aviation MCP](https://ai.variflight.com/zh/docs/aviation) · [Sabre 城市码参考](https://www.asianasabre.co.kr/CommonFiles/BoardUpLoadFiles/BookAndEduPds/1111/%28%EC%A0%95%EA%B7%9C%29%20%EA%B5%AD%EC%A0%9C%EC%84%A0%20%ED%95%AD%EA%B3%B5%EC%98%88%EC%95%BD%282022%29-%EC%B5%9C%EC%A2%85.pdf)

可选共享预算沿用 `TRAVEL_PROVIDER_BUDGET=1`，新组默认和风并发 2、启动间隔 0.5 秒，飞常准并发 1、启动间隔 1 秒。覆盖项为 `TRAVEL_QWEATHER_CONCURRENCY/INTERVAL` 和 `TRAVEL_VARIFLIGHT_CONCURRENCY/INTERVAL`。

## 验收证据

- 后端：`python -m pytest backend/tests/test_travel_suppliers.py -q`，**38 passed**。覆盖真实本机 HTTP 路由的鉴权、输入校验和返回；供应商网络层使用合成 key 与 MockTransport，业务数据库为测试临时库。
- 前端会话保护：在 `frontend` 执行 `node --test test/sessionToken.test.js`，**7 passed**。
- 前端构建：`npm run build -- --outDir dist-suppliers-check` 通过，未覆盖原构建目录。
- 浏览器：`npx playwright test --config playwright.suppliers.config.mjs`，桌面/手机共 **6 passed**。仅本机 `127.0.0.1:5287`，拦截 API 为合成数据并阻断外站；覆盖三类航班、天气归因、无自动查询、未配置提示、失败展示、退出清除结果、子路径和页面宽度。
- 真实供应商：`python backend/tools/check_travel_suppliers.py` 通过。和风杭州天气返回 7 天、来源声明完整；飞常准 **仅调用免费 `tools/list`**，三类工具及参数均存在。未调用收费航班查询，未保存实时业务结果。
- 沙箱会禁止本机套接字：最初 TestClient/浏览器运行因此阻塞；允许在沙箱外执行仅本机测试后，完整验收通过。常规纯服务测试可在沙箱内运行。
- 浏览器截图位于 `frontend/playwright-results/suppliers/`，为合成数据。摘要报告另见 `docs/verification/travel-data-suppliers-2026-11-17.json`。

## 变更位置与恢复

新增后端 `services/qweather.py`、`services/variflight.py`、`services/supplier_http.py`、`routers/travel_data.py`，注册于 `main.py`；配置字段位于 `core/config.py`，共享预算分组位于 `core/provider_budget.py`。旧 `services/weather.py` 增加和风分派。

新增前端 `views/TravelDataView.vue`、`components/WeatherAttribution.vue`；路由与导航各添加一项，行程天气增加来源声明。API 封装新增默认关闭的 `readOnly` 选项，避免航班只读 POST 失败时误提示“核对已保存内容”。原有写入保护保持默认行为。

后续恢复先阅读本文，再运行上述测试。工作树包含主会话的其他大量未提交改动，不能整文件回退或批量清理。此次没有更改酒店渠道、业务模型或业务库。

- 2026-11-20 经用户授权，用现有适配器各提交一次 2026-11-24 上海至北京直飞和票价查询。两个响应均为 HTTP 200，适配器已接收，格式均为文本且 `structured` 为空。按官方当时的查询定价，两次最多 75 积分；验收未读取扣费账单。回执只留状态和响应结构，无原文、价格或订单。[飞常准 Aviation MCP](https://ai.variflight.com/zh/docs/aviation)
- 更新页面按正文展示文本，保留换行并作为普通文本转义。可解析的 JSON 仍按缩进展示。2026-11-20 浏览器用合成文本和 JSON 验收，覆盖两个视口和 `/travel/` 子路径。酒店真实房态、订房和支付仍待具备交易能力的供应商。[本轮验收回执](verification/travel-flight-city-input-2026-11-20.json)
- 重新构建的前端与 49 项静态产物哈希一致，统一应用共 117 项。原机 Linux 隔离验收通过合成旧库升级、两次启动、49 个 `/travel/` 回环响应及合成航班接口。新版已绑定 197 项私有发布计划和专用 HTTPS 组合候选，独立复查通过临时数据清理及现役服务健康状态。[Linux 与 HTTPS 绑定交接](交接归档-航班新版Linux与HTTPS绑定.md)
- 使用同一冻结包在本机完成 Edge 桌面和手机视口全链路验收。合成账号通过页面登录后，每个视口各显式提交天气、直飞和票价三项查询。实际后端验证了匿名拒绝，供应商适配器验证了城市和日期参数，退出后页面清除结果。供应商传输为合成响应，临时服务和 SQLite 已清理。[冻结版浏览器回执](verification/travel-frozen-supplier-browser-2026-11-20.json)

## 官方依据

- [和风 API Host](https://dev.qweather.com/docs/configuration/api-host/)
- [和风 API key 标头认证](https://dev.qweather.com/docs/configuration/authentication/)
- [和风每日预报](https://dev.qweather.com/docs/api/weather/weather-daily-forecast/)
- [和风城市查询](https://dev.qweather.com/docs/api/geoapi/city-lookup/)
- [飞常准官方 Aviation MCP 文档、参数及计费](https://ai.variflight.com/docs/aviation)
