"""Project commands: uv run python utils.py test."""

import argparse
import os
import unittest
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Image inference project utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    tests = commands.add_parser("test", help="Run public API contract tests using unittest")
    tests.add_argument("--yolo", action="store_true", help="Include real YOLO inference")
    tests.add_argument("--failfast", action="store_true", help="Stop after the first failure")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    os.chdir(root)
    if args.yolo:
        os.environ["RUN_YOLO_TESTS"] = "1"
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2, failfast=args.failfast).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
