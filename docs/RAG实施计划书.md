# RAG 实施计划书：智能旅行规划 Agent

> 本文档是一份独立任务书，接收方无需了解此前的任何对话。
> 版本 v1.0 ｜ 编写日期 2026-11-26 ｜ 目标读者：负责实施的 AI 编程助手
> 项目路径：`C:\Users\i\Desktop\travel\Intelligent-Travel-agent-APP`

---

## 0. 使用说明

把本文档整段发给执行方即可开工。开工前请先完成第 1 节的「必读清单」，不要凭本文档的描述去改代码——本文档给出的行号与结构基于 2026-11-26 的仓库状态，代码可能已变动。

**执行原则**

1. 每个 Day 结束必须有可验证的产出，不通过验收不进下一天。
2. 不修改 `SYSTEM_PROMPT` 的任何一行（理由见第 8 节）。
3. 不引入独立向量数据库服务（理由见第 7 节）。
4. 所有新增代码必须能被现有 `pytest` 回归测试覆盖，且不破坏原有 59 条测试。

---

## 1. 项目现状

### 1.1 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI 0.141.1 + SQLAlchemy 2.0.52 + SQLite |
| 编排 | LangGraph 1.2.11（四节点状态图） |
| LLM | 智谱 GLM（经 `langchain-openai` 兼容接口调用） |
| 前端 | Vue 3 + TypeScript + Vite |
| 部署 | 腾讯云 + Nginx（`/travel/` 子路径，关闭 SSE 缓冲）+ systemd |

依赖清单在 `backend/requirements.txt`，全部版本已锁定，**不要随意升级或降级现有依赖**。

### 1.2 必读清单（开工前按顺序读完）

| 文件 | 为什么必读 |
|---|---|
| `backend/app/services/graph.py`（126 行） | 全篇 4 个节点与条件边，本次要改这里 |
| `backend/app/services/itinerary.py`（640 行，重点看 19-122 行的 SYSTEM_PROMPT 和 592-641 行的 `generate_itinerary`） | 提示词注入点在这一段 |
| `backend/app/db/models.py`（273 行，重点看 67-87 行 `Spot`、140-159 行 `Guide`） | 新表要跟现有 ORM 风格一致 |
| `backend/app/db/database.py` | 连接串与会话工厂 `SessionLocal` |
| `backend/app/core/llm.py`（78 行） | 现有 LLM 调用封装，embedding 调用范式参考这里 |
| `backend/app/services/clarify.py`（69 行） | 澄清输出的 `collected` 字段定义，检索 query 由它构造 |
| `backend/app/services/wikivoyage.py`（147 行） | 已有维基导游抓取模块，Day 3 要复用它的 API 调法 |

### 1.3 数据现状（2026-11-26 实测）

| 表 | 行数 | 可用字段 |
|---|---|---|
| `guides` | **0** | 表结构完整（title/content/city/is_draft），但没有任何数据 |
| `spots` | 500 | name/city/district/address/tags/description/is_free/price/lng/lat/source_url |
| `guide_spots` | 0 | 攻略↔景点多对多关联，暂无数据 |
| `users` | 23 | 有管理员账号 |

**这是本次任务最重要的前提**：攻略表是空的。任何「从已发布攻略中检索」的设计在 Day 3 之前都不成立。Day 1 必须先用 `spots` 的 500 条数据把链路跑通，Day 3 再灌攻略语料。

### 1.4 现有编排链路

`graph.py` 当前是四节点有向图：

```
clarify ──(done=True)──> generate ──> verify ──> weather ──> END
   └──────(done=False)──> END
```

- `PlanState` 是 `TypedDict`，字段有 `history / user_input / prev_collected / collected / question / done / itinerary / verify_logs / weather / logs`
- `need_generate()`（107-109 行）是条件边函数，`done` 为真返回 `"generate"`，否则返回 `END`
- `generate_node()`（50-59 行）调用 `generate_itinerary(state["collected"])`
- `verify_node()` 之后所有节点都对失败做了降级处理，不阻断主流程

### 1.5 澄清输出字段

`clarify_turn()` 返回的 `collected` 可能包含：

```
destination   目的地城市（必需）
days          天数（必需）
dates         具体日期
party         人数构成，如 "2大1小"
budget        预算（元）
pace          节奏：悠闲/紧凑/中等
preferences   特殊偏好原话，如 "室内为主""少走路""每天午餐"
```

