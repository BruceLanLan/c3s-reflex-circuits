"""The `c3s` command line: install the boundary console, start it, pair a phone, stop it.

The console itself is `console.py` in the reflex-console checkout; this package only finds
it, starts it, keeps it alive through launchd, and prints what a person needs to see (the
operator token, the pairing QR, what is waiting). It decides nothing.
"""

__version__ = "1.0.0.dev0"
