# 智能旅行规划 Agent

一个能"说话就能出路线"的旅游规划网站：输入一句"成都 3 天，带 5 岁孩子，轻松一点"，
AI 追问→生成行程→逐条去高德核实地点真实性→附天气预报，全程流式打字机输出，
最后给你一张按顺序连线的路线图。

首批覆盖 **成都 / 杭州 / 西安 / 北京 / 三亚** 五座城市，景点数据全部来自高德开放平台真实 POI。

---

## 一、怎么启动（最省事的方式）

**双击项目根目录的 `启动网站.bat`** —— 会自动开两个黑窗口（后端 8000 + 前端 5173），
然后自动打开浏览器访问 http://localhost:5173

> 用的时候两个黑窗口要**一直开着**，关掉就等于关网站。

### 手动启动（想自己控制时用）

```bash
# 1) 后端（在 backend 目录）
C:/Users/i/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m uvicorn app.main:app --port 8000

# 2) 前端（在 frontend 目录，另开一个终端）
C:/Users/i/.workbuddy/binaries/node/versions/22.22.2-2/node.exe \
  C:/Users/i/.workbuddy/binaries/node/versions/22.22.2-2/node_modules/npm/bin/npm-cli.js run dev
```

后端启动后可访问：
- 接口文档（可在线调试）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/health

### 第一次使用要做的一件事

**管理员账号需要手动提权**：先在前台注册一个账号，然后在 `backend` 目录执行：

```bash
C:/Users/i/.workbuddy/binaries/python/envs/default/Scripts/python.exe tools/make_admin.py 你的用户名
```

提示"已提升为管理员"后，**重新登录**该账号，导航栏就会出现「管理后台」。
（加 `--revoke` 可以收回管理员权限。）

---

## 二、能做什么

### 游客（不登录）
- 浏览首页（热门景区、猜你喜欢）、景点列表（关键词/城市/门票筛选）、景点详情
- 看所有人发布的旅游攻略
- 用 AI 规划助手出路线（只是不会保存到历史）

### 登录用户（额外）
- 收藏/取消收藏景点、发表评论（1-5 星评分）
- 写攻略、存草稿、编辑、删除（草稿只有自己看得到）
- AI 规划自动存为历史方案，随时切换对比
- 个人中心：改昵称、看我的收藏、看我发布的攻略

### 管理员（额外）
- 六组模块：用户管理（停用/启用、授收管理员）、景点管理（修正采集数据）、
  收藏记录、评论审核（软删）、攻略管理、AI 规划记录

---

## 三、技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | FastAPI + SQLAlchemy 2.0 | 换 MySQL 只改连接串，模型零改动 |
| AI 编排 | LangChain + **LangGraph** | 四节点状态图：澄清 → 生成 → 地图核实 → 天气 |
| 大模型 | 智谱 GLM-4-Flash | OpenAI 兼容端点，改 `base_url` 即可换 DeepSeek |
| 数据库 | SQLite（`backend/data/app.db`，已 gitignore） | 单机够用，预留 MySQL |
| 前端 | Vue 3 + vue-router + Vite | 全站 640px 移动端自适应 |
| 地图/天气 | 高德开放平台 | POI 采集、地点核实、天气、静态地图 |

> **关于 Neo4j**：已列入远景待办，不排版本。触发条件是将来要上"智能推荐引擎"或
> 三层以上关系推理——届时只替换 LangGraph 的检索节点，架构不动。

---

## 四、目录结构

```
智能旅行规划agent/
├─ 启动网站.bat             双击启动（后端 8000 + 前端 5173）
├─ PROJECT_PLAN.md          项目宪法：需求、方案、版本计划（唯一真相源）
├─ README.md                本文件
├─ backend/
│  ├─ app/
│  │  ├─ main.py            应用入口、路由挂载
│  │  ├─ db/                数据库连接 + 8 张表模型 + 老库补列迁移
│  │  ├─ routers/           auth / spots / guides / map / me / admin / planner
│  │  ├─ services/          clarify / itinerary / graph(LangGraph) / map_verify / weather
│  │  └─ security.py        PBKDF2 密码哈希 + JWT
│  ├─ scripts/fetch_pois.py 高德 POI 采集脚本（首批 530 条）
│  ├─ tools/                make_admin.py 提权、smoke_v08.py / walkthrough_v10.py 冒烟走查
│  └─ tests/                pytest（含 v1.0 边界用例）
├─ frontend/src/
│  ├─ views/                Home / Spots / SpotDetail / Plan / Guides×3 / Me / Admin
│  ├─ api/index.js          接口封装 + SSE 流式解析
│  ├─ store/auth.js         登录态
│  └─ utils/pagination.js   折叠式页码算法（纯函数，可单测）
├─ docs/
│  ├─ SESSION_LOG.md        跨会话记忆锚点（add-only，新对话先读它）
│  ├─ PROJECT_BRIEF.md      产品定位定稿
│  └─ prototype/            十页效果图原型
└─ server/ test/            Node 旧版对照实现（保留参考，不参与运行）
```

---

## 五、测试与走查

```bash
# 单元测试（在 backend 目录）
C:/Users/i/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest tests/ -q

# 全链路走查（连真实数据库，不起服务、不走代理，自动清理）
C:/Users/i/.workbuddy/binaries/python/envs/default/Scripts/python.exe tools/walkthrough_v10.py
# 加 --with-ai 会额外真实调一次智谱出 3 天行程（约 40 秒）
```

- `tests/` 覆盖：数据底座、收藏评论、个人中心/后台权限、LangGraph 编排、攻略、静态地图、v1.0 边界
- `tools/walkthrough_v10.py`：游客浏览→注册→收藏→评论→写攻略→静态地图→后台六模块→清理，一条龙 51 项
- 前端构建：`frontend` 目录执行 `vite build`

> 注意：`tests/test_m2.py`（真实调 LLM）和 `tests/test_v07_graph.py` 各需 40～100 秒，
> 全量跑建议后台执行或分文件跑。

---

## 六、环境变量

项目根目录的 `.env`（**已被 gitignore，不会进存档**）：

```
ZHIPU_API_KEY=你的智谱 key
AMAP_API_KEY=你的高德 Web 服务 key
```

---

## 七、常见问题

**Q：管理员进不去后台？**
A：注册后必须跑一次 `tools/make_admin.py 用户名` 提权，然后**重新登录**。

**Q：地图不能拖动缩放？**
A：现在是静态地图（后端代拿图片，key 不外泄）。要可拖动需去高德控制台为 key
开通「Web端(JS API)」平台权限——那和现有的 Web 服务 key 不是同一张，需另行申请。

**Q：数据库在哪、怎么重置？**
A：`backend/data/app.db`。删掉这个文件，下次启动会自动重建空库（景点数据需重新跑
`scripts/fetch_pois.py` 采集）。

**Q：换城市/加城市？**
A：改 `scripts/fetch_pois.py` 里的城市列表重跑采集即可；前端城市下拉在
`views/SpotsView.vue` 和 `views/GuidesView.vue` 的 `CITIES` 常量里。
