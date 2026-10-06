"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from typing import Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Investment report and disclosure document agent state.

    All inherited fields (user_input, status, session_id, node_history,
    error_log, hitl_*, validated_input, formatted_output, etc.) come from
    AgentState.  Only domain-specific fields are declared here.

    Caller context — validated by PreProcessNode from ``input_context``:
    - caller_channel:          Origin of the request, from a closed set
    - review_policy:           ``standard`` | ``strict``; ``strict`` forces the
                               human-review flag on regardless of product risk
    - report_char_limit:       Largest document this deployment will release,
                               forwarded from config/config.yaml by the inner
                               graph — an operator parameter, never caller data

    Validated request — produced by InputValidateNode:
    - product_name:            Investment product name, inert-rendered and bounded
    - product_key:             Catalogue key the caller's product type resolved to
    - product_type:            Catalogue label for that key (never caller text)
    - high_risk:               Risk band of the product type, from the catalogue
    - target_investor_profile: ``retail`` | ``institutional``
    - financial_data:          JSON object of validated metric name → finite number
    - fund_performance_data:   JSON object of validated metric name → finite number
    - validated_product_info:  JSON descriptor of the validated request

    Generated sections — set by the inner domain nodes:
    - prospectus_summary:      Generated prospectus summary
    - risk_disclosure:         Risk disclosure section
    - suitability_explanation: Suitability explanation section
    - compliance_checklist:    JSON-encoded statutory compliance checklist
    - human_review_required:   True when human legal / compliance review is required

    Final output — assembled by OutputFormatNode:
    - formatted_report:        Complete disclosure document (all sections combined)
    """

    # Caller context (validated) and forwarded runtime parameters
    caller_channel: Optional[str]
    review_policy: Optional[str]
    report_char_limit: Optional[int]

    # Validated request
    product_name: Optional[str]
    product_key: Optional[str]
    product_type: Optional[str]
    high_risk: Optional[bool]
    target_investor_profile: Optional[str]
    financial_data: Optional[str]
    fund_performance_data: Optional[str]
    validated_product_info: Optional[str]

    # Generated sections
    prospectus_summary: Optional[str]
    risk_disclosure: Optional[str]
    suitability_explanation: Optional[str]
    compliance_checklist: Optional[str]
    human_review_required: Optional[bool]

    # Final assembled document
    formatted_report: Optional[str]
