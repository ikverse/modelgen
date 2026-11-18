"""The messages an agent run yields, and the settings it is asked with.

These have the same names and fields as the Claude Agent SDK's, so the runner and the tests read them as
before, but ModelGen no longer needs the SDK itself (about 285 MB): every agent goes through ClaudeIn.
"""

from dataclasses import dataclass, field


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: list = field(default_factory=list)
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list
    model: str = ""


@dataclass
class UserMessage:
    content: list


@dataclass
class ResultMessage:
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    total_cost_usd: float | None = None
    usage: dict | None = None
    result: str | None = None
    errors: list | None = None
    api_error_status: int | None = None


@dataclass
class AgentOptions:
    """What one agent run is asked with. tools are claudein_client.Tool objects; builtin_tools lists ClaudeIn's
    own web tools for an agent that may use the web ([] for every other agent)."""
    model: str
    system: str
    tools: list = field(default_factory=list)
    builtin_tools: list = field(default_factory=list)
    max_turns: int | None = None
    max_budget_usd: float | None = None
    tool_timeout_s: float | None = None
    app: str = "modelgen"
