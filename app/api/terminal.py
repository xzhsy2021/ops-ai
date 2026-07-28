"""WebSocket 终端会话管理 - 参考 webssh 模式"""
import asyncio
import json
import logging
import threading
import time
import os
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.core.auth_v2 import require_admin
from app.api.helpers import api_response, audit
from app.services.remote_access import build_audit_context, format_audit_detail, get_hop_summary
from app.core.command_security import TerminalInputGuard

logger = logging.getLogger(__name__)

terminal_ws_router = APIRouter(prefix="/api/v2/servers", tags=["终端工作台"])


def _auth_websocket_user(websocket: WebSocket) -> dict | None:
    """Authenticate a WebSocket handshake with the normal session cookie.

    HTTP middleware does not run for WebSocket routes, so terminal sessions must
    re-check the cookie here before binding the browser to a remote shell.
    """
    token = websocket.cookies.get("ops_session_v2")
    if not token:
        return None
    from app.core.auth_v2 import verify_session_token, get_current_user
    token_data = verify_session_token(token)
    if not token_data:
        return None
    from app.db.base import SessionLocal
    db = SessionLocal()
    try:
        class _CookieRequest:
            cookies = {"ops_session_v2": token}
        return get_current_user(_CookieRequest(), db)
    finally:
        db.close()


async def _reject_ws(websocket: WebSocket, message: str, code: int = 1008):
    await websocket.accept()
    await websocket.send_json({"type": "error", "message": message})
    await websocket.close(code=code)


