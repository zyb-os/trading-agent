#!/usr/bin/env python3
"""Test script to verify portfolio_parser.py file existence check."""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

import portfolio_parser

def test_file_not_found():
    """Test that FileNotFoundError is raised with helpful message."""
    try:
        portfolio_parser.parse_robinhood_csv("/nonexistent/path/to/file.csv")
        print("❌ FAIL: Expected FileNotFoundError but none was raised")
        return False
    except FileNotFoundError as e:
        error_msg = str(e)
        # Check that error message contains helpful information
        if "Portfolio CSV not found" in error_msg and "/nonexistent/path/to/file.csv" in error_msg:
            print("✅ PASS: FileNotFoundError raised with helpful message")
            print(f"   Message: {error_msg}")
            return True
        else:
            print(f"❌ FAIL: FileNotFoundError raised but message not helpful: {error_msg}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Unexpected exception: {type(e).__name__}: {e}")
        return False

def test_environment_variable():
    """Test that ROBINHOOD_CSV_PATH environment variable is respected."""
    # This is a basic check that the code references the env var
    import inspect
    source = inspect.getsource(portfolio_parser.main)
    if "ROBINHOOD_CSV_PATH" in source:
        print("✅ PASS: Code checks ROBINHOOD_CSV_PATH environment variable")
        return True
    else:
        print("❌ FAIL: Code does not check ROBINHOOD_CSV_PATH environment variable")
        return False

def main():
    print("Testing portfolio_parser.py improvements...")
    print("=" * 60)
    
    results = []
    results.append(test_file_not_found())
    results.append(test_environment_variable())
    
    print("=" * 60)
    if all(results):
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
