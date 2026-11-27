"""Package information and an explicitly selected loopback HTTP server."""

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Git-compatible hosting and collaboration")
    parser.add_argument("--version", action="version", version=f"gittycat {version('gittycat')}")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="run local management and read-only Git HTTP")
    serve.add_argument("--data-root", required=True)
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "serve":
        if not 0 <= args.port <= 65535:parser.error("port must be between 0 and 65535")
        try:
            from .management import create_app
        except ModuleNotFoundError as error:
            if error.name != "expose_api":raise
            parser.error("install gittycat[http] to run the management API")
        create_app(args.data_root).run(host="127.0.0.1", port=args.port)
    return 0
