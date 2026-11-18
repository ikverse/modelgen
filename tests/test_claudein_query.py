"""Agent runs through ClaudeIn, tested with ClaudeIn's own fake: no ClaudeIn, no Claude, nothing spent."""

import asyncio
import json

from claudein_client.testing import FakeClaudeIn, answer_text

from modelgen import claudein_query, db, runner
from modelgen.messages import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from modelgen.runner import AgentTool

ECHO = AgentTool("echo", "Echo the arguments.", {"type": "object", "properties": {"x": {"type": "integer"}}},
                 lambda args: {"echo": args})


def through(fake):
    """A query_fn that runs the real adapter against the fake ClaudeIn."""
    def query_fn(*, prompt, options):
        return claudein_query.query(prompt=prompt, options=options, run=fake.run)
    return query_fn


def run(fake, name="builder", tools=(ECHO,)):
    return asyncio.run(runner.run_agent(name, "do it", list(tools), project="p", doing="part x",
                                        query_fn=through(fake)))


def collect(fake, options):
    async def main():
        return [m async for m in claudein_query.query(prompt="hi", options=options, run=fake.run)]
    return asyncio.run(main())


def test_a_run_asks_claudein_as_modelgen_with_the_agents_settings():
    fake = FakeClaudeIn().reply("built it")
    res = run(fake)
    assert res.ok and res.text == "built it"
    call = fake.calls[0]
    agent = runner.load_agent("builder")
    assert call["app"] == "modelgen" and call["model"] == agent.model and call["system"] == agent.prompt
    assert call["prompt"] == "do it" and call["tools"] == ["echo"] and call["builtin_tools"] == []
    assert call["tool_timeout_s"] == 600 and call["max_budget_usd"] >= claudein_query.MIN_BUDGET_USD
    assert db.runs()[0]["ok"] == 1


def test_only_the_researcher_asks_for_the_web_tools():
    fake = FakeClaudeIn()
    run(fake, "researcher")
    run(fake, "critic")
    assert fake.calls[0]["builtin_tools"] == ["WebSearch", "WebFetch"]
    assert fake.calls[1]["builtin_tools"] == []


def test_the_agents_tools_run_here_and_their_answers_go_back(tmp_path):
    from PIL import Image
    picture = tmp_path / "p.png"
    Image.new("RGB", (4, 4), (200, 10, 10)).save(picture)
    look = AgentTool("look", "", {"type": "object", "properties": {}}, lambda a: {"ok": True, "_images": [str(picture)]})
    fake = FakeClaudeIn().reply("looked", tools=[("echo", {"x": 3}), ("look", {})])
    res = run(fake, tools=(ECHO, look))
    assert res.ok and res.tool_calls == [{"name": "echo", "input": {"x": 3}}, {"name": "look", "input": {}}]
    (_, _, echoed), (_, _, seen) = fake.tool_answers
    assert json.loads(answer_text(echoed)) == {"echo": {"x": 3}}
    assert seen["content"][1]["type"] == "image" and seen["content"][1]["mime_type"] == "image/png"
    calls = runner.STATUS["builder-1"]["calls"]
    assert [c["tool"] for c in calls] == ["echo", "look"] and all(c["ok"] for c in calls)


def test_claudein_not_running_is_a_failed_run_that_says_so():
    res = run(FakeClaudeIn().unavailable())
    assert not res.ok and "ClaudeIn is not running" in res.error
    assert db.runs()[0]["ok"] == 0
    assert runner.STATUS["builder-1"]["state"] == "failed"


def test_the_plan_limit_is_named_with_its_reset_time():
    res = run(FakeClaudeIn().fail("rate_limited", "Claude plan limit reached; it resets at 14:00."))
    assert not res.ok
    assert runner.account_limit(res.error) == "Claude session limit reached, resets at 14:00."


def test_rounds_is_the_agents_own_limit():
    res = run(FakeClaudeIn().reply("", ending="rounds"))
    assert not res.ok and res.error.startswith("limit hit: error_max_")
    assert runner.account_limit(res.error) is None


def test_other_refusals_show_claudeins_reason():
    res = run(FakeClaudeIn().fail("not_allowed", "modelgen isn't allowed to use claude-opus-5-5."))
    assert not res.ok and res.error == "ClaudeIn: modelgen isn't allowed to use claude-opus-5-5."


def test_text_before_a_tool_call_and_the_result_come_as_messages():
    fake = FakeClaudeIn().reply("done", tools=[("echo", {"x": 1})])
    options = runner.build_options(runner.load_agent("critic"), [ECHO])
    msgs = collect(fake, options)
    assert isinstance(msgs[0], AssistantMessage) and isinstance(msgs[0].content[0], ToolUseBlock)
    assert isinstance(msgs[1], AssistantMessage) and msgs[1].content == [TextBlock(text="done")]
    assert isinstance(msgs[-1], ResultMessage) and not msgs[-1].is_error and msgs[-1].result == "done"
    assert msgs[-1].usage["input_tokens"] == 10


def test_caps_are_kept_inside_what_claudein_accepts():
    fake = FakeClaudeIn()
    options = runner.build_options(runner.load_agent("critic"), [], caps={"max_turns": 999, "max_budget_usd": 0.01})
    collect(fake, options)
    assert fake.calls[0]["max_turns"] == 200 and fake.calls[0]["max_budget_usd"] == 0.10
