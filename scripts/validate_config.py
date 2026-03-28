#!/usr/bin/env python
"""CLI tool to validate repository configuration files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.config_validator import ConfigValidator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PMTCT YAML configuration files")
    parser.add_argument("--file", "-f", help="Specific config file to validate")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print warnings for valid files")
    args = parser.parse_args()

    validator = ConfigValidator()
    if args.file:
        results = [validator.validate_file(args.file).to_dict()]
        summary = {
            "files_checked": 1,
            "valid": results[0]["valid"],
            "error_count": len(results[0]["errors"]),
            "warning_count": len(results[0]["warnings"]),
        }
    else:
        results = validator.validate_all()
        summary = validator.summarize()

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
        return 0 if summary["valid"] else 1

    for result in results:
        prefix = "[OK]" if result["valid"] else "[FAIL]"
        print(f"{prefix} {result['file']}")
        if args.verbose or not result["valid"]:
            for error in result["errors"]:
                print(f"  ERROR: {error}")
            for warning in result["warnings"]:
                print(f"  WARNING: {warning}")

    print()
    if summary["valid"]:
        print("All configuration files passed validation.")
        return 0

    print("Configuration validation failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
