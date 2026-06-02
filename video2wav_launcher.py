#!/usr/bin/env python3
"""Packaged-executable launcher for choosing CMD or GUI mode."""
from __future__ import annotations

import sys

import video2wav


def main() -> int:
    """Display a simple menu and delegate to ``video2wav.main``."""
    while True:
        print("=" * 56)
        print(" Video2WAV Launcher")
        print("=" * 56)
        print("1. Open CMD version")
        print("2. Open GUI version")
        print("3. Exit")
        choice = input("Select an option (1/2/3): ").strip()

        if choice == "1":
            sys.argv = ["video2wav.py"]
            return video2wav.main()
        if choice == "2":
            sys.argv = ["video2wav.py", "--gui"]
            return video2wav.main()
        if choice == "3":
            return 0
        print("Invalid option.\n")


if __name__ == "__main__":
    raise SystemExit(main())
