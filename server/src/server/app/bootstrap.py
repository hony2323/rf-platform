"""
Bootstrap script: create the initial user account.

Usage:
    python -m server.app.bootstrap --email admin@example.com --password secret
    python -m server.app.bootstrap --email admin@example.com --password secret --db-path /data/rf.db
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def _run(email: str, password: str, db_path: str) -> None:
    import server.storage.db as db_module
    from server.auth.passwords import hash_password
    from server.storage.db import init_db
    from server.storage.repositories.users import create_user, get_user_by_email
    from sqlalchemy.ext.asyncio import async_sessionmaker

    await init_db(db_path)
    factory = async_sessionmaker(db_module._engine, expire_on_commit=False)
    async with factory() as session:
        existing = await get_user_by_email(session, email)
        if existing is not None:
            print(f"User {email!r} already exists (id={existing.id}).", file=sys.stderr)
            sys.exit(1)
        user = await create_user(session, email, hash_password(password))
        print(f"Created user: id={user.id}  email={user.email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an RF Platform user account.")
    parser.add_argument("--email", required=True, help="User email address")
    parser.add_argument("--password", required=True, help="Plaintext password (will be hashed)")
    parser.add_argument("--db-path", default=None, help="SQLite path (default: RF_DB_PATH env or rf_platform.db)")
    args = parser.parse_args()

    if args.db_path is None:
        from server.config.settings import load_settings
        db_path = load_settings().db_path
    else:
        db_path = args.db_path

    asyncio.run(_run(args.email, args.password, db_path))


if __name__ == "__main__":
    main()
