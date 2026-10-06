"""AgentCore Platform v1.0 — caller-context bridge across the graph boundary."""

# Why this exists: GraphNode.execute() invokes the inner graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)` and forwards no other
# outer state, so the caller context the outer PreProcessNode validated would
# never reach the inner workflow on its own. Two sanctioned subclass hooks
# bridge it:
#
#   DisclosureDocGenGraphNode.extract_input(state)      [before subgraph.invoke]
#       -> set_caller_context({...})
#   DisclosureDocumentWorkflowGraph._extra_initial_state()  [inside subgraph.invoke]
#       -> returns the stashed context as inner state fields
#
# What crosses the bridge is the VALIDATED context — both values are drawn from
# closed sets by PreProcessNode — never the raw mapping the caller sent.
#
# Smuggling the values inside the request JSON is not an option: the platform
# masks that field at every node boundary, and it is the caller's request rather
# than the operator's routing metadata.
#
# A ContextVar keeps the hand-off correct per thread and per task, so concurrent
# invocations inside one process cannot observe each other's context.

from contextvars import ContextVar
from typing import Dict, Optional

_CALLER_CONTEXT: ContextVar[Optional[Dict[str, str]]] = ContextVar("fin_c2_101_caller_context", default=None)


def set_caller_context(context: Optional[Dict[str, str]]) -> None:
    """Stash the validated caller context for the imminent inner-graph invoke."""
    _CALLER_CONTEXT.set(dict(context) if context else {})


def get_caller_context() -> Dict[str, str]:
    """Read (without consuming) the stashed context; ``{}`` when none was set."""
    return _CALLER_CONTEXT.get() or {}
