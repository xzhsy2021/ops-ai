"""Session-based request protection middleware - V2 only."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

SESSION_COOKIE_NAME = "ops_session_v2"
PUBLIC_PATHS = frozenset({
    "/login",
    "/static",
    "/healthz",
    "/readyz",
    # Static browser assets must remain public in single-process SPA mode.
    # Otherwise module requests such as /assets/index-*.js are redirected to
    # /login and the browser receives text/html instead of application/javascript.
    "/vite.svg",
    "/favicon.ico",
})
PUBLIC_PREFIXES = (
    "/assets/",
    "/api/v2/auth",
    # Tool-token based endpoints must reach their route handlers so the
    # Tool Token validator can authenticate Authorization: Bearer ops_tool_*.
    # Without this, the session middleware rejects MCP / HTTP tool clients
    # before app.api.tools.get_tool_context() can validate the token.
    "/api/v2/tools",
    "/api/v2/mcp",
    "/api/v2/capabilities",
)


def create_auth_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        request.state.is_authenticated = False
        request.state.username = None

        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        if _is_public_path(path):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            from app.core.auth_v2 import verify_session_token, get_current_user as get_v2_user
            token_data = verify_session_token(token)
            if token_data:
                db = None
                try:
                    from app.db.base import SessionLocal
                    db = SessionLocal()
                    user = get_v2_user(request, db)
                    if user:
                        request.state.is_authenticated = True
                        request.state.username = user["username"]
                        return await call_next(request)
                finally:
                    if db:
                        db.close()

        if path.startswith("/api/v2/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        return RedirectResponse(
            url=_build_login_url(request),
            status_code=307,
        )


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/static/"):
        return not path.lower().endswith((".html", ".htm"))
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _build_login_url(request: Request) -> str:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return f"/login?next={quote(next_path, safe='')}"
