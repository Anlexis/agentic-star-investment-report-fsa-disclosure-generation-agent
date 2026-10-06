# Template Design Specification — FIN-C2-101

## Position in AgentCore Architecture

- **Agent Class**: `InvestmentReportFSADisclosureAgent`
- **L1 Base (framework base class)**: `AgentBaseGraph` — direct framework inheritance
- **Category**: Cat 2 — multi-step domain workflow (document-generation pattern)
- **Industry**: FIN (finance)
- **Three-layer separation**:
  - State: flat TypedDict composition (no Pydantic — not msgpack-serialisable)
  - Node: framework inheritance (`execute(self, state) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

### Cat 2 nested pattern

- **Outer graph** (`src/graph/graph.py`): `InvestmentReportFSADisclosureAgent(AgentBaseGraph)` —
  the standard five-node backbone. Domain complexity is encapsulated in the `main` slot via
  `DisclosureDocGenGraphNode(GraphNode)`.
- **Inner graph** (`src/graph/domain_workflow_graph.py`): `DisclosureDocumentWorkflowGraph(BaseGraph)`
  — a linear five-node domain pipeline.
- **Entry adapter** (`src/api/server.py`): establishes the caller's trust level from a bearer
  credential, reduces `input_context` to the declared contract and screens it, loads
  `config/config.yaml` and constructs the graph with it, and enforces the declared deadline.

### Node configuration

| Node | Class | Location | Responsibility | Trust Level | Inherits |
|------|-------|----------|---------------|-------------|---------|
| initialize | `InitializeNode` | framework | Set session_id, schema_version, caller_trust_level | ANONYMOUS | framework default |
| pre_process | `PreProcessNode` | src/nodes/ | S-1 trust gate; bound the request; screen injection and personal data; normalise the caller context | VERIFIED_EXTERNAL | FunctionNode |
| main | `DisclosureDocGenGraphNode` | src/graph/graph.py | Bridge the caller context, run the inner graph, merge or clear its result | ANONYMOUS | GraphNode |
| post_process | `PostProcessNode` | src/nodes/ | Publish the inner document as `formatted_output`; refuse an empty or non-string result | ANONYMOUS | FunctionNode |
| finalize | `FinalizeNode` | framework | Build response metadata and the final output | ANONYMOUS | framework default |

#### Inner domain nodes (`DisclosureDocumentWorkflowGraph`)

| Node | Class | Location | Responsibility | Trust Level |
|------|-------|----------|---------------|-------------|
| input_validate | `InputValidateNode` | src/nodes/ | Own the request contract: types, bounds, catalogue resolution, finite figures, inert rendering | ANONYMOUS |
| prospectus_generate | `ProspectusGenerateNode` | src/nodes/ | 目論見書要約 (金商法第13条) | ANONYMOUS |
| risk_disclosure_and_suitability | `RiskDisclosureAndSuitabilityNode` | src/nodes/ | リスク開示書 + 適合性説明書 (金商法第37条・第40条) | ANONYMOUS |
| compliance_checklist_and_human_review_flag | `ComplianceChecklistAndHumanReviewFlagNode` | src/nodes/ | 金商法 checklist; raise the human-review flag | ANONYMOUS |
| output_format | `OutputFormatNode` | src/nodes/ | Assemble the document; apply the S-3 output gate; withhold and clear on violation | ANONYMOUS |

The domain contract shared by these nodes — bounds, product catalogue, inert renderer, injection and
personal-data screens, finite-number parser, request normaliser — lives in `src/services/service.py`
so that the validator, the section writers and the assembler cannot drift apart about what a valid
request is or what may be rendered.

### Data flow

```
POST /invoke  (bearer credential → trust level; input_context reduced + screened)
  → initialize          (framework — session, schema, trust level)
  → pre_process         (VERIFIED_EXTERNAL — bound, screen, normalise context)
  → main                (DisclosureDocGenGraphNode — bridges context, runs inner graph)
        ↓
        [Inner: DisclosureDocumentWorkflowGraph]
        input_validate → prospectus_generate → risk_disclosure_and_suitability
          → compliance_checklist_and_human_review_flag → output_format
        ↓ get_output() → formatted_report → merge_output() → result
  → post_process        (ANONYMOUS — publish result as formatted_output)
  → finalize            (framework — response metadata, output)
  → END

Retry path (backbone): pre_process ← RETRY, bounded by config/config.yaml max_retry
```

### Caller-context bridge

`GraphNode.execute()` invokes the subgraph as `subgraph.invoke(user_input, session_id=…, ctx=…)`
and forwards no other outer state, so `input_context` does not cross the boundary on its own. The
validated context is bridged through the two sanctioned hooks:

```
DisclosureDocGenGraphNode.extract_input(state)        → set_caller_context({...})
DisclosureDocumentWorkflowGraph._extra_initial_state()→ seeds caller_channel / review_policy
```

A `ContextVar` (`src/graph/context_bridge.py`) carries the hand-off per thread and per task, so
concurrent invocations in one process cannot observe each other's context. What crosses is the
validated context — both values are drawn from closed sets by `PreProcessNode` — never the raw
mapping the caller sent.

### State definition

State is a flat TypedDict extending `AgentState`. All fields are JSON-serialisable primitives.

| Field | Type | Set By | Purpose |
|-------|------|--------|---------|
| `caller_channel` | `Optional[str]` | PreProcessNode | Request origin, from a closed set |
| `review_policy` | `Optional[str]` | PreProcessNode | `standard` \| `strict` |
| `product_name` | `Optional[str]` | InputValidateNode | Inert-rendered, bounded product name |
| `product_key` | `Optional[str]` | InputValidateNode | Catalogue key the product type resolved to |
| `product_type` | `Optional[str]` | InputValidateNode | Catalogue label for that key (never caller text) |
| `high_risk` | `Optional[bool]` | InputValidateNode | Risk band from the catalogue entry |
| `target_investor_profile` | `Optional[str]` | InputValidateNode | `retail` \| `institutional` |
| `financial_data` | `Optional[str]` | InputValidateNode | JSON of validated `(name, finite number)` pairs |
| `fund_performance_data` | `Optional[str]` | InputValidateNode | JSON of validated `(name, finite number)` pairs |
| `validated_product_info` | `Optional[str]` | InputValidateNode | JSON descriptor of the validated request |
| `prospectus_summary` | `Optional[str]` | ProspectusGenerateNode | 目論見書要約 (金商法第13条) |
| `risk_disclosure` | `Optional[str]` | RiskDisclosureAndSuitabilityNode | リスク開示書 (金商法第37条) |
| `suitability_explanation` | `Optional[str]` | RiskDisclosureAndSuitabilityNode | 適合性説明書 (金商法第40条) |
| `compliance_checklist` | `Optional[str]` | ComplianceChecklistAndHumanReviewFlagNode | JSON checklist |
| `human_review_required` | `Optional[bool]` | ComplianceChecklistAndHumanReviewFlagNode | Human legal review flag |
| `formatted_report` | `Optional[str]` | OutputFormatNode | The assembled document |

**State constraints (mandatory):**
- Flat TypedDict only (primitives and JSON-serialisable types)
- No credentials in State (checkpoint leakage)
- InvocationContext via `config["configurable"]` only, never in State
- No Pydantic models, dataclasses or arbitrary Python objects

## Caller-data contract

| Field | Rule |
|---|---|
| `product_name` | ≤ 120 characters; rendered inert (structural characters, control characters and line breaks removed); sentence punctuation and list markers refused; the redaction sentinel refused |
| `product_type` | Resolved against the catalogue; the document renders the catalogue's label |
| `target_investor_profile` | Closed set `{retail, institutional}`; an unrecognised value is refused, not defaulted |
| `financial_data`, `fund_performance_data` | Object of metric name → number; names match `[a-z][a-z0-9_]{0,31}`; ≤ 24 entries; every value through the finite, bounded parser |
| envelope | Unknown fields refused, not ignored |
| `input_context` | `channel` and `review_policy` only; undeclared keys dropped at the adapter; declared values screened for credential shapes |

Two rules deserve their reasons stated, because they are the ones that look optional:

- **Refusing sentence punctuation in the product name.** Stripping the document's structural
  characters stops a caller manufacturing a *section*; it does not stop them writing a *sentence* on
  the 商品名 line. In a statutory disclosure a sentence such as 「元本は保証されており損失は
  発生しません」 is a false statement of exactly the kind the document exists to prevent. No fund
  name contains sentence punctuation or a list marker, and a forged claim cannot be written without
  them.
- **Refusing the redaction sentinel.** The platform's input filter replaces personal-data shapes
  before any template code runs, so `[MASKED]` arrives as an ordinary string. A sentinel is the
  absence of a value; reporting it as the product the caller named, or certifying it in the
  compliance checklist, would state something the caller never supplied.

## Output invariant

The document must contain nothing but values this template validated, and no credential.

**There is no precision grid on this output.** Figures are disclosed as supplied: a statutory
disclosure states the caller's numbers, and rounding one would falsify the document. The invariant
is enforced on the way in instead — every figure is finite and bounded, and every rendered name is
an inert identifier — and at the boundary by the output gate below.

## Security gate placement (S-1 – S-5)

| Gate | Node | Implementation |
|------|------|---------------|
| S-1 Input trust gate | `PreProcessNode` (outer backbone) | `required_trust_level = TrustLevel.VERIFIED_EXTERNAL`; the entry adapter establishes the level from a bearer credential and never admits an anonymous caller |
| S-1 Domain input validation | `InputValidateNode` (inner) | `required_trust_level = TrustLevel.ANONYMOUS`; owns the request contract above |
| S-2 Input sanitisation | Framework `@final _security_gate_input()` + `PreProcessNode` | The framework masks the personal-data shapes it recognises; the template screens the parsed payload for injection forms and for the shapes the framework misses, and refuses the sentinel where a value would be rendered |
| S-3 Output credential scan | `OutputFormatNode.execute()` | Module-level `_security_gate_output()` over the **union** of local assignment patterns and the framework's `detect_credentials`; on violation returns ERROR and clears every output-bearing field |
| S-4 Audit trace | All `execute()` methods | `emit_trace_event(event, payload, state)` — positional args — on every domain decision |
| S-5 Credential handling | `src/api/server.py` | `provision_secrets()` via `shared.secrets.factory`; no hardcoded credential |

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → the framework `@final` gate always runs; the domain S-3 extension is a
>   module-level helper called from `execute()` (never override `_security_gate_output()`)
> - `GraphNode` (`DisclosureDocGenGraphNode`) → deliberate no-op at the subgraph boundary; the
>   boundary's own containment lives in `merge_output()`

### Containment

`AgentBaseGraph.get_output()` returns `formatted_output or result`, and it does so on an error
status as well. An output gate that merely raises, or returns ERROR without clearing, therefore
still ships the withheld document through the fallback. Two independent layers close it, and each is
falsifiable on its own:

1. `OutputFormatNode` returns ERROR and blanks `formatted_report`, `formatted_output`, `result` and
   every section field, with a closed-set reason label and no traceback.
2. `DisclosureDocGenGraphNode.merge_output()` clears rather than merges whenever the inner status is
   not success. On the ordinary path the node's `propagate` error strategy raises before this runs,
   so this layer covers the resume path — it is proved directly rather than end-to-end.

A third layer in `get_output()` was deliberately **not** added: it would contain the leak on its own
and thereby make the two above unfalsifiable.

## Framework utilisation

- [x] InvocationContext (passed via GraphNode into the inner graph)
- [x] S-1: `required_trust_level = TrustLevel.VERIFIED_EXTERNAL` on `PreProcessNode`; `ANONYMOUS`
      on all inner nodes
- [x] S-2: framework `@final _security_gate_input()` runs on every FunctionNode
- [x] S-3: module-level `_security_gate_output()` in `OutputFormatNode`, taking the union with the
      framework's `detect_credentials`
- [x] S-4: `emit_trace_event()` in every node `execute()`
- [x] S-5: `provision_secrets()` via `shared.secrets.factory` in `src/api/server.py`

### Composition pattern

- **Pattern**: GraphNode (Cat 2 subgraph)
- **Composition target**: `DisclosureDocumentWorkflowGraph` (inner `BaseGraph`)
- **Error propagation strategy**: `propagate` (SubgraphError re-raised to the outer graph)

## Import isolation confirmation

- [x] The template does not import the platform SDK (Level 0)
- [x] Import targets: `framework.*`, `shared.*`, `langgraph.*` and the standard library only
- [x] Third-party runtime dependency: `pyyaml`, declared and exact-pinned in `pyproject.toml`

## Design decision record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` (Cat 2) | `AutonomousBaseGraph` (Cat 3) | `AgentBaseGraph` | Fixed pipeline; no reasoning loop needed |
| Inner graph base | `BaseGraph` (custom topology) | `AgentBaseGraph` (five-node backbone) | `BaseGraph` | Linear domain pipeline; no retry backbone needed inside |
| Node count | 5 domain nodes (combined) | 7 nodes (separate) | 5 combined | Risk disclosure and suitability share the same inputs; the review flag is derived from the checklist |
| S-3 gate location | `_extra_security_gate_output()` hook | Module-level helper in `execute()` | Module-level helper | The hook cannot clear state; the gate must withhold *and* clear, which only `execute()` can do |
| Credential detection | Framework detector only | Local patterns only | Union of both | Neither is a superset: the framework's patterns describe credential formats, the local ones describe assignment shapes. Delegating either way narrows the gate |
| Metric channel | Opaque JSON string, rendered verbatim | Object of name → finite number | Object | The verbatim form made every figure in a statutory document caller-controlled free text, and left no place to apply the finite-number rule |
| Product type | Free-text substring match | Closed catalogue | Catalogue | Removes a free-text render and makes the risk band a function of a validated value |
| Human review flag | Separate HITL node | Flag field + output notice | Flag field | No interrupt infrastructure required; the mandatory-review notice in the document carries the obligation |
| Output rounding | Precision grid | No rounding | No rounding | Rounding a disclosed figure would falsify a statutory document; the invariant is enforced on input instead |
