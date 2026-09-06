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
