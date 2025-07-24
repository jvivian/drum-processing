# drum-processing/drum_processing/cli.py
"""Command line interface for drum_processing."""

import argparse
from .core import hello_world


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Automate drum clip preprocessing")
    parser.add_argument("--greeting", action="store_true", help="Show greeting message")

    args = parser.parse_args()

    if args.greeting:
        print(hello_world())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
