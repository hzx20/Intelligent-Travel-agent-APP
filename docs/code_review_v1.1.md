# 代码全面体检报告 v1.1

- 日期：2026-09-12
- 范围：backend/app 全部 26 个模块、frontend 核心视图与 API 层、启动网站.bat、requirements.txt、.gitignore
- 方法：逐文件通读 + 关键疑点核实（含读 cachetools 安装源码确认缓存行为），不跑改动，只出结论
- 口径：只讲核实过的事实；每条问题带文件位置、原因、修法

## 总体结论

整体质量在同类项目里是偏上的：分层清楚、权限口径统一、兜底逻辑成体系、17 个测试文件守住关键接缝。没有发现"马上会炸"的安全漏洞或数据风险。真正需要动手的是 2 个功能 bug 和 2 个"换环境就现形"的工程问题，其余属于稳健性和打磨项。

## 🔴 严重（影响功能正确性，建议先修）

### 1. 回看历史方案时，住宿匹配和床型建议用的是"当前对话"的数据

- 位置：frontend/src/views/PlanView.vue `loadPlan()`（约 478 行起）
- 原因：loadPlan 恢复了 messages/result/logs，但没恢复 `collected`。而住宿匹配 `fetchStayMatches()` 用 `collected.destination` 当城市，卡片上方的床型建议 `partyAdvice(collected.party)` 也读它。
- 后果：当前正在聊成都、点开一条杭州历史方案时，杭州的住宿名会拿 `region=成都` 去高德搜（city_limit=true 基本全脱靶），卡片全靠周边补位硬撑；床型建议显示的是成都那场对话的人数口径。数据张冠李戴。
- 修法：loadPlan 里加一行 `collected.value = p.intent || {}`（后端 plan_detail 已返回 intent；backToLive 本来就会恢复实时 collected，闭环完整）。

### 2. requirements.txt 缺三个关键依赖，且全部没锁版本

- 位置：backend/requirements.txt
- 原因：代码实际 import 了 `langgraph`（services/graph.py）、`jwt`/PyJWT（security.py）和 `sqlalchemy`（db/database.py），requirements.txt 里都没有；其余依赖也全部没有版本号。本机能跑是因为当时手动装过。（注：初版报告只写了 langgraph/pyjwt 两个，修复时用 pip 核对后又发现 sqlalchemy，一并补上。）
- 后果：换电脑、重装环境或上云时按清单安装，后端直接 ModuleNotFoundError 起不来；langchain 系升级快，不锁版本还可能出现"昨天能跑今天报错"。
- 修复（已执行）：按 venv 实测版本全部钉死（fastapi==0.141.1、langchain==1.4.0、langgraph==1.2.11、pyjwt==2.13.0、sqlalchemy==2.0.52 等 11 项），这组版本即 59 条回归通过的组合。

## 🟠 中等（稳健性 / 部署隐患）

### 3. 酒店匹配接口最坏可挂约 2 分钟

- 位置：backend/app/routers/hotels.py `_amap_text_poi` + `match_stay_names`
- 原因：每个住宿名最多串行发 3 次高德请求（每次 timeout=10 秒），名字之间还有 0.4 秒固定间隔；6 个名字全部未命中时最坏 ≈ 6×(30+0.4) 秒。接口是同步 def，还占着一个线程池线程。
- 后果：用户侧表现是住宿区一直停在"正在匹配"。
- 修法：给整个匹配加总预算（比如 15 秒），到点就返回已拿到的结果；第三级搜索（原名不限类型）超时缩到 5 秒；前端 fetch 加超时提示。

### 4. 【复核后撤回，不改】SSE 长连接"占用数据库连接池"

- 初判：`plan_stream` 的 `db: Session = Depends(get_db)` 会话要等流结束才关，担心流式期间占着池连接、并发多时耗尽（池 5+10）。
- 复核结论：**这条不成立**。SQLAlchemy 的 Session 是惰性的——创建时不从池里借连接，第一次真正执行 SQL 才借、用完即还。`plan_stream` 在最后落库前从不碰 db，所以几分钟的流式期间它只是个空壳对象，不占任何池连接。落库那一下的借还只有毫秒级。
- 处置：不改代码。若改成"落库时临时开 SessionLocal()"反而会让 conftest 的 get_db 覆盖失效，破坏现有测试隔离。特此记录，避免后人再按错误前提"优化"。

### 5. JWT_SECRET 说好的 ".env 配置" 通道实际不通

- 位置：backend/app/security.py `_jwt_secret`（44-48 行）+ core/config.py
- 原因：security.py 用 `os.getenv("JWT_SECRET")` 读环境变量，但 .env 是被 pydantic-settings 读进 Settings 对象的，不会写回 os.environ。往 .env 里写 JWT_SECRET 完全无效，代码注释里"上云前可在 .env 配置正式密钥"是落空的。
- 后果：现在靠"智谱 key 派生"的兜底密钥能用；将来换环境部署时密钥会悄悄退化，智谱 key 一换，所有用户被强制登出。
- 修法：Settings 里加 `jwt_secret: str = ""` 字段，security.py 改读 `settings.jwt_secret`，注释口径就成立了。

### 6. 对话快照写入没有单条消息的大小限制

- 位置：backend/app/routers/planner.py `StreamIn.messages`、`SnapshotIn.messages`、`update_snapshot`
- 原因：`messages[-60:]` 只限条数，不限单条文本大小；Pydantic 对 list[dict] 里每个 dict 的内容也不校验。
- 后果：异常或恶意客户端可以把超大文本写进 SQLite（chat_json 原样存、原样回传），属于低成本的数据膨胀入口。
- 修法：落库前对每条 text 截断（比如 4000 字），list 长度在 Pydantic 层限到 200。