@terminal_ws_router.post("/{name}/terminal/sessions")
async def terminal_session_create(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    from config_manager import get_server_by_name
    from app.services.terminal_sessions import create_session

    srv = get_server_by_name(name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    try:
        body = await request.json()
    except Exception:
        body = {}

    cols = int(body.get("cols", 120))
    rows = int(body.get("rows", 30))

    hop_ctx = get_hop_summary(srv)

    try:
        session = create_session(
            user=request.state.username,
            server_name=name,
            server_config=srv,
            hop_count=hop_ctx["hop_count"],
            cols=cols,
            rows=rows,
        )
    except (RuntimeError, ConnectionError, OSError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    ac = build_audit_context(srv, name)
    audit("server.terminal.session.create", "server", name,
          format_audit_detail(ac, user=request.state.username, session=session["session_id"]))

    return api_response(data={
        "session_id": session["session_id"],
        "server": name,
        "user": request.state.username,
        "cols": session["cols"],
        "rows": session["rows"],
        "created_at": session["created_at"],
        "hop_context": hop_ctx,
    })


@terminal_ws_router.delete("/{name}/terminal/sessions/{session_id}")
def terminal_session_close(
    request: Request,
    name: str,
    session_id: str,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    from app.services.terminal_sessions import close_session, get_session

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    if session["server_name"] != name:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found for '{name}'")

    try:
        close_session(session_id, username=request.state.username)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    from config_manager import get_server_by_name
    srv_cfg = get_server_by_name(name)
    ac = build_audit_context(srv_cfg, name) if srv_cfg else {"server": name, "auth_mode": "unknown", "hop_chain": "direct"}
    audit("server.terminal.session.close", "server", name,
          format_audit_detail(ac, user=request.state.username, session=session_id))
    return api_response(data={"closed": session_id})


@terminal_ws_router.get("/{name}/terminal/sessions")
def terminal_sessions_list(request: Request, name: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    from app.services.terminal_sessions import list_sessions
    sessions = list_sessions(server_name=name)
    return api_response(data={
        "server": name,
        "sessions": sessions,
        "count": len(sessions),
    })


@terminal_ws_router.websocket("/{name}/terminal/ws")
async def terminal_websocket(websocket: WebSocket, name: str):
    query_params = dict(websocket.query_params)
    session_id = query_params.get("session_id", "")

    if not session_id:
        await _reject_ws(websocket, "session_id is required")
        return

    user = _auth_websocket_user(websocket)
    if not user or not user.get("is_admin"):
        await _reject_ws(websocket, "Authentication required")
        return

    from app.services.terminal_sessions import (
        get_session, touch_session,
        resize_session, set_ws_connected, close_session,
    )

    session = get_session(session_id)
    if not session:
        await _reject_ws(websocket, f"Session '{session_id}' not found or expired")
        return

    if session["server_name"] != name:
        await _reject_ws(websocket, f"Session '{session_id}' not for server '{name}'")
        return

    if session.get("user") != user.get("username"):
        await _reject_ws(websocket, f"Session '{session_id}' is not owned by current user")
        return

    if session.get("ws_connected"):
        await _reject_ws(websocket, f"Session '{session_id}' is already connected")
        return

    channel = session["channel"]
    block_dangerous = os.getenv("TERMINAL_ALLOW_DANGEROUS", "0").lower() not in {"1", "true", "yes", "on"}
    terminal_guard = TerminalInputGuard(block_dangerous=block_dangerous)
    set_ws_connected(session_id, True)

    await websocket.accept()
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "server": name,
        "cols": session["cols"],
        "rows": session["rows"],
    })

    import queue
    input_queue: queue.Queue = queue.Queue()
    output_queue: asyncio.Queue = asyncio.Queue()
    stopped = threading.Event()
    ws_loop = asyncio.get_running_loop()

    def io_loop():
        """独立线程：统一处理 channel 读写，不阻塞 asyncio 事件循环"""
        try:
            while not stopped.is_set():
                if channel.closed:
                    break

                try:
                    data = input_queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if data is None:
                        break
                    try:
                        channel.send(data)
                    except Exception:
                        break
                    continue

                if channel.recv_ready():
                    chunks = []
                    while channel.recv_ready():
                        data = channel.recv(65536)
                        if not data:
                            break
                        chunks.append(data)
                    if chunks:
                        touch_session(session_id)
                        combined = b''.join(chunks)
                        ws_loop.call_soon_threadsafe(output_queue.put_nowait, combined)
                    continue

                stopped.wait(0.1)
        except Exception as e:
            logger.error(f"Terminal IO loop error for {session_id}: {e}")
        finally:
            try:
                ws_loop.call_soon_threadsafe(output_queue.put_nowait, None)
            except Exception:
                pass

    io_thread = threading.Thread(target=io_loop, daemon=True)
    io_thread.start()

    async def forward_output():
        try:
            while True:
                data = await output_queue.get()
                if data is None:
                    break
                if isinstance(data, bytes):
                    await websocket.send_bytes(data)
                else:
                    await websocket.send_text(str(data))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Terminal forward error for {session_id}: {e}")

    forward_task = asyncio.create_task(forward_output())

    try:
        while True:
            try:
                msg = await websocket.receive()
            except WebSocketDisconnect:
                break
            except Exception:
                break

            if "text" in msg:
                try:
                    payload = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = payload.get("type", "")

                if msg_type == "input":
                    data = payload.get("data", "")
                    if isinstance(data, str):
                        data = data.encode("utf-8", errors="replace")
                    guarded_data, guard_events = terminal_guard.feed(data)
                    for event in guard_events:
                        try:
                            audit("server.terminal.command.blocked", "server", name,
                                  f"user={user.get('username')} session={session_id} risk={event.get('level')} reason={event.get('reason')} cmd={event.get('command', '')[:120]}")
                        except Exception:
                            logger.exception("Failed to audit blocked terminal command")
                    if guarded_data:
                        input_queue.put(guarded_data)
                    touch_session(session_id)

                elif msg_type == "resize":
                    new_cols = int(payload.get("cols", session["cols"]))
                    new_rows = int(payload.get("rows", session["rows"]))
                    resize_session(session_id, new_cols, new_rows)

                elif msg_type == "ping":
                    touch_session(session_id)

            elif "bytes" in msg:
                guarded_data, guard_events = terminal_guard.feed(msg["bytes"])
                for event in guard_events:
                    try:
                        audit("server.terminal.command.blocked", "server", name,
                              f"user={user.get('username')} session={session_id} risk={event.get('level')} reason={event.get('reason')} cmd={event.get('command', '')[:120]}")
                    except Exception:
                        logger.exception("Failed to audit blocked terminal command")
                if guarded_data:
                    input_queue.put(guarded_data)
                touch_session(session_id)

    finally:
        stopped.set()
        input_queue.put(None)
        forward_task.cancel()
        try:
            await forward_task
        except Exception:
            pass
        set_ws_connected(session_id, False)
        io_thread.join(timeout=5)
        logger.info(f"Terminal WS disconnected for session {session_id}")
