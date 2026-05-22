#!/usr/bin/env python3
"""Analyze options strategies: covered calls and cash-secured puts.

Targets ~$500 revenue with risk analysis.
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def get_strike_guidance(historical: dict, ticker: str, current_price: float) -> dict:
    """Use historical analysis to recommend strike ranges and flag risks."""
    h = historical.get(ticker, {})
    if "error" in h or not h:
        return {"cc_min_otm": 2, "cc_max_otm": 5, "csp_min_otm": 3, "csp_max_otm": 7, "warnings": [], "score_adj": 0}

    trend = h.get("trend", "SIDEWAYS")
    rsi = h.get("rsi")
    resistance = h.get("resistance")
    support = h.get("support")
    vol = h.get("hist_volatility_30d", 30)

    warnings = []
    cc_min_otm = 2
    cc_max_otm = 5
    csp_min_otm = 3
    csp_max_otm = 7
    score_adj = 0  # Positive = more favorable for the strategy

    # Trend-based adjustments
    if trend == "STRONG_UPTREND":
        cc_min_otm = 4  # Higher strikes to avoid losing shares in a rally
        cc_max_otm = 8
        warnings.append(f"STRONG UPTREND: Use higher strikes (4-8% OTM) to preserve upside")
        score_adj -= 15  # Less favorable for CCs
    elif trend == "UPTREND":
        cc_min_otm = 3
        cc_max_otm = 6
        score_adj -= 5
    elif trend == "STRONG_DOWNTREND":
        cc_min_otm = 1  # Closer strikes for more premium/protection
        cc_max_otm = 4
        csp_min_otm = 5  # Deeper OTM puts in downtrends
        csp_max_otm = 10
        warnings.append(f"STRONG DOWNTREND: Be cautious, stock may continue lower")
        score_adj += 10  # More premium available for CCs
    elif trend == "DOWNTREND":
        cc_min_otm = 1
        cc_max_otm = 5
        csp_min_otm = 4
        csp_max_otm = 8

    # RSI-based signals
    if rsi and rsi > 70:
        warnings.append(f"RSI {rsi:.0f} - Overbought, pullback possible. Good CC timing.")
        score_adj += 10
    elif rsi and rsi < 30:
        warnings.append(f"RSI {rsi:.0f} - Oversold, bounce expected. Good CSP timing.")
        score_adj -= 5  # Less favorable for CCs, better for CSPs

    # Resistance-based CC strike suggestion
    if resistance and resistance > current_price:
        dist_to_resistance = (resistance - current_price) / current_price * 100
        if dist_to_resistance < 3:
            warnings.append(f"Near resistance ${resistance:.2f} ({dist_to_resistance:.1f}% away) - good CC level")

    # Support-based CSP strike suggestion
    if support and support < current_price:
        dist_to_support = (current_price - support) / current_price * 100
        if dist_to_support < 5:
            warnings.append(f"Near support ${support:.2f} ({dist_to_support:.1f}% away) - CSP caution")

    # Volatility regime
    if vol > 60:
        warnings.append(f"High volatility ({vol:.0f}%) - rich premiums but wider swings")
    elif vol < 20:
        warnings.append(f"Low volatility ({vol:.0f}%) - consider longer expirations for decent premium")

    return {
        "cc_min_otm": cc_min_otm,
        "cc_max_otm": cc_max_otm,
        "csp_min_otm": csp_min_otm,
        "csp_max_otm": csp_max_otm,
        "warnings": warnings,
        "score_adj": score_adj,
        "trend": trend,
        "rsi": rsi,
        "support": support,
        "resistance": resistance,
        "hist_volatility": vol,
    }


def analyze_covered_calls(market_data: dict) -> list[dict]:
    """Find best covered call opportunities from current positions."""
    recommendations = []
    portfolio = market_data["portfolio_value"]
    options = market_data.get("options_chains", {})
    historical = market_data.get("historical_analysis", {})

    for ticker, chain_data in options.items():
        if "error" in chain_data:
            continue

        pos = portfolio.get(ticker, {})
        lots = pos.get("lots_100", 0)
        if lots == 0:
            continue

        current_price = pos.get("current_price", 0)
        avg_cost = pos.get("avg_cost", 0)
        if current_price == 0:
            continue

        # Get historical-based strike guidance
        guidance = get_strike_guidance(historical, ticker, current_price)

        for exp, chain in chain_data.get("chains", {}).items():
            for call in chain.get("calls", []):
                strike = call["strike"]
                bid = call["bid"]
                if bid <= 0:
                    continue

                premium_per_contract = bid * 100
                total_premium = premium_per_contract * lots
                otm_pct = (strike - current_price) / current_price * 100
                # Max profit = premium + (strike - current price) * 100 * lots
                upside_to_strike = (strike - current_price) * 100 * lots
                max_profit = total_premium + max(0, upside_to_strike)
                # Breakeven if stock drops
                breakeven = current_price - bid
                downside_protection_pct = (bid / current_price) * 100
                # Risk: assignment below cost basis
                assignment_pnl = (strike - avg_cost) * 100 * lots + total_premium

                # Historical fit score: how well this strike fits the historical pattern
                in_ideal_range = guidance["cc_min_otm"] <= otm_pct <= guidance["cc_max_otm"]
                hist_score = guidance["score_adj"]
                if in_ideal_range:
                    hist_score += 20  # Bonus for being in trend-adjusted ideal range
                # Bonus if strike aligns with resistance
                if guidance.get("resistance") and abs(strike - guidance["resistance"]) / current_price * 100 < 1.5:
                    hist_score += 15  # Strike near resistance is a natural ceiling

                rec = {
                    "strategy": "COVERED CALL",
                    "ticker": ticker,
                    "action": f"Sell {lots} {ticker} {exp} ${strike:.2f} Call",
                    "expiration": exp,
                    "strike": strike,
                    "current_price": current_price,
                    "avg_cost": avg_cost,
                    "otm_pct": round(otm_pct, 2),
                    "bid": bid,
                    "contracts": lots,
                    "premium_per_contract": round(premium_per_contract, 2),
                    "total_premium": round(total_premium, 2),
                    "max_profit": round(max_profit, 2),
                    "breakeven": round(breakeven, 2),
                    "downside_protection_pct": round(downside_protection_pct, 2),
                    "assignment_pnl": round(assignment_pnl, 2),
                    "historical_fit_score": hist_score,
                    "trend": guidance.get("trend", "UNKNOWN"),
                    "in_ideal_otm_range": in_ideal_range,
                    "risks": [],
                }

                # Risk analysis
                if strike < avg_cost:
                    rec["risks"].append(
                        f"LOSS ON ASSIGNMENT: Strike ${strike:.2f} < cost basis ${avg_cost:.2f}. "
                        f"Net loss if assigned: ${assignment_pnl:,.2f}"
                    )
                if otm_pct < 1:
                    rec["risks"].append(
                        f"HIGH ASSIGNMENT RISK: Only {otm_pct:.1f}% OTM. Likely to be called away."
                    )
                if otm_pct > 10:
                    rec["risks"].append(
                        f"LOW PREMIUM: {otm_pct:.1f}% OTM, premium may not justify capital lockup."
                    )
                # Historical-based risk warnings
                for warning in guidance["warnings"]:
                    rec["risks"].append(f"HIST: {warning}")

                recommendations.append(rec)

    return recommendations


def analyze_cash_secured_puts(market_data: dict) -> list[dict]:
    """Find CSP opportunities for income generation."""
    recommendations = []
    options = market_data.get("options_chains", {})
    historical = market_data.get("historical_analysis", {})
    cash = market_data.get("total_market_value", 0) * 0.05  # Assume 5% buying power

    for ticker, chain_data in options.items():
        if "error" in chain_data:
            continue

        price_info = market_data["prices"].get(ticker, {})
        current_price = price_info.get("current_price", 0)
        if current_price == 0:
            continue

        # Get historical-based guidance
        guidance = get_strike_guidance(historical, ticker, current_price)

        for exp, chain in chain_data.get("chains", {}).items():
            for put in chain.get("puts", []):
                strike = put["strike"]
                bid = put["bid"]
                if bid <= 0:
                    continue

                premium = bid * 100
                cash_required = strike * 100
                otm_pct = (current_price - strike) / current_price * 100
                # Return on capital
                roc = (premium / cash_required) * 100 if cash_required > 0 else 0
                # Effective buy price if assigned
                effective_buy = strike - bid

                # Historical fit score for CSPs
                in_ideal_range = guidance["csp_min_otm"] <= otm_pct <= guidance["csp_max_otm"]
                hist_score = -guidance["score_adj"]  # Invert: what's bad for CC is good for CSP
                if in_ideal_range:
                    hist_score += 20
                # Bonus if strike near support level (natural floor)
                if guidance.get("support") and abs(strike - guidance["support"]) / current_price * 100 < 2:
                    hist_score += 20  # Strike near support is ideal for CSPs
                # RSI bonus: oversold = better CSP timing
                if guidance.get("rsi") and guidance["rsi"] < 35:
                    hist_score += 15

                rec = {
                    "strategy": "CASH-SECURED PUT",
                    "ticker": ticker,
                    "action": f"Sell 1 {ticker} {exp} ${strike:.2f} Put",
                    "expiration": exp,
                    "strike": strike,
                    "current_price": current_price,
                    "otm_pct": round(otm_pct, 2),
                    "bid": bid,
                    "contracts": 1,
                    "premium": round(premium, 2),
                    "cash_required": round(cash_required, 2),
                    "return_on_capital": round(roc, 2),
                    "effective_buy_price": round(effective_buy, 2),
                    "historical_fit_score": hist_score,
                    "trend": guidance.get("trend", "UNKNOWN"),
                    "in_ideal_otm_range": in_ideal_range,
                    "risks": [],
                }

                # Risk analysis
                if otm_pct < 2:
                    rec["risks"].append(
                        f"HIGH ASSIGNMENT RISK: Only {otm_pct:.1f}% OTM. High chance of assignment."
                    )
                if cash_required > 50000:
                    rec["risks"].append(
                        f"HIGH CAPITAL REQUIREMENT: ${cash_required:,.0f} cash needed to secure this put."
                    )
                # Historical-based risk warnings
                for warning in guidance["warnings"]:
                    rec["risks"].append(f"HIST: {warning}")

                # Check if assignment would create a CC-eligible position
                existing = market_data["portfolio_value"].get(ticker, {})
                existing_shares = existing.get("shares", 0)
                rec["note"] = (
                    f"If assigned: would own {existing_shares + 100} shares. "
                    f"Effective cost: ${effective_buy:.2f}/share."
                )

                recommendations.append(rec)

    return recommendations


def find_target_500_combos(cc_recs: list, csp_recs: list) -> list[dict]:
    """Find combinations of trades that target ~$500 total premium."""
    # Filter to reasonable trades
    good_cc = [r for r in cc_recs if 1 <= r["otm_pct"] <= 8 and r["total_premium"] >= 20]
    good_csp = [r for r in csp_recs if 2 <= r["otm_pct"] <= 8 and r["premium"] >= 20]

    # Sort by premium descending
    good_cc.sort(key=lambda x: x["total_premium"], reverse=True)
    good_csp.sort(key=lambda x: x["premium"], reverse=True)

    combos = []

    # Strategy 1: Single CC that gets close to $500
    for cc in good_cc:
        if 350 <= cc["total_premium"] <= 700:
            combos.append({
                "name": f"Single CC: {cc['action']}",
                "trades": [cc],
                "total_premium": cc["total_premium"],
                "total_risk_summary": cc["risks"],
            })

    # Strategy 2: CC + CSP combo
    for cc in good_cc[:5]:
        for csp in good_csp[:5]:
            total = cc["total_premium"] + csp["premium"]
            if 400 <= total <= 700:
                combos.append({
                    "name": f"CC+CSP: {cc['ticker']} CC + {csp['ticker']} CSP",
                    "trades": [cc, csp],
                    "total_premium": round(total, 2),
                    "total_risk_summary": cc["risks"] + csp["risks"],
                })

    # Strategy 3: Multiple CCs
    for i, cc1 in enumerate(good_cc[:5]):
        for cc2 in good_cc[i + 1:8]:
            total = cc1["total_premium"] + cc2["total_premium"]
            if 400 <= total <= 700:
                combos.append({
                    "name": f"Multi-CC: {cc1['ticker']} + {cc2['ticker']}",
                    "trades": [cc1, cc2],
                    "total_premium": round(total, 2),
                    "total_risk_summary": cc1["risks"] + cc2["risks"],
                })

    # Sort by closeness to $500
    combos.sort(key=lambda x: abs(x["total_premium"] - 500))
    return combos[:10]


def main():
    data_path = Path(__file__).parent / "market_data.json"
    if not data_path.exists():
        print("ERROR: Run market_data.py first")
        sys.exit(1)

    with open(data_path) as f:
        market_data = json.load(f)

    print(f"{'='*80}")
    print(f"OPTIONS STRATEGY ADVISOR - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"TARGET: ~$500 premium income")
    print(f"{'='*80}")

    # Analyze covered calls
    cc_recs = analyze_covered_calls(market_data)
    csp_recs = analyze_cash_secured_puts(market_data)

    # Find $500 target combos
    combos = find_target_500_combos(cc_recs, csp_recs)

    # Print top recommendations
    print(f"\n{'='*80}")
    print("TOP COVERED CALL OPPORTUNITIES")
    print(f"{'='*80}")

    # Best CCs: weighted by premium + historical fit score
    best_cc = sorted(
        [r for r in cc_recs if 1 <= r["otm_pct"] <= 8],
        key=lambda x: x["total_premium"] + x.get("historical_fit_score", 0) * 5,
        reverse=True,
    )[:10]

    for i, rec in enumerate(best_cc, 1):
        trend_tag = f" [{rec.get('trend', '')}]" if rec.get('trend') else ""
        ideal_tag = " ★" if rec.get("in_ideal_otm_range") else ""
        print(f"\n  [{i}] {rec['action']}{trend_tag}{ideal_tag}")
        print(f"      Price: ${rec['current_price']:.2f} | Strike: ${rec['strike']:.2f} ({rec['otm_pct']:.1f}% OTM)")
        print(f"      Premium: ${rec['total_premium']:.2f} ({rec['contracts']} contracts @ ${rec['bid']:.2f})")
        print(f"      Downside protection: {rec['downside_protection_pct']:.1f}% | Hist score: {rec.get('historical_fit_score', 0)}")
        if rec["risks"]:
            for risk in rec["risks"]:
                print(f"      ⚠ {risk}")

    print(f"\n{'='*80}")
    print("TOP CASH-SECURED PUT OPPORTUNITIES")
    print(f"{'='*80}")

    best_csp = sorted(
        [r for r in csp_recs if 2 <= r["otm_pct"] <= 8],
        key=lambda x: x["return_on_capital"] + x.get("historical_fit_score", 0) * 0.5,
        reverse=True,
    )[:10]

    for i, rec in enumerate(best_csp, 1):
        trend_tag = f" [{rec.get('trend', '')}]" if rec.get('trend') else ""
        ideal_tag = " ★" if rec.get("in_ideal_otm_range") else ""
        print(f"\n  [{i}] {rec['action']}{trend_tag}{ideal_tag}")
        print(f"      Price: ${rec['current_price']:.2f} | Strike: ${rec['strike']:.2f} ({rec['otm_pct']:.1f}% OTM)")
        print(f"      Premium: ${rec['premium']:.2f} | Cash Required: ${rec['cash_required']:,.2f}")
        print(f"      ROC: {rec['return_on_capital']:.2f}% | Effective buy: ${rec['effective_buy_price']:.2f} | Hist score: {rec.get('historical_fit_score', 0)}")
        if rec["risks"]:
            for risk in rec["risks"]:
                print(f"      ⚠ {risk}")

    print(f"\n{'='*80}")
    print("RECOMMENDED COMBINATIONS (~$500 TARGET)")
    print(f"{'='*80}")

    for i, combo in enumerate(combos[:5], 1):
        print(f"\n  COMBO [{i}]: {combo['name']}")
        print(f"  Total Premium: ${combo['total_premium']:.2f}")
        for trade in combo["trades"]:
            strategy = trade["strategy"]
            premium = trade.get("total_premium", trade.get("premium", 0))
            print(f"    → {trade['action']} = ${premium:.2f}")
        if combo["total_risk_summary"]:
            print(f"  Risks:")
            for risk in combo["total_risk_summary"]:
                print(f"    ⚠ {risk}")

    # Save recommendations
    output = {
        "timestamp": datetime.now().isoformat(),
        "covered_calls": best_cc,
        "cash_secured_puts": best_csp,
        "target_500_combos": combos[:10],
        "all_cc_recommendations": cc_recs,
        "all_csp_recommendations": csp_recs,
    }

    output_path = Path(__file__).parent / "recommendations.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n\nFull recommendations saved to: {output_path}")

    # Print news summary
    print(f"\n{'='*80}")
    print("RECENT NEWS (Key Holdings)")
    print(f"{'='*80}")
    for ticker, articles in market_data.get("news", {}).items():
        if articles:
            print(f"\n  {ticker}:")
            for a in articles[:2]:
                print(f"    • {a['title']}")


if __name__ == "__main__":
    main()