检索 query 由 `destination`（做城市硬过滤）+ `preferences` + `party` + `pace`（做语义匹配）拼成。

---

## 2. 目标

### 2.1 一句话目标

用户说「成都 3 天，带 5 岁孩子，少走路」时，系统在生成行程前从站内知识库检索相关片段，注入提示词，并在行程页展示所引用的资料出处。检索失败时照常生成，不阻断主流程。

### 2.2 可验收的成功标准

| # | 标准 | 验证方式 |
|---|---|---|
| 1 | 命令行检索「成都 带娃 少走路」返回 ≥3 条相关片段 | 直接跑 `search()` |
| 2 | 完整规划日志出现「资料检索：匹配 N 条」 | 走一次 `/planner` 接口看 SSE 日志 |
| 3 | 行程内容能看出用了资料（出现攻略里的具体地点或建议） | 人工对比有无 RAG 的两版行程 |
| 4 | 行程页能显示并点开引用的资料出处 | 前端截图 |
| 5 | 20 条自建查询的 Recall@3 ≥ 0.75 | 评测脚本 |
| 6 | `pytest` 全绿，原有 59 条测试不回归 | `cd backend && pytest` |

### 2.3 非目标（明确不做）

- 多轮对话的长期记忆检索（把用户历史偏好做向量召回）
- 引入 rerank 交叉编码器
- 引入 Chroma / Qdrant / Milvus 等独立向量数据库服务
- 改造前端技术栈或整体重构行程页
- 做多模态检索（图片、地图瓦片）

---

## 3. 总体方案

### 3.1 数据流

```
【离线 ingest】
  维基导游正文 / Spot 描述
        │
        ├─ 切块（400 字/块，重叠 80 字）
        ├─ embedding（智谱 embedding-3）
        └─ 写入 knowledge_chunks 表（向量存 BLOB）

【在线检索】
  clarify 得到 collected
        │
        ├─ 拼 query：destination + preferences + party + pace
        ├─ 城市硬过滤（WHERE city = ?）
        ├─ 余弦相似度排序
        ├─ 阈值截断（< 0.35 丢弃）
        └─ 取 Top-5 → references

【生成】
  generate_itinerary(collected, references)
        │
        └─ references 拼进 user message（不动 SYSTEM_PROMPT）
                │
                └─ 行程 JSON 附带 references → 前端展示出处
```

### 3.2 五个改动点总览

| # | 文件 | 动作 | 影响面 |
|---|---|---|---|
| 1 | `app/db/models.py` | 新增 `KnowledgeChunk` 模型 | 只增不改 |
| 2 | `app/services/knowledge.py` | **新建**，切块 / 向量化 / 检索 | 全新文件 |
| 3 | `app/services/graph.py` | 新增 `retrieve_node`，改条件边，`PlanState` 加字段 | 改 3 处 |
| 4 | `app/services/itinerary.py` | `generate_itinerary` 加可选参数 `references` | 改 2 处，向后兼容 |
| 5 | `frontend/`（行程页） | 底部展示「参考站内资料」 | 新增一个组件 |

外加两个脚本（不参与运行时，只用于灌数据）：

- `backend/scripts/ingest_spots.py`
- `backend/scripts/ingest_guides.py`

---

## 4. 详细任务

### Day 1 — 数据层与检索层（不接图）

#### 4.1.1 新增 ORM 模型

在 `app/db/models.py` 末尾追加，风格与现有模型保持一致（`Mapped` / `mapped_column`）：

```python
class KnowledgeChunk(Base):
    """知识库片段：攻略与景点统一切成片段后供语义检索。"""
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_knowledge_chunk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(20), index=True)   # guide / spot
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)       # 同一来源内的第几块
    city: Mapped[str] = mapped_column(String(30), default="", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")        # 小节标题或景点名
    content: Mapped[str] = mapped_column(Text)                         # 片段正文
    tags: Mapped[str] = mapped_column(String(200), default="")         # 逗号分隔
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_url: Mapped[str] = mapped_column(String(500), default="")   # 出处链接
    updated_at: Mapped[datetime] = mapped_column(default=now_local, onupdate=now_local)
```

需要额外 `from sqlalchemy import LargeBinary`。

建表方式：项目若已有迁移机制就沿用；若没有，用 `Base.metadata.create_all(bind=engine)` 在启动时补齐（只建不存在的表，不影响现有表）。

#### 4.1.2 新建 `app/services/knowledge.py`

只暴露三个函数，其余为内部实现：

