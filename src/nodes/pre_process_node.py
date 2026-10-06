"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import (
    MAX_REQUEST_CHARS,
    normalise_context,
    scan_pii,
    scan_pii_payload,
    screen_injection,
)


class PreProcessNode(FunctionNode):
    """Outer backbone: admit only a request this pipeline is willing to work on.

    Enforces the trust gate, bounds the request, screens the parsed payload for
    prompt-injection forms and for personal data, and normalises the caller
    context to its declared contract. The request itself is handed on unchanged
    in ``validated_input``; field-level validation belongs to the domain
    validator inside the workflow graph, which owns the product contract.

    The screens here are the template's own. The platform applies an input
    policy of its own before this node runs, but that policy does not refuse
    every form — ``<<SYS>>`` and a directive split by markup both pass it — so a
    template that relied on it would return success on the forms it misses.
    """

    # Outer backbone S-1 gate: external callers must present at least VERIFIED_EXTERNAL.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("user_input", "")
        raw_context = state.get("input_context", {})

        # Type guard: reject non-string input before any string operation
        # (a dict / int / None payload would otherwise fail on .strip()).
        if not isinstance(user_input, str):
            return self._refuse(
                state,
                "non_string_input",
                f"user_input must be a string, got {type(user_input).__name__}",
            )

        if not user_input.strip():
            return self._refuse(state, "empty_input", "user_input is empty or missing")

        if len(user_input) > MAX_REQUEST_CHARS:
            return self._refuse(
                state,
                "request_too_large",
                f"user_input must be at most {MAX_REQUEST_CHARS} characters",
            )

        stripped = user_input.strip()

        try:
            parsed: Any = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            parsed = None

        # Screen the RAW text first: a payload can carry a control token in a
        # position that JSON parsing would never expose as a value. Then screen
        # the PARSED payload depth-first, keys included, because a \u-escaped
        # directive is invisible until the parser has resolved it.
        finding = screen_injection(stripped)
        if finding is None and parsed is not None:
            finding = screen_injection(parsed)
        if finding is not None:
            # silent: the message names the screen finding itself.
            return self._refuse(state, finding, f"request refused by the input screen ({finding})", silent=True)

        pii_category = scan_pii_payload(parsed) if parsed is not None else scan_pii(stripped)
        if pii_category is not None:
            return self._refuse(
                state,
                "pii_detected",
                f"request carries personal data ({pii_category}); " "remove it before requesting a disclosure document",
            )

        context = normalise_context(raw_context)

        emit_trace_event(
            "pre_process_validation_passed",
            {
                "channel": context["channel"],
                "review_policy": context["review_policy"],
                "input_length": len(stripped),
            },
            state,
        )

        return {
            "validated_input": stripped,
            "caller_channel": context["channel"],
            "review_policy": context["review_policy"],
            "status": AgentStatus.SUCCESS.value,
        }

    def _refuse(self, state: dict[str, Any], reason: str, message: str, *, silent: bool = False) -> Dict[str, Any]:
        """Refuse the request, naming the reason from a closed set and never the value."""
        emit_trace_event("pre_process_validation_failed", {"reason": reason}, state)
        return {
            "status": AgentStatus.ERROR.value,
            "error_log": [f"PreProcessNode: {message}"],
            # The runner surfaces `formatted_output or result` as `output`. A reason left only in
            # error_log reaches no one: the terminal result carries just `status`, and get_output()
            # does not copy error_log out of the graph -- the caller sees a blank spinner.
            # A SCREENED refusal stays silent. Its message names the marker that caught the
            # payload, and handing that back lets an attacker probe the screen one try at a
            # time. A VALIDATION refusal says what to fix -- without it the caller cannot tell
            # a rejected request from a hung one.
            **(
                {}
                if silent
                else {"formatted_output": "Request could not be completed. " + f"PreProcessNode: {message}"}
            ),
        }
