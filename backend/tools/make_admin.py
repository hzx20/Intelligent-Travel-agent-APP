"""把指定用户提升为管理员（或取消）。

用法（在 backend/ 目录下）：
  python tools/make_admin.py 用户名           # 提升为管理员
  python tools/make_admin.py 用户名 --revoke  # 取消管理员
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import init_db  # noqa: E402
from app.db.models import User  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--revoke", action="store_true", help="取消管理员身份")
    args = parser.parse_args()

    init_db()
    from app.db.database import SessionLocal

    db = SessionLocal()
    user = db.query(User).filter(User.username == args.username).first()
    if user is None:
        print(f"用户 {args.username} 不存在（请先在前端注册）")
        return
    user.is_admin = not args.revoke
    db.commit()
    print(f"{user.username} 管理员状态 → {'是' if user.is_admin else '否'}")


if __name__ == "__main__":
    main()
