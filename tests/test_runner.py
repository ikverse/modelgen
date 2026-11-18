"""The runner, tested with a fake query function: no Claude call is made."""

import asyncio
import json
from pathlib import Path

import pytest

from modelgen import config, db, runner
from modelgen.messages import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from modelgen.runner import AgentTool

PLAN_MODELS = {
    "director": "claude-opus-5-5", "brief": "claude-sonnet-5-5", "architect": "claude-opus-5-5",
    "builder": "claude-sonnet-5-5", "assembler": "claude-sonnet-5-5", "materials": "claude-sonnet-5-5",
    "animator": "claude-sonnet-5-5", "critic": "claude-opus-5-5", "librarian": "claude-haiku-5-5",
    "shell_architect": "claude-opus-5-5", "shell_builder": "claude-opus-5-5",
    "researcher": "claude-sonnet-5-5", "caster": "claude-haiku-5-5",
}
# who they sit in for on the map
SEATS = {"shell_architect": "architect", "shell_builder": "builder", "researcher": "brief", "caster": "brief"}


def result_message(**kw):
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
                session_id="s", total_cost_usd=0.0123, result="final text")
    return ResultMessage(**{**base, **kw})


def fake_query(captured, messages=None):
    async def query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        for m in messages or [
            AssistantMessage(content=[TextBlock(text="thinking"),
                                      ToolUseBlock(id="t1", name="echo", input={"x": 1})], model="m"),
            result_message(),
        ]:
            yield m
    return query


ECHO = AgentTool("echo", "Echo the arguments.", {"type": "object", "properties": {"x": {"type": "integer"}}},
                 lambda args: {"echo": args})


def run(name="builder", **kw):
    captured = {}
    res = asyncio.run(runner.run_agent(name, "do it", [ECHO], project="p", doing="part x",
                                       query_fn=fake_query(captured, kw.get("messages"))))
    return res, captured


def test_every_agent_file_loads_with_the_planned_model():
    agents = {a.name: a for a in runner.list_agents()}
    assert set(agents) == set(PLAN_MODELS)
    for name, model in PLAN_MODELS.items():
        assert agents[name].model == model
        assert len(agents[name].prompt) > 400
        assert agents[name].seat == SEATS.get(name, name)


def test_a_shell_agent_sits_in_its_counterpart_seat_with_its_own_caps():
    captured = {}
    asyncio.run(runner.run_agent("shell_builder", "do it", [ECHO], project="p", doing="part body",
                                 query_fn=fake_query(captured)))
    o = captured["options"]
    caps = config.caps("shell_builder")
    assert o.model == "claude-opus-5-5"
    assert o.max_turns == caps["max_turns"] and o.max_budget_usd == caps["max_budget_usd"]
    assert db.runs()[0]["agent"] == "shell_builder" and db.runs()[0]["seat"] == "builder-1"
    assert runner.STATUS["builder-1"]["role"] == "builder"
    roles = [s["role"] for s in runner.status_snapshot()]
    assert "shell_builder" not in roles and "shell_architect" not in roles
    assert roles.count("architect") == 1


def test_options_follow_the_rules():
    _, captured = run()
    o = captured["options"]
    caps = config.caps("builder")
    assert o.app == "modelgen"
    assert o.builtin_tools == []  # no web for a builder
    assert [t.name for t in o.tools] == ["echo"]
    # a question to the person must give up before ClaudeIn stops waiting for the tool's answer
    assert config.get("questions", "timeout_s") < o.tool_timeout_s <= 600
    assert o.max_turns == caps["max_turns"] and o.max_budget_usd == caps["max_budget_usd"]
    assert o.model == "claude-sonnet-5-5"
    assert o.system == runner.load_agent("builder").prompt
    assert captured["prompt"] == "do it"


def test_returns_text_tool_calls_and_cost_and_records_the_run():
    res, _ = run()
    assert res.ok and res.text == "final text"
    assert res.tool_calls == [{"name": "echo", "input": {"x": 1}}]
    assert res.cost_usd == 0.0123 and res.turns == 2
    row = db.runs()[0]
    assert row["agent"] == "builder" and row["cost_usd"] == 0.0123 and row["ok"] == 1 and row["project"] == "p"
    assert db.spend("p") == {"model": 0.0123, "today": 0.0123}
    assert runner.STATUS["builder-1"]["state"] == "idle"


def test_limit_hit_is_not_ok_but_cost_is_kept():
    res, _ = run(messages=[result_message(subtype="error_max_budget_usd", is_error=True, total_cost_usd=1.0)])
    assert not res.ok and "limit" in res.error
    assert db.runs()[0]["cost_usd"] == 1.0


def test_a_crash_in_the_query_is_reported_not_raised():
    async def broken(*, prompt, options):
        raise RuntimeError("CLI not found")
        yield  # pragma: no cover

    res = asyncio.run(runner.run_agent("critic", "x", [], query_fn=broken))
    assert not res.ok and "CLI not found" in res.error
    assert db.runs()[0]["ok"] == 0
    assert runner.STATUS["critic"]["state"] == "failed"


def test_tool_handlers_return_json_pictures_and_errors(tmp_path):
    from PIL import Image
    picture = tmp_path / "p.png"
    Image.new("RGB", (4, 4), (200, 10, 10)).save(picture)

    def fails(args):
        raise ValueError("bad input")

    tools = [AgentTool("look", "", {"type": "object", "properties": {}}, lambda a: {"ok": True, "_images": [str(picture)]}),
             AgentTool("fails", "", {"type": "object", "properties": {}}, fails)]
    agent = runner.load_agent("critic")
    look, broken = runner.make_tools(agent, tools)
    out = asyncio.run(look.handler({}))
    assert json.loads(out["content"][0]["text"]) == {"ok": True}
    assert out["content"][1]["type"] == "image" and out["content"][1]["mimeType"] == "image/png"
    err = asyncio.run(broken.handler({}))
    assert err["is_error"] and "bad input" in err["content"][0]["text"]


def test_the_claude_session_limit_is_named_with_its_reset_time():
    err = ("ResultError: Claude Code returned an error result: You've hit your session limit · "
           "resets 1:40am (Africa/Cairo) (exit code: 1)")
    assert runner.account_limit(err) == "Claude session limit reached, resets 1:40am"
    assert runner.account_limit("You've hit your usage limit") == "Claude session limit reached"
    assert runner.account_limit("limit hit: error_max_turns") is None  # the agent's own cap, not the account's
    assert runner.account_limit(None) is None


def test_planning_stops_at_once_on_the_session_limit_and_says_so(monkeypatch):
    import asyncio
    from modelgen import pipeline
    from modelgen.tools.library import Library
    tries = []

    async def out_of_usage(name, prompt, tools=(), **kw):
        tries.append(name)
        return runner.RunResult(agent=name, ok=False, error="ResultError: You've hit your session limit · "
                                                            "resets 1:40am (Africa/Cairo) (exit code: 1)")

    monkeypatch.setattr(runner, "run_agent", out_of_usage)
    name = db.create_project("airpods", "AirPods")["name"]
    with pytest.raises(pipeline.PipelineError) as exc:
        asyncio.run(pipeline._architect(name, {"object": "AirPods"}, Library()))
    assert tries == ["architect"]  # no second try: it would fail the same way until the reset
    assert "did not submit a plan (Claude session limit reached, resets 1:40am)" in str(exc.value)
