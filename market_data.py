#!/usr/bin/env python3
"""Fetch current market data, options chains, and news for portfolio tickers."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import yfinance as yf


def compute_rsi(prices, period=14):
    """Compute RSI (Relative Strength Index) from a price series."""
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def find_support_resistance(prices, window=20):
    """Find recent support and resistance levels from price history."""
    if len(prices) < window:
        return None, None

    # Find local minima (support) and maxima (resistance)
    supports = []
    resistances = []
    for i in range(window, len(prices) - window):
        if prices[i] == min(prices[i - window:i + window]):
            supports.append(prices[i])
        if prices[i] == max(prices[i - window:i + window]):
            resistances.append(prices[i])

    support = round(np.mean(supports[-3:]), 2) if supports else round(min(prices[-60:]), 2)
    resistance = round(np.mean(resistances[-3:]), 2) if resistances else round(max(prices[-60:]), 2)
    return support, resistance


def fetch_historical_analysis(tickers: list[str]) -> dict:
    """Fetch 6-month history and compute technical signals for each ticker.

    Returns trend direction, moving averages, RSI, support/resistance,
    historical volatility, and momentum indicators to help predict
    near-term price behavior for options strategy selection.
    """
    results = {}
    batch = yf.Tickers(" ".join(tickers))

    for ticker in tickers:
        try:
            t = batch.tickers[ticker]
            hist = t.history(period="6mo")
            if hist.empty or len(hist) < 30:
                results[ticker] = {"error": "Insufficient history"}
                continue

            closes = hist["Close"].values
            current = closes[-1]

            # Moving averages
            sma_20 = round(np.mean(closes[-20:]), 2)
            sma_50 = round(np.mean(closes[-50:]), 2) if len(closes) >= 50 else None
            sma_200 = round(np.mean(closes[-200:]), 2) if len(closes) >= 200 else None

            # Trend determination
            if sma_50 is not None:
                if current > sma_20 > sma_50:
                    trend = "STRONG_UPTREND"
                elif current > sma_20:
                    trend = "UPTREND"
                elif current < sma_20 < sma_50:
                    trend = "STRONG_DOWNTREND"
                elif current < sma_20:
                    trend = "DOWNTREND"
                else:
                    trend = "SIDEWAYS"
            else:
                trend = "UPTREND" if current > sma_20 else "DOWNTREND"

            # RSI
            rsi = compute_rsi(closes) if len(closes) >= 15 else None

            # Historical volatility (annualized, from 30-day returns)
            daily_returns = np.diff(closes[-31:]) / closes[-31:-1]
            hist_vol_30d = round(np.std(daily_returns) * np.sqrt(252) * 100, 2)

            # Support and resistance
            support, resistance = find_support_resistance(closes)

            # Momentum: price change over various periods
            pct_1w = round((current / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else None
            pct_1m = round((current / closes[-22] - 1) * 100, 2) if len(closes) >= 22 else None
            pct_3m = round((current / closes[-63] - 1) * 100, 2) if len(closes) >= 63 else None

            # Recent price range (last 30 days)
            recent_high = round(max(closes[-30:]), 2)
            recent_low = round(min(closes[-30:]), 2)

            # Distance from key levels
            dist_from_support = round((current - support) / current * 100, 2) if support else None
            dist_from_resistance = round((resistance - current) / current * 100, 2) if resistance else None

            # Prediction signals for options
            signals = []
            if rsi and rsi > 70:
                signals.append("OVERBOUGHT - pullback likely, good for covered calls")
            elif rsi and rsi < 30:
                signals.append("OVERSOLD - bounce likely, good for cash-secured puts")

            if trend in ("STRONG_UPTREND", "UPTREND"):
                signals.append("UPTREND - use higher CC strikes to avoid assignment, be cautious with CSPs at current levels")
            elif trend in ("STRONG_DOWNTREND", "DOWNTREND"):
                signals.append("DOWNTREND - lower CC strikes for more premium, CSPs at deeper OTM near support")

            if dist_from_resistance and dist_from_resistance < 3:
                signals.append("NEAR RESISTANCE - potential reversal, favorable for covered calls")
            if dist_from_support and dist_from_support < 3:
                signals.append("NEAR SUPPORT - potential bounce, favorable for cash-secured puts")

            if hist_vol_30d > 60:
                signals.append("HIGH VOLATILITY - rich premiums but wider price swings")
            elif hist_vol_30d < 20:
                signals.append("LOW VOLATILITY - thin premiums, consider wider expirations")

            results[ticker] = {
                "trend": trend,
                "sma_20": sma_20,
                "sma_50": sma_50,
                "sma_200": sma_200,
                "rsi": rsi,
                "hist_volatility_30d": hist_vol_30d,
                "support": support,
                "resistance": resistance,
                "dist_from_support_pct": dist_from_support,
                "dist_from_resistance_pct": dist_from_resistance,
                "recent_30d_high": recent_high,
                "recent_30d_low": recent_low,
                "pct_change_1w": pct_1w,
                "pct_change_1m": pct_1m,
                "pct_change_3m": pct_3m,
                "signals": signals,
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}

    return results


def fetch_current_prices(tickers: list[str]) -> dict:
    """Fetch current prices and key metrics for a list of tickers."""
    results = {}
    batch = yf.Tickers(" ".join(tickers))

    for ticker in tickers:
        try:
            t = batch.tickers[ticker]
            info = t.info
            hist = t.history(period="5d")
            current_price = hist["Close"].iloc[-1] if not hist.empty else info.get("regularMarketPrice", 0)
            prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else current_price

            results[ticker] = {
                "current_price": round(current_price, 2),
                "prev_close": round(prev_close, 2),
                "day_change": round(current_price - prev_close, 2),
                "day_change_pct": round((current_price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "market_cap": info.get("marketCap", 0),
                "pe_ratio": info.get("trailingPE"),
                "52w_high": info.get("fiftyTwoWeekHigh"),
                "52w_low": info.get("fiftyTwoWeekLow"),
                "avg_volume": info.get("averageDailyVolume10Day", 0),
                "iv_30d": info.get("impliedVolatility"),  # May not be available
            }
        except Exception as e:
            results[ticker] = {"error": str(e), "current_price": 0}

    return results


def fetch_options_chain(ticker: str, max_expirations: int = 4) -> dict:
    """Fetch options chain data for covered call / CSP analysis."""
    try:
        t = yf.Ticker(ticker)
        expirations = t.options[:max_expirations] if t.options else []

        chains = {}
        for exp in expirations:
            chain = t.option_chain(exp)
            calls = chain.calls
            puts = chain.puts

            # Filter to reasonable strikes (within 15% of current price)
            hist = t.history(period="1d")
            if hist.empty:
                continue
            current = hist["Close"].iloc[-1]
            low_bound = current * 0.85
            high_bound = current * 1.15

            filtered_calls = calls[
                (calls["strike"] >= current * 0.98) &
                (calls["strike"] <= high_bound)
            ].head(8)

            filtered_puts = puts[
                (puts["strike"] >= low_bound) &
                (puts["strike"] <= current * 1.02)
            ].head(8)

            chains[exp] = {
                "calls": [
                    {
                        "strike": row["strike"],
                        "bid": row["bid"],
                        "ask": row["ask"],
                        "last": row["lastPrice"],
                        "volume": int(row["volume"]) if row["volume"] == row["volume"] else 0,
                        "open_interest": int(row["openInterest"]) if row["openInterest"] == row["openInterest"] else 0,
                        "implied_vol": round(row["impliedVolatility"] * 100, 1) if row["impliedVolatility"] == row["impliedVolatility"] else None,
                    }
                    for _, row in filtered_calls.iterrows()
                ],
                "puts": [
                    {
                        "strike": row["strike"],
                        "bid": row["bid"],
                        "ask": row["ask"],
                        "last": row["lastPrice"],
                        "volume": int(row["volume"]) if row["volume"] == row["volume"] else 0,
                        "open_interest": int(row["openInterest"]) if row["openInterest"] == row["openInterest"] else 0,
                        "implied_vol": round(row["impliedVolatility"] * 100, 1) if row["impliedVolatility"] == row["impliedVolatility"] else None,
                    }
                    for _, row in filtered_puts.iterrows()
                ],
            }

        return {"ticker": ticker, "expirations": expirations[:max_expirations], "chains": chains}
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def fetch_news(tickers: list[str], max_per_ticker: int = 3) -> dict:
    """Fetch recent news for tickers."""
    news = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            articles = t.news[:max_per_ticker] if t.news else []
            news[ticker] = [
                {
                    "title": a.get("title", ""),
                    "publisher": a.get("publisher", ""),
                    "link": a.get("link", ""),
                    "published": a.get("providerPublishTime", ""),
                }
                for a in articles
            ]
        except Exception:
            news[ticker] = []
    return news


def main():
    # Load portfolio summary
    summary_path = Path(__file__).parent / "portfolio_summary.json"
    if not summary_path.exists():
        print("ERROR: Run portfolio_parser.py first")
        sys.exit(1)

    with open(summary_path) as f:
        portfolio = json.load(f)

    tickers = portfolio["tickers_held"]
    cc_tickers = list(portfolio["positions_with_100_lots"].keys())

    print(f"Fetching market data for {len(tickers)} tickers...")
    print(f"Options chains for covered call candidates: {cc_tickers}")

    # Fetch prices
    prices = fetch_current_prices(tickers)

    # Fetch options chains for CC candidates + high-value CSP targets
    options_data = {}
    for ticker in cc_tickers:
        print(f"  Fetching options chain: {ticker}...")
        options_data[ticker] = fetch_options_chain(ticker)

    # Also fetch options for CSP candidates (tickers you might want to buy)
    csp_candidates = ["TSLA", "NVDA", "NFLX", "RDDT", "ALAB"]
    for ticker in csp_candidates:
        if ticker not in options_data:
            print(f"  Fetching options chain (CSP): {ticker}...")
            options_data[ticker] = fetch_options_chain(ticker)

    # Fetch historical analysis for all tickers
    print("Fetching historical price analysis...")
    all_options_tickers = list(set(cc_tickers + csp_candidates))
    historical = fetch_historical_analysis(all_options_tickers)

    # Fetch news for key holdings
    print("Fetching news...")
    news = fetch_news(cc_tickers[:8])  # Top 8 to avoid rate limits

    # Compute portfolio value
    portfolio_value = {}
    total_value = 0
    total_cost = 0
    for ticker, pos in portfolio["positions"].items():
        price = prices.get(ticker, {}).get("current_price", 0)
        mkt_val = price * pos["shares"]
        pnl = mkt_val - pos["total_invested"]
        portfolio_value[ticker] = {
            "shares": pos["shares"],
            "avg_cost": pos["avg_cost_basis"],
            "current_price": price,
            "market_value": round(mkt_val, 2),
            "unrealized_pnl": round(pnl, 2),
            "pnl_pct": round(pnl / pos["total_invested"] * 100, 2) if pos["total_invested"] > 0 else 0,
            "lots_100": pos["lots_100"],
        }
        total_value += mkt_val
        total_cost += pos["total_invested"]

    market_data = {
        "timestamp": datetime.now().isoformat(),
        "prices": prices,
        "portfolio_value": portfolio_value,
        "total_market_value": round(total_value, 2),
        "total_cost_basis": round(total_cost, 2),
        "total_unrealized_pnl": round(total_value - total_cost, 2),
        "historical_analysis": historical,
        "options_chains": options_data,
        "news": news,
        "covered_call_candidates": cc_tickers,
    }

    output_path = Path(__file__).parent / "market_data.json"
    with open(output_path, "w") as f:
        json.dump(market_data, f, indent=2, default=str)

    print(f"\nOutput written to: {output_path}")

    # Print summary
    print(f"\n{'='*80}")
    print(f"PORTFOLIO SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*80}")
    print(f"{'Ticker':<8} {'Shares':>6} {'Avg Cost':>10} {'Price':>10} {'Mkt Value':>12} {'P&L':>12} {'P&L%':>8}")
    print(f"{'-'*8} {'-'*6} {'-'*10} {'-'*10} {'-'*12} {'-'*12} {'-'*8}")

    for ticker in sorted(portfolio_value.keys()):
        v = portfolio_value[ticker]
        pnl_sign = "+" if v["unrealized_pnl"] >= 0 else ""
        print(
            f"{ticker:<8} {v['shares']:>6} ${v['avg_cost']:>9.2f} ${v['current_price']:>9.2f} "
            f"${v['market_value']:>11.2f} {pnl_sign}${v['unrealized_pnl']:>10.2f} {pnl_sign}{v['pnl_pct']:>6.1f}%"
        )

    print(f"\n  Total Market Value:  ${total_value:>12,.2f}")
    print(f"  Total Cost Basis:   ${total_cost:>12,.2f}")
    pnl = total_value - total_cost
    print(f"  Unrealized P&L:     {'+'if pnl>=0 else ''}${pnl:>12,.2f}")
    print(f"  Est. Cash Balance:  ${portfolio.get('estimated_cash_balance', 0):>12,.2f}")

    # Print historical analysis summary
    print(f"\n{'='*80}")
    print(f"HISTORICAL PRICE ANALYSIS")
    print(f"{'='*80}")
    print(f"{'Ticker':<8} {'Trend':<18} {'RSI':>6} {'Vol30d':>8} {'1W%':>8} {'1M%':>8} {'Support':>10} {'Resist':>10}")
    print(f"{'-'*8} {'-'*18} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*10}")
    for ticker in sorted(historical.keys()):
        h = historical[ticker]
        if "error" in h:
            print(f"{ticker:<8} {'(insufficient data)'}")
            continue
        rsi_str = f"{h['rsi']:.0f}" if h['rsi'] else "N/A"
        w1 = f"{h['pct_change_1w']:+.1f}%" if h['pct_change_1w'] is not None else "N/A"
        m1 = f"{h['pct_change_1m']:+.1f}%" if h['pct_change_1m'] is not None else "N/A"
        print(
            f"{ticker:<8} {h['trend']:<18} {rsi_str:>6} {h['hist_volatility_30d']:>7.1f}% {w1:>8} {m1:>8} "
            f"${h['support']:>9.2f} ${h['resistance']:>9.2f}"
        )
        if h['signals']:
            for sig in h['signals']:
                print(f"         → {sig}")


if __name__ == "__main__":
    main()
