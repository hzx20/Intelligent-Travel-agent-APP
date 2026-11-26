# 酒店渠道库存同步演示

这个模块模拟外部渠道向旅行平台推送酒店房态，演示 HTTP 接口、数据校验、SQL 持久化和同步记录。数据完全由调用方提供：它不是海外渠道的真实接入，也不提供实时房价、预订、支付或扣库存。现有高德酒店搜索接口仍用于展示地图地点，两者的数据来源不同。

## 准备

按项目 README 启动后端，访问 `http://localhost:8000/docs` 可查看交互式接口文档。注册用户后，在 `backend` 目录运行 `..\.venv\Scripts\python tools/make_admin.py 用户名` 提升管理员，再重新登录获取 token。接口需要 `Authorization: Bearer <token>`，普通用户无权调用。

下面的示例在 PowerShell 中执行，使用 `curl.exe` 避免 PowerShell 把 `curl` 解释为其他命令。先在 `/docs` 的登录接口获取管理员 token，并替换下面的值。不要将真实 token 提交到仓库。

```powershell
$token = '替换为管理员登录返回的 token'
$base = 'http://localhost:8000/api/admin/channel-inventory'
$authHeader = "Authorization: Bearer $token"
```

## 推送模拟数据

`request_id` 是调用方为一次推送生成的唯一标识；重复发送同一标识和同一内容可安全重试。不同内容必须使用新的标识；重复标识对应不同内容会被拒绝。下面的数据只是示例，日期和价格均不代表真实酒店报价。`price_minor` 是货币的最小单位，例如 USD 的 12900 表示 129.00 美元；库存 `available` 为房间数量。

```powershell
$payload = @{
  request_id = 'demo-sync-001'
  items = @(
    @{
      hotel_id = 'hotel-demo-1'; hotel_name = 'Demo Hotel'
      room_id = 'room-standard'; room_name = 'Standard Room'
      date = '2027-01-15'; available = 5
      price_minor = 12900; currency = 'USD'
    },
    @{
      hotel_id = 'hotel-demo-1'; hotel_name = 'Demo Hotel'
      room_id = 'room-standard'; room_name = 'Standard Room'
      date = '2027-01-16'; available = 3
      price_minor = 13500; currency = 'USD'
    }
  )
} | ConvertTo-Json -Depth 5
$payloadPath = Join-Path $env:TEMP 'travel-channel-sync.json'
[System.IO.File]::WriteAllText($payloadPath, $payload, [System.Text.UTF8Encoding]::new($false))
curl.exe -sS -X POST "$base/sync" -H $authHeader -H 'Content-Type: application/json' --data-binary "@$payloadPath"
```

## 查询与验证

```powershell
curl.exe -sS "$base/inventory?hotel_id=hotel-demo-1&stay_date=2027-01-15" -H $authHeader
curl.exe -sS "$base/sync-runs?limit=50&offset=0" -H $authHeader
# 重发同一个请求，检查不会重复生成库存
curl.exe -sS -X POST "$base/sync" -H $authHeader -H 'Content-Type: application/json' --data-binary "@$payloadPath"
```

按日期查询可检查对应房态、价格和币种；去掉 `stay_date` 可查看两天的数据。同步记录中包含请求标识、状态和尝试次数。再次发送同一成功请求会返回 `replayed: true`，库存不应增加重复行。修改房态后使用新的 `request_id` 推送，再查询可验证更新；使用原 `request_id` 推送不同内容会返回 409。无 token 和普通用户分别应无法访问管理接口。格式错误、负库存或负价格等无效数据应被拒绝，不能作为有效房态入库。

模拟渠道固定为 `demo_overseas`。每批 1–100 条，同一批内酒店 ID、房型 ID 和日期的组合不能重复；数量和价格为非负整数，币种为三位大写字母。库存以渠道、酒店、房型和日期为键更新。列表支持 `limit` 和 `offset`；库存还支持 `hotel_id` 与 `stay_date` 过滤。同步失败时库存事务回滚，服务尽力记录 `failed` 状态，可使用相同请求重试；并发写入冲突可能返回 409，也可使用相同请求重试。成功重放不再次执行同步。

接口字段、筛选条件和具体响应以 `/docs` 为准。该演示不连接真实供应商，无法保证生产环境的渠道授权、库存实时性、汇率、税费或预订一致性。测试使用项目后端的 pytest；项目 README 列出了离线回归命令。
