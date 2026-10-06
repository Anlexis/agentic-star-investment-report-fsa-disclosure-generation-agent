"""AgentCore Platform v1.0"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_factory import resolve_llm
from src.services.llm_review import render_review, review_result
from framework.security import detect_credentials_in_value


class PostProcessNode(FunctionNode):
    """Outer backbone: publish the inner graph's document as the response body.

    Reads ``result`` (set by DisclosureDocGenGraphNode.merge_output) and passes
    it through as ``formatted_output``. No domain transformation happens here —
    the document is assembled and gated inside the inner graph.

    Trust level: ANONYMOUS — the outer pre_process already enforced
    VERIFIED_EXTERNAL.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        result = state.get("result")
        if not isinstance(result, str) or not result.strip():
            # The inner graph either produces a document string or fails. An
            # empty or non-string result must not be published as a success:
            # the response envelope treats an empty formatted_output as absent
            # and falls back to the raw inner result, so publishing one here
            # would re-open the very channel the output gate exists to close.
            emit_trace_event(
                "post_process_output_rejected",
                {"reason": "unusable_result", "result_type": type(result).__name__},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PostProcessNode: inner graph did not return a document"],
                "formatted_output": "",
                "result": "",
            }

        emit_trace_event(
            "post_process_output_finalized",
            {"output_length": len(result), "has_content": bool(result)},
            state,
        )

        # Advisory second read: what the computed result leaves unaccounted for in the
        # caller's own words. It can only append remarks -- the answer above is
        # deterministic and is not revisited. The client is built per invocation and kept
        # in a local, because node instances are shared through the registry LRU cache.
        _llm, _ = resolve_llm(None, state)
        _remarks = review_result(
            _llm,
            user_input=str(state.get("user_input") or ""),
            result=result,
            domain="FIN InvestmentReportFSADisclosureAgent",
        )
        _review = render_review(_remarks)
        # This node has no domain credential scan -- its invariants are presence checks --
        # so the guard calls the framework detector directly. "No domain gate" is not "no
        # gate": the framework re-scans this node's returned dict and RAISES on a match,
        # which discards the whole delta. An advisory remark that turned a success into a
        # discarded error would be changing the outcome, which this design forbids.
        if _review and isinstance(result, str) and not detect_credentials_in_value(result + _review):
            result = result + _review
        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS.value,
        }
