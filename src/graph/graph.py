"""AgentCore Platform v1.0"""

# Cat 2 outer graph — AgentBaseGraph + GraphNode in the `main` slot.
#
# Architecture:
#   Outer graph (this file):
#     InvestmentReportFSADisclosureAgent(AgentBaseGraph)
#     Fixed 5-node backbone: initialize → pre_process → main → post_process → finalize
#     Domain logic encapsulated inside DisclosureDocGenGraphNode (main slot).
#
#   Inner graph (src/graph/domain_workflow_graph.py):
#     DisclosureDocumentWorkflowGraph(BaseGraph)
#     Linear pipeline: input_validate → prospectus_generate → risk_disclosure_and_suitability
#                       → compliance_checklist_and_human_review_flag → output_format
#
# Rules:
#   ✅ Outer graph inherits AgentBaseGraph (direct framework inheritance)
#   ✅ Call super().register_nodes() in outer graph
#   ✅ Assign a GraphNode subclass to the `main` slot
#   ✅ Inner graph lives at src/graph/domain_workflow_graph.py
#   ✅ merge_output() returns only changed state keys
#   ❌ Do NOT override add_edges() on the outer graph

from pathlib import Path
from typing import Any, ClassVar, Dict

import yaml

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.graph.context_bridge import set_caller_context
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

# Repo-root runtime config: src/graph/graph.py -> parents[2] = repo root.
# config/agent.yaml is the static registry manifest; the runtime parameters
# (max_retry, timeout_s) live in config/config.yaml, which is what the graph
# and the entry adapter read at run time.
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


class DisclosureDocGenGraphNode(GraphNode):
    """GraphNode wrapping the inner DisclosureDocumentWorkflowGraph.

    Assigned to the `main` slot of InvestmentReportFSADisclosureAgent. Calls the
    inner graph with the validated request and maps the result back into the
    outer state via merge_output().

    Trust level: ANONYMOUS — the trust gate is enforced by the outer
    PreProcessNode (VERIFIED_EXTERNAL). GraphNode carries a deliberate no-op
    security gate at the subgraph boundary (ADR-017).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> Any:
        """Instantiate the inner domain workflow graph with the runtime config.

        The config is forwarded rather than replaced with ``{}``: an empty config
        here would make every declared runtime parameter dead inside the inner
        graph while leaving the manifest claiming otherwise.
        """
        from src.graph.domain_workflow_graph import DisclosureDocumentWorkflowGraph

        return DisclosureDocumentWorkflowGraph(config=self._parent_config())

    def _parent_config(self) -> Dict[str, Any]:
        """Forward config/config.yaml to the inner graph under ``configurable``."""
        try:
            runtime = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            runtime = {}
        if not isinstance(runtime, dict):
            runtime = {}
        return {"configurable": dict(runtime)}

    def extract_input(self, state: AgentState) -> str:
        """Hand the validated request to the inner graph, bridging the context.

        The bridge runs here because the framework's GraphNode invokes the
        subgraph with the request string alone — the validated caller context
        would otherwise stop at this boundary.
        """
        set_caller_context(
            {
                "caller_channel": str(state.get("caller_channel") or "unknown"),
                "review_policy": str(state.get("review_policy") or "standard"),
            }
        )
        return str(state.get("validated_input") or state.get("user_input") or "")

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner result back into the outer state.

        On any non-success inner status the output-bearing fields are cleared
        rather than merged. The framework's response envelope falls back to
        ``result`` even on an error status, so merging an inner result that the
        inner output gate declined to release would ship it inside the error
        envelope — the one channel that is caller-visible on a failed run.
        """
        status = sub_result.get("status")
        success_values = {AgentStatus.SUCCESS, AgentStatus.SUCCESS.value}
        if status not in success_values:
            return {"result": "", "formatted_output": "", "status": status}
        return {
            "result": sub_result.get("output"),
            "status": status,
        }


class InvestmentReportFSADisclosureAgent(AgentBaseGraph):
    """Cat 2 outer graph: investment report and disclosure document generator.

    Backbone (fixed — same as Cat 1):
        START → initialize → pre_process → main → {route} → post_process → finalize → END
                                                  ↓ (RETRY, max_retry from config)
                                               pre_process

    Domain logic is encapsulated in DisclosureDocGenGraphNode (`main` slot),
    which drives the inner DisclosureDocumentWorkflowGraph.

    The class name must match the ``class:`` field of config/agent.yaml.
    """

    @property
    def name(self) -> str:
        return "InvestmentReportFSADisclosureAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # fills: initialize, finalize (required)
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = DisclosureDocGenGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
