#!/usr/bin/env python3
"""Check CV environment, Python version, GPU/CUDA acceleration, and libraries."""

import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import argparse
from cv_assistant.cli import cmd_check_env


def main():
    parser = argparse.ArgumentParser(description="Check environment and GPU availability.")
    cmd_check_env(parser.parse_args())


if __name__ == "__main__":
    main()
