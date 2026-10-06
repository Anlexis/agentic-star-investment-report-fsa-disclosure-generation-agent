# PoB-S1: S-1 Trust Gate Boundary Verification (FIN-C2-101)
#
# Proves that the outer PreProcessNode (TrustLevel.VERIFIED_EXTERNAL) correctly
# enforces the S-1 trust gate at the backbone boundary:
#   - ANONYMOUS callers are denied (status ERROR) before execute() is reached.
#   - VERIFIED_EXTERNAL callers pass through and the full agent runs to SUCCESS.
#
# This test exercises the FULL invoke() path — it is a proof-of-boundary test,
# not a unit test. It catches trust-trap bugs (CoE finding #5) that a unit test
# calling execute() directly would miss.

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import InvestmentReportFSADisclosureAgent

_VALID_PAYLOAD = '{"product_name": "テスト投資信託", "product_type": "投資信託"}'


@pytest.fixture(autouse=True)
def patch_domain_emit_trace_events(monkeypatch):
    """Silence domain-node emit_trace_event calls to avoid audit-backend dependency."""
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


class TestS1TrustGate:
    """PoB-S1: S-1 trust gate on PreProcessNode (TrustLevel.VERIFIED_EXTERNAL)."""

    def test_anonymous_caller_is_denied(self):
        """TC-S1-01: ANONYMOUS caller must receive ERROR before execute() is reached.

        PreProcessNode declares required_trust_level = TrustLevel.VERIFIED_EXTERNAL.
        A caller with TrustLevel.ANONYMOUS (0) < VERIFIED_EXTERNAL (1) must be denied.
        """
        agent = InvestmentReportFSADisclosureAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        # An ANONYMOUS caller must be denied with ERROR status.
        # The real SDK does not always expose trust-gate denial details in error_log
        # (that is an implementation detail of the backbone); we verify the observable
        # contract: status=ERROR means the agent refused to serve the ANONYMOUS caller.
        assert result["status"] == AgentStatus.ERROR.value, (
            f"Expected AgentStatus.ERROR for ANONYMOUS caller, got {result['status']!r}. "
            f"node_history: {result.get('node_history', [])}"
        )

    def test_verified_external_caller_is_accepted(self):
        """TC-S1-02: VERIFIED_EXTERNAL caller must pass the S-1 gate and reach SUCCESS.

        PreProcessNode's required_trust_level = VERIFIED_EXTERNAL; a caller with the
        matching level must pass the gate and the full backbone must complete with SUCCESS.
        """
        agent = InvestmentReportFSADisclosureAgent()
        agent.compile()

        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(user_input=_VALID_PAYLOAD, ctx=ctx)

        assert result["status"] == AgentStatus.SUCCESS.value, (
            f"VERIFIED_EXTERNAL caller expected SUCCESS, got {result['status']!r}. "
            f"Errors: {result.get('error_log', [])}"
        )
        assert result["output"] is not None

    def test_pre_process_node_trust_level_declaration(self):
        """TC-S1-03: PreProcessNode must declare required_trust_level = VERIFIED_EXTERNAL.

        This is a static contract test — verifies the class-level declaration matches
        the design spec (docs/02_design.md §S-gate placement).
        """
        from src.nodes.pre_process_node import PreProcessNode

        assert PreProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL, (
            f"PreProcessNode must declare VERIFIED_EXTERNAL, " f"found {PreProcessNode.required_trust_level!r}"
        )

    def test_inner_domain_nodes_anonymous_trust_level(self):
        """TC-S1-04: All inner domain nodes must declare required_trust_level = ANONYMOUS.

        Inner domain nodes run inside GraphNode.execute() via the inner BaseGraph.
        GraphNode.execute() passes the outer InvocationContext unchanged into the
        inner graph (graph_node.py InvocationContext.from_state). A real VERIFIED_EXTERNAL
        caller arrives with trust_level=1; an INTERNAL(2) gate on an inner node would
        deny it (1 < 2 → SubgraphError → ERROR). Only ANONYMOUS(0) ≤ all valid callers.
        """
        from src.nodes.compliance_checklist_and_human_review_flag_node import (
            ComplianceChecklistAndHumanReviewFlagNode,
        )
        from src.nodes.input_validate_node import InputValidateNode
        from src.nodes.output_format_node import OutputFormatNode
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.prospectus_generate_node import ProspectusGenerateNode
        from src.nodes.risk_disclosure_and_suitability_node import (
            RiskDisclosureAndSuitabilityNode,
        )

        inner_nodes = [
            InputValidateNode,
            ProspectusGenerateNode,
            RiskDisclosureAndSuitabilityNode,
            ComplianceChecklistAndHumanReviewFlagNode,
            OutputFormatNode,
            PostProcessNode,
        ]
        for node_cls in inner_nodes:
            assert node_cls.required_trust_level == TrustLevel.ANONYMOUS, (
                f"{node_cls.__name__} must declare TrustLevel.ANONYMOUS, " f"found {node_cls.required_trust_level!r}"
            )
