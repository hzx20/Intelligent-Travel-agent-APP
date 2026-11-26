# 公网 IP 测试签发交接

2026-11-19 最新进度：另获授权的单次复验已执行并通过。请以[测试签发复验交接](交接归档-预算版公网IP测试签发复验.md)为准；以下保留第一次回执和复验准备，执行入口已用过，不再运行。

2026-11-19，Asia/Shanghai。经授权，公网验证通过，原机已安装官方 Certbot 5.10.0。仅执行一次 Let’s Encrypt 测试签发，流程已进入签发并回收临时状态，但签发后校验未通过，**不记为测试签发验收通过**。未发布证书、开放 443、重载 nginx 或切换线上服务。

- 公网验证：从原有 HTTP 入口读回了唯一临时文件的完整正文。原 SSH 过程未确认清理，随后以只读复查确认同一文件、两级临时目录均已消失；共享 nginx 宿主和容器内哈希与原版一致。未重复创建探针。修正本机 SSH 控制器的行尾处理，保留原回执及独立清理证据。
- 安装：第一次等待 Snap 安装超时，snapd 中同一变更 2 继续执行。跟踪至实际 `Done` 后确认 Certbot 5.10.0；未重复安装。软件留在原机。
- 签发：复用变更 2，执行一次固定测试 CA 的 `certonly`。原回执显示 `request_staging_only`、公开目录清理通过，随后出现 `RuntimeError`；该版没有区分证书解析、剩余期限和续期配置等具体检查点，已无法从已回收的文件确认确切原因。未再向 CA 提交订单。独立只读核对确认测试状态与挑战目录均不存在，未生成全局 `/etc/letsencrypt` 状态，共享 nginx 配置哈希未变。
- 排查发现旧解析器会误拒绝 OpenSSL 显示为 `critical` 的 SAN 标题，这是一种可能的原因，并未证明是本次线上回执的确切原因。已支持两种合法标题，仍要求只有一个与目标一致的 IP SAN；新增 Certbot 子命令完成事件及后续分项校验。用 7 张本机合成证书验证相关正反例，另 21 项控制器回归通过。本机证据不替代真实测试签发验收。

[脱敏机器回执](verification/budget-ip-acme-staging-2026-11-19.json)绑定原始探针、安装超时及签发回执的哈希。原始文件在 `.release-candidates/budget-https-load-2026-11-19/https-ip-acme-existing-webroot/`；[前置方案](交接归档-预算版公网IP证书预检.md)是历史记录，其中两个执行入口均已用过，不再运行。需要复核时可先在 `backend` 跑 `python -B -m pytest tests/test_acme_probe.py tests/test_acme_staging.py -q`；默认的 `python -B -s backend/tools/run_acme_staging.py` 仅检查本机文件。

本轮停在一次测试签发后、真实证书验收不足。若继续，用已装的 Certbot，不再安装、不重复写原探针；任何新订单均需新的明确授权及新的单次回执，不能覆盖原始尝试。正式证书、自动续期、独立 443、后端入站、真实设备、正式留存备份及切换仍待后续。`production_ready=false`。

## 新的一次复验（仅准备，未执行）

`python -B -s backend/tools/run_acme_staging.py` 默认不联网。它已核对公网验证、第一次测试订单回执的固定哈希、独立清理结果，以及尚不存在复验尝试记录。只有收到明确的新授权后，才运行下方命令；复验不会重装 Certbot 或重写公网探针，仍会使用原 HTTP 验证路径提交这一次新的测试订单。它会核对当前主机、仅复用已验证完成的安装变更 `2`、始终使用测试 CA，并单独保存 `staging-reverification-attempt.jsonl`。新回执分别记录 Certbot 返回、公开文件清理、证书 IP 与测试签发者、续期配置和现役共享配置的核对。若无完整成功回执则停止，不重试、不提交正式订单。

```text
python -B -s backend/tools/run_acme_staging.py --execute --reverify-staging --expected-sha256 b63e9dbcf9b2c2ad9c09b848d1067d3664ecb82cc7fbed36ea910c19fd42655d
```

22 项本机控制器回归通过。测试证明缺少原回执或清理证据时不会连接，重用已安装版本后不会覆盖现有回执，同一复验入口有记录后不能再执行。本轮只是本机准备，未向 CA 提交任何新订单。[复验准备回执](verification/budget-ip-acme-staging-reverification-preflight-2026-11-19.json)
