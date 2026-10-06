# PB-6: Backbone Invoke Order Verification (FIN-C2-101)
#
# Verifies that a full Graph().invoke() with a real VERIFIED_EXTERNAL caller
# executes the backbone in the mandatory order:
#   InitializeNode → PreProcessNode → DisclosureDocGenGraphNode → PostProcessNode → FinalizeNode
#
# Design rules:
#   - Uses InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL) — the same
#     trust path a real external caller uses. An INTERNAL context would mask the inner-node
#     trust-trap (review finding 5) and produce a false green.
#   - Asserts result["status"] == AgentStatus.SUCCESS.value (a non-SUCCESS status short-circuits
#     main→finalize and skips post_process, making the backbone-order check meaningless).
#   - Asserts result["output"] is not None (the assembled disclosure document).
#   - Asserts result["node_history"] exactly equals the expected 5-node backbone order.
#
# Canonical reference: FIN-C2-101 (Cat-2, DocGen pattern, nested outer/inner graph).

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import DisclosureDocGenGraphNode, InvestmentReportFSADisclosureAgent

# The `main` slot is occupied by DisclosureDocGenGraphNode (a GraphNode subclass).
_MAIN_SLOT_NODE = DisclosureDocGenGraphNode

# Minimum SUCCESS-yielding payload: product_name + product_type are the only required fields.
_VALID_PAYLOAD = '{"product_name": "テスト投資信託", "product_type": "投資信託"}'

# Exact backbone execution order for a SUCCESS-path invoke().
# node_history is Annotated[list[str], operator.add] — BaseNode.__call__() appends
# type(node).__name__ after each successful execute().
_EXPECTED_BACKBONE_ORDER = [
    "InitializeNode",
    "PreProcessNode",
    "DisclosureDocGenGraphNode",
    "PostProcessNode",
    "FinalizeNode",
]


@pytest.fixture(autouse=True)
def patch_domain_emit_trace_events(monkeypatch):
    """Silence domain-node emit_trace_event calls to avoid audit-backend dependency.

    Patches the imported name in each node module (not shared.* in sys.modules —
    which would break the framework's own shared.security imports).
    """
    noop = lambda *a, **k: None  # noqa: E731
    for module_path in (
        "src.nodes.pre_process_node",
        "src.nodes.input_validate_node",
        "src.nodes.prospectus_generate_node",
        "src.nodes.risk_disclosure_and_suitability_node",
        "src.nodes.compliance_checklist_and_human_review_flag_node",
        "src.nodes.output_format_node",
        "src.nodes.post_process_node",
    ):
        monkeypatch.setattr(module_path + ".emit_trace_event", noop)


class TestPbInvokeOrder:
    """PB-6: Full Graph().invoke() must follow the 5-node backbone order."""

    def test_backbone_order_verified_external_caller(self):
        """TC-PB6-01: VERIFIED_EXTERNAL caller triggers full backbone SUCCESS path.

        Exercises the same trust path a real external production caller uses.
        Asserts backbone node order (node_history), SUCCESS status, and non-None output.
        """
        agent = InvestmentReportFSADisclosureAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        # Status must be SUCCESS — any other status short-circuits the backbone
        # (ERROR skips post_process, making the node-order assertion meaningless).
        assert result["status"] == AgentStatus.SUCCESS.value, (
            f"Expected AgentStatus.SUCCESS, got {result['status']!r}. " f"Errors: {result.get('error_log', [])}"
        )

        # Output must be non-None: the inner graph assembled a disclosure document.
        assert result["output"] is not None, "result['output'] should contain the assembled disclosure document"

        # Core PB-6 assertion: backbone node execution order.
        node_history = result.get("node_history", [])
        assert node_history == _EXPECTED_BACKBONE_ORDER, (
            f"Backbone node order mismatch.\n" f"Expected: {_EXPECTED_BACKBONE_ORDER}\n" f"Actual:   {node_history}"
        )

    def test_main_slot_is_graph_node(self):
        """TC-PB6-02: The `main` slot must be occupied by a GraphNode subclass.

        Verifies the Cat-2 nested pattern contract at the class level.
        """
        from framework.nodes.graph_node import GraphNode

        assert issubclass(
            _MAIN_SLOT_NODE, GraphNode
        ), f"{_MAIN_SLOT_NODE.__name__} must be a GraphNode subclass for Cat-2 nested pattern"
        assert _MAIN_SLOT_NODE.__name__ == "DisclosureDocGenGraphNode"
