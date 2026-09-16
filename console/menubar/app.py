"""The fly in the menu bar: what is waiting, and one way to stop everything.

    c3s menubar                # needs the `menubar` extra (rumps)
    c3s menubar --print-menu   # build it and print its items, without running

Five items and no more, because a menu bar is not a console:

    open the console      the page, at #approvals when something waits there
    pending N             how many refusals a person can resolve (I-2's list)
    stop all              write the person's `blocked` bit for every agent
    restart the console   launchd kickstart, or down-and-up
    quit                  quit this menu bar, not the console

It polls `GET /api/state` every two seconds and it must survive a console that is not
running: then the title is the fly with a dot and every item says so, rather than an
exception on the user's screen. The menu bar never approves anything — `confirm` belongs
to the page, the Cardputer or the chat, where the person can read what they are approving.
"""

from __future__ import annotations

import subprocess
import threading

from c3s_cli import console_client as client
from c3s_cli import paths, service

FLY = "🪰"
POLL_S = 2.0

OPEN = "open the console"
PENDING = "pending"
STOP = "stop all"
RESTART = "restart the console"
DOWN = "console not running"


class ConsoleBar:
    """The menu bar app. `rumps` is imported when this is built, not when it is imported,
    so `--print-menu` and the tests work on a machine without it."""

    def __init__(self, port: int) -> None:
        import rumps

        self.rumps = rumps
        self.port = port
        self.app = rumps.App(f"{FLY}", title=FLY, quit_button=None)
        self.item_open = rumps.MenuItem(OPEN, callback=self.open_console)
        self.item_pending = rumps.MenuItem(f"{PENDING} —", callback=self.open_approvals)
        self.item_stop = rumps.MenuItem(STOP, callback=self.stop_all)
        self.item_restart = rumps.MenuItem(RESTART, callback=self.restart)
        self.item_quit = rumps.MenuItem("quit", callback=self.quit)
        self.app.menu = [self.item_open, self.item_pending, None, self.item_stop,
                         self.item_restart, None, self.item_quit]
        self.timer = rumps.Timer(self.refresh, POLL_S)

    # ---------------------------------------------------------------- what the menu says

    def titles(self) -> list[str]:
        return [item.title for item in
                (self.item_open, self.item_pending, self.item_stop, self.item_restart, self.item_quit)]

    def refresh(self, _timer=None) -> None:
        """Poll the console. Any failure means "not running", never a traceback."""
        try:
            state = client.state(self.port, timeout=1.5)
        except Exception:
            self.app.title = f"{FLY}·"
            self.item_pending.title = DOWN
            self.item_stop.title = f"{STOP} (console not running)"
            return
        if "pending" in state:
            count = len(state["pending"])
            self.app.title = f"{FLY} {count}" if count else FLY
            self.item_pending.title = f"{PENDING} {count}" if count else "nothing waiting"
        else:
            # I-2's list is the one place that decides what waits for a person; a console
            # without it gets no guess from here.
            self.app.title = FLY
            self.item_pending.title = f"{PENDING} — (this console has no pending[] yet)"
        blocked = sum(1 for a in state.get("agents", []) if (a.get("armed") or {}).get("blocked"))
        agents = len(state.get("agents", []))
        self.item_stop.title = (f"{STOP} ({blocked}/{agents} blocked)" if blocked else
                                f"{STOP} ({agents} agent{'s' if agents != 1 else ''})")

    # ------------------------------------------------------------------------- the items

    def open_console(self, _item=None) -> None:
        subprocess.run(["open", f"http://127.0.0.1:{self.port}/#overview"], check=False)

    def open_approvals(self, _item=None) -> None:
        subprocess.run(["open", f"http://127.0.0.1:{self.port}/#approvals"], check=False)

    def stop_all(self, _item=None) -> None:
        token = client.operator_token()
        if not token:
            self.notify("no operator token", f"none in {paths.TOKEN_FILE}")
            return
        try:
            state = client.state(self.port, timeout=2.0)
        except Exception as e:
            self.notify("the console is not answering", str(e)[:120])
            return
        names = [a["agent"] for a in state.get("agents", [])]
        if not names:
            self.notify("nothing to stop", "the console knows no agents yet")
            return
        ok = 0
        for name in names:
            try:
                status, _ = client.write_person_bit(name, {"blocked": 1}, self.port, token)
                ok += status == 200
            except Exception:
                pass
        self.notify("stop all", f"{ok}/{len(names)} agent(s) blocked — the page or `c3s stop-all --resume` lifts it")
        self.refresh()

    def restart(self, _item=None) -> None:
        def work() -> None:
            if service.loaded():
                _ok, note = service.kickstart()
                self.notify("restarting", note[:120] or "launchd kickstart")
            else:
                from c3s_cli import cli

                cli.main(["--port", str(self.port), "down"])
                cli.main(["--port", str(self.port), "up", "--no-browser"])
                self.notify("restarting", "stopped and started again")
            self.refresh()

        threading.Thread(target=work, daemon=True).start()

    def quit(self, _item=None) -> None:
        self.rumps.quit_application()

    def notify(self, title: str, message: str) -> None:
        try:
            self.rumps.notification(title, "", message)
        except Exception:          # notifications need a bundled app; the menu still works
            print(f"{title}: {message}", flush=True)

    def run(self) -> None:
        self.refresh()
        self.timer.start()
        self.app.run()


def main(port: int | None = None, print_menu: bool = False) -> int:
    port = port or paths.default_port()
    if print_menu:
        try:
            bar = ConsoleBar(port)
        except ImportError as e:
            print(f"rumps is not installed: {e}")
            return 1
        bar.refresh()
        print(f"title: {bar.app.title}")
        for title in bar.titles():
            print(f"  {title}")
        return 0
    try:
        ConsoleBar(port).run()
    except ImportError as e:
        print(f"the menu bar needs rumps: pip install rumps ({e})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
