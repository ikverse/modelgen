"""When the server stops by itself, and how it restarts.

ModelGen.bat starts the server with --auto-stop: it stops GRACE_S after its last page closes (a reload
or an update restart reconnects well inside that), but never while a build or a /v1 job is running or
queued; those finish first. A start with no page (the quiet start after an update) stops after
NO_PAGE_S if no page ever connects. Restart starts ModelGen.vbs /noopen and closes this server; the
open page reconnects to the new one.
"""

import os
import subprocess
import threading
import time

from . import config

GRACE_S = 15
NO_PAGE_S = 120
CHECK_S = 1
RESTART_DELAY_S = 0.5


class Watch:
    """Counts the open pages and says when the server may stop."""

    def __init__(self, busy, grace=GRACE_S, no_page=NO_PAGE_S, clock=time.monotonic):
        self.busy, self.grace, self.no_page, self.clock = busy, grace, no_page, clock
        self.pages = 0
        self.seen = False
        self.since = clock()  # when the page count last fell to zero (or the start)

    def opened(self):
        self.pages += 1
        self.seen = True

    def closed(self):
        self.pages = max(0, self.pages - 1)
        if self.pages == 0:
            self.since = self.clock()

    def due(self):
        if self.pages:
            return False
        if self.clock() - self.since < (self.grace if self.seen else self.no_page):
            return False
        return not self.busy()


def launcher():
    return config.ROOT / "ModelGen.vbs"


def restart(delay=RESTART_DELAY_S, popen=subprocess.Popen, exit_now=None):
    """Start a new ModelGen with no window and no new page, then close this one. The start goes
    through cmd's `start`, so the new server is not a child of this one: ModelGen.bat stops the old
    server with its whole process tree, and must not take the new one down with it."""
    vbs = launcher()
    if not vbs.is_file():
        raise FileNotFoundError(f"{vbs.name} is missing, so ModelGen cannot start itself again")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def go():
        popen(["cmd", "/c", "start", "", "wscript.exe", str(vbs), "/noopen"], creationflags=flags,
              close_fds=True, cwd=str(config.ROOT))
        threading.Timer(1.0, exit_now or (lambda: os._exit(0))).start()

    threading.Timer(delay, go).start()  # the answer reaches the page first
