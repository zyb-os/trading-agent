#!/usr/bin/env python3
"""Parse Robinhood CSV export and compute current portfolio positions."""

import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


def parse_robinhood_csv(filepath: str) -> list[dict]:
    """Parse Robinhood CSV handling multiline Description fields."""
    # Check if file exists before attempting to open
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"Portfolio CSV not found at {filepath}. "
            "Please ensure the Robinhood account history CSV has been exported and "
            "the correct path is provided. If using the trading agent via orchestrator, "
            "verify the csv_path parameter points to a valid file."
        )
    
    records = []
    with open(filepath, "r") as f:
        content = f.read()

    # The CSV has multiline fields (CUSIP info in Description).
    # Python's csv module handles quoted multiline fields correctly.
    reader = csv.DictReader(content.splitlines())

    # But the issue is that Robinhood's export has rows that span multiple lines
    # within quoted fields. We need to re-join lines properly.
    # Let's use a different approach: read the raw file and parse manually.

    lines = content.split("\n")
    header = None
    current_row = ""
    raw_rows = []

    for line in lines:
        if header is None:
            header = line.strip()
            continue

        current_row += line + "\n"
        # Count quotes - if even, the row is complete
        quote_count = current_row.count('"')
        if quote_count % 2 == 0:
            raw_rows.append(current_row.strip())
            current_row = ""

    # Now parse each complete row
    headers = list(csv.reader([header]))[0]

    for raw in raw_rows:
        if not raw.strip():
            continue
        try:
            parsed = list(csv.reader([raw]))[0]
            if len(parsed) >= len(headers):
                record = {}
                for i, h in enumerate(headers):
                    record[h] = parsed[i] if i < len(parsed) else ""
                records.append(record)
        except Exception:
            continue

    return records


def parse_amount(amount_str: str) -> float:
    """Parse dollar amount string like '$1,234.56' or '($1,234.56)' to float."""
    if not amount_str:
        return 0.0
    amount_str = amount_str.strip()
    negative = amount_str.startswith("(") or amount_str.startswith("-")
    cleaned = re.sub(r"[$(,)\s]", "", amount_str)
    if not cleaned:
        return 0.0
    try:
        val = float(cleaned)
        return -val if negative else val
    except ValueError:
        return 0.0


def compute_positions(records: list[dict]) -> dict:
    """Compute net share positions and cost basis from transaction records.

    Uses FIFO-like average cost: tracks buy lots, reduces proportionally on sells.
    """
    positions = defaultdict(lambda: {
        "buy_lots": [],  # List of (qty, price_per_share)
        "shares": 0,
        "options_history": [],
        "dividends": 0.0,
        "description": "",
    })

    buy_codes = {"Buy"}
    sell_codes = {"Sell"}
    option_codes = {"STO", "BTO", "STC", "BTC", "OEXP", "OASGN"}

    # Process records in chronological order (CSV is newest-first)
    for rec in reversed(records):
        ticker = rec.get("Instrument", "").strip()
        trans_code = rec.get("Trans Code", "").strip()
        qty_str = rec.get("Quantity", "").strip()
        price_str = rec.get("Price", "").strip()
        amount_str = rec.get("Amount", "").strip()
        date = rec.get("Activity Date", "").strip()
        desc = rec.get("Description", "").strip()

        if not ticker:
            continue

        qty = float(qty_str) if qty_str else 0
        price = parse_amount(price_str)
        amount = parse_amount(amount_str)

        pos = positions[ticker]

        if not pos["description"] and desc and "CUSIP" not in desc:
            pos["description"] = desc

        if trans_code in buy_codes:
            cost_per = abs(amount) / qty if qty > 0 else price
            pos["buy_lots"].append((qty, cost_per))
            pos["shares"] += qty
        elif trans_code in sell_codes:
            # Remove shares FIFO
            to_sell = qty
            pos["shares"] -= qty
            while to_sell > 0 and pos["buy_lots"]:
                lot_qty, lot_price = pos["buy_lots"][0]
                if lot_qty <= to_sell:
                    to_sell -= lot_qty
                    pos["buy_lots"].pop(0)
                else:
                    pos["buy_lots"][0] = (lot_qty - to_sell, lot_price)
                    to_sell = 0
        elif trans_code in ("CDIV", "MDIV"):
            pos["dividends"] += amount
        elif trans_code == "SPL":
            # Stock split: adjust all lot prices proportionally
            if pos["shares"] > 0:
                ratio = (pos["shares"] + qty) / pos["shares"]
                pos["buy_lots"] = [
                    (lq * ratio, lp / ratio) for lq, lp in pos["buy_lots"]
                ]
            pos["shares"] += qty
        elif trans_code in option_codes:
            pos["options_history"].append({
                "date": date, "type": trans_code, "desc": desc, "amount": amount,
            })

    # Build active positions
    active = {}
    for ticker, pos in positions.items():
        shares = int(round(pos["shares"]))
        if shares > 0:
            total_cost = sum(q * p for q, p in pos["buy_lots"])
            avg_cost = total_cost / shares if shares > 0 else 0
            active[ticker] = {
                "ticker": ticker,
                "shares": shares,
                "avg_cost_basis": round(avg_cost, 2),
                "total_invested": round(total_cost, 2),
                "dividends_received": round(pos["dividends"], 2),
                "description": pos["description"],
                "lots_100": shares // 100,
                "odd_lot": shares % 100,
            }

    return active