```python
def chunk_text(text: str, title: str = "", *, size: int = 400, overlap: int = 80) -> list[str]
    """把长文本切成片段。优先按空行分段，段落超长再按句号切；
    相邻片段保留 overlap 个字符的重叠，避免一句话被切断在两块里。"""

async def embed_texts(texts: list[str]) -> list[list[float]]
    """批量调 embedding 接口。单批不超过 16 条，失败重试 2 次，全失败抛异常。"""

def search(query: str, city: str = "", k: int = 5, min_score: float = 0.35) -> list[dict]
    """同步函数（供 asyncio.to_thread 调用）。
    1) 按 city 过滤（city 非空时）
    2) 对 embedding 非空的片段算余弦相似度
    3) 过滤低于 min_score 的
    4) 按分数降序取前 k 条
    返回 [{"source_type","source_id","city","title","content","source_url","score"}]
    任何异常返回空列表，不向上抛。"""
```

**余弦相似度实现**：优先用 `numpy`（`pip install numpy`，约 15MB，无编译依赖）。若不引入 numpy，用纯 Python 的 `sum(a*b) / (sqrt(sum(a²)) * sqrt(sum(b²)))`，5000 条以内可接受（单次约 300ms）。

**embedding 选择**：走智谱 `embedding-3` 接口，复用 `app/core/llm.py` 里已有的 API key 与 base_url，新增一个 `embed()` 函数即可。理由见第 7 节。

#### 4.1.3 新建 `backend/scripts/ingest_spots.py`

把 500 条 `spots` 切成片段入库，用来验证链路。

```
读取所有 spots（city 非空、description 非空）
每条拼成一段文本："{name}｜{city}{district}｜标签：{tags}｜{description}"
若文本长度 < 80 字，整条作为一个片段；否则按 chunk_text 切块
清掉该 source_type='spot' 的旧片段后批量写入
打印：处理 N 条，生成 M 个片段
```

**必须幂等**：重跑不能产生重复。先 `DELETE FROM knowledge_chunks WHERE source_type='spot'`，再插。

#### 4.1.4 Day 1 验收

在 `backend/` 下执行：

```bash
python scripts/ingest_spots.py      # 应输出：处理约 500 条，生成 500-800 个片段
python -c "from app.services.knowledge import search; r=search('成都 带娃 少走路','成都',3); print(len(r)); print(r[0] if r else '')"
```

通过标准：返回 ≥3 条，且内容与「成都、亲子、步行少」语义相关。若返回空或全不相关，先查 embedding 是否写入成功、city 字段是否匹配（注意 `spots.city` 存的是「成都」还是「成都市」）。

---

### Day 2 — 接入编排与生成

#### 4.2.1 `graph.py` 改动（三处）

**第一处**，`PlanState` 增加字段：

```python
references: list[dict]   # 检索到的站内资料
```

**第二处**，新增节点，放在 `generate_node` 之前：

```python
async def retrieve_node(state: PlanState) -> dict:
    """节点 1.5：站内资料检索。失败不阻断主流程。"""
    from app.services import knowledge

    collected = state.get("collected") or {}
    city = collected.get("destination") or collected.get("city", "")
    parts = [collected.get("preferences", ""), collected.get("party", ""), collected.get("pace", "")]
    query = " ".join(p for p in parts if p) or "景点 推荐"
    try:
        refs = await asyncio.to_thread(knowledge.search, query, city, 5)
    except Exception:
        refs = []
    msg = f"资料检索：匹配 {len(refs)} 条站内资料" if refs else "资料检索：本次未匹配到站内资料，按常规生成"
    return {"references": refs, "logs": _log(state, msg)}
```

**第三处**，改条件边并注册节点。原来：

```python
g.add_node("generate", generate_node)
g.add_conditional_edges("clarify", need_generate, {"generate": "generate", END: END})
g.add_edge("generate", "verify")
```

改为：

```python
g.add_node("retrieve", retrieve_node)
g.add_node("generate", generate_node)
g.add_conditional_edges("clarify", need_generate, {"generate": "retrieve", END: END})
g.add_edge("retrieve", "generate")
g.add_edge("generate", "verify")
```

**注意**：`need_generate` 函数体一行都不要改，只改映射表的 value。`END` 分支必须保留——它表示「需求没收集完整，结束本轮等用户补充」。

#### 4.2.2 `generate_node` 传参

```python
async def generate_node(state: PlanState) -> dict:
    itinerary = await generate_itinerary(
        state.get("collected") or {},
        references=state.get("references") or [],
    )
    ...
```

