# Test Specification — FIN-C2-101 InvestmentReportFSADisclosureAgent

## Overview

Test coverage for FIN-C2-101, the investment report and disclosure document agent (Cat 2,
document-generation pattern, FIN industry). The agent assembles 目論見書要約, リスク開示書,
適合性説明書 and the 金商法 compliance checklist from a validated product request.

Two properties shape the suite:

- **Refusals are proved against the template, not against the platform.** Injection and
  personal-data tests call `execute()` directly, with no framework wrapper in front, because a test
  that passes only while a platform gate is active tells you nothing about the template.
- **The public path is exercised through the real ASGI entry point.** A suite that only calls
  `execute()` cannot see an adapter that establishes no trust level, which is the defect this
  template shipped with.

---

## 1. Test structure

```
tests/
├── unit/
│   ├── test_agent.py                          # Per-node unit tests (direct execute() calls)
│   └── test_framework_compliance_tc06_tc07.py # TC-06/07: @final gate override protection
├── integration/
│   └── test_invoke_api.py                     # End-to-end through the real ASGI /invoke
└── proof_of_boundary/
    ├── test_import_isolation.py               # PB-4: no platform-SDK imports
    ├── test_state_safety.py                   # PB-2/PB-5: state type safety
    ├── test_pb_invoke_order.py                # PB-6: backbone invoke order (full invoke)
    ├── test_pb7_hitl_interrupt_propagation.py # PB-7: interrupt propagation contract
    ├── test_s1_trust_gate.py                  # PoB-S1: trust gate boundary
    └── test_s3_output_gate.py                 # PoB-S3: output gate detection + containment
```

---

## 2. Unit tests (`tests/unit/test_agent.py`)

