"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import (
    MAX_PRODUCT_NAME_CHARS,
    is_redacted,
    render_inert,
    render_metric_block,
)


def load_metrics(raw: object) -> List[Tuple[str, float]]:
    """Re-read the validated metric pairs the request validator serialised.

    The pairs were validated once, by ``normalise_request``; this only restores
    them from their state representation. A value that does not round-trip is
    dropped rather than rendered, because a figure that cannot be read back is a
    figure this document cannot vouch for.
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    pairs: List[Tuple[str, float]] = []
    if isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                name, value = entry
                if isinstance(name, str) and isinstance(value, (int, float)):
                    pairs.append((name, float(value)))
    return pairs


class ProspectusGenerateNode(FunctionNode):
    """Inner domain node: write the prospectus summary section.

    Input state fields:
      - product_name (str): validated, inert-rendered product name
      - product_type (str): catalogue label for the resolved product type
      - fund_performance_data (str): JSON of validated performance metrics

    Output state fields:
      - prospectus_summary (str): the generated prospectus summary section

    Trust level: ANONYMOUS — the trust gate is enforced by the outer backbone.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        product_name = render_inert(state.get("product_name"), MAX_PRODUCT_NAME_CHARS)
        product_type = render_inert(state.get("product_type"), MAX_PRODUCT_NAME_CHARS)
        performance = load_metrics(state.get("fund_performance_data"))

        if not product_name or is_redacted(state.get("product_name")):
            emit_trace_event(
                "prospectus_generate_failed",
                {"reason": "unusable_product_name"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ProspectusGenerateNode: product_name is required for prospectus generation"],
            }

        performance_section = render_metric_block(performance, "運用実績データ:", "運用実績: (データ未提供)")

        prospectus_summary = (
            "【目論見書要約】\n"
            f"商品名: {product_name}\n"
            f"商品種別: {product_type}\n"
            f"{performance_section}\n"
            "\n"
            "■ 投資方針\n"
            f"本商品（{product_name}）は、投資家の資産形成を目的として運用されます。\n"
            "投資に際しては、元本保証はなく、運用結果によっては投資元本を割り込む可能性があります。\n"
            "\n"
            "■ 費用\n"
            "販売手数料・信託報酬・その他費用は、別途交付される費用説明書を参照ください。\n"
            "\n"
            "本書は金融商品取引法第13条に基づき作成された目論見書の要約です。\n"
            "投資判断に際しては、交付目論見書の全文をご確認ください。\n"
            "[本書は自動生成されました]"
        )

        emit_trace_event(
            "prospectus_generated",
            {
                "product_key": state.get("product_key", ""),
                "performance_metrics": len(performance),
                "summary_length": len(prospectus_summary),
            },
            state,
        )

        return {
            "prospectus_summary": prospectus_summary,
            "status": AgentStatus.SUCCESS.value,
        }