#### 4.2.3 `itinerary.py` 改动（两处）

**第一处**，函数签名：

```python
async def generate_itinerary(collected, references=None):
```

**第二处**，构造 user message 处（原 604-607 行）：

```python
ref_block = ""
if references:
    lines = []
    for i, ref in enumerate(references[:5], 1):
        lines.append(f"[{i}] 《{ref['title']}》{ref['content']}")
    ref_block = (
        "\n【可参考的站内资料】\n" + "\n".join(lines) +
        "\n请在有把握时优先采用上述资料中的地点与路线；"
        "资料未覆盖的部分按常识安排，不要编造具体价格与营业时间。"
    )

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": f"【用户需求】{requirement}{schedule}{ref_block}\n请生成行程 JSON。{correction}"},
]
```

**红线**：`SYSTEM_PROMPT` 一个字都不要动。它是完整的行程 JSON schema，改错一个字段会让整个输出结构校验失败。

**返回值**：`_validated_itinerary()` 返回的 dict 里追加 `references` 字段（只放 id / title / source_url，不放 content，避免前端响应体过大），由 `generate_node` 合并进行程对象。

#### 4.2.4 前端展示

行程页底部增加一行：

```
本行程参考了站内 2 篇资料：[《成都 3 天亲子攻略》] [《成都交通》]
```

文案在无检索结果时不渲染。

#### 4.2.5 Day 2 验收

- 完整跑一次规划（`POST /planner/plan` 或前端发起），SSE 日志里依次出现：`需求澄清` → `资料检索：匹配 N 条` → `行程生成` → `地图核实` → `天气查询`
- 行程内容与不带 RAG 的版本对比，能指出至少 1 处来自检索资料的安排
- `pytest` 全绿

---

### Day 3 — 语料扩充（关键节点）

只有这一步做完，这个功能才叫 RAG；只做 Day 1-2 只是「景点模糊查询」。

#### 4.3.1 新建 `backend/scripts/ingest_guides.py`

复用 `app/services/wikivoyage.py` 里的 MediaWiki API 调法，但改抓正文：

```
接口：https://zh.wikivoyage.org/w/api.php
参数：action=parse & page={城市名} & prop=text & format=json & formatversion=2 & variant=zh-hans
```

流程：

1. 城市列表固定写死 20 个：成都、杭州、西安、厦门、北京、上海、重庆、长沙、青岛、大理、丽江、苏州、南京、广州、深圳、武汉、昆明、桂林、哈尔滨、三亚
2. 每个城市抓正文 HTML → 剥标签取纯文本 → 按 `##` 小节聚合
3. 调 `chunk_text()` 切块，`city` 填城市名，`title` 填小节名，`source_type='guide'`，`source_url` 填 `https://zh.wikivoyage.org/w/index.php?oldid={revid}`
4. 幂等写入（先按 source_type='guide' 清空再插）
5. 打印每个城市抓到的片段数，抓失败的跳过并记录

#### 4.3.2 授权与合规

维基导游正文为 CC BY-SA 4.0 授权。项目里已有 `LICENSE_URL` 常量。要求：

- `source_url` 必须存，前端展示出处时带上链接
- 新增的参考资料展示区，底部加一行「资料来自中文维基导游，CC BY-SA 4.0」

#### 4.3.3 Day 3 验收

```bash
python scripts/ingest_guides.py
```

- 片段总数 > 200
- 中文维基导游的大城市条目内容较厚，成都、西安、杭州、北京应各有 20 个片段以上；小城市可能只有几百字，允许失败，但要打印原因
- 重跑一次，片段总数不变（验证幂等）

---

### Day 4 — 评测与调优

#### 4.4.1 构造评测集

新建 `backend/tests/eval_queries.json`，20 条查询，覆盖三类：

| 类型 | 示例 | 数量 |
|---|---|---|
| 亲子/老人 | 成都带 5 岁孩子少走路 | 6 |
| 同义词 | 带娃、老人、不想爬山、室内为主 | 6 |
| 具体需求 | 杭州 3 天预算 2000、西安下雨天去哪 | 8 |

每条标注「应该命中哪些片段」（用 `knowledge_chunks.id`）。标注方式：人工看检索结果，认定为正确的记下来。

#### 4.4.2 指标计算

```
Recall@3 = 前 3 条里出现任意一条正确片段的查询数 / 总查询数
```

新建 `backend/scripts/eval_retrieval.py` 跑这个指标，输出：

