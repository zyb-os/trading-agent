#!/usr/bin/env python3
"""Send trading analysis summary as a formatted HTML email."""

import json
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def load_config() -> dict:
    """Load email configuration from config.json."""
    config_path = SCRIPT_DIR / "config.json"
    if not config_path.exists():
        print("ERROR: config.json not found. Copy config.example.json and fill in your settings.")
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def build_changes_summary(current: dict, previous: dict) -> str:
    """Build an HTML section summarizing what changed since the last run."""
    prev_ts = previous.get("timestamp", "")
    try:
        prev_time = datetime.fromisoformat(prev_ts).strftime("%H:%M")
    except Exception:
        prev_time = "earlier"

    changes = []

    # Compare top covered calls
    curr_cc = current.get("covered_calls", [])[:5]
    prev_cc = previous.get("covered_calls", [])[:5]
    curr_cc_actions = {c["action"] for c in curr_cc}
    prev_cc_actions = {c["action"] for c in prev_cc}

    new_cc = curr_cc_actions - prev_cc_actions
    dropped_cc = prev_cc_actions - curr_cc_actions

    for action in new_cc:
        rec = next((c for c in curr_cc if c["action"] == action), None)
        if rec:
            changes.append(("NEW CC", f"{action} &mdash; ${rec.get('total_premium', 0):,.0f} premium", "#22c55e"))
    for action in dropped_cc:
        changes.append(("DROPPED CC", f"{action} no longer in top 5", "#6b7280"))

    # Compare top CSPs
    curr_csp = current.get("cash_secured_puts", [])[:5]
    prev_csp = previous.get("cash_secured_puts", [])[:5]
    curr_csp_actions = {c["action"] for c in curr_csp}
    prev_csp_actions = {c["action"] for c in prev_csp}

    new_csp = curr_csp_actions - prev_csp_actions
    dropped_csp = prev_csp_actions - curr_csp_actions

    for action in new_csp:
        rec = next((c for c in curr_csp if c["action"] == action), None)
        if rec:
            changes.append(("NEW CSP", f"{action} &mdash; ${rec.get('premium', 0):,.0f} premium", "#60a5fa"))
    for action in dropped_csp:
        changes.append(("DROPPED CSP", f"{action} no longer in top 5", "#6b7280"))

    # Compare premium changes for trades that stayed
    for cc in curr_cc:
        prev_match = next((p for p in prev_cc if p["action"] == cc["action"]), None)
        if prev_match:
            curr_prem = cc.get("total_premium", 0)
            prev_prem = prev_match.get("total_premium", 0)
            diff = curr_prem - prev_prem
            if abs(diff) >= 5:
                arrow = "&#9650;" if diff > 0 else "&#9660;"
                color = "#22c55e" if diff > 0 else "#ef4444"
                changes.append(("PREMIUM", f"{cc['action']}: ${prev_prem:,.0f} &#8594; ${curr_prem:,.0f} ({arrow} ${abs(diff):,.0f})", color))

    for csp in curr_csp:
        prev_match = next((p for p in prev_csp if p["action"] == csp["action"]), None)
        if prev_match:
            curr_prem = csp.get("premium", 0)
            prev_prem = prev_match.get("premium", 0)
            diff = curr_prem - prev_prem
            if abs(diff) >= 5:
                arrow = "&#9650;" if diff > 0 else "&#9660;"
                color = "#22c55e" if diff > 0 else "#ef4444"
                changes.append(("PREMIUM", f"{csp['action']}: ${prev_prem:,.0f} &#8594; ${curr_prem:,.0f} ({arrow} ${abs(diff):,.0f})", color))

    # Compare top combo
    curr_combos = current.get("target_500_combos", [])[:3]
    prev_combos = previous.get("target_500_combos", [])[:3]
    curr_combo_names = {c["name"] for c in curr_combos}
    prev_combo_names = {c["name"] for c in prev_combos}

    for name in curr_combo_names - prev_combo_names:
        combo = next((c for c in curr_combos if c["name"] == name), None)
        if combo:
            changes.append(("NEW COMBO", f"{name} &mdash; ${combo.get('total_premium', 0):,.0f}", "#a78bfa"))

    if not changes:
        return f"""
<tr>
<td style="padding:20px 30px 8px;">
  <div style="background-color:#263040; border-radius:8px; padding:14px 18px; border-left:3px solid #22c55e;">
    <span style="color:#22c55e; font-size:14px; font-weight:600;">&#9889; No Changes Since {prev_time}</span>
    <p style="margin:6px 0 0; color:#94a3b8; font-size:12px;">Top recommendations, premiums, and combo strategies are unchanged from the previous run.</p>
  </div>
</td>
</tr>"""

    html = f"""
<tr>
<td style="padding:20px 30px 8px;">
  <h2 style="margin:0 0 12px; color:#fbbf24; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">&#9889; Changes Since {prev_time}</h2>"""

    for tag, desc, color in changes:
        html += f"""
  <div style="padding:5px 0; font-size:12px;">
    <span style="display:inline-block; background-color:{color}22; color:{color}; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600; margin-right:8px;">{tag}</span>
    <span style="color:#e2e8f0;">{desc}</span>
  </div>"""

    html += """
</td>
</tr>"""
    return html


