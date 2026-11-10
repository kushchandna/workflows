"""The built-in review agents, and choosing which agents a review runs."""

from importlib.resources import files
from types import MappingProxyType

from workflows.code_review.errors import ReviewInputError
from workflows.code_review.models import ReviewAgent


def _builtin(name: str, needs_codebase: bool = False) -> ReviewAgent:
    path = files("workflows.code_review") / "prompts" / "agents" / f"{name}.md"
    return ReviewAgent(
        name=name,
        instructions=path.read_text(encoding="utf-8").strip(),
        needs_codebase=needs_codebase,
    )


BUILTIN_AGENTS = MappingProxyType(
    {
        agent.name: agent
        for agent in [
            _builtin("test-coverage"),
            _builtin("code-patterns", needs_codebase=True),
            _builtin("security"),
            _builtin("ui"),
        ]
    }
)

# The agents a review runs when the input doesn't choose.
DEFAULT_AGENTS: tuple[str, ...] = ("test-coverage", "code-patterns", "security")


def resolve_agents(selected: list[str | ReviewAgent] | None) -> list[ReviewAgent]:
    """The agents to run, in order. A name picks a built-in; a custom agent may reuse one."""
    chosen: list[ReviewAgent] = []
    for item in DEFAULT_AGENTS if selected is None else selected:
        if isinstance(item, str):
            if item not in BUILTIN_AGENTS:
                known = ", ".join(BUILTIN_AGENTS)
                raise ReviewInputError(
                    f"unknown review agent {item!r} (built-in: {known})"
                )
            item = BUILTIN_AGENTS[item]
        if any(agent.name == item.name for agent in chosen):
            raise ReviewInputError(f"review agent {item.name!r} is chosen twice")
        chosen.append(item)
    return chosen
