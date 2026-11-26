# 交接归档：RAG 检索层上线（2026-11-26）

## 结论

站内资料检索（RAG）已随新版本发布到 `https://134.175.137.18/travel/`。线上后端为应用包 `486ac4f73e495ab1b0a446023be6bcadbc7e61722206da322d0dc70f75b2cc6c`，前端为 `100244128c8be4e326a72962cbf7e31cd0ec74aaa8a360a6e91066e66212c888`。健康检查 `{"status":"ok"}`，服务器本地检索「成都 带娃 少走路」返回 3 条、472ms。

## 版本与提交

| 项 | 值 |
|---|---|
| RAG 功能提交 | `6a660a9 feat(rag): 站内资料检索接入行程生成` |
| 发布工具补全提交 | `43bda9e chore(release): 补齐发布工具、部署配置与验收记录` |
| GitHub main | `43bda9ee023366a0b8c70d2d58ae723675412121`（与本地一致） |
| 后端应用包 | `486ac4f7…75b2cc6c`，120 个文件，`prepared_python 3.12.3` |
| 前端包 | `10024412…6212c888`，发布状态 `committed / matches_published: true` |

## 线上变更

1. **代码**：新应用包放在 `/opt/travel/releases/486ac4f7…/`（root:root，目录 0755、文件 0644），通过 `release_candidate.py verify` 逐文件校验。
2. **运行环境**：沿用 `/opt/travel/runtime-envs/28ba4881…/`，只补装 `numpy==2.5.3`（腾讯云 PyPI 镜像，16.7MB），`pip check` 无冲突。旧代码不 import numpy，回退不受影响。
3. **systemd**：只改 drop-in `/etc/systemd/system/travel.service.d/55-weather-cache-release.conf` 的 `WorkingDirectory` 指向新包，保留 `TRAVEL_SHARED_CACHE=1`；主 unit 与 `ExecStart` 未动。
4. **语料**：服务器访问 `zh.wikivoyage.org` 会超时，不能直接跑 `ingest_guides.py`。改为本机导出的 `knowledge-2026-11-26.jsonl`（1708 片段，10.1MB，sha256 `02189c49…9cf9f5b`），在停写窗口用 `scripts/import_knowledge.py` 导入；导入前 `--dry-run` 校验条数与 1024 维。
5. **前端**：`npm run build:travel` 构建后上传，`frontend_release.py prepare → verify → publish`，发布后 `status` 为 committed。

## 上线前备份

| 文件 | 说明 |
|---|---|
| `/opt/travel/backup/2026-11-26-rag/app-before-rag.db` | 停写前 SQLite 快照，638,976 字节，完整性与外键检查通过 |
| `…/55-weather-cache-release.conf.bak` | 原 drop-in（指向 929624…） |
| `…/travel.service.bak` | 原主 unit |

数据库新增的 `knowledge_chunks` 表与 1708 条记录不影响旧版读取；回退代码时无需回退数据库。

## 回退步骤

```bash
# 1. 后端回退到上一版（929624…）
sudo cp /opt/travel/backup/2026-11-26-rag/55-weather-cache-release.conf.bak \
  /etc/systemd/system/travel.service.d/55-weather-cache-release.conf
sudo systemctl daemon-reload && sudo systemctl restart travel
curl -ksS https://134.175.137.18/travel/api/health

# 2. 前端回退（保留当前资源，只还原入口与清单）
PY=/opt/travel/runtime-envs/28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281/bin/python
cd /opt/travel/releases/486ac4f7…/
$PY backend/tools/frontend_release.py rollback \
  --target /opt/continew/web/travel \
  --release-id 100244128c8be4e326a72962cbf7e31cd0ec74aaa8a360a6e91066e66212c888
```

回退代码后，检索节点不存在，规划回到四节点；`knowledge_chunks` 留在库里无害。不要用 `app-before-rag.db` 覆盖当前库，除非确认 21:50 之后没有新写入。

## 验证记录

- 首页 `200`，新 `PlanView-C83LK-1b.js` 可访问且包含「参考了站内」文案。
- `/travel/api/health` 返回 `{"status":"ok"}`。
- 服务器进程内检索：3 条结果，Top1 成都动物园，472ms（含首次 TLS 与 numpy 冷启动）。
- 线上完整规划：见同目录 `verification/rag-production-2026-11-26.json`。

## 已知限制

- 服务器无法访问维基导游，语料更新必须走「本机灌库 → export → 上传 → import」。
- `numpy` 装在共享运行环境里，未按应用包单独建 venv；后续如换 Python 版本需重新建环境。
- 应用包只收录 `backend/app` 与固定工具，灌库/导入脚本不在包内，需从源码目录运行。
- 本次未改 nginx、TLS、入站规则与数据库结构（只新增表）。
