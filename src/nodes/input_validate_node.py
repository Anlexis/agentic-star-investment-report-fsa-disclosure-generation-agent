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

from src.services.service import RequestError, normalise_request


class InputValidateNode(FunctionNode):
    """Inner domain node: turn the request envelope into the validated descriptor.

    This node owns the product contract. Nothing downstream re-reads the raw
    request, so every value the document is built from is either drawn from the
    product catalogue or has passed through the inert renderer here.

    Accepted envelope (JSON object):
      - ``product_name``             (str)    required; rendered into the document
      - ``product_type``             (str)    required; resolved against the catalogue
      - ``target_investor_profile``  (str)    ``retail`` (default) | ``institutional``
      - ``financial_data``           (object) metric name → finite number
      - ``fund_performance_data``    (object) metric name → finite number

    An unrecognised field is refused rather than ignored: a misspelt name that
    is silently dropped produces a document assembled from less than the caller
    believed they supplied.

    Trust level: ANONYMOUS — the trust gate is enforced by the outer backbone.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input") or ""
        if not isinstance(raw, str):
            return self._refuse("request", "must be a JSON object", state)

        try:
            parsed: Any = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return self._refuse("request", "must be valid JSON", state)

        try:
            descriptor = normalise_request(parsed)
        except RequestError as err:
            return self._refuse(err.field, err.reason, state)

        emit_trace_event(
            "input_validation_passed",
            {
                "product_key": descriptor["product_key"],
                "high_risk": descriptor["high_risk"],
                "target_investor_profile": descriptor["target_investor_profile"],
                "financial_metrics": len(descriptor["financial_data"]),
                "performance_metrics": len(descriptor["fund_performance_data"]),
            },
            state,
        )

        return {
            "product_name": descriptor["product_name"],
            "product_key": descriptor["product_key"],
            "product_type": descriptor["product_label"],
            "high_risk": descriptor["high_risk"],
            "target_investor_profile": descriptor["target_investor_profile"],
            "financial_data": json.dumps(descriptor["financial_data"], ensure_ascii=False),
            "fund_performance_data": json.dumps(descriptor["fund_performance_data"], ensure_ascii=False),
            "validated_product_info": json.dumps(
                {
                    "product_name": descriptor["product_name"],
                    "product_key": descriptor["product_key"],
                    "product_type": descriptor["product_label"],
                    "target_investor_profile": descriptor["target_investor_profile"],
                    "financial_metrics": len(descriptor["financial_data"]),
                    "performance_metrics": len(descriptor["fund_performance_data"]),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "status": AgentStatus.SUCCESS.value,
        }

    def _refuse(self, field: str, reason: str, state: dict[str, Any]) -> Dict[str, Any]:
        """Refuse the request naming the field at fault — never the value."""
        emit_trace_event("input_validation_failed", {"field": field}, state)
        return {
            "status": AgentStatus.ERROR.value,
            "error_log": [f"InputValidateNode: {field}: {reason}"],
            # The runner surfaces `formatted_output or result` as `output`. A reason left only in
            # error_log reaches no one: the terminal result carries just `status`, and get_output()
            # does not copy error_log out of the graph -- the caller sees a blank spinner.
            "formatted_output": "Request could not be completed. " + (f"InputValidateNode: {field}: {reason}"),
        }
