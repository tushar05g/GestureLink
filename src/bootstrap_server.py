"""
bootstrap_server.py - Lightweight target-PC bootstrap for temporary agent sessions.

Flow:
1) Controller requests session by IP against this bootstrap service.
2) Local user on target PC gets an approval prompt.
3) If approved, a temporary agent process is started.
4) On disconnect/timeout, agent process is stopped and session cleaned up.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

logger = logging.getLogger("gesture_control.bootstrap")


class SessionRequest(BaseModel):
    relay_ws: str = Field(..., description="Cloud relay websocket URL for agent.")
    controller_id: str = Field(default="mobile", description="Controller client identifier.")
    agent_id: str = Field(default="", description="Optional fixed agent ID.")
    token: str = Field(default="", description="Optional relay auth token.")
    session_ttl_seconds: int = Field(default=180, ge=30, le=7200)


class SessionHeartbeat(BaseModel):
    controller_id: str = Field(default="mobile")


@dataclass
class AgentSession:
    session_id: str
    controller_id: str
    relay_ws: str
    agent_id: str
    token: str
    created_at: float
    expires_at: float
    status: str
    process: subprocess.Popen[Any] | None = None
    last_heartbeat: float = 0.0


class BootstrapManager:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()

    def request_session(self, req: SessionRequest) -> AgentSession:
        relay_ws = req.relay_ws.strip()
        if not relay_ws:
            raise ValueError("relay_ws is required")

        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        agent_id = req.agent_id.strip() or f"pc-{socket.gethostname()}"
        session = AgentSession(
            session_id=session_id,
            controller_id=req.controller_id.strip() or "mobile",
            relay_ws=relay_ws,
            agent_id=agent_id,
            token=req.token.strip(),
            created_at=now,
            expires_at=now + int(req.session_ttl_seconds),
            status="pending",
            last_heartbeat=now,
        )

        with self._lock:
            self._sessions[session_id] = session

        if not self._ask_local_permission(session):
            with self._lock:
                session.status = "denied"
            return session

        try:
            proc = self._start_agent_process(session)
            with self._lock:
                session.process = proc
                session.status = "active"
            return session
        except Exception as exc:
            with self._lock:
                session.status = f"failed: {exc}"
            raise

    def get_session(self, session_id: str) -> AgentSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def heartbeat(self, session_id: str, controller_id: str) -> AgentSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError("session not found")
            if controller_id and controller_id != session.controller_id:
                raise PermissionError("controller mismatch")
            session.last_heartbeat = time.time()
            if session.status == "active":
                session.expires_at = session.last_heartbeat + 30
            return session

    def disconnect(self, session_id: str) -> AgentSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError("session not found")
        self._stop_session(session, reason="disconnected")
        return session

    def gc_expired(self) -> int:
        now = time.time()
        expired: list[AgentSession] = []

        with self._lock:
            for session in self._sessions.values():
                if session.status in ("active", "pending") and now >= session.expires_at:
                    expired.append(session)

        for session in expired:
            self._stop_session(session, reason="expired")

        return len(expired)

    def _stop_session(self, session: AgentSession, reason: str) -> None:
        proc = session.process
        if proc and proc.poll() is None:
            try:
                # Start-new-session process group: terminate all children if any.
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=4)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        with self._lock:
            session.status = reason
            session.process = None

    def _start_agent_process(self, session: AgentSession) -> subprocess.Popen[Any]:
        repo_root = Path(__file__).resolve().parents[1]
        run_py = repo_root / "run.py"

        cmd = [
            sys.executable,
            str(run_py),
            "--agent",
            "--relay-ws",
            session.relay_ws,
            "--agent-id",
            session.agent_id,
        ]
        if session.token:
            cmd.extend(["--agent-token", session.token])

        logger.info("Starting temp agent for session %s", session.session_id)
        return subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _ask_local_permission(self, session: AgentSession) -> bool:
        if os.environ.get("GESTURELINK_BOOTSTRAP_AUTO_APPROVE", "").strip() == "1":
            return True

        message = (
            "GestureLink: Incoming Remote Control Request\n\n"
            f"Controller: {session.controller_id}\n"
            f"Relay: {session.relay_ws}\n\n"
            "Allow temporary remote control?\n"
            "(If you select Yes, the GestureLink service will also be installed permanently on this PC.)"
        )

        try:
            import tkinter as tk
            from tkinter import messagebox

            result: dict[str, bool] = {"allow": False}

            def _dialog() -> None:
                root = tk.Tk()
                root.withdraw()
                # Ensure it's on top
                root.attributes("-topmost", True)
                allow = messagebox.askyesno("GestureLink Permission", message)
                result["allow"] = bool(allow)
                root.destroy()

            t = threading.Thread(target=_dialog, daemon=True)
            t.start()
            t.join(timeout=60)
            
            if result["allow"]:
                # Trigger automatic installation
                self._trigger_auto_install()
                
            return bool(result["allow"])
        except Exception:
            logger.warning("Permission dialog unavailable; denying by default.")
            return False

    def _trigger_auto_install(self) -> None:
        """Runs the install_service.sh script in the background."""
        repo_root = Path(__file__).resolve().parents[1]
        install_sh = repo_root / "install_service.sh"
        if install_sh.exists():
            logger.info("Triggering automatic service installation...")
            try:
                # Run with sudo if available, or just run normally
                subprocess.Popen(["bash", str(install_sh)], cwd=str(repo_root), start_new_session=True)
            except Exception as e:
                logger.error("Failed to trigger auto-install: %s", e)


def build_bootstrap_app() -> FastAPI:
    app = FastAPI(title="GestureLink Bootstrap", version="0.1.0")
    manager = BootstrapManager()

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/session/request")
    async def session_request(req: SessionRequest) -> JSONResponse:
        try:
            session = manager.request_session(req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to start agent: {exc}") from exc

        return JSONResponse(
            {
                "session_id": session.session_id,
                "status": session.status,
                "agent_id": session.agent_id,
                "expires_at": int(session.expires_at),
            }
        )

    @app.get("/session/{session_id}")
    async def session_get(session_id: str) -> JSONResponse:
        session = manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        return JSONResponse(
            {
                "session_id": session.session_id,
                "status": session.status,
                "controller_id": session.controller_id,
                "agent_id": session.agent_id,
                "expires_at": int(session.expires_at),
                "last_heartbeat": int(session.last_heartbeat),
                "agent_running": bool(session.process and session.process.poll() is None),
            }
        )

    @app.post("/session/{session_id}/heartbeat")
    async def session_heartbeat(session_id: str, hb: SessionHeartbeat) -> JSONResponse:
        try:
            session = manager.heartbeat(session_id=session_id, controller_id=hb.controller_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        return JSONResponse(
            {
                "ok": True,
                "session_id": session.session_id,
                "status": session.status,
                "expires_at": int(session.expires_at),
            }
        )

    @app.post("/session/{session_id}/disconnect")
    async def session_disconnect(session_id: str) -> JSONResponse:
        try:
            session = manager.disconnect(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "session_id": session.session_id, "status": session.status})

    @app.on_event("startup")
    async def startup() -> None:
        async def _gc_loop() -> None:
            while True:
                expired_count = manager.gc_expired()
                if expired_count:
                    logger.info("Expired sessions cleaned: %d", expired_count)
                await asyncio.sleep(2)

        app.state.gc_task = asyncio.create_task(_gc_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = getattr(app.state, "gc_task", None)
        if task:
            task.cancel()

    return app


def run_bootstrap_server(host: str = "0.0.0.0", port: int = 8765) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info("Starting bootstrap server on http://%s:%d", host, port)
    uvicorn.run(build_bootstrap_app(), host=host, port=port, log_level="info", ws="wsproto")
