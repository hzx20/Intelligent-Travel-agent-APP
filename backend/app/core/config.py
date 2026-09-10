"""配置模块：读取项目根目录 .env（与 Node 版 config.js 行为一致）。

对应关系：server/src/config.js → backend/app/core/config.py
- Node 手写 loadEnv 解析 .env → Python 用 pydantic-settings（业界主流配置库）自动解析
- 真实环境变量优先于 .env 文件的规则保持不变
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 本文件位于 backend/app/core/，项目根在上三级
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    zhipu_api_key: str = ""
    amap_api_key: str = ""
    # 动态地图（Web端 JS API）专用钥匙：与上面的 Web 服务 key 不通用，v1.1 新增
    amap_js_key: str = ""
    amap_js_security_code: str = ""
    # 模型名保持 glm-4-flash，与 Node 版一致
    glm_model: str = "glm-4-flash"

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

if not settings.zhipu_api_key:
    raise RuntimeError("缺少 ZHIPU_API_KEY：请确认项目根目录 .env 文件存在且包含该钥匙")


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
