"""用户安全：密码哈希（标准库 PBKDF2）+ 登录 token（PyJWT HS256）。

不引入 passlib/bcrypt 等重依赖；上云前可在 .env 配置 JWT_SECRET 正式密钥。
"""
import base64
import hashlib
import hmac
import secrets
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _jwt_secret() -> str:
    """未配置时生成本机持久随机密钥；部署时应显式配置 JWT_SECRET。"""
    if settings.jwt_secret:
        return settings.jwt_secret
    from app.db.database import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / ".jwt_secret"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        secret = secrets.token_urlsafe(48)
        f.write(secret)
    return secret


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
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        return None
