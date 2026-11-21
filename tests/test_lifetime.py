"""The server stops by itself a little after its last page closes, but not while work is running."""

from fastapi.testclient import TestClient

from modelgen import lifetime, server


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def watch(busy=False):
    clock = Clock()
    state = {"busy": busy}
    return lifetime.Watch(lambda: state["busy"], grace=15, no_page=120, clock=clock), clock, state


def test_it_stops_15_seconds_after_the_last_page_closes():
    w, clock, _ = watch()
    w.opened()
    w.opened()
    w.closed()
    clock.t = 100
    assert not w.due()  # one page is still open
    w.closed()
    clock.t = 114
    assert not w.due()
    clock.t = 115
    assert w.due()


def test_a_page_that_reconnects_inside_the_wait_keeps_it_running():
    w, clock, _ = watch()
    w.opened()
    w.closed()
    clock.t = 10
    w.opened()  # a reload, or the page coming back after an update restart
    clock.t = 60
    assert not w.due()
    w.closed()
    clock.t = 74
    assert not w.due()  # the wait starts again from the second close


def test_running_builds_and_jobs_keep_it_running_until_they_finish():
    w, clock, state = watch(busy=True)
    w.opened()
    w.closed()
    clock.t = 1000
    assert not w.due()
    state["busy"] = False
    assert w.due()


def test_a_start_with_no_page_waits_two_minutes():
    w, clock, _ = watch()
    clock.t = 119
    assert not w.due()
    clock.t = 120
    assert w.due()


def test_the_page_socket_is_counted(monkeypatch):
    w, _, _ = watch()
    monkeypatch.setattr(server, "_watch", w)
    with TestClient(server.app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert w.pages == 1
    assert w.pages == 0 and w.seen


def test_restart_asks_first_while_a_build_runs(monkeypatch):
    called = []
    monkeypatch.setattr(lifetime, "restart", lambda: called.append(True))
    monkeypatch.setattr(server, "_busy", lambda: ["car"])
    with TestClient(server.app) as c:
        assert c.post("/api/restart", json={}).json() == {"action": "confirm", "running": ["car"]}
        assert not called
        monkeypatch.setattr(server.updater, "running_builds", lambda: [])
        assert c.post("/api/restart", json={"force": True}).json() == {"action": "restarting"}
    assert called == [True]


def test_restart_starts_the_launcher_outside_this_process_tree(monkeypatch):
    started, exited = [], []
    monkeypatch.setattr(lifetime.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda self: fn()})())
    lifetime.restart(popen=lambda cmd, **kw: started.append(cmd), exit_now=lambda: exited.append(True))
    assert started[0][:4] == ["cmd", "/c", "start", ""] and started[0][-1] == "/noopen"
    assert started[0][-2].endswith("ModelGen.vbs") and exited == [True]
