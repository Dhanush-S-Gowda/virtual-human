#!/usr/bin/env python3
"""Benchmark CV pipeline performance and frame rates."""

import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import argparse
from cv_assistant.cli import cmd_benchmark


def main():
    parser = argparse.ArgumentParser(description="Benchmark CV pipeline.")
    parser.add_argument("--video", default=None, help="Path to video file (optional)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    cmd_benchmark(parser.parse_args())


if __name__ == "__main__":
    main()
