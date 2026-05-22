"""
pipeline.py — Runs the trading analysis pipeline steps as subprocesses.

Each step script (portfolio_parser.py, market_data.py, options_advisor.py,
slack_notifier.py, email_notifier.py) is invoked with a temporary workspace
directory as cwd, mirroring the original run_analysis.py approach but
returning structured dicts instead of writing global JSON files.
"""
from __future__ import annotations

import json
import logging
import pathlib
import shutil
import subprocess
import sys
import tempfile

logger = logging.getLogger(__name__)

AGENT_DIR = pathlib.Path(__file__).parent


def _run_script(name: str, workspace: pathlib.Path, timeout: int = 300) -> str:
    """Run a pipeline script with workspace as cwd. Returns combined stdout."""
    result = subprocess.run(
        [sys.executable, str(AGENT_DIR / name)],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.stderr:
        logger.debug("%s stderr: %s", name, result.stderr[-2000:])
    if result.returncode != 0:
        raise RuntimeError(
            f"{name} failed (exit {result.returncode}):\n"
            f"STDERR: {result.stderr[-1500:]}"
        )
    return result.stdout


def _setup_workspace(csv_path: str) -> pathlib.Path:
    """Create a temp workspace, stage the CSV and config."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix="trading-agent-"))
    shutil.copy2(csv_path, ws / "robinhood_report.csv")

    # Copy config.json so notifiers can find their credentials
    cfg = AGENT_DIR / "config.json"
    if cfg.exists():
        shutil.copy2(cfg, ws / "config.json")
    else:
        logger.warning("config.json not found in agent dir — notifications will be skipped")

    return ws


def _read_json(workspace: pathlib.Path, filename: str) -> dict:
    path = workspace / filename
    if not path.exists():
        raise RuntimeError(f"Expected output file not found: {filename}")
    return json.loads(path.read_text())


def _summarise_recommendations(rec: dict) -> dict:
    """Return a concise top-3 summary from the full recommendations dict."""
    # Key names may vary; try common variants
    def _pick(d: dict, *keys):
        for k in keys:
            if k in d:
                return d[k]
        # Fall back to first list-valued key
        for v in d.values():
            if isinstance(v, list):
                return v
        return []

    ccs   = _pick(rec, "top_covered_calls", "covered_calls", "cc_recommendations")[:3]
    csps  = _pick(rec, "top_cash_secured_puts", "cash_secured_puts", "csp_recommendations")[:3]
    combos = _pick(rec, "target_500_combos", "combos", "recommended_combos")[:3]
    return {
        "top_covered_calls":      ccs,
        "top_cash_secured_puts":  csps,
        "recommended_combos":     combos,
    }


# ── Public pipeline functions ──────────────────────────────────────────────────

def run_portfolio_parse(csv_path: str) -> dict:
    """
    Step 1 only: parse the Robinhood CSV and return portfolio_summary dict.
    Fast — no network calls.
    """
    ws = _setup_workspace(csv_path)
    try:
        logger.info("Running portfolio_parser.py …")
        _run_script("portfolio_parser.py", ws, timeout=30)
        return _read_json(ws, "portfolio_summary.json")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def run_options_analysis(csv_path: str) -> dict:
    """
    Steps 1-3: parse CSV → fetch market data → generate recommendations.
    No notifications sent. May take up to 3 minutes (yfinance network calls).
    """
    ws = _setup_workspace(csv_path)
    try:
        logger.info("Running portfolio_parser.py …")
        _run_script("portfolio_parser.py", ws, timeout=30)
        portfolio = _read_json(ws, "portfolio_summary.json")
        tickers   = portfolio.get("tickers_held", [])

        logger.info("Running market_data.py for %d ticker(s) …", len(tickers))
        _run_script("market_data.py", ws, timeout=240)

        logger.info("Running options_advisor.py …")
        _run_script("options_advisor.py", ws, timeout=60)
        recommendations = _read_json(ws, "recommendations.json")

        # Extract all lists (full, not truncated)
        def _pick(d: dict, *keys):
            for k in keys:
                if k in d:
                    return d[k]
            return []

        return {
            "tickers_analyzed":    tickers,
            "covered_calls":       _pick(recommendations, "top_covered_calls",     "covered_calls"),
            "cash_secured_puts":   _pick(recommendations, "top_cash_secured_puts", "cash_secured_puts"),
            "target_combos":       _pick(recommendations, "target_500_combos",     "combos",
                                         "recommended_combos"),
        }
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def run_full_analysis(
    csv_path: str,
    send_slack: bool = False,
    send_email: bool = False,
) -> dict:
    """
    Full pipeline: parse → market data → recommendations → (optional) notifications.
    Returns a concise summary dict.
    """
    ws = _setup_workspace(csv_path)
    try:
        logger.info("Running portfolio_parser.py …")
        _run_script("portfolio_parser.py", ws, timeout=30)
        portfolio = _read_json(ws, "portfolio_summary.json")
        tickers   = portfolio.get("tickers_held", [])

        logger.info("Running market_data.py for %d ticker(s) …", len(tickers))
        _run_script("market_data.py", ws, timeout=240)

        logger.info("Running options_advisor.py …")
        _run_script("options_advisor.py", ws, timeout=60)
        recommendations = _read_json(ws, "recommendations.json")

        notifications_sent: dict[str, bool | str] = {}

        if send_slack:
            if not (ws / "config.json").exists():
                notifications_sent["slack"] = "skipped — config.json missing"
            else:
                try:
                    logger.info("Running slack_notifier.py …")
                    _run_script("slack_notifier.py", ws, timeout=30)
                    notifications_sent["slack"] = True
                except Exception as exc:
                    logger.warning("Slack notification failed: %s", exc)
                    notifications_sent["slack"] = f"failed: {exc}"

        if send_email:
            if not (ws / "config.json").exists():
                notifications_sent["email"] = "skipped — config.json missing"
            else:
                try:
                    logger.info("Running email_notifier.py …")
                    _run_script("email_notifier.py", ws, timeout=30)
                    notifications_sent["email"] = True
                except Exception as exc:
                    logger.warning("Email notification failed: %s", exc)
                    notifications_sent["email"] = f"failed: {exc}"

        summary = _summarise_recommendations(recommendations)
        return {
            "total_positions":       len(portfolio.get("positions", {})),
            "tickers":               tickers,
            "estimated_cash_balance": portfolio.get("estimated_cash_balance", 0),
            **summary,
            "notifications_sent":    notifications_sent,
        }
    finally:
        shutil.rmtree(ws, ignore_errors=True)
