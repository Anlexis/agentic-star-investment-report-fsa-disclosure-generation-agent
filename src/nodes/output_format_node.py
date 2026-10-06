"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]
#  - Never import from mediator/, api/, or other agents

import re
from typing import Any, ClassVar, Dict, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

from src.services.service import MAX_PRODUCT_NAME_CHARS, render_inert, resolve_report_limit

# Output credential scan. The platform ships its own detector and this node uses
# it as the FLOOR, not as a replacement: the platform's patterns describe
# credential FORMATS (sk-…, eyJ…, AKIA…, Bearer …, database URIs) and match none
# of the assignment shapes below, while these local patterns match none of the
# formats. Either set alone is narrower than the union, and a value one side
# catches and the other misses is a bypass — so the scan takes both.
#
# Keeping the local set is deliberate. Delegating entirely to the platform
# detector would look like a tightening and would in fact drop `password=…`,
# `api_key=…` and `secret=…` from what this document is checked for.
_LOCAL_CREDENTIAL_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("api_key_assignment", re.compile(r"(?i)api[_\-]?key\s*[:=]\s*\S{8,}")),
    ("password_assignment", re.compile(r"(?i)password\s*[:=]\s*\S{4,}")),
    ("secret_assignment", re.compile(r"(?i)secret\s*[:=]\s*\S{8,}")),
    ("token_assignment", re.compile(r"(?i)(?:access[_\-]?)?token\s*[:=]\s*\S{8,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


class OutputGateViolation(ValueError):
    """The assembled document cannot be released as it stands.

    Carries the closed-set finding labels only. It never carries the matched
    text: the matched text is the credential.
    """

    def __init__(self, findings: List[str]) -> None:
        super().__init__(
            "OutputFormatNode S-3: output withheld — credential pattern detected " f"({', '.join(findings)})"
        )
        self.findings = findings


def scan_credentials(text: str) -> List[str]:
    """Return the union of local and platform credential findings, as labels."""
    findings = [label for label, pattern in _LOCAL_CREDENTIAL_PATTERNS if pattern.search(text)]
    for finding in detect_credentials(text):
        label = str(finding.get("type", "credential"))
        if label not in findings:
            findings.append(label)
    return findings


def _security_gate_output(text: str) -> None:
    """Domain output gate: raise when the assembled document carries a credential.

    Called from ``OutputFormatNode.execute()``, which converts the exception into
    a refusal that clears the output-bearing fields. The framework's own output
    gate is ``@final`` and cannot be overridden, so the domain check lives here.
    """
    findings = scan_credentials(text)
    if findings:
        raise OutputGateViolation(findings)


class OutputFormatNode(FunctionNode):
    """Inner domain node: assemble the sections into the final document.

    Combines the prospectus summary, risk disclosure, suitability explanation
    and compliance checklist, then applies the domain output gate before the
    document can be returned.

    On a gate violation the node returns ERROR **and clears every output-bearing
    field**. Returning ERROR alone would not contain anything: the framework's
    response envelope falls back to ``result`` even on an error status, so a
    withheld document that is still sitting in state ships inside the error
    envelope. Raising would not contain it either — it would additionally put a
    traceback and this file's path into the error log.

    Input state fields:
      - product_name (str), prospectus_summary (str), risk_disclosure (str),
        suitability_explanation (str), compliance_checklist (str),
        human_review_required (bool)

    Output state fields:
      - formatted_report (str): the complete disclosure document

    Trust level: ANONYMOUS — the trust gate is enforced by the outer backbone.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        product_name = render_inert(state.get("product_name"), MAX_PRODUCT_NAME_CHARS) or "投資商品"
        prospectus_summary = (state.get("prospectus_summary") or "").strip()
        risk_disclosure = (state.get("risk_disclosure") or "").strip()
        suitability_explanation = (state.get("suitability_explanation") or "").strip()
        compliance_checklist = (state.get("compliance_checklist") or "").strip()
        human_review_required = bool(state.get("human_review_required"))

        if not prospectus_summary:
            emit_trace_event(
                "output_format_failed",
                {"reason": "missing_prospectus_summary"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["OutputFormatNode: prospectus_summary is required for output assembly"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("OutputFormatNode: prospectus_summary is required for output assembly"),
            }

        divider = "=" * 60

        review_notice = (
            (
                "\n\n"
                "⚠️  重要事項 — 必須レビュー通知\n"
                "本書は自動生成されました。\n"
                "正式文書として使用する前に、法務・コンプライアンス担当者による\n"
                "必須レビューが完了していません。人間によるレビューおよび承認が必要です。\n"
                "自動生成文書の利用に際しては金融商品取引法第43条の4の規定に従ってください。"
            )
            if human_review_required
            else (
                "\n\n"
                "✓ 自動コンプライアンスチェック完了\n"
                "本書は自動生成されました。\n"
                "利用前に法務・コンプライアンス担当者による確認を推奨します。"
            )
        )

        formatted_report = (
            "=== 投資商品開示書類一式 ===\n"
            f"商品名: {product_name}\n"
            f"\n{divider}\n"
            "[第1部] 目論見書要約\n"
            f"{divider}\n"
            f"{prospectus_summary}\n"
            f"\n{divider}\n"
            "[第2部] リスク開示書 / 適合性説明書\n"
            f"{divider}\n"
            f"{risk_disclosure}\n"
            f"\n{suitability_explanation}\n"
            f"\n{divider}\n"
            "[第3部] 金融商品取引法コンプライアンスチェックリスト\n"
            f"{divider}\n"
            f"{compliance_checklist or '(チェックリスト未生成)'}\n"
            f"{review_notice}"
        )

        # The limit is the operator's, declared in config/config.yaml and
        # forwarded into this graph's initial state; the module default applies
        # only when no usable value was declared.
        report_limit = resolve_report_limit(state.get("report_char_limit"))
        if len(formatted_report) > report_limit:
            emit_trace_event(
                "output_format_failed",
                {"reason": "report_too_large", "report_length": len(formatted_report)},
                state,
            )
            return self._withhold(
                state,
                "report_too_large",
                f"assembled document exceeds {report_limit} characters",
            )

        try:
            _security_gate_output(formatted_report)
        except OutputGateViolation as violation:
            return self._withhold(
                state,
                "credential_detected",
                "assembled document withheld by the output gate " f"({', '.join(violation.findings)})",
                findings=violation.findings,
            )

        emit_trace_event(
            "output_format_assembled",
            {
                "report_length": len(formatted_report),
                "human_review_required": human_review_required,
                "sections_included": [
                    "prospectus_summary",
                    "risk_disclosure",
                    "suitability_explanation",
                    "compliance_checklist",
                ],
            },
            state,
        )

        return {
            "formatted_report": formatted_report,
            "status": AgentStatus.SUCCESS.value,
        }

    def _withhold(
        self,
        state: dict[str, Any],
        reason: str,
        message: str,
        findings: List[str] | None = None,
    ) -> Dict[str, object]:
        """Refuse to release the document and clear every output-bearing field.

        The reason is a closed-set label and the finding labels name pattern
        classes; neither carries any part of the document or of the matched
        value.
        """
        emit_trace_event(
            "output_gate_withheld",
            {"reason": reason, "findings": findings or []},
            state,
        )
        return {
            "status": AgentStatus.ERROR.value,
            "error_log": [f"OutputFormatNode: {message}"],
            "formatted_report": "",
            "formatted_output": "",
            "result": "",
            "prospectus_summary": "",
            "risk_disclosure": "",
            "suitability_explanation": "",
            "compliance_checklist": "",
        }
