# Investment Report & FSA Disclosure Generation Agent

AI agent for generating investment reports and FSA disclosure documents, built with Agentic Star.

> **Category**: Cat 2 (industry-vertical pipeline)
> **Industry**: Finance
> **Template ID**: FIN-C2-101

## Overview

Assembles the disclosure pack that has to accompany a retail investment product in Japan. The
caller describes one product — its name, its type, the investor segment it is offered to, and any
figures they want disclosed — and the agent produces a prospectus summary, a risk disclosure, a
suitability explanation and a statutory compliance checklist, combined into a single document with
a human-review notice attached.

The product type is resolved against a catalogue of eleven instrument classes, and that catalogue
entry decides the risk band. The band and the investor segment together decide the growth-quota
eligibility statement and whether the document carries the mandatory legal-review notice, so two
callers describing different products receive materially different documents rather than the same
boilerplate.

Generation is deterministic and rules-based: it renders a fixed document structure from a validated
request. It does not call a language model, does not retrieve any external record, and does not
verify that the figures the caller supplies are true. It produces a draft for human legal and
compliance review, never a document fit to issue as it stands — every document it emits says so.

Every caller-supplied string is bounded and rendered inert before it can reach the document, and
every caller-supplied figure passes a finite, bounded parser. That is not incidental: the document
is a statutory disclosure whose structure carries meaning, so an unfiltered line break or a
sentence on the product-name line would be a statement the template did not write.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded mode.
Without the platform the agent fails during import and graph compilation rather than starting in a
partially working state — the framework packages it depends on are not present, so there is nothing
to degrade to. This is intentional: a half-running agent is worse than one that refuses to start.

The entry point additionally refuses to serve traffic until an invocation credential is configured,
answering with 503 rather than admitting unauthenticated callers.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling the agent

`POST /invoke` with a bearer credential. `input` is a JSON-encoded string describing one product:

```json
{
  "input": "{\"product_name\": \"eMAXIS Slim 全世界株式\", \"product_type\": \"投資信託\", \"target_investor_profile\": \"retail\", \"financial_data\": {\"nav\": 12345.67}}",
  "session_id": "example-001",
  "input_context": {"channel": "web", "review_policy": "standard"}
}
```

| Field | Required | Contract |
|---|---|---|
| `product_name` | yes | At most 120 characters. Rendered into the document, so structural characters, line breaks, sentence punctuation and list markers are refused. |
| `product_type` | yes | One of the catalogue keys or their accepted spellings (`投資信託`, `株式`, `債券`, `etf`, `reit`, `fx`, `デリバティブ`, `オプション`, `先物`, `cfd`, `暗号資産`). |
| `target_investor_profile` | no | `retail` (default) or `institutional`. |
| `financial_data` | no | Object of metric name → finite number. Names match `[a-z][a-z0-9_]*`, at most 24 entries. |
| `fund_performance_data` | no | Same contract as `financial_data`. |

`input_context` accepts `channel` and `review_policy` only; anything else is dropped before the
graph is invoked. `review_policy: "strict"` forces the mandatory-review notice on regardless of the
product's risk band.

An unrecognised field is refused rather than ignored, and a refusal names the field at fault and
never the value.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime parameters
docs/         design and operational documentation
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/config.yaml` for your own runtime parameters.
2. Extend the product catalogue and the statutory article list in `src/services/service.py` for
   your own instrument coverage and jurisdiction.
3. Review the section writers under `src/nodes/` for the wording your own compliance function
   requires.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
