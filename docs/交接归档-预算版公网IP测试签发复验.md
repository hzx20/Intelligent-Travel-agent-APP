# 公网 IP 测试签发复验交接

2026-11-19，Asia/Shanghai。经用户另行授权，在 `134.175.137.18` 仅提交一次新的 Let’s Encrypt 测试订单。签发与证书核验通过。本轮停在测试验收，未申请正式证书、开放 443、重载 nginx 或切换线上服务。

- 复用已完成的 Snap 变更 `2` 和 Certbot 5.10.0；无需再安装，也未重复写入公网探针。固定 ACME 测试目录，未向正式 CA 提交订单。
- Certbot 子命令完成，实际测试证书的唯一 IP SAN 为 `134.175.137.18`。OpenSSL 核对了测试签发者和剩余有效期；Certbot 续期元数据指向测试 CA。退出码为 0，签发与验证阶段约 14.48 秒。测试证书不是公网可用的正式证书。
- 回执确认临时证书、私钥、测试账号状态、挑战文件和本次创建的目录已清理。现役共享 nginx 的宿主与容器配置哈希仍与此前相同。已安装的 Certbot 保留。
- 第一次校验未通过的回执仍原样保留。本次证明修正后的完整流程通过；没有倒推第一次失败的确切原因。

[脱敏机器回执](verification/budget-ip-acme-staging-reverification-2026-11-19.json)保存范围、结果、耗时和两次回执指纹。实际第二次回执 `.release-candidates/budget-https-load-2026-11-19/https-ip-acme-existing-webroot/staging-reverification-attempt.jsonl`，SHA256 `ea526fead5f71b4d39eb3d9ad73035075ae2c15856d36a624f620c7766037bea`。默认 `python -B -s backend/tools/run_acme_staging.py` 仍可本机复核；本次单次执行机会已用完，不要重复 `--reverify-staging`。 [历史第一次尝试](交接归档-预算版公网IP测试签发.md)

下次先读本归档和[独立 HTTPS 代理候选](交接归档-预算版独立HTTPS代理.md)。接下来审阅**正式签发与短周期续期、到期告警**，明确正式密钥存放、权限、独立 nginx 的证书加载与回退，并针对拟执行范围另行授权。正式 443、实际代理私网地址、后端 8000 入站限制、真实账号与真机、正式留存备份和维护切换仍待验收。不要复用此次测试签发授权去签正式证书或打开端口。`production_ready=false`。
