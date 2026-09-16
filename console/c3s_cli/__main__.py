"""`python -m c3s_cli …` — the same command as `c3s`, for a checkout with no install."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
