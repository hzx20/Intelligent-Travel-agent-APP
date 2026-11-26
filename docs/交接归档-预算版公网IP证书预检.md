# 公网 IP 证书预检交接

2026-11-19 最新进度详见[公网 IP 测试签发交接](交接归档-预算版公网IP测试签发.md)：探针与安装已确认，一次测试签发后校验未通过。以下待执行命令仅作历史参考，不重新运行。

2026-11-19 授权执行记录：原机的一次公网随机文件正文完全匹配。控制器未能从同一 SSH 会话确认清理，保留原回执；随后只读复查，确认该文件及本次两级临时目录均不存在，宿主和容器的共享配置哈希仍与原版相同。无需重复写文件。测试签发控制器只会在原始正文结果和同名、同回执哈希的独立清理证据齐全时接受这种情况。以下的文件探针命令为执行前记录，不要再次运行。

2026-11-19，Asia/Shanghai。用户的腾讯云截图确认了同一台广州轻量应用服务器及公网 IPv4 `134.175.137.18`。本轮按 `https://134.175.137.18/travel/` 准备证书测试方案。截图用于确认机器；本轮只读取现场元数据，未签发证书、未安装软件或改变线上配置。

- 宿主 `/opt/continew/web` 已只读挂入 `continew-web` 的 `/usr/share/nginx/html`。宿主与容器的现役配置字节哈希一致，同上一次审阅。当前 `.well-known` 尚不存在，主机只监听 80，未发现 Certbot，已安装 snap 命令。
- 现役 HTTP `location /` 已先尝试读取实际文件，再回退到共享站点入口。本机 nginx 用同一规则验证：存在的挑战文件可以读回预期内容，缺失文件却可能返回共享站点的 200。因此，在原机先通过一次临时公开随机测试文件比对**完整正文**，不能只看状态码。如果正文匹配，测试签发无需重载 nginx；否则停止并审阅带明确挑战路径的候选。已准备复用现有挂载的挑战候选，不需要为了它给共享容器新增挂载。
- Let’s Encrypt 的 IP 证书使用短有效期配置；官方给出的 Certbot **5.4 或以上**支持 IP webroot，验证使用公网 80，不需要先开 443。[Certbot IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot) · [HTTP-01 验证说明](https://letsencrypt.org/docs/challenge-types/)。腾讯云的 443 入站在以后独立代理就绪时再核对；不能从当前缺少监听推断云侧规则状态。[腾讯云实例防火墙](https://cloud.tencent.com/document/product/1207/44577/)
- 已从原机直接通过 HTTPS GET 核实两个外部前提：官方 Snap 商店 API 的 `latest/stable amd64` 当时为 Certbot 5.10.0（修订 5940），且 [Let’s Encrypt 测试目录](https://letsencrypt.org/docs/staging-environment/)返回有效的注册、订单和 nonce 端点。原机预期 Certbot 路径及全局签发状态路径均未出现。此检查未访问账户/订单写入口、未下载或安装软件；结果不代表公开挑战可读、实际安装或签发已通过。

## 已准备的下一步（待授权）

临时公开文件的控制器 `backend/tools/run_acme_probe.py` 已通过 9 项本机测试。默认运行 `python -B -s backend/tools/run_acme_probe.py` 只编译并核对脚本，不连接服务器。收到本次授权后，才可在仓库根运行 `python -B -s backend/tools/run_acme_probe.py --execute --expected-sha256 fb7c503949f39f90d64d2b4faf2693c96b87f481ad09a7a678805b2d4335db02`。该控制器仅执行下方第 1 步：在同一 SSH 会话中创建和清理测试文件，核对正文与清理结果全通过才给出成功回执。若 SSH 中断，原机等待超时后清理；未取得清理回执则先核对确切的文件名，不重复执行。该控制器不会安装任何软件或联系证书机构。

测试签发的控制器 `backend/tools/run_acme_staging.py` 已加入仓库。它独立核对第 1 步的成功和完整清理回执，无回执时在连接服务器前拒绝；默认模式只做本机审阅。收到安装与测试签发授权、且第 1 步通过后，执行 `python -B -s backend/tools/run_acme_staging.py --execute --expected-sha256 41ab3687db6a11fb1cfdf1693de464a0a8142e842ee0c203e5391d79f7349e21`。它固定使用 Staging CA、私有临时状态和空白客户端配置，禁用目录钩子，核对测试签发者及唯一的 IP SAN，删除测试状态后复核原共享配置。公网验证及测试签发控制器合计 19 项本机检查通过。服务器安装或签发均未开始。

本机另用 OpenSSL 3.5.7 生成六张临时自签证书，复验签发脚本的实际证书解析：接受唯一正确 IP 的模拟测试证书；拒绝错误 IP、附带域名、把 IP 写成 DNS 名、缺少 SAN 或签发者不是测试标记的证书。剩余有效期的正反例也通过，临时证书与密钥已清理。此项只证明本机 OpenSSL 输出与验证逻辑相符，不证明证书由 ACME 签发或受公网信任；复验脚本 `verify_staging_certificate_check.py` 和结果已归档。

1. 重新核对共享配置哈希 `300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73`、80 和公开挂载。在 `/opt/continew/web/.well-known/acme-challenge/` 使用新随机文件名写入**合成公开测试文本**；仅对既定 IP 发一次普通 HTTP 读取，比较正文。无论结果如何都删除该测试文件；只清理由本轮创建且为空的目录，保留已有内容。不改 nginx，不开 443。
2. 如果正文满足要求且用户已授权安装，先确认无既有 Certbot 安装或待保留的签发状态，再从 [Certbot 官方 snap](https://certbot.eff.org/instructions?os=snap&tab=standard&ws=nginx) 安装，直接用 `/snap/bin/certbot` 工作，不创建全局软链接、不使用自动修改 nginx 的安装器。检查版本和 `--ip-address`、webroot 支持，随后仅调用测试环境，用独立私有的 `--config-dir`、`--work-dir`、`--logs-dir`，不与生产证书或账号混用。

   ```bash
   /snap/bin/certbot certonly --server https://acme-staging-v02.api.letsencrypt.org/directory --preferred-profile shortlived \
     --webroot --webroot-path /opt/continew/web --ip-address 134.175.137.18 \
     --non-interactive --agree-tos --register-unsafely-without-email \
     --no-directory-hooks --config "$STAGING_EMPTY_CONFIG" \
     --config-dir "$STAGING_CONFIG" --work-dir "$STAGING_WORK" --logs-dir "$STAGING_LOGS"
   ```

3. 通过本地证书解析确认 `IP Address:134.175.137.18` 匹配，记录脱敏结果，回收本次测试证书、测试密钥和公开测试文件。Snap 安装会留下软件（及其默认管理任务），不能把此安装称为纯只读。测试证书不可信，不用于公网服务、不导出密钥、不安装到代理。

任何前提失败都停止，不改用正式 CA。若安装已完成，官方 Certbot 可能保留在原机；若 SSH 中断或超时，先按单次回执确认确切进程、安装状态与临时目录，不重新运行。私有证书或签发状态绝不写入公开 webroot。后续正式签发、自动续期和独立 443 代理上线需根据本次结果再审阅，正式 8000 入站限制、真实账号和设备验收也仍待完成。

复验入口：在 `backend` 运行 `python -B -m pytest tests/test_travel_https.py tests/test_budget_https_load.py -q`（本机 nginx、OpenSSL 和回环）；已有隔离 [HTTPS 代理报告](交接归档-预算版独立HTTPS代理.md)、[代理信任回执](交接归档-预算版代理信任验收.md)。本轮私有候选在 `.release-candidates/budget-https-load-2026-11-19/https-ip-acme-existing-webroot/`，含其他站点共享配置的文件仅供本机私有审阅；[机器回执](verification/budget-ip-acme-preflight-2026-11-19.json)不含账号标识、截图或证书。
