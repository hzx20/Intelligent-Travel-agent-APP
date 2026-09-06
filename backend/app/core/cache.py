"""进程内缓存（Redis 替代方案）。

背景：技术栈升级要求"缓存与会话管理改用 Redis"，但本机无 Docker（命令验证
docker 不存在），Redis 无法在本机运行。因此当前版本采用业界常见的降级方案：
- 缓存 → cachetools 的 TTLCache（进程内带过期时间的字典）
- 会话 → JWT 无状态令牌（后续登录功能上线时落地）
- Redis 保留在依赖规划中，作为将来上云部署时的升级项，接口不变直接切换

对应关系：Redis 缓存位 → backend/app/core/cache.py（TTLCache 单例）
"""
from cachetools import TTLCache

# 全局共享缓存：最多 1024 条，每条存活 30 分钟
cache: TTLCache = TTLCache(maxsize=1024, ttl=1800)