def build_html_email(market_data: dict, recommendations: dict, previous_recs: dict | None = None) -> str:
    """Build a formatted HTML email from analysis data."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    portfolio = market_data.get("portfolio_value", {})
    historical = market_data.get("historical_analysis", {})
    total_value = market_data.get("total_market_value", 0)
    total_cost = market_data.get("total_cost_basis", 0)
    total_pnl = market_data.get("total_unrealized_pnl", 0)
    pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
    pnl_color = "#22c55e" if total_pnl >= 0 else "#ef4444"

    # Build changes summary if previous data exists
    changes_html = ""
    if previous_recs:
        changes_html = build_changes_summary(recommendations, previous_recs)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background-color:#111827; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#111827; padding:20px;">
<tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" style="background-color:#1f2937; border-radius:12px; overflow:hidden;">

<!-- Header -->
<tr>
<td style="background: linear-gradient(135deg, #1e3a5f, #1e293b); padding:24px 30px;">
  <h1 style="margin:0; color:#f8fafc; font-size:22px; font-weight:700;">Trading Analysis Report</h1>
  <p style="margin:6px 0 0; color:#94a3b8; font-size:13px;">{timestamp}</p>
</td>
</tr>

{changes_html}

<!-- Portfolio Overview -->
<tr>
<td style="padding:24px 30px;">
  <h2 style="margin:0 0 16px; color:#e2e8f0; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Portfolio Overview</h2>
  <table width="100%" cellpadding="8" cellspacing="0">
    <tr>
      <td style="color:#94a3b8; font-size:13px;">Market Value</td>
      <td style="color:#f8fafc; font-size:15px; font-weight:600; text-align:right;">${total_value:,.2f}</td>
    </tr>
    <tr>
      <td style="color:#94a3b8; font-size:13px;">Cost Basis</td>
      <td style="color:#f8fafc; font-size:15px; text-align:right;">${total_cost:,.2f}</td>
    </tr>
    <tr>
      <td style="color:#94a3b8; font-size:13px;">Unrealized P&L</td>
      <td style="color:{pnl_color}; font-size:15px; font-weight:600; text-align:right;">{"+" if total_pnl >= 0 else ""}${total_pnl:,.2f} ({pnl_pct:+.1f}%)</td>
    </tr>
  </table>
</td>
</tr>

<!-- Positions Table -->
<tr>
<td style="padding:0 30px 24px;">
  <h2 style="margin:0 0 12px; color:#e2e8f0; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Positions</h2>
  <table width="100%" cellpadding="6" cellspacing="0" style="font-size:12px;">
    <tr style="background-color:#374151;">
      <th style="color:#94a3b8; text-align:left; padding:8px 6px; font-weight:600;">Ticker</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Shares</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Avg Cost</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Price</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Mkt Value</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">P&L</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">P&L%</th>
    </tr>"""

    sorted_positions = sorted(portfolio.items(), key=lambda x: x[1].get("market_value", 0), reverse=True)
    for i, (ticker, p) in enumerate(sorted_positions):
        price = p.get("current_price", 0)
        pnl = p.get("unrealized_pnl", 0)
        pnl_p = p.get("pnl_pct", 0)
        shares = p.get("shares", 0)
        avg_cost = p.get("avg_cost", 0)
        mkt_val = p.get("market_value", 0)
        row_color = pnl_color_cell = "#22c55e" if pnl >= 0 else "#ef4444"
        bg = "#1f2937" if i % 2 == 0 else "#263040"

        html += f"""
    <tr style="background-color:{bg};">
      <td style="color:#f8fafc; padding:6px; font-weight:600;">{ticker}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">{shares}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">${avg_cost:.2f}</td>
      <td style="color:#f8fafc; padding:6px; text-align:right;">${price:.2f}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">${mkt_val:,.0f}</td>
      <td style="color:{pnl_color_cell}; padding:6px; text-align:right;">{"+" if pnl >= 0 else ""}${pnl:,.0f}</td>
      <td style="color:{pnl_color_cell}; padding:6px; text-align:right;">{pnl_p:+.1f}%</td>
    </tr>"""

    html += """
  </table>
</td>
</tr>

<!-- Historical Analysis -->
<tr>
<td style="padding:0 30px 24px;">
  <h2 style="margin:0 0 12px; color:#e2e8f0; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Historical Price Analysis</h2>
  <table width="100%" cellpadding="6" cellspacing="0" style="font-size:12px;">
    <tr style="background-color:#374151;">
      <th style="color:#94a3b8; text-align:left; padding:8px 6px; font-weight:600;">Ticker</th>
      <th style="color:#94a3b8; text-align:left; padding:8px 6px; font-weight:600;">Trend</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">RSI</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Vol 30d</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">1W</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">1M</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Support</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px; font-weight:600;">Resist</th>
    </tr>"""

    for i, (ticker, h) in enumerate(sorted(historical.items())):
        if "error" in h:
            continue
        bg = "#1f2937" if i % 2 == 0 else "#263040"
        trend = h.get("trend", "N/A")
        rsi = h.get("rsi")
        vol = h.get("hist_volatility_30d", 0)
        w1 = h.get("pct_change_1w")
        m1 = h.get("pct_change_1m")
        support = h.get("support", 0)
        resistance = h.get("resistance", 0)

        # Color-code trend
        if "UPTREND" in trend:
            trend_color = "#22c55e"
        elif "DOWNTREND" in trend:
            trend_color = "#ef4444"
        else:
            trend_color = "#f59e0b"

        # Color-code RSI
        rsi_str = f"{rsi:.0f}" if rsi else "N/A"
        if rsi and rsi > 70:
            rsi_color = "#ef4444"
        elif rsi and rsi < 30:
            rsi_color = "#22c55e"
        else:
            rsi_color = "#cbd5e1"

        w1_str = f"{w1:+.1f}%" if w1 is not None else "N/A"
        m1_str = f"{m1:+.1f}%" if m1 is not None else "N/A"
        w1_color = "#22c55e" if w1 and w1 > 0 else "#ef4444"
        m1_color = "#22c55e" if m1 and m1 > 0 else "#ef4444"

        html += f"""
    <tr style="background-color:{bg};">
      <td style="color:#f8fafc; padding:6px; font-weight:600;">{ticker}</td>
      <td style="color:{trend_color}; padding:6px; font-size:11px;">{trend}</td>
      <td style="color:{rsi_color}; padding:6px; text-align:right;">{rsi_str}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">{vol:.0f}%</td>
      <td style="color:{w1_color}; padding:6px; text-align:right;">{w1_str}</td>
      <td style="color:{m1_color}; padding:6px; text-align:right;">{m1_str}</td>
      <td style="color:#60a5fa; padding:6px; text-align:right;">${support:.2f}</td>
      <td style="color:#f472b6; padding:6px; text-align:right;">${resistance:.2f}</td>
    </tr>"""

        # Signal row
        signals = h.get("signals", [])
        if signals:
            html += f"""
    <tr style="background-color:{bg};">
      <td colspan="8" style="color:#94a3b8; padding:2px 6px 8px 20px; font-size:11px; font-style:italic;">
        {"<br>".join("&#8594; " + s for s in signals)}
      </td>
    </tr>"""

    html += """
  </table>
</td>
</tr>

<!-- Covered Call Recommendations -->
<tr>
<td style="padding:0 30px 24px;">
  <h2 style="margin:0 0 12px; color:#e2e8f0; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Top Covered Call Opportunities</h2>
  <table width="100%" cellpadding="6" cellspacing="0" style="font-size:12px;">
    <tr style="background-color:#374151;">
      <th style="color:#94a3b8; text-align:left; padding:8px 6px;">#</th>
      <th style="color:#94a3b8; text-align:left; padding:8px 6px;">Action</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">OTM%</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">Premium</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">Protection</th>
      <th style="color:#94a3b8; text-align:left; padding:8px 6px;">Trend</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">Score</th>
    </tr>"""

    best_cc = recommendations.get("covered_calls", [])[:8]
    for i, cc in enumerate(best_cc):
        bg = "#1f2937" if i % 2 == 0 else "#263040"
        ideal = " &#9733;" if cc.get("in_ideal_otm_range") else ""
        trend = cc.get("trend", "")
        score = cc.get("historical_fit_score", 0)
        score_color = "#22c55e" if score > 15 else "#f59e0b" if score > 0 else "#ef4444"
        safe = "&#9989;" if cc.get("strike", 0) >= cc.get("avg_cost", 0) else "&#9888;&#65039;"

        html += f"""
    <tr style="background-color:{bg};">
      <td style="color:#cbd5e1; padding:6px;">{i+1}</td>
      <td style="color:#f8fafc; padding:6px;">{safe} {cc.get('action', '')}{ideal}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">{cc.get('otm_pct', 0):.1f}%</td>
      <td style="color:#22c55e; padding:6px; text-align:right; font-weight:600;">${cc.get('total_premium', 0):,.0f}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">{cc.get('downside_protection_pct', 0):.1f}%</td>
      <td style="color:#94a3b8; padding:6px; font-size:11px;">{trend}</td>
      <td style="color:{score_color}; padding:6px; text-align:right;">{score}</td>
    </tr>"""

    html += """
  </table>
</td>
</tr>

<!-- CSP Recommendations -->
<tr>
<td style="padding:0 30px 24px;">
  <h2 style="margin:0 0 12px; color:#e2e8f0; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Top Cash-Secured Put Opportunities</h2>
  <table width="100%" cellpadding="6" cellspacing="0" style="font-size:12px;">
    <tr style="background-color:#374151;">
      <th style="color:#94a3b8; text-align:left; padding:8px 6px;">#</th>
      <th style="color:#94a3b8; text-align:left; padding:8px 6px;">Action</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">OTM%</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">Premium</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">Cash Req</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">ROC</th>
      <th style="color:#94a3b8; text-align:right; padding:8px 6px;">Score</th>
    </tr>"""

    best_csp = recommendations.get("cash_secured_puts", [])[:8]
    for i, csp in enumerate(best_csp):
        bg = "#1f2937" if i % 2 == 0 else "#263040"
        ideal = " &#9733;" if csp.get("in_ideal_otm_range") else ""
        score = csp.get("historical_fit_score", 0)
        score_color = "#22c55e" if score > 15 else "#f59e0b" if score > 0 else "#ef4444"

        html += f"""
    <tr style="background-color:{bg};">
      <td style="color:#cbd5e1; padding:6px;">{i+1}</td>
      <td style="color:#f8fafc; padding:6px;">{csp.get('action', '')}{ideal}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">{csp.get('otm_pct', 0):.1f}%</td>
      <td style="color:#22c55e; padding:6px; text-align:right; font-weight:600;">${csp.get('premium', 0):,.0f}</td>
      <td style="color:#cbd5e1; padding:6px; text-align:right;">${csp.get('cash_required', 0):,.0f}</td>
      <td style="color:#60a5fa; padding:6px; text-align:right;">{csp.get('return_on_capital', 0):.2f}%</td>
      <td style="color:{score_color}; padding:6px; text-align:right;">{score}</td>
    </tr>"""

    html += """
  </table>
</td>
</tr>

<!-- Combo Strategies -->
<tr>
<td style="padding:0 30px 24px;">
  <h2 style="margin:0 0 12px; color:#e2e8f0; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Recommended Combos (~$500 Target)</h2>"""

    combos = recommendations.get("target_500_combos", [])[:5]
    for i, combo in enumerate(combos):
        html += f"""
  <div style="background-color:#263040; border-radius:8px; padding:12px 16px; margin-bottom:10px; border-left:3px solid #60a5fa;">
    <div style="color:#f8fafc; font-size:13px; font-weight:600; margin-bottom:6px;">
      #{i+1} {combo.get('name', '')} &mdash; <span style="color:#22c55e;">${combo.get('total_premium', 0):,.0f}</span>
    </div>"""
        for trade in combo.get("trades", []):
            premium = trade.get("total_premium", trade.get("premium", 0))
            html += f"""
    <div style="color:#94a3b8; font-size:12px; padding-left:12px;">&#8627; {trade.get('action', '')} = ${premium:,.0f}</div>"""
        if combo.get("total_risk_summary"):
            html += f"""
    <div style="color:#f59e0b; font-size:11px; margin-top:6px; padding-left:12px;">&#9888; {combo['total_risk_summary'][0][:120]}</div>"""
        html += """
  </div>"""

    html += """
</td>
</tr>

<!-- Risk Alerts -->"""

    risk_items = []
    for ticker, p in portfolio.items():
        pnl_p = p.get("pnl_pct", 0)
        if pnl_p < -30:
            risk_items.append(f"{ticker} down {pnl_p:.1f}% from cost basis")

    if risk_items:
        html += """
<tr>
<td style="padding:0 30px 24px;">
  <h2 style="margin:0 0 12px; color:#ef4444; font-size:16px; border-bottom:1px solid #374151; padding-bottom:8px;">Risk Alerts</h2>"""
        for item in risk_items[:5]:
            html += f"""
  <div style="color:#fca5a5; font-size:13px; padding:4px 0;">&#128680; {item}</div>"""
        html += """
</td>
</tr>"""

    # Footer
    html += f"""
<tr>
<td style="padding:16px 30px; border-top:1px solid #374151;">
  <p style="margin:0; color:#64748b; font-size:11px; text-align:center;">
    Auto-generated by Trading Agent &bull; Not financial advice
  </p>
</td>
</tr>

</table>
</td></tr></table>
</body>
</html>"""

    return html