def compute_options_income(records: list[dict]) -> dict:
    """Compute total options premium income by ticker."""
    income = defaultdict(lambda: {"total_premium": 0.0, "trades": []})

    for rec in records:
        ticker = rec.get("Instrument", "").strip()
        trans_code = rec.get("Trans Code", "").strip()
        amount_str = rec.get("Amount", "").strip()
        date = rec.get("Activity Date", "").strip()
        desc = rec.get("Description", "").strip()

        if not ticker:
            continue

        amount = parse_amount(amount_str)

        if trans_code == "STO":  # Sell to Open (premium received)
            income[ticker]["total_premium"] += amount
            income[ticker]["trades"].append({
                "date": date,
                "desc": desc,
                "premium": amount,
            })

    return dict(income)


def compute_cash_available(records: list[dict]) -> float:
    """Estimate available cash from deposits, withdrawals, and trade P&L."""
    cash = 0.0
    for rec in records:
        amount_str = rec.get("Amount", "").strip()
        amount = parse_amount(amount_str)
        cash += amount
    return round(cash, 2)


def main():
    # Accept filepath from command line or environment variable, with fallback to default
    csv_path = None
    
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    elif os.environ.get("ROBINHOOD_CSV_PATH"):
        csv_path = os.environ.get("ROBINHOOD_CSV_PATH")
    else:
        csv_path = str(Path(__file__).parent.parent / "robinhood_report.csv")
    
    # Check if running in workspace mode (pipeline.py creates this)
    workspace_csv = Path("robinhood_report.csv")
    if workspace_csv.exists() and (not os.path.isabs(csv_path) or not Path(csv_path).exists()):
        csv_path = str(workspace_csv)

    print(f"Parsing: {csv_path}")
    records = parse_robinhood_csv(csv_path)
    print(f"Total records parsed: {len(records)}")

    positions = compute_positions(records)
    options_income = compute_options_income(records)
    estimated_cash = compute_cash_available(records)

    summary = {
        "positions": positions,
        "options_income_history": options_income,
        "estimated_cash_balance": estimated_cash,
        "tickers_held": sorted(positions.keys()),
        "positions_with_100_lots": {
            t: p for t, p in positions.items() if p["lots_100"] > 0
        },
    }

    output_path = Path(__file__).parent / "portfolio_summary.json"
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nOutput written to: {output_path}")
    print(f"\n{'='*60}")
    print("CURRENT POSITIONS")
    print(f"{'='*60}")
    print(f"{'Ticker':<8} {'Shares':>8} {'Avg Cost':>10} {'Invested':>12} {'100-Lots':>8}")
    print(f"{'-'*8} {'-'*8} {'-'*10} {'-'*12} {'-'*8}")

    for ticker in sorted(positions.keys()):
        p = positions[ticker]
        print(f"{ticker:<8} {p['shares']:>8} ${p['avg_cost_basis']:>9.2f} ${p['total_invested']:>11.2f} {p['lots_100']:>8}")

    print(f"\nEstimated Cash Balance: ${estimated_cash:,.2f}")

    # Covered call candidates (need 100+ shares)
    cc_candidates = {t: p for t, p in positions.items() if p["lots_100"] > 0}
    print(f"\n{'='*60}")
    print("COVERED CALL CANDIDATES (100+ shares)")
    print(f"{'='*60}")
    for ticker, p in sorted(cc_candidates.items()):
        print(f"  {ticker}: {p['shares']} shares ({p['lots_100']} contracts possible)")

    # Options income history
    print(f"\n{'='*60}")
    print("OPTIONS PREMIUM INCOME HISTORY")
    print(f"{'='*60}")
    total_premium = 0
    for ticker, data in sorted(options_income.items()):
        print(f"  {ticker}: ${data['total_premium']:.2f} ({len(data['trades'])} trades)")
        total_premium += data["total_premium"]
    print(f"\n  TOTAL OPTIONS INCOME: ${total_premium:,.2f}")


if __name__ == "__main__":
    main()
