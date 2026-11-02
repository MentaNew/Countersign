"""Sign-in sessions for the person (not the agent). A random token per sign-in, stored only as
a hash, ending after idle time, after a fixed maximum, on sign-out, or when ended from the
Security page. Browsers get it in an HttpOnly cookie that page scripts cannot read; scripts can
use it as a Bearer token."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.db.models import UserSession

COOKIE = "gr_session"
CSRF_HEADER = "x-guardrail-request"  # required with the cookie on changes (blocks cross-site forms)
TOUCH_EVERY = timedelta(seconds=60)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


class LoginSessions:
    def __init__(self, db: async_sessionmaker[AsyncSession], idle: timedelta, max_age: timedelta,
                 model: type = UserSession):
        self.db, self.idle, self.max_age, self.model = db, idle, max_age, model

    async def create(self, user: str, method: str, user_agent: str | None = None,
                     ip: str | None = None) -> tuple[str, Any]:
        token = secrets.token_urlsafe(32)
        row = self.model(id=f"ses_{secrets.token_hex(6)}", token_hash=token_hash(token),
                          user=user, method=method, expires_at=now() + self.max_age,
                          user_agent=(user_agent or "")[:300] or None, ip=ip)
        async with self.db.begin() as s:
            s.add(row)
        return token, row

    async def resolve(self, token: str) -> Any:
        """The live session for this token, or None (unknown, ended, idle too long, too old)."""
        t = now()
        async with self.db.begin() as s:
            row = (await s.execute(select(self.model).where(
                self.model.token_hash == token_hash(token)))).scalar_one_or_none()
            if row is None or row.ended_at is not None:
                return None
            if t >= row.expires_at or t - row.last_seen_at >= self.idle:
                row.ended_at = t
                return None
            if t - row.last_seen_at >= TOUCH_EVERY:
                row.last_seen_at = t
            s.expunge(row)
            return row

    async def end(self, session_id: str, user: str) -> bool:
        async with self.db.begin() as s:
            r = await s.execute(update(self.model).where(
                self.model.id == session_id, self.model.user == user,
                self.model.ended_at.is_(None)).values(ended_at=now()))
        return r.rowcount > 0

    async def end_all(self, user: str, keep: str | None = None) -> int:
        async with self.db.begin() as s:
            q = update(self.model).where(self.model.user == user,
                                          self.model.ended_at.is_(None))
            if keep:
                q = q.where(self.model.id != keep)
            r = await s.execute(q.values(ended_at=now()))
        return r.rowcount

    async def list(self, user: str, current: str | None) -> list[dict[str, Any]]:
        t = now()
        async with self.db() as s:
            rows = (await s.execute(select(self.model).where(
                self.model.user == user, self.model.ended_at.is_(None),
                self.model.expires_at > t).order_by(self.model.last_seen_at.desc()))).scalars()
            return [{"id": r.id, "method": r.method, "created_at": r.created_at.isoformat(),
                     "last_seen_at": r.last_seen_at.isoformat(),
                     "expires_at": min(r.expires_at, r.last_seen_at + self.idle).isoformat(),
                     "user_agent": r.user_agent, "ip": r.ip, "current": r.id == current}
                    for r in rows if t - r.last_seen_at < self.idle]


_bearer = HTTPBearer(auto_error=False)


def make_session_dependency(logins: LoginSessions, cookie: str = COOKIE):
    """The signed-in person, from the session cookie (browser) or a Bearer session token
    (scripts). With the cookie, changes also need the CSRF header, which another website's form
    or script cannot add without the browser asking this server first."""

    async def require_user(request: Request,
                           cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
        token, via_cookie = (cred.credentials, False) if cred else (
            request.cookies.get(cookie), True)
        if not token:
            raise HTTPException(401, "sign-in required")
        row = await logins.resolve(token)
        if row is None:
            raise HTTPException(401, "your session ended; sign in again")
        if via_cookie and request.method not in ("GET", "HEAD", "OPTIONS") \
                and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, "missing request header")
        request.state.session_id = row.id
        return row.user

    return require_user
