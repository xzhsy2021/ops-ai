"""
OPS 用户密码管理工具

用法:
    # 列出所有用户
    python manage_users.py list

    # 重置指定用户的密码（交互式输入）
    python manage_users.py reset --username admin

    # 重置指定用户的密码（命令行指定）
    python manage_users.py reset --username admin --password NewPass123

    # 查看帮助
    python manage_users.py --help
"""

import sys
import os
import argparse
import hashlib
import secrets

sys.path.insert(0, os.path.dirname(__file__))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${hashed.hex()}"


def _get_db_url():
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    for p in ["sqlite:///./ops.db", "sqlite:///./data/ops_data.db", "sqlite:///./ops_data.db"]:
        db_path = p.replace("sqlite:///", "")
        if os.path.exists(db_path):
            return p

    db_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(db_dir, exist_ok=True)
    return f"sqlite:///{db_dir}/ops_data.db"


def _get_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(_get_db_url(), connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def cmd_list():
    from app.db.repository import UserRepository
    db = _get_session()
    try:
        repo = UserRepository(db)
        users = repo.list_all()
        if not users:
            print("No users found. Run 'python manage_users.py create --username admin'")
            return
        print(f"{'ID':<36} {'Username':<20} {'Role':<12} {'Admin':<8} {'Deploy':<8}")
        print("-" * 86)
        for u in users:
            print(f"{u.id:<36} {u.username:<20} {u.role:<12} {str(u.is_admin):<8} {str(u.can_deploy):<8}")
        print(f"\nTotal: {len(users)} user(s)")
    finally:
        db.close()


def cmd_reset(username: str, password: str = None):
    from app.db.repository import UserRepository
    db = _get_session()
    try:
        repo = UserRepository(db)
        user = repo.get_by_username(username)
        if not user:
            print(f"Error: User '{username}' not found")
            sys.exit(1)

        if not password:
            import getpass
            pw1 = getpass.getpass("New password: ")
            if len(pw1) < 6:
                print("Error: Password must be at least 6 characters")
                sys.exit(1)
            pw2 = getpass.getpass("Confirm password: ")
            if pw1 != pw2:
                print("Error: Passwords do not match")
                sys.exit(1)
            password = pw1

        user.password_hash = hash_password(password)
        repo.update(user)
        print(f"Password for '{username}' has been updated.")
        print("Please store the new password securely.")
    finally:
        db.close()


def cmd_create(username: str, password: str = None, role: str = "admin", admin: bool = True):
    from app.db.repository import UserRepository
    db = _get_session()
    try:
        repo = UserRepository(db)
        if repo.get_by_username(username):
            print(f"Error: User '{username}' already exists. Use 'reset' to change password.")
            sys.exit(1)

        if not password:
            password = secrets.token_urlsafe(12)
            print("Generated password, shown once. Store it securely:")
            print(password)

        password_hash = hash_password(password)
        repo.create(username=username, password_hash=password_hash, role=role,
                     is_admin=admin, can_deploy=(role != "viewer"))
        print(f"User '{username}' created successfully.")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="OPS User Management CLI")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("list", help="List all users")

    p_reset = sub.add_parser("reset", help="Reset user password")
    p_reset.add_argument("--username", "-u", required=True, help="Username")
    p_reset.add_argument("--password", "-p", default=None, help="New password (will prompt if omitted)")

    p_create = sub.add_parser("create", help="Create a new user")
    p_create.add_argument("--username", "-u", required=True, help="Username")
    p_create.add_argument("--password", "-p", default=None, help="Password (auto-generated if omitted)")
    p_create.add_argument("--role", "-r", default="admin", choices=["admin", "developer", "viewer"],
                          help="Role (default: admin)")
    p_create.add_argument("--no-admin", action="store_true", help="Do not grant admin privileges")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "reset":
        cmd_reset(args.username, args.password)
    elif args.command == "create":
        cmd_create(args.username, args.password, args.role, not args.no_admin)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
