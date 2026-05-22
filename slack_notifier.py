#!/usr/bin/env python3
"""Send trading analysis summaries to Slack via Incoming Webhook."""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def load_config() -> dict:
    """Load configuration from config.json."""
    config_path = SCRIPT_DIR / "config.json"
    if not config_path.exists():
        print("ERROR: config.json not found. Copy config.example.json and fill in your settings.")
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def send_slack_message(blocks: list[dict], text: str = "Trading Analysis Update"):
    """Send a message to Slack using the webhook from config."""
    config = load_config()
    slack_cfg = config.get("slack", {})

    if not slack_cfg.get("enabled", False):
        print("Slack notifications disabled in config.json")
        return

    webhook_url = slack_cfg.get("webhook_url", "")
    if not webhook_url:
        print("ERROR: No Slack webhook_url in config.json")
        return

    payload = {"text": text, "blocks": blocks}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                print("Slack notification sent successfully")
            else:
                print(f"Slack returned status {resp.status}")
    except urllib.error.URLError as e:
        print(f"Failed to send Slack notification: {e}")


def build_portfolio_blocks(market_data: dict, recommendations: dict) -> list[dict]:
    """Build Slack Block Kit message from analysis data."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    portfolio = market_data.get("portfolio_value", {})
    total_value = market_data.get("total_market_value", 0)
    total_cost = market_data.get("total_cost_basis", 0)
    total_pnl = market_data.get("total_unrealized_pnl", 0)
    pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
    pnl_emoji = ":chart_with_upwards_trend:" if total_pnl >= 0 else ":chart_with_downwards_trend:"

    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"Trading Analysis - {timestamp}"}
    })

    # Portfolio overview
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*Portfolio Overview* {pnl_emoji}\n"
                f">*Market Value:* ${total_value:,.2f}\n"
                f">*Cost Basis:* ${total_cost:,.2f}\n"
                f">*Unrealized P&L:* {'+'if total_pnl>=0 else ''}${total_pnl:,.2f} ({pnl_pct:+.1f}%)"
            ),
        },
    })

    # Top movers
    sorted_positions = sorted(
        portfolio.items(),
        key=lambda x: x[1].get("pnl_pct", 0),
        reverse=True,
    )

    winners = [
        f"`{t}` {p['pnl_pct']:+.1f}%"
        for t, p in sorted_positions[:3]
        if p.get("pnl_pct", 0) > 0
    ]
    losers = [
        f"`{t}` {p['pnl_pct']:+.1f}%"
        for t, p in sorted_positions[-3:]
        if p.get("pnl_pct", 0) < 0
    ]

    movers_text = ""
    if winners:
        movers_text += f":green_circle: *Winners:* {', '.join(winners)}\n"
    if losers:
        movers_text += f":red_circle: *Losers:* {', '.join(losers)}"

    if movers_text:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": movers_text},
        })

    # Position details
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*Positions Summary*",
        },
    })

    # Build positions table in chunks (Slack has 3000 char limit per text block)
    pos_lines = []
    for ticker in sorted(portfolio.keys()):
        p = portfolio[ticker]
        price = p.get("current_price", 0)
        pnl = p.get("unrealized_pnl", 0)
        pnl_p = p.get("pnl_pct", 0)
        shares = p.get("shares", 0)
        dot = ":green_circle:" if pnl >= 0 else ":red_circle:"
        pos_lines.append(
            f"{dot} `{ticker:<6}` {shares:>5} shares | "
            f"${price:>9.2f} | P&L: {'+'if pnl>=0 else ''}${pnl:,.0f} ({pnl_p:+.1f}%)"
        )

    # Split into chunks of ~10 positions per block
    for i in range(0, len(pos_lines), 10):
        chunk = "\n".join(pos_lines[i : i + 10])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })

    # Options recommendations
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": "Options Recommendations (~$500 Target)"},
    })

    # Top covered calls
    best_cc = recommendations.get("covered_calls", [])[:5]
    if best_cc:
        cc_text = "*Top Covered Calls:*\n"
        for i, cc in enumerate(best_cc, 1):
            premium = cc.get("total_premium", 0)
            otm = cc.get("otm_pct", 0)
            strike = cc.get("strike", 0)
            ticker = cc.get("ticker", "")
            exp = cc.get("expiration", "")
            avg_cost = cc.get("avg_cost", 0)

            safe = ":white_check_mark:" if strike >= avg_cost else ":warning:"
            cc_text += (
                f"{safe} `{i}.` Sell {cc.get('contracts',1)} {ticker} {exp} "
                f"${strike:.2f}C | *${premium:.0f}* premium | {otm:.1f}% OTM"
            )
            if strike < avg_cost:
                cc_text += f" | _Strike < cost ${avg_cost:.2f}_"
            cc_text += "\n"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": cc_text},
        })

    # Top CSPs
    best_csp = recommendations.get("cash_secured_puts", [])[:3]
    if best_csp:
        csp_text = "*Top Cash-Secured Puts:*\n"
        for i, csp in enumerate(best_csp, 1):
            premium = csp.get("premium", 0)
            strike = csp.get("strike", 0)
            ticker = csp.get("ticker", "")
            exp = csp.get("expiration", "")
            cash_req = csp.get("cash_required", 0)
            roc = csp.get("return_on_capital", 0)

            csp_text += (
                f":moneybag: `{i}.` Sell 1 {ticker} {exp} "
                f"${strike:.2f}P | *${premium:.0f}* premium | "
                f"${cash_req:,.0f} cash | {roc:.1f}% ROC\n"
            )

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": csp_text},
        })

    # Best combos targeting $500
    combos = recommendations.get("target_500_combos", [])[:3]
    if combos:
        combo_text = "*Recommended Combos (~$500):*\n"
        for i, combo in enumerate(combos, 1):
            combo_text += f":dart: `{i}.` {combo['name']} → *${combo['total_premium']:.0f}*\n"
            for trade in combo.get("trades", []):
                premium = trade.get("total_premium", trade.get("premium", 0))
                combo_text += f"     ↳ {trade['action']} = ${premium:.0f}\n"
            if combo.get("total_risk_summary"):
                combo_text += f"     :warning: {combo['total_risk_summary'][0][:100]}\n"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": combo_text},
        })

    # Risk alerts
    blocks.append({"type": "divider"})
    risk_items = []
    for ticker, p in portfolio.items():
        pnl_p = p.get("pnl_pct", 0)
        if pnl_p < -30:
            risk_items.append(f":rotating_light: `{ticker}` down {pnl_p:.1f}% from cost basis")

    if risk_items:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Risk Alerts:*\n" + "\n".join(risk_items[:5]),
            },
        })

    # Footer
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f":robot_face: Auto-generated by Trading Agent | "
                    f"Next run in ~1 hour | "
                    f"_Not financial advice_"
                ),
            }
        ],
    })

    return blocks


def main():
    # Load data files
    market_path = SCRIPT_DIR / "market_data.json"
    recs_path = SCRIPT_DIR / "recommendations.json"

    if not market_path.exists() or not recs_path.exists():
        print("ERROR: Run the analysis pipeline first (run_analysis.py)")
        sys.exit(1)

    with open(market_path) as f:
        market_data = json.load(f)
    with open(recs_path) as f:
        recommendations = json.load(f)

    blocks = build_portfolio_blocks(market_data, recommendations)
    send_slack_message(blocks)


if __name__ == "__main__":
    main()
