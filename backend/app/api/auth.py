from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import user_out
from app.config import get_settings
from app.db import get_db
from app.models import Notification, User
from app.security import COOKIE, current_user, issue_token, verify_password
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        audit.record(db, action="auth.login_failed", summary=f"Failed sign-in for {body.email[:80]}", actor_type="system", actor_id="auth")
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    response.set_cookie(COOKIE, issue_token(user), httponly=True, samesite="lax", secure=get_settings().cookie_secure,
                        max_age=get_settings().session_hours * 3600, path="/")
    audit.record(db, action="auth.login", summary=f"{user.name} signed in", actor_type="human", actor_id=user.email)
    db.commit()
    return user_out(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    unread = db.query(Notification).filter_by(user_id=user.id, read=False).count()
    return {**user_out(user), "unread_notifications": unread}


@router.get("/demo-users")
def demo_users(db: Session = Depends(get_db)):
    """Login-page quick pick. Only exposed in demo mode."""
    if not get_settings().demo_mode:
        return []
    return [user_out(u) for u in db.scalars(select(User).order_by(User.id))]
