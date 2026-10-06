"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]
#  - Never import from mediator/, api/, or other agents

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import (
    INVESTOR_PROFILES,
    MAX_PRODUCT_NAME_CHARS,
    RISK_BAND_HIGH,
    RISK_BAND_STANDARD,
    render_inert,
    render_metric_block,
)
from src.nodes.prospectus_generate_node import load_metrics


class RiskDisclosureAndSuitabilityNode(FunctionNode):
    """Inner domain node: write the risk-disclosure and suitability sections.

    The risk band comes from the product catalogue entry the request validator
    resolved, not from a substring search over caller text, so two callers who
    name the same product type always receive the same band.

    Input state fields:
      - product_name (str), product_type (str): validated, catalogue-derived
      - high_risk (bool): risk band of the resolved product type
      - target_investor_profile (str): retail | institutional
      - financial_data (str): JSON of validated financial metrics

    Output state fields:
      - risk_disclosure (str), suitability_explanation (str)

    Trust level: ANONYMOUS — the trust gate is enforced by the outer backbone.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        product_name = render_inert(state.get("product_name"), MAX_PRODUCT_NAME_CHARS)
        product_type = render_inert(state.get("product_type"), MAX_PRODUCT_NAME_CHARS)
        profile = state.get("target_investor_profile") or "retail"
        financial = load_metrics(state.get("financial_data"))

        if not product_name or not product_type or profile not in INVESTOR_PROFILES:
            emit_trace_event(
                "risk_disclosure_failed",
                {"reason": "incomplete_validated_request"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "RiskDisclosureAndSuitabilityNode: "
                    "a validated product name, type and investor profile are required"
                ],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + (
                    "RiskDisclosureAndSuitabilityNode: a validated product name, type and investor profile are required"
                ),
            }

        high_risk = bool(state.get("high_risk"))
        risk_level = RISK_BAND_HIGH if high_risk else RISK_BAND_STANDARD
        loss_warning = (
            "市場リスクおよびカウンターパーティリスクを含む高水準のリスクがあり、"
            "投資元本の全額を失う可能性があります。"
            if high_risk
            else "市場リスクにより投資元本を割り込む可能性があります。"
        )

        financial_section = render_metric_block(financial, "財務データ参照:", "財務データ: (未提供)")

        risk_disclosure = (
            "【リスク開示書 (金融商品取引法第37条・第37条の3準拠)】\n"
            f"商品名: {product_name}\n"
            f"商品種別: {product_type}\n"
            f"リスク区分: {risk_level}\n"
            f"{financial_section}\n"
            "\n"
            "■ 主なリスク\n"
            f"1. 価格変動リスク: {loss_warning}\n"
            "2. 流動性リスク: 市場環境によっては希望する価格・時期での換金が困難な場合があります。\n"
            "3. 信用リスク: 発行体の財務状況悪化等により価値が大幅に下落する可能性があります。\n"
            + (
                "4. レバレッジリスク: 当該商品はレバレッジ効果により損失が元本を超える場合があります。\n"
                if high_risk
                else ""
            )
            + "\n"
            "本書は金融商品取引法第37条の3に基づく書面です。\n"
            "[本書は自動生成されました]"
        )

        # Growth-quota eligibility: retail investors, standard-risk products only.
        nisa_eligible = (not high_risk) and profile == "retail"
        nisa_status = "NISA 2.0 成長投資枠: 適格" if nisa_eligible else "NISA 2.0 成長投資枠: 非適格"
        nisa_reason = (
            "リスク区分が高リスクに該当するため、NISA 2.0 成長投資枠の対象外です。"
            if high_risk
            else (
                "機関投資家向け商品のためNISA 2.0の適用対象外です。"
                if profile != "retail"
                else "本商品はNISA 2.0 成長投資枠の適格要件を満たしています。"
            )
        )

        suitability_explanation = (
            "【適合性説明書 (金融商品取引法第40条準拠・NISA 2.0対応)】\n"
            f"商品名: {product_name}\n"
            f"対象投資家区分: {INVESTOR_PROFILES[profile]} ({profile})\n"
            f"リスク区分: {risk_level}\n"
            f"{nisa_status}\n"
            f"判定理由: {nisa_reason}\n"
            "\n"
            "■ 適合性確認事項\n"
            "1. 投資経験: 本商品は投資経験を有する方を対象としています。\n"
            "2. リスク許容度: お客様のリスク許容度と商品リスクが適合しているか確認が必要です。\n"
            "3. 投資目的: 長期的な資産形成を目的とした投資に適しています。\n"
            "\n"
            "本書は金融商品取引法第40条（適合性の原則）に基づく説明書です。\n"
            "[本書は自動生成されました]"
        )

        emit_trace_event(
            "risk_disclosure_and_suitability_generated",
            {
                "product_key": state.get("product_key", ""),
                "risk_level": risk_level,
                "nisa_eligible": nisa_eligible,
                "target_investor_profile": profile,
                "financial_metrics": len(financial),
            },
            state,
        )

        return {
            "risk_disclosure": risk_disclosure,
            "suitability_explanation": suitability_explanation,
            "status": AgentStatus.SUCCESS.value,
        }
