"""配置模块：读取项目根目录 .env（与 Node 版 config.js 行为一致）。

对应关系：server/src/config.js → backend/app/core/config.py
- Node 手写 loadEnv 解析 .env → Python 用 pydantic-settings（业界主流配置库）自动解析
- 真实环境变量优先于 .env 文件的规则保持不变
"""
from pathlib import Path
import os

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# 本文件位于 backend/app/core/，项目根在上三级
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Versioned releases keep operational data and secrets outside the code package.
# An explicitly configured file must exist; a typo must not silently change JWTs.
_configured_env_file = os.environ.get('TRAVEL_ENV_FILE')
if _configured_env_file is None:
    ENV_FILE = PROJECT_ROOT / '.env'
else:
    ENV_FILE = Path(_configured_env_file)
    if not ENV_FILE.is_absolute() or not ENV_FILE.is_file():
        raise ValueError('TRAVEL_ENV_FILE must name an existing absolute file')


class Settings(BaseSettings):
    zhipu_api_key: str = ""
    amap_api_key: str = ""
    qweather_api_key: str = Field(default="", repr=False)
    qweather_api_host: str = ""
    variflight_api_key: str = Field(default="", repr=False)
    # 动态地图（Web端 JS API）专用钥匙：与上面的 Web 服务 key 不通用，v1.1 新增
    amap_js_key: str = ""
    amap_js_security_code: str = ""
    # 模型名保持 glm-4-flash，与 Node 版一致
    glm_model: str = "glm-4-flash"
    # 登录 token 的签名密钥：上云/换环境前务必在 .env 里配置 JWT_SECRET；
    # 不配时使用保存在 backend/data/ 下的本机随机密钥
    jwt_secret: str = ""

    # ---- Rerank（两阶段检索的第二阶段，2026-11-27 新增）----
    # 模型选型依据：智谱官方提供托管 rerank 接口（docs.bigmodel.cn「文本重排序」，
    # POST /api/paas/v4/rerank），0 内存占用，2G 服务器首选；本地 ONNX 方案仅作
    # 托管接口不可用时的后备。默认关闭：.env 设 RERANK_ENABLED=1 或调用
    # search(..., rerank=True) 才启用，保证对现有 556 个测试与线上行为零影响。
    rerank_enabled: bool = False
    # 智谱托管 rerank 的模型名（官方文档示例值就是 "rerank"）
    rerank_model: str = "rerank"
    # 混合排序后送入 rerank 的候选池大小：比 k 大才能"捞回"排在后面的正确片段
    rerank_pool: int = 10
    # rerank 单次 HTTP 超时（秒）。超时/失败一律回退混合排序，不阻断主流程
    rerank_timeout: float = 5.0
    # relevance_score 低于该值视为不相关直接丢弃；全部低于时回退混合排序保证可用
    rerank_min_score: float = 0.10

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

def _bypass_proxy_for_cn_apis():
    """国内 API（智谱/高德）一律直连，绕过系统代理。

    为什么：环境里常驻 FlClash 代理（给 GitHub 用），但智谱/高德是国内服务，
    走代理纯屬绕路——代理一抖（FlClash 假死是常态），AI 生成和高德核实就跟着超时。
    httpx / langchain-openai 都遵守 NO_PROXY 约定，进程内改环境变量即可全局生效。
    """
    import os

    domains = "open.bigmodel.cn,restapi.amap.com,webapi.amap.com"
    current = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
    merged = f"{current},{domains}" if current else domains
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


_bypass_proxy_for_cn_apis()
