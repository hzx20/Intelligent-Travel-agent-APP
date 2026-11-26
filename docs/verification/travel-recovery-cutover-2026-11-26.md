# 2026-11-26 攻略与照片恢复版上线验收

用户要求尽快上线。北京时间 09:02:38 至 09:02:45 执行切换，成功回执返回时后端已运行、首页已发布、健康接口为 `200`。服务先停止写入，再导出并校验明文 SQLite 快照。通过现有 HTTPS 代理原地发布静态文件，挂载目录 inode 保持一致。登录签名、现有业务数据库及既有运行依赖继续使用。新版包括中文维基导游导入、共享缓存等待修正和足迹照片回执恢复。

| 打包项 | 已核对的版本 |
|---|---|
| 现役 Linux 应用 | `929624036b944be1ce913f871070edcd5993a68beb638b5f41dad237689b3c34` |
| 现役前端 | `591787ffcd689465c2c720bfb07f69b87cb5a9e48cf86c38e1872d4488bd5aba` |
| 前一版 Linux 应用 | `8d2565cb6758665106c6a6193d5f9db46eef5f2859294cd5e4e0740eb6f63169` |

## 原机隔离与正式目录

旧候选专项诊断在原机使用四进程和合成库。511 个冷键成功、128 个热键命中、8 个超额请求被拒且未触发加载、残留租约 0。此前的严格失败未复现。新版 `283d4b3f94b7` 在原机完整验收通过，核对 63 项依赖、119 个文件、50 项静态响应、公开攻略预览和导入、合成旧库升级、两次启动、跨进程及重启天气缓存、同键租约和四进程容量。临时数据与上传包已清理。正式预置 119 个文件，清单 `79b945151434149ff2d414b1d86b0509b7e8a3b1f2a78dbc8cda6ea3f398a7c7` 与隔离包对应，预置期间原后端未重启。

上线后按新版冻结 ORM 清单只读核对现役 SQLite，30 张表、258 个字段、30 个主键、18 个唯一约束、47 个索引和 42 个外键均齐全。本次只查询结构元数据，核对前后均为新版本的同一个进程。[现役数据库结构回执](travel-recovery-live-schema-2026-11-26.json)

```powershell
..\.venv-analysis\Scripts\python.exe -B .release-candidates/supplier-city-2026-11-20/travel-recovery-linux/verify_live_recovery_schema.py --inspect-origin
```

```powershell
$base = '.release-candidates/supplier-city-2026-11-20'
$bundle = "$base/travel-recovery-linux"
$python = '..\.venv-analysis\Scripts\python.exe'
& $python -B "$base/diagnose_cache_capacity_linux.py" --run-reviewed 64dc42e0fbab2588d44600199905ce87f2919504159715852ddb1bb741b94d88
& $python -B "$bundle/run_linux_budget_review.py" --execute
& $python -B "$bundle/stage_travel_recovery.py" --inspect-origin
& $python -B "$bundle/stage_travel_recovery.py" --stage-reviewed-upload c4b51508119b19113a013f7d9f14ec52d998f424cf0be8cf787eac5545729bf2
& $python -B "$bundle/switch_travel_recovery.py" --switch-reviewed b00a10055f52b550631ff121b7dbfbc05306614d6c006b577381ea5f2eda5c2b
```

```text
2026-11-26 09:02:38 +08:00
{"state":"travel_recovery_active","application":"929624036b944be1ce913f871070edcd5993a68beb638b5f41dad237689b3c34","frontend":"591787ffcd689465c2c720bfb07f69b87cb5a9e48cf86c38e1872d4488bd5aba","health_http":200,"backup":"/var/lib/travel-cutover/travel-recovery-release-20261126/previous.sqlite3","backup_sha256":"06f782e4a37b0920adb6962458d05c9edf031e17f32b7af21ca566e1dc101ba2","static_mount_inode_unchanged":true,"current_database_retained":true}
2026-11-26 09:02:45 +08:00
```

## 公网冒烟

