"""`c3s` — install it, start it, see it, stop it.

    c3s up            start the console on this machine and open the page
    c3s up --lan      the same, reachable from a phone on the same Wi-Fi (see the warning)
    c3s up --service  install the launchd agent as well, so a reboot or a kill brings it back
    c3s status        is it running, what is waiting for a person, where is the log
    c3s pair          print the pairing QR again
    c3s token         show the operator token; `c3s token rotate` replaces it
    c3s stop-all      block every agent the console knows (needs the operator token)
    c3s down          stop it (`--service` also unloads the launchd agent)
    c3s demo          the simulated workbench (W4's `examples/workbench.py`)

Nothing here performs an action for an agent, holds a key, or signs anything. `stop-all`
is the only command that writes to the console, and it writes the person's `blocked` bit.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import console_client as client
from . import paths, qr, service

CLI_STATE = paths.CONFIG_DIR / "cli-state.json"


# ------------------------------------------------------------------------------- helpers

def say(text: str = "") -> None:
    print(text, flush=True)


def bad(text: str, code: int = 1) -> int:
    print(text, file=sys.stderr, flush=True)
    return code


def _remember(**fields) -> None:
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    known = _recalled()
    known.update(fields)
    CLI_STATE.write_text(json.dumps(known, indent=2) + "\n")


def _recalled() -> dict:
    try:
        return json.loads(CLI_STATE.read_text())
    except (OSError, ValueError):
        return {}


def _console_env(repo: Path, host: str, port: int, cardputer: str | None) -> dict:
    env = dict(os.environ)
    env.update({"C3S_REPO": str(repo), "CONSOLE_HOST": host, "CONSOLE_PORT": str(port),
                "PYTHONUNBUFFERED": "1"})
    if cardputer:
        env["REFLEX_CARDPUTER"] = cardputer
    env["PATH"] = f"{Path.home()}/.foundry/bin:" + env.get("PATH", "/usr/bin:/bin")
    return env


def _pid_on_port(port: int) -> int | None:
    """Whoever is listening on that port, ours or not."""
    done = subprocess.run(["lsof", "-ti", f":{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    for line in done.stdout.split():
        try:
            return int(line)
        except ValueError:
            continue
    return None


def _command_of(pid: int) -> str:
    done = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return done.stdout.strip()


def _env_of(pid: int) -> str:
    """The environment of one of our own processes (macOS `ps -E`), best effort."""
    done = subprocess.run(["ps", "-Ewww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return done.stdout.strip()


def _pending(state: dict) -> tuple[int | None, str]:
    """How many things wait for a person, from the console's own `pending` list (I-2).

    Deliberately not recomputed here: API.md froze that list so the page, the device, the
    chat and this command all read one answer. A console that predates it says so.
    """
    if "pending" in state:
        return len(state["pending"]), f"{len(state['pending'])}"
    return None, "— (this console has no pending[] yet: I-2 in docs/API.md)"


def _bound_host() -> str | None:
    """What address the console is listening on: the plist for a launchd service, the note
    from the last `c3s up` otherwise. It decides whether a phone can reach it at all, so
    neither is guessed — None means nobody here started this console."""
    if service.loaded():
        host = service.plist_env().get("CONSOLE_HOST")
        if host:
            return host
    return _recalled().get("host")


def _pairing_url(port: int, token: str | None, ip: str | None = None) -> str | None:
    """The URL the QR carries: the token rides in the **fragment**, never the query string.

    A fragment is not sent to the server (docs/API.md, "Pairing a phone"), so the token
    cannot appear in the console's log — which prints whole request lines — nor in a
    proxy's log or a `Referer`. The page reads it once, stores it and clears the bar.
    """
    ip = ip or next(iter(paths.lan_ipv4()), None)
    if not ip:
        return None
    fragment = f"#approvals&token={token}" if token else "#approvals"
    return f"http://{ip}:{port}/{fragment}"


def _print_qr(url: str, invert: bool) -> None:
    say(qr.render(url, level="M", invert=invert))
    say(f"  {url}")


def _pairing_caveats(port: int, with_token: bool) -> None:
    """Say what the phone will actually get, instead of promising what it might not.

    The URL itself loads today (the path is `/`); what is still missing is the page-side
    reader for the fragment's token, which the coordinator owns in the integration pass.
    """
    if with_token:
        say("  NOTE: the page does not read the token out of the fragment yet (docs/INSTALL.md §8 — "
            "the coordinator owns that change). Until it lands the phone reaches the approvals "
            "view and asks for the token once: paste what `c3s token` prints.")


def _open_page(port: int) -> None:
    url = f"http://127.0.0.1:{port}/#overview"
    try:
        subprocess.run(["open", url], check=False, capture_output=True)
    except OSError:
        import webbrowser

        webbrowser.open(url)


# ------------------------------------------------------------------------------- commands

def cmd_up(args) -> int:
    try:
        console = paths.console_dir(remember=True)
        repo = paths.circuits_repo()
    except paths.Missing as e:
        return bad(str(e), 2)

    host = "0.0.0.0" if args.lan else args.host
    port = args.port
    running = _pid_on_port(port)
    if running:
        ours = "console.py" in _command_of(running)
        if client.is_up(port):
            say(f"a console is already answering on http://127.0.0.1:{port} (pid {running}"
                f"{', console.py' if ours else ', not console.py'}).")
            say("  leave it as it is, `c3s down` to stop it, or `c3s up --port <other>` beside it.")
            if args.service:
                # Never silently skip --service: how the console starts is exactly what the
                # person asked to change, and rewriting the plist under a running console
                # would leave the two disagreeing until something restarted it.
                if service.loaded():
                    say("  --service: the launchd agent is already loaded. To change how it starts "
                        "(port, --lan, --cardputer): `c3s down --service`, then `c3s up --service …`.")
                else:
                    say("  --service was NOT installed: this console runs outside launchd. "
                        "`c3s down` first, then `c3s up --service`.")
                return 1
            return cmd_status(args) if ours else 1
        return bad(f"port {port} is taken by pid {running} ({_command_of(running) or 'unknown'}) and it is not "
                   f"answering /api/state: stop it, or use --port <other>.")

    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    env = _console_env(repo, host, port, args.cardputer)
    began = time.monotonic()
    if args.service:
        if args.cardputer:
            os.environ["REFLEX_CARDPUTER"] = args.cardputer   # plist_body picks it up
        plist = service.write_plist(console, repo, host, port)
        ok, note = service.bootstrap()
        if not ok:
            return bad(f"launchd would not load {plist}: {note}")
        say(f"launchd agent {paths.LABEL} loaded from {plist}")
        say("  it starts at login, and is restarted if it dies (KeepAlive).")
    else:
        log = paths.LOG_FILE.open("a")
        log.write(f"\n=== c3s up {time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"host={host} port={port} python={sys.executable} ===\n")
        log.flush()
        child = subprocess.Popen([sys.executable, "-u", "console.py"], cwd=console, env=env,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        paths.PID_FILE.write_text(f"{child.pid}\n")

    try:
        took = client.wait_until_up(port)
    except client.ConsoleDown as e:
        return bad(f"{e}\n  the last lines of the log:\n" + _tail(paths.LOG_FILE, 12))
    _remember(host=host, port=port, console_dir=str(console), repo=str(repo),
              python=sys.executable, via="launchd" if args.service else "process",
              started_at=time.time())

    say(f"console up on http://127.0.0.1:{port} in {took:.1f}s "
        f"(bound to {host}; log: {paths.LOG_FILE})")
    say(f"  total from `c3s up` to /api/state answering 200: {time.monotonic() - began:.1f}s")
    token = client.operator_token()
    say()
    say("operator token — the person's key to install rules, confirm and block:")
    say(f"  {token or 'not written yet; see the log'}")
    say(f"  kept in {paths.TOKEN_FILE} (mode 600). The page asks for it once.")
    say()
    if host == "0.0.0.0":
        url = _pairing_url(port, token)
        if url:
            say("pair a phone on the same Wi-Fi — scan this, and the page keeps the token:")
            _print_qr(url, args.invert_qr)
            say("  the token is in that URL. Only scan it on a network you trust; "
                "`c3s token rotate` replaces it.")
            _pairing_caveats(port, with_token=True)
        else:
            say("no LAN address found, so there is nothing to pair with yet.")
    else:
        say(f"bound to {host}: only this machine can reach it. For a phone on the same Wi-Fi, "
            "`c3s up --lan` (docs/INSTALL.md says what that exposes).")
    if not args.no_browser:
        _open_page(port)
    return 0


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError as e:
        return f"  (no log: {e})"


def cmd_status(args) -> int:
    port = args.port
    say(f"console        http://127.0.0.1:{port}")
    known = _recalled()
    try:
        state = client.state(port)
    except Exception as e:
        say(f"               not answering ({e})")
        say(f"launchd        {'loaded' if service.loaded() else 'not loaded'} ({paths.LABEL})")
        say(f"log            {paths.LOG_FILE}")
        say(f"last start     {known.get('via', '—')} at "
            f"{time.strftime('%H:%M:%S', time.localtime(known['started_at'])) if known.get('started_at') else '—'}")
        return 3
    pid = service.pid() or _pid_on_port(port)
    up_for = time.time() - float(state.get("started_at") or time.time())
    count, pending_text = _pending(state)
    agents = state.get("agents", [])
    blocked = [a["agent"] for a in agents if (a.get("armed") or {}).get("blocked")]
    under_launchd = service.loaded()
    say(f"answering      yes, {up_for / 60:.0f} min (pid {pid or '?'}, "
        f"{'launchd' if under_launchd else 'plain process'})")
    # The plist is the record for a service; a note from the last `c3s up` for a plain
    # process. Either beats guessing, because "bound to" is what decides whether a phone
    # can reach it at all.
    say(f"bound to       {_bound_host() or '(unknown: this console was not started by c3s)'}")
    say(f"agents         {len(agents)}" + (f" — blocked: {', '.join(blocked)}" if blocked else ""))
    say(f"waiting for a person  {pending_text}")
    circuits = [f"{c}:{'deny' if p.get('deny_all') else str(p['circuit']['nand']) + '+' + str(p['circuit']['latch'])}"
                for c, p in (state.get("policies") or {}).items() if p]
    say(f"circuits       {' '.join(circuits) or 'none installed'}")
    chain = state.get("chain") or {}
    say(f"second opinion {'on — ' + str(chain.get('rpc')) if chain.get('enabled') else 'off'}")
    say(f"log            {paths.LOG_FILE}")
    if count:
        say()
        say(f"{count} thing(s) wait for you: open http://127.0.0.1:{port}/#approvals")
    return 0


def cmd_down(args) -> int:
    port = args.port
    stopped = False
    if args.service or service.loaded():
        if not args.service:
            say(f"the console is a launchd agent ({paths.LABEL}): killing it only makes launchd "
                "start it again. `c3s down --service` unloads it.")
            return 1
        ok, note = service.bootout()
        say(f"launchd agent {paths.LABEL}: {note}")
        paths.PLIST.unlink(missing_ok=True)
        say(f"removed {paths.PLIST}")
        stopped = ok
    pid = _pid_on_port(port)
    if pid and "console.py" in _command_of(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if _pid_on_port(port) != pid:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
        say(f"stopped the console on :{port} (pid {pid})")
        paths.PID_FILE.unlink(missing_ok=True)
        stopped = True
    elif pid:
        return bad(f"pid {pid} holds :{port} but it is not console.py ({_command_of(pid)}): left alone.")
    if not stopped:
        say(f"nothing to stop on :{port}")
    return 0


def _console_module():
    """Import the checkout's console.py without starting a server.

    Only for the few things the console defines for a command line to call — today
    `token_rotate(agent)` (I-3), which needs no console running because the binding lives
    in a file. Importing it puts the circuits repository on sys.path and compiles nothing.
    """
    import importlib.util

    path = paths.console_dir() / "console.py"
    spec = importlib.util.spec_from_file_location("reflex_console_module", path)
    if spec is None or spec.loader is None:
        raise paths.Missing(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    sys.modules[spec.name] = module          # @dataclass looks the module up while it runs
    spec.loader.exec_module(module)
    return module


def cmd_token(args) -> int:
    token = client.operator_token()
    if getattr(args, "agent", None):
        # I-3: rotating an *agent's* token is dropping the binding, not issuing a secret —
        # the console only ever held the hash, and the next token is whatever that agent's
        # own environment says. console.py owns that file; this only calls it.
        if args.rotate != "rotate":
            return bad("--agent goes with rotate: `c3s token rotate --agent <name>`.")
        try:
            rotate = getattr(_console_module(), "token_rotate", None)
        except paths.Missing as e:
            return bad(str(e), 2)
        if rotate is None:
            return bad("this checkout's console.py has no token_rotate(): update it (I-3 in docs/API.md).")
        result = rotate(args.agent)
        if result.get("rotated"):
            say(f"dropped the binding for {args.agent}: {result.get('next')}")
        else:
            say(f"{args.agent}: {result.get('why')}")
        return 0
    if args.rotate == "rotate":
        import secrets

        if os.environ.get("REFLEX_OPERATOR_TOKEN"):
            return bad("REFLEX_OPERATOR_TOKEN is set in this shell, which overrides the file: "
                       "unset it before rotating, or the new token would be ignored.")
        pid = service.pid() or _pid_on_port(args.port)
        pinned = pid is not None and "REFLEX_OPERATOR_TOKEN=" in _env_of(pid)
        fresh = secrets.token_urlsafe(24)
        paths.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        paths.TOKEN_FILE.write_text(fresh)
        try:
            paths.TOKEN_FILE.chmod(0o600)
        except OSError:
            pass
        say(f"new operator token written to {paths.TOKEN_FILE}:")
        say(f"  {fresh}")
        if pinned:
            say("  the running console was started with REFLEX_OPERATOR_TOKEN in its environment, "
                "so it keeps using that one: restart it without that variable for this to take effect.")
            return 0
        if service.loaded():
            ok, note = service.kickstart()
            say(f"  restarting the launchd agent so it reads the new token: {note}")
            if ok:
                try:
                    say(f"  answering again after {client.wait_until_up(args.port):.1f}s")
                except client.ConsoleDown as e:
                    return bad(str(e))
        elif pid:
            say("  the running console still holds the old token in memory: `c3s down` then `c3s up`.")
        say("  every page, phone and bot that kept the old token must be given this one; "
            "the old pairing QR is now useless.")
        return 0
    if not token:
        say(f"no operator token yet: it is written to {paths.TOKEN_FILE} the first time the console starts.")
        return 3
    say(token)
    if os.environ.get("REFLEX_OPERATOR_TOKEN"):
        say("  (from REFLEX_OPERATOR_TOKEN in this shell, not from the file)", )
    else:
        say(f"  (from {paths.TOKEN_FILE})")
    return 0


def cmd_pair(args) -> int:
    token = client.operator_token()
    ips = paths.lan_ipv4()
    if not ips:
        return bad("this machine has no LAN address, so there is nothing to pair with.")
    ip = args.ip or ips[0]
    url = _pairing_url(args.port, None if args.no_token else token, ip)
    assert url
    bound = _bound_host()
    say(f"scan this on a phone on the same Wi-Fi ({ip}):")
    say()
    _print_qr(url, args.invert_qr)
    say()
    if bound and bound != "0.0.0.0":
        say(f"the console is bound to {bound}, so the phone cannot reach it: restart it with "
            "`c3s down && c3s up --lan`.")
    if not args.no_token:
        say("the token is in that URL: it crosses your network once and the page removes it from "
            "the address bar. On a network you do not trust, use `c3s pair --no-token` and type "
            "the token by hand, or `c3s token rotate` afterwards.")
        _pairing_caveats(args.port, with_token=True)
    if len(ips) > 1:
        say(f"other addresses of this machine: {', '.join(ips[1:])} (`c3s pair --ip <one>`)")
    return 0


def cmd_stop_all(args) -> int:
    token = client.operator_token()
    if not token:
        return bad(f"no operator token in {paths.TOKEN_FILE}: start the console first.")
    try:
        state = client.state(args.port)
    except Exception as e:
        return bad(f"{e}: nothing was changed.")
    agents = [a["agent"] for a in state.get("agents", [])]
    if not agents:
        say("the console knows no agents yet, so there is nothing to stop. Rules stay as they are; "
            "any agent that appears later meets them.")
        return 0
    bit = 0 if args.resume else 1
    failed = []
    for name in agents:
        status, body = client.write_person_bit(name, {"blocked": bit}, args.port, token)
        mark = "ok" if status == 200 else f"{status} {body.get('error', body)}"
        if status != 200:
            failed.append(name)
        say(f"  {'unblocked' if args.resume else 'blocked'} {name}: {mark}")
    done = "resumed" if args.resume else "stopped"
    say(f"{len(agents) - len(failed)}/{len(agents)} agent(s) {done}")
    if not args.resume:
        say("`blocked` stays where it was put: every class whose rules forbid acting while blocked "
            "refuses from now on. `c3s stop-all --resume` lifts it, as does the page or the device.")
    return 1 if failed else 0


def cmd_demo(args) -> int:
    try:
        console = paths.console_dir()
    except paths.Missing as e:
        return bad(str(e), 2)
    workbench = console / "examples" / "workbench.py"
    if not workbench.is_file():
        say("the simulated workbench (mailbox, calendar, files) is W4's and is not in this checkout yet: "
            f"it will be {workbench}.")
        say("until then: `c3s up`, then Connect on the page shows how to point a model at the console.")
        return 3
    say(f"running {workbench}")
    return subprocess.run([sys.executable, str(workbench), *args.rest], cwd=console).returncode


def cmd_menubar(args) -> int:
    try:
        from menubar.app import main as menubar_main
    except ImportError as e:
        return bad(f"the menu bar needs rumps: `uv tool install 'c3s-circuit-agent[menubar]'` "
                   f"or `pip install rumps` ({e}).")
    return menubar_main(port=args.port, print_menu=args.print_menu)


# ---------------------------------------------------------------------------------- entry

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="c3s", description="The boundary console: start it, see it, stop it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Rules are compiled to circuits and every decision is made by them; nothing here "
               "acts for an agent, holds a key or signs anything.")
    parser.add_argument("--port", type=int, default=paths.default_port(),
                        help="the console's port (default %(default)s, or CONSOLE_PORT)")
    subs = parser.add_subparsers(dest="command", required=True)

    up = subs.add_parser("up", help="start the console and open the page")
    up.add_argument("--host", default=paths.default_host(), help="address to bind (default %(default)s)")
    up.add_argument("--lan", action="store_true",
                    help="bind 0.0.0.0 so a phone on the same Wi-Fi can reach it, and print the pairing QR")
    up.add_argument("--service", action="store_true",
                    help="also install the launchd agent, so a reboot or a kill brings it back")
    up.add_argument("--no-browser", action="store_true", help="do not open the page")
    up.add_argument("--cardputer", metavar="PORT", default=os.environ.get("REFLEX_CARDPUTER"),
                    help="the Cardputer as a physical confirm key: 1 to find it, or a /dev path")
    up.add_argument("--invert-qr", action="store_true", help="QR for a light terminal background")
    up.set_defaults(func=cmd_up)

    status = subs.add_parser("status", help="is it running, and what waits for a person")
    status.set_defaults(func=cmd_status)

    down = subs.add_parser("down", help="stop the console")
    down.add_argument("--service", action="store_true", help="also unload and remove the launchd agent")
    down.set_defaults(func=cmd_down)

    token = subs.add_parser("token", help="show the operator token")
    token.add_argument("rotate", nargs="?", choices=["rotate"], help="replace it with a new one")
    token.add_argument("--agent", metavar="NAME",
                       help="rotate that agent's own token instead (I-3): drop the binding, "
                            "so its next request with a token binds the name again")
    token.set_defaults(func=cmd_token)

    pair = subs.add_parser("pair", help="print the pairing QR again")
    pair.add_argument("--ip", help="which of this machine's addresses to put in the URL")
    pair.add_argument("--no-token", action="store_true", help="leave the token out of the URL")
    pair.add_argument("--invert-qr", action="store_true", help="QR for a light terminal background")
    pair.set_defaults(func=cmd_pair)

    stop = subs.add_parser("stop-all", help="block every agent the console knows")
    stop.add_argument("--resume", action="store_true", help="lift it again")
    stop.set_defaults(func=cmd_stop_all)

    demo = subs.add_parser("demo", help="the simulated workbench")
    demo.add_argument("rest", nargs=argparse.REMAINDER)
    demo.set_defaults(func=cmd_demo)

    bar = subs.add_parser("menubar", help="the macOS menu bar app (needs the menubar extra)")
    bar.add_argument("--print-menu", action="store_true", help="build it and print its items, without running")
    bar.set_defaults(func=cmd_menubar)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except paths.Missing as e:
        return bad(str(e), 2)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
