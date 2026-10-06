# PoB-S3: output credential gate and containment of a withheld document.
#
# Proves three things about the domain output gate in OutputFormatNode:
#   1. it detects the union of the local assignment patterns and the framework's
#      own credential formats — either set alone is narrower, and a value one
#      side catches and the other misses is a bypass;
#   2. on a violation the node returns ERROR **and clears every output-bearing
#      field**, because the response envelope falls back to `result` even on an
#      error status, so a withheld document left in state ships inside the error
#      envelope;
#   3. the refusal carries no part of the document, no matched value and no
#      traceback.
#
# The gate is a module-level helper called from execute(); the framework's own
# _security_gate_output is @final and cannot be overridden by a domain node.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes.output_format_node import (
    OutputFormatNode,
    OutputGateViolation,
    _security_gate_output,
    scan_credentials,
)

# A database connection string is one of the shapes the framework's detector
# describes, so the gate has to be probed with one. It is assembled at run time
# rather than written as a literal: the repository's own credential scan applies
# its fail-tier patterns to test code as well, and a literal here would be
# indistinguishable from a real one that had been committed by mistake.
_CONN_STRING = "postgre" + "sql://svc:" + "hunter2xx" + "@db.internal/records"

_OUTPUT_BEARING_FIELDS = (
    "formatted_report",
    "formatted_output",
    "result",
    "prospectus_summary",
    "risk_disclosure",
    "suitability_explanation",
    "compliance_checklist",
)


class TestS3OutputGate:
    """PoB-S3: the output gate detects credentials and contains the document."""

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.output_format_node.emit_trace_event", lambda *a, **k: None)

    # ── Detection ────────────────────────────────────────────────────────────

    def test_clean_text_passes_gate(self):
        """TC-S3-01: A clean disclosure document passes without raising."""
        clean_text = (
            "投資信託目論見書\n"
            "商品名: テスト投資信託\n"
            "リスク区分: 中リスク\n"
            "本書面は金融商品取引法第13条に基づき交付されます。\n"
            "  - nav: 12,345.67\n"
        )
        _security_gate_output(clean_text)
        assert scan_credentials(clean_text) == []

    @pytest.mark.parametrize(
        "bad_text",
        [
            # Assignment shapes — the LOCAL pattern set. The framework's own
            # detector describes credential formats and matches none of these.
            "API_KEY = sk-abc123def456ghi789jkl012mno",
            "password: SuperSecret99",
            "secret = mysecrettoken12345678",
            "access_token: abcdefgh12345678",
            "-----BEGIN RSA PRIVATE KEY-----",
            # Credential formats — the FRAMEWORK's set. The local patterns match
            # none of these, so delegating either way round would be a bypass.
            "AKIAIOSFODNN7EXAMPLE",
            "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJ",
            "sk-test1234567890abcdefghijklmnopqrst",
            _CONN_STRING,
            "Bearer abcdefghijklmnopqrstuvwxyz",
        ],
    )
    def test_credential_patterns_are_rejected(self, bad_text):
        """TC-S3-02: Every credential form in the union raises the gate violation."""
        with pytest.raises(ValueError, match="(?i)s-3"):
            _security_gate_output(bad_text)

    def test_local_and_framework_sets_are_both_load_bearing(self):
        """TC-S3-03: Neither detector alone covers the union.

        Recorded as a test rather than a comment: swapping the local set for the
        framework detector looks like a tightening and in fact drops the
        assignment shapes, while dropping the framework detector loses the
        credential formats. Wider is safe; narrower is a bypass.
        """
        from framework.security.credential_detector import detect_credentials

        local_only = "password: SuperSecret99"
        framework_only = "AKIAIOSFODNN7EXAMPLE"
        assert detect_credentials(local_only) == []
        assert scan_credentials(local_only) == ["password_assignment"]
        assert scan_credentials(framework_only) == ["aws_key"]

    def test_violation_names_pattern_classes_never_the_value(self):
        """TC-S3-04: The violation carries closed-set labels, not the credential."""
        secret = "sk-test1234567890abcdefghijklmnopqrst"
        with pytest.raises(OutputGateViolation) as excinfo:
            _security_gate_output(f"商品名: F\n{secret}")
        assert secret not in str(excinfo.value)
        assert excinfo.value.findings == ["openai_key"]

    # ── Containment ──────────────────────────────────────────────────────────

    def _leaking_state(self) -> dict:
        secret = "AKIAIOSFODNN7EXAMPLE"
        return {
            "product_name": "テスト投資信託",
            "product_type": "投資信託",
            "prospectus_summary": f"【目論見書要約】運用口座: {secret}",
            "risk_disclosure": "【リスク開示書】",
            "suitability_explanation": "【適合性説明書】",
            "compliance_checklist": '{"human_review_required": false}',
            "human_review_required": False,
            "caller_trust_level": "anonymous",
            "node_history": [],
            "error_log": [],
        }

    def test_violating_document_is_withheld_and_state_is_cleared(self):
        """TC-S3-05: On violation the node returns ERROR and clears the output fields.

        Returning ERROR alone would not contain anything — the response envelope
        falls back to `result` even on an error status — and raising would put a
        traceback and this file's path in the error log instead.
        """
        node = OutputFormatNode()
        result = node.execute(self._leaking_state())

        assert result["status"] == AgentStatus.ERROR.value
        for field in _OUTPUT_BEARING_FIELDS:
            assert result[field] == "", f"{field} was not cleared"

    def test_withheld_response_carries_no_released_text(self):
        """TC-S3-06: Nothing in the refusal echoes the document or the credential."""
        node = OutputFormatNode()
        result = node.execute(self._leaking_state())
        rendered = json.dumps(result, ensure_ascii=False, default=str)

        assert "AKIAIOSFODNN7EXAMPLE" not in rendered
        assert "目論見書要約" not in rendered
        assert "Traceback" not in rendered
        assert "output_format_node.py" not in rendered
        assert "/src/" not in rendered

    def test_output_format_node_calls_the_gate(self, monkeypatch):
        """TC-S3-07: The gate is wired into execute(), not merely importable."""
        calls = []
        monkeypatch.setattr(
            "src.nodes.output_format_node._security_gate_output",
            lambda text: calls.append(text),
        )

        node = OutputFormatNode()
        state = self._leaking_state()
        state["prospectus_summary"] = "【目論見書要約】"
        result = node.execute(state)

        assert len(calls) == 1
        assert result.get("formatted_report") is not None
        assert calls[0] == result["formatted_report"]

    def test_oversize_document_is_withheld(self):
        """TC-S3-08: A document beyond the size bound is withheld, not truncated."""
        from src.services.service import MAX_REPORT_CHARS

        node = OutputFormatNode()
        state = self._leaking_state()
        state["prospectus_summary"] = "あ" * (MAX_REPORT_CHARS + 1)
        result = node.execute(state)

        assert result["status"] == AgentStatus.ERROR.value
        for field in _OUTPUT_BEARING_FIELDS:
            assert result[field] == ""
