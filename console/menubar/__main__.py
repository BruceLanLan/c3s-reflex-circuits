"""`python -m menubar` — the same as `c3s menubar`."""

import argparse

from c3s_cli import paths

from .app import main

parser = argparse.ArgumentParser(prog="python -m menubar", description="The fly in the menu bar.")
parser.add_argument("--port", type=int, default=paths.default_port())
parser.add_argument("--print-menu", action="store_true", help="print the items instead of running")
args = parser.parse_args()
raise SystemExit(main(port=args.port, print_menu=args.print_menu))
