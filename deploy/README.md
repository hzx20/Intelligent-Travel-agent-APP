# 部署说明（腾讯云轻量 · 2 核 4G · Ubuntu 24.04）

线上地址：**http://134.175.137.18/travel/**

这台服务器同时跑着另一个项目（AI 工单系统，占用 80 端口根路径）。旅行规划 Agent
挂在 `/travel/` 子路径下，两个站点共用一个 nginx 容器，互不影响。

## 架构

```
浏览器
  │  http://134.175.137.18/travel/
  ▼
continew-web（nginx 1.27 容器，80 端口）
  ├─ /travel/             → /usr/share/nginx/html/travel/（静态文件，容器内）
  └─ /travel/api/         → http://172.18.0.1:8000/api/（宿主机上的 FastAPI）
                                   │
                                   ├─ SQLite  /opt/travel/app/backend/data/app.db
                                   ├─ 智谱 GLM-4-Flash（行程生成）
                                   └─ 高德开放平台（POI 核实 / 天气 / 周边酒店）
```

## 目录与进程

| 项 | 位置 |
|---|---|
| 后端代码 | `/opt/travel/app/backend` |
| Python 虚拟环境 | `/opt/travel/venv` |
| 环境变量 | `/opt/travel/app/.env`（600 权限，不入库） |
| 数据库 | `/opt/travel/app/backend/data/app.db` |
| 前端静态文件 | `/opt/continew/web/travel`（容器挂载为 `/usr/share/nginx/html/travel`） |
| 后端服务 | `systemctl status travel`（uvicorn，0.0.0.0:8000） |
| 反向代理 | `continew-web` 容器内的 nginx，配置在宿主机 `/opt/continew/nginx.conf` |

## 首次部署

```bash
# 1. 代码与依赖
sudo mkdir -p /opt/travel/app /opt/travel/web
sudo chown -R $USER:$USER /opt/travel
# 把仓库内容解压到 /opt/travel/app（不含 .git）
python3 -m venv /opt/travel/venv
/opt/travel/venv/bin/pip install -r /opt/travel/app/backend/requirements.txt

# 2. 环境变量（四个钥匙 + JWT 密钥）
cat > /opt/travel/app/.env <<'EOF'
ZHIPU_API_KEY=...
AMAP_API_KEY=...
AMAP_JS_KEY=...
AMAP_JS_SECURITY_CODE=...
JWT_SECRET=...
EOF
chmod 600 /opt/travel/app/.env

# 3. 采集景点数据（5 城 500 条，约 2 分钟）
cd /opt/travel/app/backend
/opt/travel/venv/bin/python scripts/fetch_pois.py --all

# 4. 后端服务
sudo cp deploy/travel.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now travel

# 5. 前端（构建到 /travel/ 子路径）
cd frontend && npm ci && npm run build:travel
rsync -a --delete dist/ /opt/continew/web/travel/

# 6. nginx（只加 location 块，不动原有站点）
#    把 deploy/nginx-travel.conf 里的内容合并进 /opt/continew/nginx.conf 的 server 块
sudo docker exec continew-web nginx -t && sudo docker exec continew-web nginx -s reload
```

## 日常更新

```bash
# 后端：改完代码重启即可（uvicorn 单进程，约 3 秒）
sudo systemctl restart travel

# 前端
cd frontend && npm run build:travel
rsync -a --delete dist/ /opt/continew/web/travel/
```

## 为什么这样部署

- **不新开端口**：云防火墙只放行 80/443，8080 等端口从公网连不上（实测 TCP 能握手，
  HTTP 请求无响应）。所以只能共用 80 端口，用子路径区分。
- **不新起 nginx 容器**：直接复用现有的 `continew-web`，少一个进程、少一份内存。
  代价是配置文件由两个项目共享，改动前必须备份并 `nginx -t`。
- **SQLite 而不是 MySQL**：单机低并发够用，省下约 400MB 内存；这台机器还要跑
  MySQL + Redis + Spring Boot。
- **单 worker**：2 核机器上多 worker 收益有限，且进程内缓存（酒店、地图）不共享。

## 资源控制

| 项 | 值 | 目的 |
|---|---|---|
| systemd `MemoryMax` | 1200M | 防止异常请求把机器内存打满 |
| uvicorn workers | 1 | 与进程内缓存一致，降低内存 |
| nginx `limit_req` | 6 次/分钟/IP（规划接口） | 保护模型与高德配额 |
| nginx `proxy_read_timeout` | 300s | 行程生成最慢约 2 分钟，留余量 |

## 排障

```bash
systemctl status travel -l          # 服务状态
journalctl -u travel -n 100 --no-pager   # 后端日志
sudo docker exec continew-web tail -50 /var/log/nginx/error.log
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1/travel/api/health
```

常见问题：

- `/travel/api/*` 返回 404 且错误日志是 `connect() failed (111)`：后端没起来，或
  uvicorn 只监听了 127.0.0.1（容器通过 `172.18.0.1` 访问宿主机，必须监听 0.0.0.0）。
- 页面白屏但接口正常：前端构建时没带 `--mode travel`，资源路径成了 `/assets/...`。
- 地图空白、提示未配置：`.env` 里的 `AMAP_JS_KEY` 不是「Web端(JS API)」类型，
  或安全密钥不匹配。

## 限制

- 纯 HTTP，没有域名和证书；登录 token 存在 localStorage，公开演示够用，不适合承载真实用户。
- 演示站点开放注册，规划接口按 IP 限流；如果被滥用，先在 nginx 层封 IP 或关闭注册。
- 数据只有 5 个城市 500 条 POI，超出范围的地点会被核实环节替换或标记未核实。
