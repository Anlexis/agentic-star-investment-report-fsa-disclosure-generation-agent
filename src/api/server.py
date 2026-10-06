"""AgentCore Platform v1.0"""

# Standalone HTTP entry point. This module is an adapter: it establishes what
# the platform needs to know about a request and then hands it to the graph. It
# holds no domain logic.
#
# Three things have to happen here before the graph is invoked:
#
#   1. Establish the caller's trust level from a bearer credential. The
#      pipeline's entry gate requires a verified external caller, so an adapter
#      that admits unauthenticated callers at the anonymous level produces an
#      agent that refuses every request it is given — an error raised at the
#      entry node, with nothing in the response explaining why.
#   2. Reduce the caller's context to the declared contract and screen it for
#      credential shapes. The platform's output check scans every value of every
#      node result, and the first node returns the invocation context verbatim
#      in its own result, so a credential-shaped string anywhere in the context
#      fails the run before any of this template's code executes. The request
#      cannot succeed either way; refusing it here turns an opaque first-node
#      error into a response that names the field at fault.
#   3. Load config/config.yaml and hand it to the graph, so the declared runtime
#      parameters are the ones in force rather than framework defaults.

import asyncio
import hmac
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from shared.secrets import factory as secrets_factory

from src.graph.graph import InvestmentReportFSADisclosureAgent
from src.services.service import CALLER_CONTEXT_FIELDS, MAX_REQUEST_CHARS

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_SAFE_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DEFAULT_TIMEOUT_S = 30.0


def load_runtime_config(path: Path = _CONFIG_PATH) -> Dict[str, Any]:
    """Read the declared runtime parameters; an absent file means framework defaults."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


RUNTIME_CONFIG: Dict[str, Any] = load_runtime_config()

app = FastAPI(title="Investment report and disclosure document agent")

# The namespace and agent name must match config/agent.yaml (namespace and class).
agent = InvestmentReportFSADisclosureAgent(config=RUNTIME_CONFIG)
agent.compile()
agent.provision_secrets(secrets_factory(namespace="fin", agent_name="InvestmentReportFSADisclosureAgent"))


class InvokeRequest(BaseModel):
    """The request body accepted by /invoke."""

    input: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    session_id: str = Field(default="", max_length=128)
    input_context: Optional[Dict[str, Any]] = None


def request_timeout_s() -> float:
    """The declared request deadline, falling back only when none is declared."""
    declared = RUNTIME_CONFIG.get("timeout_s", _DEFAULT_TIMEOUT_S)
    try:
        value = float(declared)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    return value if value > 0 else _DEFAULT_TIMEOUT_S


def resolve_trust_level(request: Request) -> TrustLevel:
    """Map the presented bearer credential to a trust level, or refuse.

    There is no anonymous path. The entry gate requires a verified external
    caller, so admitting an unauthenticated one at the anonymous level only
    defers the refusal to a place where the caller cannot see the reason.

    Two credentials are accepted because two are presented in practice: the
    ordinary invocation token, and the separate runner credential used by the
    deployment smoke check for entries declared at the internal trust level.
    """
    external = os.environ.get("INVOKE_AUTH_TOKEN", "")
    internal = os.environ.get("STG_INTERNAL_RUNNER_TOKEN", "")
    if not external and not internal:
        raise HTTPException(
            status_code=503,
            detail="Invocation auth is not configured; set INVOKE_AUTH_TOKEN.",
        )

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(status_code=401, detail="Bearer credential required.")
    if internal and hmac.compare_digest(presented, internal):
        return TrustLevel.INTERNAL
    if external and hmac.compare_digest(presented, external):
        return TrustLevel.VERIFIED_EXTERNAL
    raise HTTPException(status_code=401, detail="Bearer credential rejected.")


def _field_label(name: Any, position: int) -> str:
    """Name a context field only when the name is itself safe to echo back."""
    if isinstance(name, str) and _SAFE_FIELD_NAME.match(name) and not detect_credentials_in_value(name):
        return f"input_context.{name}"
    return f"input_context field #{position}"


def prepare_input_context(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop undeclared keys, then refuse any declared value carrying a credential.

    Dropping rather than ignoring is the operative verb. A validator that
    ignores an undeclared key leaves it in the mapping handed to invoke(), from
    where it reaches the entry node's result and the platform's output check
    just the same. Only the declared keys travel onward.

    Fields are screened one at a time so a refusal can name the field. Screening
    per field is exactly equivalent to screening the whole mapping — the
    platform's scan of a mapping is the union over its values — so naming the
    field neither widens nor narrows what is refused. The status is 400 rather
    than 422 because 422 belongs to request-schema validation and returns a
    differently shaped body.
    """
    if not raw:
        return {}
    prepared: Dict[str, Any] = {}
    for position, key in enumerate(sorted(raw, key=str), start=1):
        if key not in CALLER_CONTEXT_FIELDS:
            continue
        value = raw[key]
        if detect_credentials_in_value(value):
            raise HTTPException(
                status_code=400,
                detail=f"Credential-shaped value refused in {_field_label(key, position)}.",
            )
        prepared[key] = value
    return prepared


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    trust_level = resolve_trust_level(request)
    input_context = prepare_input_context(req.input_context)
    with bound_secrets(agent._secrets_provider):  # noqa: SLF001 — provider handover
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust_level,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(agent.invoke, req.input, ctx=ctx, input_context=input_context),
                timeout=request_timeout_s(),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail="Document generation exceeded the configured deadline.",
            )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "InvestmentReportFSADisclosureAgent"}