### 7. "规划过程"日志里城市名永远是空的

- 位置：backend/app/services/graph.py `generate_node`（56 行）
- 原因：取的是 `collected.get('city','')`，但澄清模块收集的字段名是 `destination`（clarify.py 的 REQUIRED、前端 onRegionPick 都用 destination）。
- 后果：前端规划过程里永远显示"行程生成： 2 天框架完成"，城市名缺位。纯展示 bug，一行修复：与 weather_node 对齐改成 `destination or city`。

### 8. 异常细节原文推给前端

- 位置：backend/app/routers/planner.py `plan_stream` 的 except 分支（约 270 行）
- 原因：`{"error": str(e)}` 把内部异常文本（含文件路径、库名）直接送进聊天窗口。
- 后果：本地无碍，上云就是信息泄露面。
- 修法：对外给固定文案"生成失败，请稍后重试"，str(e) 写进后端日志。

## 🟡 轻微（边界 / 性能 / 风格 / 小安全项）

9. **页面卸载不清理**：PlanView 没写 onUnmounted——AMap 实例没 destroy，typeOut 的 setInterval、地图保存的 saveTimer、撤销倒计时、进行中的 streamPlan 请求都没有清理或 AbortController。切页影响小，长时间用是慢性泄漏。
10. **composeReply 依赖组件状态**：里面用了 `spotTotal.value`（当前结果的数量），stubChat 用它给历史方案造对话时，行程还没切换，"没有可核实的坐标"这行提示可能误判。建议改成只依据入参 it 判断。
11. **模型输出的两处健壮性**：`extract_json` 若返回数组/字符串，后续 `it.get` 会崩；`days` 里混进非 dict 元素时 `_ensure_traffic` 会崩。都会以一次"生成失败"收场，加 isinstance 过滤即可。
12. **注册竞态**：先查后插，并发同用户名会触发唯一约束 500；登录注册也没有频率限制（本地无所谓，上云要加）。
13. **LIKE 通配符未转义**（spots/guides/admin 三处搜索）：关键词里的 `%`、`_` 会被当通配符，只影响搜索精度，不是注入——SQLAlchemy 全程参数化。
14. **风格小项**：hotels.py 开头重复 `from typing import Any`；多处 `Field(default=[])` 建议改 `default_factory=list`；hotels 的 `radius` 缺下限（ge=100）；`core/cache.py` 已经没有任何地方引用，属死代码。
15. **封面与图集可能不一致**：guides.py `_save_images` 的封面取自原始列表首项，若首项是空白串，封面为空但图集有图。UI 正常路径触发不了，纯 API 调用才会。
16. **缓存线程安全**：cachetools 官方口径 TTLCache 非线程安全，FastAPI 同步端点跑在线程池里，并发下有小概率竞态；单机低并发无碍，要稳就套 threading.Lock。另：已核实新版 cachetools 的 `in` 判断自带过期检查（读了安装源码），hotels.py 现有 `if key in CACHE` 写法安全，不会取出过期值。
17. **没有 CORS 配置**：开发期靠 vite 代理同源，没问题；将来前后端分开部署必须补 CORSMiddleware，且用白名单，不要用 `*`。
18. **URL 不校验协议**：攻略图片、头像接受任意字符串（建议后端限 https?:// 前缀）；`/api/map/static` 的 size 参数不校验格式（高德会拒，防御性问题）。
19. **启动脚本两个小坑**：后端端口被占时不检测（Errno 10048 会复现，可先 netstat 探测）；固定等 6 秒就开浏览器，前端冷启动慢时首屏会报错，需要手动刷新。
20. **两个直连 LLM 的旧接口裸奔**：`/api/itinerary`、`/api/clarify` 无鉴权无频控，拿到地址就能烧配额。本地无所谓，上云前要么挂登录，要么下线（stream 已覆盖其功能）。

## ✅ 核实过、可以放心的点

- 密码 PBKDF2 十万次迭代 + compare_digest 恒时比较；JWT HS256 带过期，无效 token 统一返回 None。
- 管理员接口整组挂守卫；草稿只有作者/管理员可见；他人记录统一 404 不暴露存在性。
- 服务端 key（POI/天气/静态图）全程不下发；JS key 下发是高德官方设计，注释里讲清楚了。
- 前端全站没有 v-html，气泡用插值、地图弹窗手动 esc() 转义；SQLAlchemy 全参数化，无拼接 SQL。
- `_ensure_traffic`/`_ensure_stays` 幂等兜底、SSE 日志只推增量、65 条确定性回归测试——这些底子是这次能放心改的前提。

## 建议的动手顺序

1. 先修 1、2（一个是一行前端修复，一个是补依赖并锁版本）；
2. 部署前修 5、8；
3. 3、6 看体验反馈再定；轻微项随版本顺手清。

## 修复记录（2026-09-12 当轮执行）

- 已修 7 项：#1（loadPlan 恢复 collected）、#2（requirements 补 sqlalchemy/langgraph/pyjwt 并全量锁版本）、#3（匹配总预算 12 秒 + 三级超时 10/8/5）、#5（jwt_secret 走 Settings，.env 通道打通）、#6（快照清洗：条数 200/单条 4000 字封顶）、#7（generate_node 日志改读 destination）、#8（SSE 异常脱敏，详情进后端日志）。
- 撤回 1 项：#4（复核后确认 Session 惰性连接，不改，理由见上）。
- 后端版本号 0.14.2 → 0.14.3；确定性回归 59 条全绿（1.74s）。
