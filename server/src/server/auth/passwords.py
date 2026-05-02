from __future__ import annotations

import re

import bcrypt

_PASSWORD_RULES = [
    (lambda p: len(p) >= 8, "at least 8 characters"),
    (lambda p: bool(re.search(r"[A-Z]", p)), "at least one uppercase letter"),
    (lambda p: bool(re.search(r"[a-z]", p)), "at least one lowercase letter"),
    (lambda p: bool(re.search(r"\d", p)), "at least one digit"),
]


def validate_password_strength(password: str) -> str | None:
    """Return an error message if the password fails any requirement, else None."""
    for check, desc in _PASSWORD_RULES:
        if not check(password):
            return f"Password must contain {desc}"
    return None


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(plain.encode(), hashed.encode())