Every unit test calls `node.execute(state)` directly and patches `emit_trace_event` at the node
module level (never via `sys.modules` stubs, which would break the framework's own imports).

### 2.1 PreProcessNode (outer backbone)

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-PPE-01 | Valid request passes | Product request JSON | `status=SUCCESS`, `validated_input` set |
| TC-PPE-02 | Empty request refused | `""` | `status=ERROR` |
| TC-PPE-03 | Whitespace-only refused | `"   "` | `status=ERROR` |
| TC-PPE-04 | Non-string request refused | dict / int / None / list | `status=ERROR`, no exception |
| TC-PPE-05 | Oversize request refused | > `MAX_REQUEST_CHARS` | `status=ERROR` |
| TC-PPE-06 | Trust level declaration | — | `required_trust_level == VERIFIED_EXTERNAL` |
| TC-PPE-07 | Injection forms refused by the template | `<\|im_start\|>`, `[INST]`, `<<SYS>>`, directive phrase, markup-spliced, roleplay, Japanese directive | `status=ERROR`, no `validated_input` |
| TC-PPE-08 | Hostile field NAME refused | `{"<\|im_start\|>": …}` | `status=ERROR`, name not echoed |
| TC-PPE-09 | `\u`-escaped directive refused | `<<SYS>> …` | `status=ERROR` |
| TC-PPE-10 | Legitimate domain text unaffected | `システム総合ファンド`, `Global Systematic Equity Fund`, bracketed index fund name | `status=SUCCESS` |
| TC-PPE-11 | Personal data refused | email, phone, individual number without separators | `status=ERROR` |
| TC-PPE-12 | Refusal never echoes the value | Directive + secret string | Secret absent from `error_log` |
| TC-PPE-13 | Context reduced to the contract | `channel`, `review_policy`, undeclared key | Declared values kept, undeclared absent |
| TC-PPE-14 | Unrecognised context values defaulted | `channel="☠"`, `review_policy="off"` | `unknown` / `standard` |

### 2.2 InputValidateNode (inner domain — owns the request contract)

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-IVN-01 | Valid request | Required fields | `status=SUCCESS`, catalogue-derived values |
| TC-IVN-02 | Product type from the catalogue | `product_type="fx"` | Label `外国為替証拠金取引 (FX)`, `high_risk=True` |
| TC-IVN-03 | Unsupported product type refused | `架空商品` | `status=ERROR` |
| TC-IVN-04 | Required fields missing | Each omitted in turn | `status=ERROR` |
| TC-IVN-05 | Non-string product name refused cleanly | `product_name=12345` | `status=ERROR`, no traceback |
| TC-IVN-06 | Invalid JSON refused | `"not json at all"` | `status=ERROR` |
| TC-IVN-07 | Unknown envelope field refused | `unexpected_field` | `status=ERROR`, field named |
| TC-IVN-08 | Descriptor carries validated values only | Valid request | Descriptor keys are the fixed set |
| TC-IVN-09 | Investor profile is a closed set | `"vip"` | `status=ERROR` |
| TC-IVN-10 | Redaction sentinel refused | `product_name="[MASKED]"` | `status=ERROR`, field named |
| TC-IVN-11 | Oversize product name refused | 500 characters | `status=ERROR` |
| TC-IVN-12 | Structural characters stripped | Name with `\n` and `■` | Neither survives into the name |
| TC-IVN-13 | Trust level declaration | — | `required_trust_level == ANONYMOUS` |
| TC-IVN-14 | Finite metrics accepted | `{"nav": 12345.67, "aum": 900}` | Sorted, validated pairs |
| TC-IVN-15 | Non-finite / out-of-range refused | `NaN`, `Infinity`, `-Infinity`, `nan`, `inf`, `True`, `±1e20`, non-numeric, `None`, list — per metric field | `status=ERROR` |
| TC-IVN-16 | Metric names restricted to an inert alphabet | `NAV`, `nav; drop`, `■risk`, 40 chars, `1nav` | `status=ERROR` |
| TC-IVN-17 | Metric count capped | `MAX_METRIC_ENTRIES + 1` | `status=ERROR` |

### 2.3 ProspectusGenerateNode

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-PGN-01 | Generates the summary | Validated state | Product name and 第13条 present |
| TC-PGN-02 | Unusable product name refused | Empty name | `status=ERROR` |
| TC-PGN-03 | Redaction sentinel refused | `[MASKED]` | `status=ERROR` |
| TC-PGN-04 | Metrics rendered | One performance metric | `return_1y: 4.2` present |
| TC-PGN-05 | Section depends on the figure | 1.0 vs 999999.0 | Sections differ |
| TC-PGN-06 | Trust level declaration | — | `required_trust_level == ANONYMOUS` |

### 2.4 RiskDisclosureAndSuitabilityNode

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-RDS-01 | Both sections generated | Validated state | 第37条 and 第40条 present |
| TC-RDS-02 | Band follows the catalogue | standard vs high-risk state | 中リスク / 高リスク + レバレッジリスク |
| TC-RDS-03 | Eligibility depends on band and profile | standard / high-risk / institutional | 適格 / 非適格 / 非適格 |
| TC-RDS-04 | Incomplete validated request refused | Empty name; unvalidated profile | `status=ERROR` |
| TC-RDS-05 | Financial metrics rendered | `nav=12345.5` | `nav: 12,345.5` present |
| TC-RDS-06 | Trust level declaration | — | `required_trust_level == ANONYMOUS` |

### 2.5 ComplianceChecklistAndHumanReviewFlagNode

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-CCL-01 | Checklist is well-formed JSON | Validated state | 7 articles, product name present |
| TC-CCL-02 | No forced review for a complete standard document | Standard risk + disclosure present | `human_review_required=False` |
| TC-CCL-03 | High risk forces review | `high_risk=True` | `human_review_required=True` |
| TC-CCL-04 | Missing disclosure forces review | `risk_disclosure=""` | `human_review_required=True` |
| TC-CCL-05 | Strict review policy forces review | `review_policy="strict"` | `human_review_required=True`, reason recorded |
| TC-CCL-06 | Unusable product name refused | Empty name | `status=ERROR` |
| TC-CCL-07 | Trust level declaration | — | `required_trust_level == ANONYMOUS` |

### 2.6 OutputFormatNode

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-OFN-01 | Assembles the document | All sections | Three parts and the product name present |
| TC-OFN-02 | Missing leading section refused | `prospectus_summary=""` | `status=ERROR` |
| TC-OFN-03 | Review notice when flagged | `human_review_required=True` | `必須レビュー通知` present |
| TC-OFN-04 | Trust level declaration | — | `required_trust_level == ANONYMOUS` |

Output-gate detection and containment are covered in PoB-S3 (§4.5).

### 2.7 PostProcessNode

| Test ID | Description | Input | Expected |
|---------|-------------|-------|----------|
| TC-PPN-01 | Publishes the document | Document string | `formatted_output` set, `status=SUCCESS` |
| TC-PPN-02 | Empty result refused, not published | `result=""` | `status=ERROR`, `formatted_output` and `result` cleared |
| TC-PPN-03 | Non-string result refused | `result={"leak": …}` | `status=ERROR`, nothing rendered |
| TC-PPN-04 | Trust level declaration | — | `required_trust_level == ANONYMOUS` |

> TC-PPN-02 replaced a test that asserted `formatted_output == ""` **as a success**. That is the
> falsy value which re-opens the response envelope's fallback to `result`, so the old assertion
> pinned the defect rather than the contract.

### 2.8 Framework compliance

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-06 | Overriding `_security_gate_input` | `TypeError` at class definition |
| TC-07 | Overriding `_security_gate_output` | `TypeError` at class definition |

---

## 3. Integration tests (`tests/integration/test_invoke_api.py`)

Every case goes through the real ASGI application and the compiled graph.

### 3.1 Entry-point authentication

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-E2E-01 | `/health` needs no credential | 200 |
| TC-E2E-02 | Missing credential refused | 401 |
| TC-E2E-03 | Wrong credential refused | 401 |
| TC-E2E-04 | Runner credential accepted | 200, `status=success` |
| TC-E2E-05 | Valid credential produces a document | Three parts, > 500 characters |

### 3.2 Caller-data contract

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-E2E-06 | Metrics reach the document | `nav: 12,345.67` rendered |
| TC-E2E-07 | Output moves with the input | 1 vs 999,999 produce different documents |
| TC-E2E-08 | Risk band changes the document | 中リスク/適格 vs 高リスク/非適格 + レバレッジリスク |
| TC-E2E-09 | Investor profile changes eligibility | 非適格, 機関投資家 |
| TC-E2E-10 | Context bridge reaches the inner graph | `review_policy=strict` adds the review notice |
| TC-E2E-11 | Declared runtime config is in force | `agent.config` and the deadline match `config/config.yaml` |

### 3.3 Rejection paths

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-E2E-12 | Request size bounded by the schema | 422 |
| TC-E2E-13 | Injection forms publish nothing | `status != success`, empty output |
| TC-E2E-14 | A newline cannot manufacture a section | Forged and flattened claims refused; an ordinary fund name still produces a document |
| TC-E2E-15 | Non-finite figures refused end to end | `status != success`, empty output |
| TC-E2E-16 | Personal data refused | `status != success` |
| TC-E2E-17 | Redaction sentinel never certified | `[MASKED]` absent from output, `status != success` |

### 3.4 Context channel

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-E2E-18 | Credential in a declared context field | 400 naming `input_context.channel` |
| TC-E2E-19 | Refusal does not echo the credential | Value absent from the response |
| TC-E2E-20 | Undeclared context keys dropped | 200, `status=success` |
| TC-E2E-21 | Screen matches the platform block set | Refusal ⟺ `detect_credentials_in_value(value)` for every declared field |

### 3.5 Containment

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-E2E-22 | Error envelope carries no document | No credential, no document text, no traceback, no source path |
| TC-E2E-23 | `merge_output` clears on a non-success inner result | `result` and `formatted_output` blank; success path still merges |

---

## 4. Proof-of-boundary tests

### 4.1 PB-4: import isolation (`test_import_isolation.py`)

No source file under `src/` imports the platform SDK (Level 0 — prohibited).

### 4.2 PB-2/PB-5: state safety (`test_state_safety.py`)

`src/schemas/state.py` declares a flat TypedDict with JSON-serialisable field types only.

### 4.3 PB-6: backbone invoke order (`test_pb_invoke_order.py`)

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-PB6-01 | Full invoke as a VERIFIED_EXTERNAL caller | `node_history` equals the five-node backbone order, `status=SUCCESS`, non-None output |
| TC-PB6-02 | The `main` slot holds a GraphNode | `DisclosureDocGenGraphNode` is a `GraphNode` subclass |

### 4.4 PoB-S1: trust gate (`test_s1_trust_gate.py`)

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-S1-01 | ANONYMOUS caller denied | `status=ERROR` |
| TC-S1-02 | VERIFIED_EXTERNAL accepted | `status=SUCCESS` |
| TC-S1-03 | `PreProcessNode` trust level | `== VERIFIED_EXTERNAL` |
| TC-S1-04 | Inner nodes ANONYMOUS | All six declare `ANONYMOUS` |

### 4.5 PoB-S3: output gate (`test_s3_output_gate.py`)

| Test ID | Description | Expected |
|---------|-------------|----------|
| TC-S3-01 | Clean document passes | No exception, no findings |
| TC-S3-02 | Union of credential forms rejected | Five assignment shapes + five platform formats each raise |
| TC-S3-03 | Both detector sets are load-bearing | Framework detector finds nothing in `password: …`; the local set does |
| TC-S3-04 | Violation names classes, not values | Credential absent from the message |
| TC-S3-05 | Violation clears the output-bearing fields | All seven fields blank |
| TC-S3-06 | Refusal carries no released text | No credential, no document text, no traceback, no source path |
| TC-S3-07 | The gate is wired into `execute()` | Called exactly once, on the assembled document |
| TC-S3-08 | Oversize document withheld | `status=ERROR`, fields cleared |

### 4.6 PB-7: interrupt propagation (`test_pb7_hitl_interrupt_propagation.py`)

Skipped for this template: it declares no `hitl` block in `config/config.yaml` and calls no
`interrupt()`. The file is present so that the contract is enforced if that changes.

---

## 5. Security requirement coverage

| Requirement | Implementation | Tests |
|---|---|---|
| S-1 Trust gate | `PreProcessNode.required_trust_level = VERIFIED_EXTERNAL`; adapter resolves the level from a bearer credential | TC-S1-01…04, TC-E2E-02…05 |
| S-1 Injection screen | `PreProcessNode` + `screen_injection()` | TC-PPE-07…10, TC-E2E-13 |
| S-2 Personal data | Framework mask + `scan_pii_payload()`; redaction sentinel refused | TC-PPE-11, TC-IVN-10, TC-PGN-03, TC-E2E-16, TC-E2E-17 |
| S-3 Output gate | `output_format_node._security_gate_output()` over the union | TC-S3-01…08, TC-E2E-22 |
| S-3 Containment | Node clearing + `merge_output()` clearing | TC-S3-05, TC-S3-06, TC-E2E-22, TC-E2E-23 |
| S-4 Audit trace | `emit_trace_event()` in every node | Covered on every path exercised above |
| S-5 Credentials | `provision_secrets()`; context screened at the adapter | TC-E2E-18…21 |
| Finite-number rule | `finite_in_range()` on every caller figure | TC-IVN-15, TC-E2E-15 |
| Structural bounds | Request, name, metric count and document size caps | TC-PPE-05, TC-IVN-11, TC-IVN-17, TC-S3-08, TC-E2E-12 |

---

## 6. CI gate coverage

| Gate | Covered by |
|---|---|
| `gate-import-isolation` | PB-4 |
| `gate-trust-level-check` | TC-S1-04 and `scripts/check_trust_level.py` |
| `gate-audit-trace-check` | `scripts/check_audit_trace.py` over `src/` |
| `gate-manifest-schema` | `scripts/check_manifest_schema.py config/agent.yaml` |
| `gate-cat-consistency` | `config/agent.yaml` and `README.md` agree on the template id and category |
| `gate-scaffold-integrity` | `tests/unit/` non-empty; every required invariant file present |
| `gate-forbidden-strings` / `gate-credential-scan` | `scripts/check_forbidden_strings.py`, `scripts/check_credentials.py` |
