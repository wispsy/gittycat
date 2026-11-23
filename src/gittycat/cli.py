"""Package information; network hosting is a later implementation milestone."""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Git-compatible hosting and collaboration")
    parser.add_argument("--version", action="version", version="gittycat 0.1.0")
    parser.parse_args(argv)
    return 0
