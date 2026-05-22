#!/usr/bin/env python3
"""
Trading Agent — entry point.

Registers with the agent-orchestrator and waits for task requests.
Exposes capabilities for Robinhood portfolio analysis, options recommendations,
and notifications.

Usage:
    python main.py [--orchestrator-url http://localhost:8000]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)

# ── Banner ────────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════╗
║              Trading Agent  v1.0.0                   ║
║   Robinhood Portfolio Analysis & Options Advisor     ║
╚══════════════════════════════════════════════════════╝

Capabilities exposed:
  • analyze_portfolio          — full pipeline + optional Slack/email
  • get_portfolio_positions     — parse Robinhood CSV (fast, no network)
  • get_options_recommendations — live market data + CC/CSP analysis

Notes:
  • CSV input: absolute path to Robinhood account history export
  • get_options_recommendations may take up to 3 min (yfinance)
  • Place config.json in this directory for Slack/email notifications
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trading Agent — connects to the agent-orchestrator "
                    "and provides Robinhood portfolio analysis."
    )
    parser.add_argument(
        "--orchestrator-url",
        default=os.environ.get("ORCHESTRATOR_URL", "http://localhost:8000"),
        help="Orchestrator base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: INFO)",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    print(BANNER)
    print(f"  Orchestrator: {args.orchestrator_url}\n")

    from orchestrator_client import OrchestratorClient

    async def _run() -> None:
        client = OrchestratorClient(orchestrator_url=args.orchestrator_url)
        await client.start()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
