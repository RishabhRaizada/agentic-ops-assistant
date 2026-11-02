"""Authentication (bcrypt + JWT in an httpOnly cookie) and role/region authorization helpers."""

from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Case, User
from app.services import audit

COOKIE = "opspilot_session"
WRITE_ROLES = {"analyst", "ops_manager", "regional_head", "director"}


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def issue_token(user: User) -> str:
    s = get_settings()
    # Real time, not demo time: advancing the demo clock must not log everyone out.
    from datetime import UTC, datetime

    exp = datetime.now(UTC) + timedelta(hours=s.session_hours)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, s.secret_key, algorithm="HS256")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    try:
        data = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Session expired") from e
    user = db.get(User, int(data["sub"]))
    if not user or not user.active:
        raise HTTPException(401, "Unknown user")
    # CSRF: state-changing requests must carry the custom header (cross-site forms cannot set it).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "opspilot":
        raise HTTPException(403, "Missing CSRF header")
    return user


def deny(db: Session, user: User, action: str, reason: str, case: Case | None = None, **meta) -> HTTPException:
    """Record an unauthorized attempt in the audit trail, commit it, and return the 403 to raise."""
    audit.record(
        db,
        action="unauthorized_attempt",
        summary=f"{user.name} ({user.role}, {user.region}) tried to {action}: {reason}",
        actor_type="human",
        actor_id=user.email,
        case_id=case.id if case else None,
        meta={"attempted": action, "reason": reason, **meta},
        pinned=True,
    )
    db.commit()
    return HTTPException(403, reason)


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin only")
    return user


def can_write_case(user: User, case: Case) -> tuple[bool, str]:
    if user.role not in WRITE_ROLES:
        return False, f"role '{user.role}' is read-only for requests"
    if user.region != "ALL" and case.region and case.region != user.region:
        return False, f"request belongs to region {case.region}; you are scoped to {user.region}"
    return True, ""


def ensure_can_write(db: Session, user: User, case: Case, action: str) -> None:
    ok, why = can_write_case(user, case)
    if not ok:
        raise deny(db, user, action, why, case)
