# Portfolio Parser Fix - File Existence Check

## Problem
The `portfolio_parser.py` file was attempting to open CSV files without checking if they exist first, resulting in unhelpful `FileNotFoundError` exceptions that didn't guide users on how to resolve the issue.

## Solution Implemented

### 1. Added File Existence Check
Added an explicit check at the beginning of `parse_robinhood_csv()` function:
```python
if not os.path.exists(filepath):
    raise FileNotFoundError(
        f"Portfolio CSV not found at {filepath}. "
        "Please ensure the Robinhood account history CSV has been exported and "
        "the correct path is provided. If using the trading agent via orchestrator, "
        "verify the csv_path parameter points to a valid file."
    )
```

### 2. Enhanced Path Resolution in main()
Modified the `main()` function to support multiple path sources in priority order:
1. Command-line argument (highest priority)
2. `ROBINHOOD_CSV_PATH` environment variable
3. Default path fallback
4. Workspace mode detection (for pipeline.py integration)

```python
csv_path = None

if len(sys.argv) > 1:
    csv_path = sys.argv[1]
elif os.environ.get("ROBINHOOD_CSV_PATH"):
    csv_path = os.environ.get("ROBINHOOD_CSV_PATH")
else:
    csv_path = str(Path(__file__).parent.parent / "robinhood_report.csv")

# Check if running in workspace mode (pipeline.py creates this)
workspace_csv = Path("robinhood_report.csv")
if workspace_csv.exists() and not os.path.isabs(csv_path):
    csv_path = str(workspace_csv)
```

## Benefits

1. **Clear Error Messages**: Users now get helpful guidance when the CSV file is missing
2. **Configurable Path**: Supports environment variable `ROBINHOOD_CSV_PATH` for configuration
3. **Backward Compatible**: Maintains existing behavior for pipeline.py and direct script usage
4. **Early Failure**: Fails fast with clear message before attempting to parse

## Testing

A test script `test_portfolio_parser_fix.py` has been created to verify:
- FileNotFoundError is raised with helpful message
- Environment variable support is present in the code

## Usage Examples

### Via Command Line
```bash
python portfolio_parser.py /path/to/robinhood_report.csv
```

### Via Environment Variable
```bash
export ROBINHOOD_CSV_PATH=/path/to/robinhood_report.csv
python portfolio_parser.py
```

### Via Orchestrator (existing behavior maintained)
```json
{
  "capability": "get_portfolio_positions",
  "input_data": {
    "csv_path": "/absolute/path/to/robinhood_report.csv"
  }
}
```

## Files Modified
- `trading-agent/portfolio_parser.py` - Added file existence check and enhanced path resolution

## Files Added
- `trading-agent/test_portfolio_parser_fix.py` - Test script for verification
- `trading-agent/PORTFOLIO_PARSER_FIX.md` - This documentation
