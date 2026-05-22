"""
orchestrator_client.py — Connects the trading-agent to the agent-orchestrator.

Protocol checklist:
  ✓ POST /api/v1/agents/register — capability schemas + stable identity
  ✓ WS /ws/{agent_id} — persistent connection with exponential-backoff reconnect
  ✓ Close code 4004 — re-register then reconnect
  ✓ Heartbeat every 15 s — status, current_load, active_tasks, metrics
  ✓ task_request → capability handler → task_response
  ✓ status_update sent on task start / finish (available ↔ busy)
  ✓ Status machine: starting → available → busy → draining → offline
  ✓ Metrics: tasks_completed, tasks_failed, avg_response_time_ms, uptime_seconds
  ✓ Graceful shutdown on SIGINT/SIGTERM: draining → wait → DELETE → WS close
  ✓ settings_push — hot-update config from orchestrator common settings
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets
import websockets.exceptions

import pipeline

# ── Stable agent identity ───────────────────────────────────────────────────

_AGENT_ID_FILE = Path(".agent_id")


def _stable_agent_id() -> str:
    if _AGENT_ID_FILE.exists():
        return _AGENT_ID_FILE.read_text().strip()
    new_id = str(uuid.uuid4())
    _AGENT_ID_FILE.write_text(new_id)
    logger.info("Generated new stable agent ID: %s", new_id)
    return new_id


logger = logging.getLogger(__name__)

# ── Agent identity ──────────────────────────────────────────────────────────

AGENT_NAME        = "trading-agent"
AGENT_VERSION     = "1.0.0"
AGENT_DESCRIPTION = (
    "Analyses a Robinhood portfolio to identify covered call and cash-secured put "
    "opportunities targeting ~$500 monthly premium. Fetches live market data, "
    "computes technical indicators (RSI, support/resistance, trend), and ranks "
    "options strategies. Optionally sends results via Slack and email."
)

REGISTRATION_PAYLOAD: dict = {
    "name":        AGENT_NAME,
    "description": AGENT_DESCRIPTION,
    "version":     AGENT_VERSION,
    "capabilities": [
        {
            "name": "analyze_portfolio",
            "description": (
                "Run the full trading analysis pipeline: parse a Robinhood CSV export, "
                "fetch live market data and options chains via yfinance, generate covered "
                "call and cash-secured put recommendations, and optionally send Slack/email "
                "notifications. Returns a concise summary with top recommendations. "
                "Allow up to 5 minutes (timeout_ms: 300000) for large portfolios."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "csv_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the Robinhood account history CSV export. "
                            "Optional if trading_csv_path is configured in dashboard settings "
                            "or supplied by the filesystem-agent."
                        ),
                    },
                    "send_slack": {
                        "type": "boolean",
                        "description": "Send a Slack notification with results (requires config.json). Default: false.",
                    },
                    "send_email": {
                        "type": "boolean",
                        "description": "Send an email notification with results (requires config.json). Default: false.",
                    },
                },
                "required": [],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "total_positions":        {"type": "integer"},
                    "tickers":                {"type": "array", "items": {"type": "string"}},
                    "estimated_cash_balance": {"type": "number"},
                    "top_covered_calls":      {"type": "array"},
                    "top_cash_secured_puts":  {"type": "array"},
                    "recommended_combos":     {"type": "array"},
                    "notifications_sent":     {"type": "object"},
                },
            },
            "tags": ["trading", "portfolio", "options", "notifications", "stock", "invest", "market", "finance", "analyze", "report", "robinhood", "covered call"],
        },
        {
            "name": "get_portfolio_positions",
            "description": (
                "Parse a Robinhood account history CSV and return current positions: "
                "shares held, average cost basis, covered-call lot eligibility, "
                "estimated cash balance, and historical options income. Fast — no network calls."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "csv_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the Robinhood account history CSV export. "
                            "Optional if trading_csv_path is configured in dashboard settings "
                            "or supplied by the filesystem-agent."
                        ),
                    },
                },
                "required": [],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "positions":                {"type": "object"},
                    "tickers_held":             {"type": "array", "items": {"type": "string"}},
                    "positions_with_100_lots":  {"type": "array", "items": {"type": "string"}},
                    "estimated_cash_balance":   {"type": "number"},
                    "options_income_history":   {"type": "object"},
                },
            },
            "tags": ["trading", "portfolio", "stock", "holdings", "shares", "invest", "finance", "positions", "account", "robinhood"],
        },
        {
            "name": "get_options_recommendations",
            "description": (
                "Fetch live market data and generate covered call and cash-secured put "
                "recommendations for a Robinhood portfolio. Does not send notifications. "
                "Returns full ranked lists. May take up to 3 minutes (yfinance calls). "
                "Recommend timeout_ms: 300000."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "csv_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the Robinhood account history CSV export. "
                            "Optional if trading_csv_path is configured in dashboard settings "
                            "or supplied by the filesystem-agent."
                        ),
                    },
                },
                "required": [],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "tickers_analyzed":  {"type": "array", "items": {"type": "string"}},
                    "covered_calls":     {"type": "array"},
                    "cash_secured_puts": {"type": "array"},
                    "target_combos":     {"type": "array"},
                },
            },
            "tags": ["trading", "options", "analysis", "calls", "puts", "strategy", "recommendations", "yield", "stock", "finance", "invest"],
        },
    ],
    "tags": ["trading", "portfolio", "options-analysis", "robinhood", "yfinance"],
    "metadata": {
        "language":     "python",
        "data_source":  "yfinance + Robinhood CSV",
        "strategies":   ["covered_calls", "cash_secured_puts"],
        "note":         "Requires Robinhood CSV export and internet access for yfinance",
    },
    "required_settings": [
        {
            "key": "trading_csv_path",
            "label": "Robinhood CSV Path",
            "type": "string",
            "required": False,
            "default": "",
            "description": (
                "Absolute path to the Robinhood account history CSV export file. "
                "When set, capabilities no longer require csv_path in input_data — "
                "the configured path is used automatically. "
                "Can also be supplied dynamically via the filesystem-agent."
            ),
        },
    ],
}

# ── Constants ───────────────────────────────────────────────────────────────

HEARTBEAT_INTERVAL_S: int   = 15
MAX_BACKOFF_S:        int   = 60
DRAIN_TIMEOUT_S:      int   = 30


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _envelope(
    sender_id: str,
    msg_type: str,
    payload: dict,
    recipient_id: str | None = None,
    correlation_id: str | None = None,
    msg_id: str | None = None,
) -> str:
    return json.dumps({
        "id":             msg_id or str(uuid.uuid4()),
        "type":           msg_type,
        "sender_id":      sender_id,
        "recipient_id":   recipient_id,
        "payload":        payload,
        "timestamp":      _now_iso(),
        "correlation_id": correlation_id,
    })


# ── Main client ─────────────────────────────────────────────────────────────

class OrchestratorClient:
    """Registers the trading-agent with the orchestrator and handles task requests."""

    def __init__(self, orchestrator_url: str = "http://localhost:8000") -> None:
        self._base = orchestrator_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=15)

        self._agent_id: str = ""
        self._ws_url:   str = ""

        self._status:           str   = "starting"
        self._active_tasks:     int   = 0
        self._tasks_completed:  int   = 0
        self._tasks_failed:     int   = 0
        self._total_duration_ms: float = 0.0
        self._start_time:       float = time.monotonic()
        self._shutting_down:    bool  = False
        self._current_ws:       Any   = None

        self._pending_responses: dict[str, asyncio.Future] = {}

        # Configured path to the Robinhood CSV — set via dashboard settings or
        # overridden per-call via input_data.csv_path.
        self._csv_path: str = ""

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))

        await self._register()
        await self._connect_loop()

    # ── Registration ─────────────────────────────────────────────────────────

    async def _register(self) -> None:
        url = f"{self._base}/api/v1/agents/register"
        logger.info("Registering with orchestrator at %s …", url)
        payload = {**REGISTRATION_PAYLOAD, "id": _stable_agent_id()}
        resp = await self._http.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        self._agent_id = data["agent_id"]
        self._ws_url   = data["ws_url"]
        # Merge orchestrator-wide settings then agent-specific settings so that
        # agent-level overrides (e.g. trading_csv_path) take precedence.
        merged = {**data.get("common_settings", {}), **data.get("agent_settings", {})}
        self._apply_settings(merged)
        logger.info("Registered — agent_id=%s  csv_path=%r", self._agent_id, self._csv_path)

    # ── WebSocket connection loop ─────────────────────────────────────────────

    async def _connect_loop(self) -> None:
        backoff = 1.0
        while not self._shutting_down:
            try:
                logger.info("Connecting to %s …", self._ws_url)
                async with websockets.connect(self._ws_url) as ws:
                    backoff = 1.0
                    await self._run_session(ws)

            except websockets.exceptions.ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None
                if code == 4004:
                    logger.warning("Unknown agent_id (4004) — re-registering …")
                    try:
                        await self._register()
                    except Exception as reg_exc:
                        logger.error("Re-registration failed: %s", reg_exc)
                elif code == 4003:
                    logger.info("Agent is disabled by orchestrator (4003) — will retry so dashboard enable can restore connection")
                    backoff = max(backoff, 10.0)
                elif self._shutting_down:
                    break
                else:
                    logger.warning("WS closed (code=%s) — retry in %.0fs", code, backoff)

            except (OSError, Exception) as exc:
                if self._shutting_down:
                    break
                logger.warning("WS error (%s) — retry in %.0fs", exc, backoff)

            if not self._shutting_down:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def _run_session(self, ws) -> None:
        self._current_ws = ws
        self._status = "available"
        logger.info("WebSocket session active — status: available")
        try:
            await asyncio.gather(
                self._heartbeat_loop(ws),
                self._recv_loop(ws),
            )
        finally:
            self._current_ws = None
            self._status = "offline"
            for fut in list(self._pending_responses.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("WebSocket session ended"))

    # ── Heartbeat ────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self, ws) -> None:
        while True:
            await self._ws_send(ws, self._msg(
                "heartbeat",
                {
                    "status":                self._status,
                    "current_load":          min(self._active_tasks / 4, 1.0),
                    "active_tasks":          self._active_tasks,
                    "expected_wait_time_ms": 0,
                    "metrics":               self._metrics(),
                },
            ))
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

    # ── Receive loop ──────────────────────────────────────────────────────────

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON WS frame ignored")
                continue

            mtype  = msg.get("type", "?")
            sender = msg.get("sender_id", "?")
            _lvl = logging.DEBUG if mtype in ("agent_registered", "agent_offline", "heartbeat_ack", "settings_push") else logging.INFO
            logger.log(_lvl, "← [%s] from=%s  %s", mtype, sender,
                        json.dumps(msg.get("payload", {}))[:200])
            await self._dispatch(ws, msg)

    async def _dispatch(self, ws, msg: dict) -> None:
        mtype   = msg.get("type", "")
        payload = msg.get("payload", {})

        if mtype == "task_request":
            asyncio.create_task(self._handle_incoming_task(ws, msg))

        elif mtype == "task_response":
            corr = msg.get("correlation_id")
            if corr and corr in self._pending_responses:
                fut = self._pending_responses.pop(corr)
                if not fut.done():
                    fut.set_result(payload)

        elif mtype == "settings_push":
            settings = payload.get("settings", payload)
            self._apply_settings(settings)
            logger.info("Settings pushed: %d key(s), csv_path=%r", len(settings), self._csv_path)

        elif mtype == "agent_registered":
            logger.info("Peer joined: %s", payload.get("agent_id"))

        elif mtype == "agent_offline":
            logger.info("Peer left: %s (reason: %s)",
                        payload.get("agent_id"), payload.get("reason"))

        elif mtype == "error":
            logger.error("Orchestrator error [%s]: %s",
                         payload.get("code"), payload.get("detail"))
            original_id = payload.get("original_message_id")
            if original_id and original_id in self._pending_responses:
                fut = self._pending_responses.pop(original_id)
                if not fut.done():
                    fut.set_exception(RuntimeError(
                        f"[{payload.get('code')}] {payload.get('detail')}"
                    ))

        elif mtype in ("broadcast", "discovery_response"):
            logger.debug("Unhandled (ignored): %r", mtype)

        else:
            logger.debug("Unknown message type: %r", mtype)

    # ── Incoming task handling ─────────────────────────────────────────────────

    async def _handle_incoming_task(self, ws, msg: dict) -> None:
        req_id     = msg.get("id")
        sender_id  = msg.get("sender_id")
        payload    = msg.get("payload", {})
        capability = payload.get("capability")
        input_data = payload.get("input_data", {})

        self._active_tasks += 1
        self._status = "busy"

        t0 = time.monotonic()
        try:
            if capability == "analyze_portfolio":
                output, error = await self._cap_analyze_portfolio(input_data)
            elif capability == "get_portfolio_positions":
                output, error = await self._cap_get_portfolio_positions(input_data)
            elif capability == "get_options_recommendations":
                output, error = await self._cap_get_options_recommendations(input_data)
            else:
                output, error = None, f"Unknown capability: {capability!r}"

            duration_ms = (time.monotonic() - t0) * 1000

            if error:
                self._tasks_failed += 1
                await self._ws_send(ws, self._msg(
                    "task_response",
                    {"success": False, "error": error, "duration_ms": round(duration_ms, 1)},
                    recipient_id=sender_id,
                    correlation_id=req_id,
                ))
            else:
                self._tasks_completed += 1
                self._total_duration_ms += duration_ms
                await self._ws_send(ws, self._msg(
                    "task_response",
                    {"success": True, "output_data": output, "duration_ms": round(duration_ms, 1)},
                    recipient_id=sender_id,
                    correlation_id=req_id,
                ))

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            self._tasks_failed += 1
            logger.exception("Unhandled exception in capability %r", capability)
            await self._ws_send(ws, self._msg(
                "task_response",
                {"success": False, "error": str(exc), "duration_ms": round(duration_ms, 1)},
                recipient_id=sender_id,
                correlation_id=req_id,
            ))

        finally:
            self._active_tasks = max(0, self._active_tasks - 1)
            self._status = "draining" if self._shutting_down else (
                "busy" if self._active_tasks else "available"
            )
            await self._send_status_update(ws)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _apply_settings(self, settings: dict) -> None:
        """Apply a settings dict, updating live config values."""
        path = settings.get("trading_csv_path", "")
        if path and isinstance(path, str):
            self._csv_path = path.strip()

    def _resolve_csv_path(self, input_data: dict) -> str:
        """Return the CSV path to use for this call.

        Priority:
          1. input_data.csv_path  — supplied by the planner / filesystem-agent
          2. self._csv_path       — configured via orchestrator dashboard settings
        """
        return (input_data.get("csv_path") or "").strip() or self._csv_path

    # ── Capability handlers ───────────────────────────────────────────────────

    async def _cap_analyze_portfolio(
        self, input_data: dict
    ) -> tuple[dict | None, str | None]:
        csv_path = self._resolve_csv_path(input_data)
        if not csv_path:
            return None, (
                "No CSV path provided. Either pass csv_path in input_data or "
                "configure trading_csv_path in the orchestrator dashboard settings."
            )
        import os
        if not os.path.isfile(csv_path):
            return None, f"CSV file not found: {csv_path}"

        send_slack = bool(input_data.get("send_slack", False))
        send_email = bool(input_data.get("send_email", False))

        logger.info("analyze_portfolio: csv=%s slack=%s email=%s", csv_path, send_slack, send_email)
        try:
            result = await asyncio.to_thread(
                pipeline.run_full_analysis, csv_path, send_slack, send_email
            )
            return result, None
        except Exception as exc:
            return None, str(exc)

    async def _cap_get_portfolio_positions(
        self, input_data: dict
    ) -> tuple[dict | None, str | None]:
        csv_path = self._resolve_csv_path(input_data)
        if not csv_path:
            return None, (
                "No CSV path provided. Either pass csv_path in input_data or "
                "configure trading_csv_path in the orchestrator dashboard settings."
            )
        import os
        if not os.path.isfile(csv_path):
            return None, f"CSV file not found: {csv_path}"

        logger.info("get_portfolio_positions: csv=%s", csv_path)
        try:
            result = await asyncio.to_thread(pipeline.run_portfolio_parse, csv_path)
            return result, None
        except Exception as exc:
            return None, str(exc)

    async def _cap_get_options_recommendations(
        self, input_data: dict
    ) -> tuple[dict | None, str | None]:
        csv_path = self._resolve_csv_path(input_data)
        if not csv_path:
            return None, (
                "No CSV path provided. Either pass csv_path in input_data or "
                "configure trading_csv_path in the orchestrator dashboard settings."
            )
        import os
        if not os.path.isfile(csv_path):
            return None, f"CSV file not found: {csv_path}"

        logger.info("get_options_recommendations: csv=%s", csv_path)
        try:
            result = await asyncio.to_thread(pipeline.run_options_analysis, csv_path)
            return result, None
        except Exception as exc:
            return None, str(exc)

    # ── Status update ─────────────────────────────────────────────────────────

    async def _send_status_update(self, ws) -> None:
        await self._ws_send(ws, self._msg(
            "status_update",
            {
                "status":       self._status,
                "current_load": min(self._active_tasks / 4, 1.0),
                "active_tasks": self._active_tasks,
                "metrics":      self._metrics(),
            },
        ))

    # ── Graceful shutdown ─────────────────────────────────────────────────────

    async def _graceful_shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("Shutdown signal — draining …")
        self._status = "draining"

        deadline = time.monotonic() + DRAIN_TIMEOUT_S
        while self._active_tasks > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.5)

        if self._active_tasks:
            logger.warning("Drain timeout — %d task(s) still active", self._active_tasks)

        if self._agent_id:
            try:
                await self._http.delete(f"{self._base}/api/v1/agents/{self._agent_id}")
                logger.info("Deregistered from orchestrator.")
            except Exception as exc:
                logger.warning("Failed to deregister: %s", exc)

        await self._http.aclose()
        logger.info("Shutdown complete.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _ws_send(self, ws, msg_str: str) -> None:
        msg   = json.loads(msg_str)
        mtype = msg.get("type", "?")
        log   = logger.debug if mtype in ("heartbeat", "status_update") else logger.info
        log("→ [%s] to=%s  %s",
            mtype,
            msg.get("recipient_id") or "orchestrator",
            json.dumps(msg.get("payload", {}))[:200])
        try:
            await ws.send(msg_str)
        except websockets.exceptions.ConnectionClosed:
            raise  # propagate → heartbeat loop exits → asyncio.gather raises → reconnect
        except Exception as exc:
            logger.warning("WS send failed: %s", exc)

    def _msg(
        self,
        msg_type: str,
        payload: dict,
        recipient_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        return _envelope(self._agent_id, msg_type, payload, recipient_id, correlation_id)

    def _metrics(self) -> dict:
        n = self._tasks_completed + self._tasks_failed
        return {
            "tasks_completed":      self._tasks_completed,
            "tasks_failed":         self._tasks_failed,
            "avg_response_time_ms": round(self._total_duration_ms / n, 1) if n else 0.0,
            "uptime_seconds":       round(time.monotonic() - self._start_time, 1),
        }