```
总查询 20，命中 16，Recall@3 = 0.80
未命中的查询：xxx, xxx
平均检索耗时：xx ms
```

#### 4.4.3 调优手段（按优先级）

1. **调阈值**：`min_score` 从 0.35 起调，看 0.30 / 0.35 / 0.40 三档的 Recall 变化
2. **调 K**：3 / 5 / 8 三档
3. **混合检索**：向量分 + 关键词分加权。关键词分用 jieba 分词后算命中率，`final = 0.7 * vec + 0.3 * kw`。只有当纯向量 Recall@3 < 0.75 时才做
4. **query 改写**：把 `preferences` 里的口语（带娃、少走路）扩展成书面词（亲子、步行距离短），再送去 embedding。这是最有效的单点优化，优先试

#### 4.4.4 Day 4 验收

- Recall@3 ≥ 0.75
- 检索平均耗时 < 500ms（含 embedding API 调用）
- 把指标数字、阈值、K 值记进项目 README

---

## 5. 数据结构与接口契约

### 5.1 `search()` 返回结构

```json
[
  {
    "source_type": "guide",
    "source_id": 12,
    "city": "成都",
    "title": "交通",
    "content": "地铁 1 号线可直达宽窄巷子……",
    "source_url": "https://zh.wikivoyage.org/w/index.php?oldid=123456",
    "score": 0.72
  }
]
```

### 5.2 行程对象新增字段

```json
{
  "title": "...",
  "days": [...],
  "references": [
    {"source_id": 12, "title": "成都·交通", "source_url": "https://..."}
  ]
}
```

### 5.3 日志文案规范

| 场景 | 日志 |
|---|---|
| 检索到资料 | `资料检索：匹配 5 条站内资料` |
| 未检索到 | `资料检索：本次未匹配到站内资料，按常规生成` |
| 检索异常 | 同上（不暴露异常细节给用户，异常记服务端日志） |

---

## 6. 提示词注入模板（可直接复制）

```
【可参考的站内资料】
[1] 《成都·交通》地铁 1 号线可直达宽窄巷子，景区停车位紧张，建议地铁出行。
[2] 《成都·亲子》人民公园适合带小孩，上午人少；熊猫基地建议 8:30 前到，避开人流。
[3] 《成都·美食》玉林路小馆人均 60 元左右，适合家庭用餐。

请在有把握时优先采用上述资料中的地点与路线；资料未覆盖的部分按常识安排，不要编造具体价格与营业时间。
```

设计要点：

- 编号 + 《标题》便于模型引用，也便于前端做锚点
- 结尾那句「不要编造」必须有。注入了资料以后，模型反而更容易把资料里没有的细节脑补成事实
- 片段控制在 5 条以内。超过 5 条，token 成本上升但召回增益很小

---

## 7. 依赖与部署

### 7.1 唯一新增依赖：numpy

```
numpy==2.3.4
```

理由：算余弦相似度需要向量运算。5000 条 × 1024 维，纯 Python 约 300ms，numpy 约 2ms。体积 15MB，纯 wheel 安装，无编译依赖。

### 7.2 明确不引入的东西

| 方案 | 为什么不选 |
|---|---|
| Chroma / Qdrant / Milvus | 数据量只有几千条，独立服务要占 200-500MB 内存，服务器只有 2G；且多一个进程要写 systemd 单元、多一个健康检查、多一个故障点 |
| `sqlite-vec` 扩展 | 需要在每个环境加载扩展，Windows 开发机与 Linux 服务器的编译产物不同，部署风险高于收益 |
| `sentence-transformers` + torch | torch 约 2GB，装完 2G 内存的机器基本没有余量；Python 3.13 的兼容性也需验证 |
| `fastembed`（ONNX） | 可行备选，约 145MB（onnxruntime + 模型），但需要多花半天验证 Windows / Linux 双端。当前没有离线需求，用不上 |

### 7.3 embedding 走智谱 API

