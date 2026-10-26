"""Prepare a software request for review without storing a draft."""

from typing import Any
from uuid import uuid4

from ..contoso_open_request import (
    IntakeText,
    _customer_partition,
    _missing_intake_fields,
)


def contoso_review_request(
    requested_software: IntakeText = "",
    business_objective: IntakeText = "",
    capabilities: IntakeText = "",
    systems_and_constraints: IntakeText = "",
    decision_maker: IntakeText = "",
    preferred_deadline: IntakeText = "",
) -> dict[str, Any]:
    """Review a proposed software request in conversation or a prefilled MCP App.

    Accepts incomplete details and reports missing required fields. Writes nothing
    to storage. Unsubmitted details stay in the conversation or open form.
    Returns a submission_id; retain it when submitting the complete brief with
    contoso_submit_request after explicit customer confirmation. Reuse that ID
    and the same brief if retrying an uncertain submission; never generate a new
    review merely to retry. Keep approximate deadlines as the customer's text.
    """
    _customer_partition()
    brief = {
        "requested_software": requested_software,
        "business_objective": business_objective,
        "capabilities": capabilities,
        "systems_and_constraints": systems_and_constraints,
        "decision_maker": decision_maker,
        "preferred_deadline": preferred_deadline,
    }
    return {
        "view": "review",
        "submission_id": uuid4().hex,
        "brief": brief,
        "missing_required_fields": _missing_intake_fields(brief),
    }