| 核对 | 结果 |
|---|---|
| 首页、健康接口、证书 | 均为 `200`。浏览器使用 TLS 1.3，证书可信、IP 匹配，到期为北京时间 11 月 29 日 21:21:39。 |
| 主要页面 | 灵感、天气与航班、景点、攻略、AI 规划、创建旅行返回 `200`，页面加载正常。 |
| 登录和核心表单 | 用合成测试账号登录 `200`、个人中心可打开；创建旅行 `201`，重载仍可读取。 |
| 静态资源及控制台 | 桌面观察到 36 个站内静态响应，未见 `404`；浏览器控制台错误和未捕获异常为 `0`。 |
| 完整公开静态清单 | 通过公网 HTTPS 对首页和全部 48 个公开构建资产逐项读取，49 个文件全部返回 `200`，SHA-256 均与冻结清单一致。共核对 592,682 字节。 |
| 手机触摸视口 | Chrome 390×844 和 320×667 均通过导航、游客排期和刷新保留；两组均无资产失败、横向溢出或控制台错误。 |
| 新攻略入口 | 浏览器直读杭州公开维基导游一次，修订号 `226986`，预览 `200` 并返回 39 条。勾选一条写入本机旅行，重载后安排仍显示固定修订来源。生产写操作为 `0`。 |
| 旁路和现役站点 | 共享站根路径 `200`，旅行 HTTP 页面 `308`，明文 API `403`，公网 8000 被拦截。 |

应用户要求优先上线，09:30 复查了公网入口。HTTPS 首页返回 `200`，`curl` 证书校验结果为 `0`；首页 SHA-256 `c61d2f3e81b0d710d62022a3741abe048a405c66c87b8c7e13068dc4badabf78` 与发布候选一致。HTTP 返回 `308`，跳转到同一 HTTPS 首页。复查后停止新增验收，没有再次执行切换。

北京时间 06:17:56 最近一次计划续期检查及装载任务已复核为 `unchanged`，证书指纹保存、代理和公网一致。该轮无需 nginx 重载，等待后续实际证书更换。发布未执行付费天气、航班或订房查询。照片恢复已在冻结版双视口及回退演练通过，公网冒烟使用合成账号验证基础表单和游客攻略，实体设备与真实业务账号仍待覆盖。

```powershell
$leaf = (Get-Content -Raw -Encoding UTF8 'docs/verification/travel-edge-renewal-2026-11-26.json' | ConvertFrom-Json).saved_selected_and_public_leaf_sha256
& $python -B deploy/check-travel-public-edge.py --mode open --interface-index 18 --expected-leaf $leaf --frontend-manifest "$base/frontend-travel-recovery-20261125/release.json"
node deploy/live-travel-smoke.cjs --live
node "$base/verify-live-mobile.cjs" chrome
node "$bundle/verify_live_public_guide.cjs"
```

[公网入口回执](travel-recovery-public-edge-2026-11-26.json) · [公网静态文件回执](travel-recovery-public-assets-2026-11-26.json) · [桌面回执](travel-recovery-desktop-smoke-2026-11-26.json) · [手机视口回执](travel-recovery-touch-smoke-2026-11-26.jsonl) · [攻略实测回执](travel-recovery-public-guide-2026-11-26.json) · [证书回执](travel-edge-renewal-2026-11-26.json)

### 生产游客复制的响应丢失验收

在公网 Edge 的 390×844 触摸视口中，创建一份含第 2 天安排和一条关联手动预订的本机合成旅行。用一个生产合成账号发起复制，服务器实际返回 `200` 并保存，测试浏览器随后丢弃这次响应。另一个标签修改本机旅行标题，再回到原标签刷新并点击继续。前后两次复制请求正文一致，第二次返回同一账号旅行，账号列表仅有一份。原本机改名仍保留，账号版保留初次提交的标题、第 2 天安排、关联预订和合成订单号。生产增加一个合成账号和一份测试旅行，未调用真实订房接口。[生产回执](travel-live-guest-transfer-2026-11-26.json)

```powershell
node .release-candidates/supplier-city-2026-11-20/travel-recovery-linux/verify_live_guest_transfer.cjs --live
```

## 回退及保留资料

系统仍运行新版，原机只读回执核对了服务工作目录、备份 `root:root 0600`、容器挂载以及写入开放应用编号与快照摘要。回退目录为 `/var/lib/travel-cutover/travel-recovery-release-20261126`，权限 `0700`。其中 `previous.sqlite3` 是停写时点快照，`previous-drop-in.conf` 与两份旧开站脚本用于恢复，`write-opening.json` 明确记录后续可能已经接受新写入。优先回退代码和首页并保留当时的业务库。切换前快照保留用于独立恢复核对。未执行生产回退。[原机读回](travel-recovery-origin-readback-2026-11-26.txt) · [本机回退验收](../交接归档-攻略图片导入恢复.md#最新候选回退实测)

```powershell
# 仅在获准回退且确认当前状态后执行
& $python -B "$bundle/switch_travel_recovery.py" --rollback-reviewed b00a10055f52b550631ff121b7dbfbc05306614d6c006b577381ea5f2eda5c2b
```
