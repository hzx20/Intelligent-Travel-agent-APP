# 正式 HTTPS 入口候选交接

2026-11-19，Asia/Shanghai。已完成域名和公网 IPv4 两种入口的配置设计、本机 nginx 验收及服务器只读检查。正式入口尚未选择；未申请证书，未改变 DNS、防火墙、Docker 或 nginx，未发布。

正式入口建议优先选已有、由项目方控制的域名。如果服务器位于中国内地，先核对网站和域名的备案及接入要求，见[腾讯云轻量应用服务器备案说明](https://cloud.tencent.com/document/product/1207/45756)。暂无域名时，公网 IPv4 可作为待审备选。Let's Encrypt 可以为公网 IP 签发短有效期证书，通常 160 小时；需确认 IP 长期稳定、自动续期、到期告警及证书更新能安全加载。其官方文档提供 Certbot 的 IP webroot 路径与续期说明。[IP 证书公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability) · [Certbot IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)

## 已验收的配置行为

- 沿用新版 Linux 候选 `553a85ca32f4a4d339baaa618b8c9fc97e756b96d713dbcbbcd45ffe974ae363` 和私有计划 `81ee1674adc6b9267e4b526ffdbf5313cae0b8094c5039ca220c528ef500f5b5`。生成器 `backend/tools/travel_https.py` 校验原件哈希、拒绝陌生 include 或监听、保留非旅行 HTTP 指令；输出独立文件，不改冻结计划。
- HTTP 浏览页 308 跳向固定正式地址，保持路径和查询参数；拒绝明文 API 和写请求，不将它们代理给后端。正式 HTTPS 的子路径、静态和反代规则仍来自同版候选，维护模式也经 HTTPS 返回 503、`Retry-After: 60`、`Cache-Control: no-store`。域名模式拒绝错误 SNI；两种模式拒绝不匹配的 HTTP Host。共享 HTTP 首页在隔离验收中仍可访问。
- IP 候选另有仅开放 ACME 挑战的引导版。引导版由原现场 HTTP 字节生成，而非从发布候选生成，避免证书准备过程提前开放新版旅行服务。正常、维护和引导三个文件已存放在 `.release-candidates/budget-https-load-2026-11-19/https-ip-review/`，此目录含共享配置，只供私有审阅。
- 本机 nginx 使用临时 DNS SAN/IP SAN 证书，连同现有负载回归共 4 项通过。测试仅走环回，未向公网 IP 或任何证书服务发请求。[脱敏回执](verification/budget-https-entry-2026-11-19.json)记录候选哈希与现场证据。
- 服务器实际只监听 80，`continew-web` 的 80 映射到宿主，443 无映射，镜像内 nginx 为 1.27.0。仅把 `listen 443` 写入容器配置，无法使当前公网入口可访问。开通前需审查 443 的宿主监听、云侧入口及证书加载；若由共享容器终止，需评估容器端口映射变更对同机另一站点的影响；若用独立边缘代理，要核对现有 HTTP 入口和信任链。此次没有选择或部署这些拓扑。

## 停止点和恢复入口

本轮停在两种方案均可审阅、正式入口未选。先确认域名或选择现有公网 IP 作为候选，再复核现场共享配置哈希、访问控制、443 终止点和续期方案。只有在确定签发、端口、部署与回退步骤后才申请证书或改配置。IP 路径先用 ACME 测试环境走通挑战和更新流程；当地证书验收必须使用受信任链且不能跳过证书校验。

本机复验：在 `backend` 运行 `python -B -m pytest tests/test_travel_https.py tests/test_budget_https_load.py -q`；需要可用的便携 nginx 和 OpenSSL（可以用 `TRAVEL_TEST_OPENSSL` 指定）。正式环境后来还需核验证书与域名/IP 匹配、首次访问和深链重载、共享站点、副本权限、生产代理和专用账号；在桌面浏览器和真实 Android/iOS 上完成登录、两账号隔离、预算保存、退出/换号、弱网下的恢复。更换入口后重新登录，因为旧 HTTP 的浏览器存储不会与新 HTTPS 源共享。不要提交账号密码、令牌或证书私钥到归档。生产流量切换及正式备份留在后续维护板块，`production_ready=false`。
