"""用户安全：密码哈希（标准库 PBKDF2）+ 登录 token（PyJWT HS256）。

不引入 passlib/bcrypt 等重依赖；上云前可在 .env 配置 JWT_SECRET 正式密钥。
"""
import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

PBKDF2_ITERATIONS = 100_000
TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    """格式：pbkdf2$迭代次数$盐(base64)$哈希(base64)"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$", 3)
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def _jwt_secret() -> str:
    """优先取 .env 的 JWT_SECRET；未配置时用开发期派生值（上云前务必配置正式值）。"""
    return os.getenv("JWT_SECRET") or "dev-secret::" + hashlib.sha256(
        settings.zhipu_api_key.encode()
    ).hexdigest()


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> int | None:
    """校验并返回 user_id；无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        return int(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
