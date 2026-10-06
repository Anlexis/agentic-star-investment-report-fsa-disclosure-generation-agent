"""AgentCore Platform v1.0"""

# Inner domain workflow graph for the investment-disclosure generator.
# Called by DisclosureDocGenGraphNode.get_subgraph() in graph.py.
#
# Architecture:
#   Inherits BaseGraph (fully custom node topology — no forced backbone).
#   Linear pipeline:
#     START → input_validate → prospectus_generate → risk_disclosure_and_suitability
#           → compliance_checklist_and_human_review_flag → output_format → END
#
# Rules:
#   ✅ Place at src/graph/domain_workflow_graph.py
#   ✅ Inherit BaseGraph (custom topology — no pre_process/main/post_process slots)
#   ✅ Implement all BaseGraph abstract methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ register_nodes() instantiates every node with NO ctor args
#   ✅ get_output() designed together with outer DisclosureDocGenGraphNode.merge_output()
#   ❌ Do NOT register initialize / finalize (outer backbone concern)
#   ❌ Do NOT call super() in register_nodes()

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus

from src.graph.context_bridge import get_caller_context
from src.nodes.compliance_checklist_and_human_review_flag_node import (
    ComplianceChecklistAndHumanReviewFlagNode,
)
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.prospectus_generate_node import ProspectusGenerateNode
from src.nodes.risk_disclosure_and_suitability_node import RiskDisclosureAndSuitabilityNode
from src.schemas.state import State
from src.services.service import resolve_report_limit


class DisclosureDocumentWorkflowGraph(BaseGraph):
    """Inner graph: multi-step disclosure document generation.

    Drives the domain pipeline from the validated product request to the fully
    assembled disclosure document (prospectus summary, risk disclosure,
    suitability explanation and statutory compliance checklist).

    Pipeline (linear):
        START → input_validate → prospectus_generate → risk_disclosure_and_suitability
              → compliance_checklist_and_human_review_flag → output_format → END

    Called by DisclosureDocGenGraphNode.get_subgraph() in graph.py.
    get_output() feeds DisclosureDocGenGraphNode.merge_output().
    """

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "DisclosureDocumentWorkflowGraph"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config for this domain workflow."""
        return None

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all inner domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Every node is instantiated with no constructor arguments.
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["prospectus_generate"] = ProspectusGenerateNode()
        self._nodes["risk_disclosure_and_suitability"] = RiskDisclosureAndSuitabilityNode()
        self._nodes["compliance_checklist_and_human_review_flag"] = ComplianceChecklistAndHumanReviewFlagNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear disclosure document generation pipeline."""
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "prospectus_generate")
        self._sg.add_edge("prospectus_generate", "risk_disclosure_and_suitability")
        self._sg.add_edge(
            "risk_disclosure_and_suitability",
            "compliance_checklist_and_human_review_flag",
        )
        self._sg.add_edge("compliance_checklist_and_human_review_flag", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Conditional routing — required by the BaseGraph contract.

        The topology above is linear, so add_edges() never wires this method.
        It is annotated with this graph's own State rather than the base state:
        the graph runtime reads a path callable's annotation as its input schema
        and projects away every field the annotation does not declare, so a base
        annotation here would hide the domain fields from any future branch.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "output_format"

    # ── Caller context and runtime parameters ─────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the bridged caller context and the declared document-size limit.

        The framework invokes a subgraph with the request string alone, so
        without this both the context validated at the entry boundary and the
        runtime parameters declared in config/config.yaml would stop at that
        boundary. See src/graph/context_bridge.py for the context half.
        """
        extra: Dict[str, Any] = dict(get_caller_context())
        configurable = self.config.get("configurable") or {}
        extra["report_char_limit"] = resolve_report_limit(configurable.get("max_report_chars"))
        return extra

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: State) -> Dict[str, Any]:
        """Shape the sub_result returned to the outer DisclosureDocGenGraphNode.

        Designed together with DisclosureDocGenGraphNode.merge_output():
            sub_result   = self.get_output(final_inner_state)
            outer_delta  = outer_node.merge_output(outer_state, sub_result)
        """
        return {
            "output": state.get("formatted_report"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
