"""One agent run through ClaudeIn, the tray program that asks Claude Code with this PC's Claude login.

query(prompt=..., options=AgentOptions) yields the messages the runner reads: AssistantMessage (TextBlock,
ToolUseBlock) while the agent works, then one ResultMessage. ClaudeIn's failures come back as an error
ResultMessage, never as an exception, so the runner records them like any failed run:
- ClaudeIn not running or not set up: "ClaudeIn is not running on this PC ..."
- the Claude plan's limit: a "usage limit" error with its reset time, which runner.account_limit names
- a turn or dollar cap: error_max_turns / error_max_budget_usd, as before

The tool handlers run here, in ModelGen, and claudein_client sends their answers back to ClaudeIn.
ClaudeIn's protocol: ..\\ClaudeIn\\docs\\protocol.md, section 3.4.
"""

import time
from contextlib import aclosing

import claudein_client

from .messages import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

MIN_BUDGET_USD = 0.10   # ClaudeIn refuses less; ModelGen's own spending limits still count the real cost
MAX_BUDGET_USD = 50.0
MAX_TURNS = 200
MAX_TOOL_TIMEOUT_S = 600  # the longest ClaudeIn waits for one tool answer
NOT_RUNNING = "ClaudeIn is not running on this PC. Start ClaudeIn (its icon is in the hidden icons by the clock), then try again."


def _clamp(value, low, high):
    return None if value is None else max(low, min(high, value))


def _usage(u):
    u = u or {}
    return {"input_tokens": u.get("input") or 0, "output_tokens": u.get("output") or 0,
            "cache_read_input_tokens": u.get("cache_read") or 0,
            "cache_creation_input_tokens": u.get("cache_write") or 0}


def _failed(message, started, cost=None, turns=0):
    return ResultMessage(subtype="error_during_execution", duration_ms=int((time.monotonic() - started) * 1000),
                         duration_api_ms=0, is_error=True, num_turns=turns, session_id="claudein",
                         total_cost_usd=cost, errors=[message])


def result_message(r, max_turns, started):
    """ClaudeIn's result as a ResultMessage."""
    cost = r.api_price_usd
    kind = r.error_kind
    subtype, errors, status = "success", None, None
    if r.ok:
        pass
    elif r.ending == "rounds" or kind == "limit":
        stopped_early = max_turns is not None and r.turns and r.turns < max_turns
        subtype = "error_max_budget_usd" if stopped_early else "error_max_turns"
        errors = [r.reason]
    elif kind == "rate_limited":
        subtype, errors, status = "error_during_execution", [f"usage limit reached: {r.reason}"], 429
    elif r.ending == "cut short":
        subtype, errors = "error_during_execution", ["the answer hit the length limit"]
    else:
        subtype, errors = "error_during_execution", [f"ClaudeIn: {r.reason}"]
        if kind == "service" or r.ending == "stopped":
            status = 503
    return ResultMessage(
        subtype=subtype, duration_ms=int((time.monotonic() - started) * 1000), duration_api_ms=0,
        is_error=not r.ok, num_turns=r.turns, session_id="claudein", total_cost_usd=cost, usage=_usage(r.usage),
        result=r.answer, errors=errors, api_error_status=status)


async def query(*, prompt, options, run=None):
    """One agent run through ClaudeIn. `run` is claudein_client.run, or a fake's run in tests."""
    run = run or claudein_client.run
    model = options.model
    max_turns = _clamp(options.max_turns, 1, MAX_TURNS)
    budget = _clamp(options.max_budget_usd, MIN_BUDGET_USD, MAX_BUDGET_USD)
    timeout = _clamp(options.tool_timeout_s, 1, MAX_TOOL_TIMEOUT_S)
    started = time.monotonic()
    text = []

    def flush_text():
        s = "".join(text).strip()
        text.clear()
        return [AssistantMessage(content=[TextBlock(text=s)], model=model)] if s else []

    try:
        stream = run(app=options.app, model=model, system=options.system, prompt=prompt, tools=list(options.tools),
                     builtin_tools=list(options.builtin_tools), max_turns=max_turns, max_budget_usd=budget,
                     tool_timeout_s=timeout)
        async with aclosing(stream):
            async for event in stream:
                if isinstance(event, claudein_client.Text):
                    text.append(event.delta)
                elif isinstance(event, claudein_client.ServerTool):
                    for msg in flush_text():
                        yield msg
                elif isinstance(event, claudein_client.ToolCall):
                    for msg in flush_text():
                        yield msg
                    yield AssistantMessage(content=[ToolUseBlock(id=event.id, name=event.name, input=event.input)],
                                           model=model)
                elif isinstance(event, claudein_client.Done):
                    for msg in flush_text():
                        yield msg
                    yield result_message(event.result, max_turns, started)
                    return
    except claudein_client.ClaudeInUnavailable:
        yield _failed(NOT_RUNNING, started)
        return
    except claudein_client.ClaudeInError as exc:
        yield _failed(f"ClaudeIn refused the request: {exc.message}", started)
        return
    yield _failed(NOT_RUNNING, started)