def send_email(config: dict, html_body: str):
    """Send the HTML email via SMTP."""
    email_cfg = config["email"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Trading Analysis - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg["From"] = email_cfg["from"]
    msg["To"] = email_cfg["to"]

    # Plain text fallback
    msg.attach(MIMEText("View this email in an HTML-capable client for the full report.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(email_cfg["smtp_user"], email_cfg["smtp_password"])
            server.sendmail(email_cfg["from"], email_cfg["to"], msg.as_string())
        print("Email notification sent successfully")
    except Exception as e:
        print(f"Failed to send email: {e}")


def main():
    config = load_config()

    if not config.get("email", {}).get("enabled", False):
        print("Email notifications disabled in config.json")
        return

    market_path = SCRIPT_DIR / "market_data.json"
    recs_path = SCRIPT_DIR / "recommendations.json"
    prev_path = SCRIPT_DIR / "previous_recommendations.json"

    if not market_path.exists() or not recs_path.exists():
        print("ERROR: Run the analysis pipeline first (run_analysis.py)")
        sys.exit(1)

    with open(market_path) as f:
        market_data = json.load(f)
    with open(recs_path) as f:
        recommendations = json.load(f)

    # Load previous recommendations if available (within 2 hours)
    previous_recs = None
    if prev_path.exists():
        try:
            with open(prev_path) as f:
                previous_recs = json.load(f)
            prev_ts = datetime.fromisoformat(previous_recs.get("timestamp", ""))
            age_hours = (datetime.now() - prev_ts).total_seconds() / 3600
            if age_hours > 2:
                previous_recs = None
            else:
                print(f"Comparing with previous run ({age_hours:.1f}h ago)")
        except Exception:
            previous_recs = None

    html = build_html_email(market_data, recommendations, previous_recs)
    send_email(config, html)


if __name__ == "__main__":
    main()