- 复用 `.env` 里已有的 `ZHIPU_API_KEY`，与聊天模型共用同一个 key
- 端点：`https://open.bigmodel.cn/api/paas/v4/embeddings`（与 `llm.py` 里 `ZHIPU_BASE_URL` 同一前缀，智谱此接口兼容 OpenAI 的 embeddings 协议）
- 模型：`embedding-3`
- 实现方式：在 `app/core/llm.py` 新增 `async def embed(texts: list[str]) -> list[list[float]]`，用 httpx 直连即可，不需要额外 SDK
- 成本：500 条景点约 5 万 token，约 3 分钱；单次检索 1 次调用，可忽略
- 延迟：100–300ms，加在生成前，用户可感知但可接受（生成本身要几十秒）
- 失败降级：embedding 调用失败 → `search()` 返回空 → 照常生成，日志提示未匹配到资料
- **注意**：项目有可选的调用配额控制 `app/core/provider_budget.py`（由环境变量 `TRAVEL_PROVIDER_BUDGET` 开启）。若生产环境已开启，批量灌库时的 embedding 调用要分批并加间隔，否则可能触发 `ProviderBusy`

### 7.4 部署注意

- 新增的 `requirements.txt` 条目要同步到服务器
- 数据库文件 `backend/data/app.db` 会增大（已有 500 条 spot 片段，每条向量 4KB，约 2MB；加 300 条攻略片段约 1.2MB）。可接受，但要把 db 加进备份范围
- systemd 服务无需改动（不新增进程）

---

## 8. 红线（违反会破坏现有功能）

| # | 禁止事项 | 后果 |
|---|---|---|
| 1 | 修改 `itinerary.py` 里的 `SYSTEM_PROMPT` | 行程 JSON schema 变更，`_validate_draft` 全部校验失败，规划功能整体不可用 |
| 2 | 删除 `need_generate` 的 `END` 分支 | 用户只说「我想去成都」时会直接生成行程，跳过澄清追问 |
| 3 | 让检索异常向上抛出 | 检索故障会导致整个规划失败，违背现有「天气失败不阻断」的设计原则 |
| 4 | 用 `source_url` 之外的方式引用第三方内容 | 维基导游是 CC BY-SA 授权，不标出处构成侵权 |
| 5 | 把 embedding 存进现有 `guides` / `spots` 表 | 污染业务表结构，后续迁移困难 |
| 6 | 在 `retrieve_node` 里做同步阻塞调用 | LangGraph 节点是 async 的，阻塞会卡住整个 SSE 流 |
| 7 | 改动 `clarify.py` 的字段名 | 前端快捷模块和 `need_generate` 都依赖 `destination`，改了就断 |

---

## 9. 风险与应对

| 风险 | 概率 | 应对 |
|---|---|---|
| 中文维基导游小城市条目太短，片段不够 | 中 | 只保留片段数 > 5 的城市；不足 200 个片段时补抓省级条目（如「四川」） |
| `spots.city` 存的是「成都市」而 `destination` 是「成都」，过滤后为空 | 高 | Day 1 先跑一次 `SELECT DISTINCT city FROM spots` 核对，搜索时做模糊匹配（`LIKE '%成都%'`）或建别名表 |
| embedding API 超时导致规划变慢 | 中 | 设 8 秒超时，失败直接返回空；检索结果做 10 分钟 TTL 缓存（同城市同偏好不重复调） |
| 注入资料后行程 token 超限 | 中 | 5 条 × 400 字上限；`itinerary.py` 原有的 16000 字长度检查要重新测阈值 |
| 前端展示出处时响应体过大 | 低 | references 只传 id/title/url，不传 content |
| 回归测试因新增节点失败 | 中 | 先跑一次 `pytest` 记录基线；`retrieve_node` 对未 mock 的检索调用要能优雅降级 |

---

## 10. 交付物清单

| # | 交付物 | 位置 |
|---|---|---|
| 1 | ORM 模型 | `app/db/models.py` 末尾 |
| 2 | 检索服务 | `app/services/knowledge.py`（新建） |
| 3 | 景点灌库脚本 | `scripts/ingest_spots.py`（新建） |
| 4 | 攻略灌库脚本 | `scripts/ingest_guides.py`（新建） |
| 5 | 评测脚本 | `scripts/eval_retrieval.py`（新建） |
| 6 | 评测集 | `tests/eval_queries.json`（新建） |
| 7 | 编排改动 | `app/services/graph.py`（3 处） |
| 8 | 生成改动 | `app/services/itinerary.py`（2 处） |
| 9 | 前端出处组件 | `frontend/` 行程页 |
| 10 | 指标记录 | `README.md` 新增一节「RAG 检索指标」 |

---

## 11. 一句话提醒

Day 3 之前，这个功能不能叫 RAG。它只是「按城市过滤 + 语义相似度排序的景点查询」。只有灌进真实攻略正文、能引用攻略里的具体建议，才有资格在简历和面试里写 RAG。
