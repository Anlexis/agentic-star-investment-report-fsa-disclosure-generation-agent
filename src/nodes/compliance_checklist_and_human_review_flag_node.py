"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import MAX_PRODUCT_NAME_CHARS, render_inert

_STATUTORY_ARTICLES: tuple[str, ...] = (
    "第13条: 目論見書交付義務 — 生成文書の交付義務充足確認",
    "第37条: リスク説明義務 — リスク開示書の完全性確認",
    "第37条の3: 書面交付義務 — 電磁的方法による交付要件確認",
    "第38条: 禁止行為 — 断定的判断提供・虚偽説明のないこと確認",
    "第40条: 適合性の原則 — 投資家属性との適合性確認",
    "第43条の4: 自動生成開示義務 — 自動生成であることの表示確認",
    "NISA 2.0: 成長投資枠適格要件 — 商品種別・リスク区分の確認",
)


class ComplianceChecklistAndHumanReviewFlagNode(FunctionNode):
    """Inner domain node: build the statutory checklist and set the review flag.

    The flag is raised when the product carries the high-risk band, when a prior
    section is missing, or when the caller asked for the strict review policy on
    the invocation context. That last route is what makes the caller context
    observable in the document rather than only in the audit log.

    Input state fields:
      - product_name (str), product_type (str): validated, catalogue-derived
      - high_risk (bool): risk band of the resolved product type
      - risk_disclosure (str): the generated risk disclosure section
      - review_policy (str): standard | strict

    Output state fields:
      - compliance_checklist (str): JSON-encoded checklist
      - human_review_required (bool)

    Trust level: ANONYMOUS — the trust gate is enforced by the outer backbone.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        product_name = render_inert(state.get("product_name"), MAX_PRODUCT_NAME_CHARS)
        product_type = render_inert(state.get("product_type"), MAX_PRODUCT_NAME_CHARS)
        risk_disclosure = (state.get("risk_disclosure") or "").strip()
        review_policy = state.get("review_policy") or "standard"

        if not product_name:
            emit_trace_event(
                "compliance_check_failed",
                {"reason": "unusable_product_name"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ComplianceChecklistAndHumanReviewFlagNode: product_name is required"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("ComplianceChecklistAndHumanReviewFlagNode: product_name is required"),
            }

        checklist_items = [
            {
                "article": article,
                "status": "確認待ち",
                "note": "自動生成済み — 法務・コンプライアンス担当者による最終確認が必要",
            }
            for article in _STATUTORY_ARTICLES
        ]

        is_high_risk = bool(state.get("high_risk"))
        strict_policy = review_policy == "strict"
        needs_review = is_high_risk or strict_policy or not risk_disclosure

        review_reasons: List[str] = []
        if is_high_risk:
            review_reasons.append(f"高リスク商品種別 ({product_type})")
        if strict_policy:
            review_reasons.append("依頼元がレビュー方針 strict を指定")
        if not risk_disclosure:
            review_reasons.append("リスク開示書が生成されていません")

        checklist_payload = {
            "product_name": product_name,
            "product_type": product_type,
            "review_policy": review_policy,
            "human_review_required": needs_review,
            "review_reasons": review_reasons,
            "checklist": checklist_items,
        }

        compliance_checklist = json.dumps(checklist_payload, ensure_ascii=False, indent=2)

        emit_trace_event(
            "compliance_checklist_generated",
            {
                "product_key": state.get("product_key", ""),
                "items_count": len(checklist_items),
                "human_review_required": needs_review,
                "review_reasons": review_reasons,
            },
            state,
        )

        return {
            "compliance_checklist": compliance_checklist,
            "human_review_required": needs_review,
            "status": AgentStatus.SUCCESS.value,
        }
