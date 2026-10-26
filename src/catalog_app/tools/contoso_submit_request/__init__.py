"""Create a saved software request only after explicit submission."""

from typing import Any

from azure.core.exceptions import ResourceExistsError
from mcp.server.mcpserver.exceptions import ToolError

from ..contoso_open_request import (
    INTAKE_FIELDS,
    IntakeText,
    SubmissionId,
    _brief,
    _customer_partition,
    _missing_intake_fields,
    _owned_request,
    _storage,
    _view,
)


def contoso_submit_request(
    submission_id: SubmissionId,
    requested_software: IntakeText,
    business_objective: IntakeText,
    capabilities: IntakeText,
    decision_maker: IntakeText,
    systems_and_constraints: IntakeText = "",
    preferred_deadline: IntakeText = "",
) -> dict[str, Any]:
    """Submit the complete software brief after the customer's explicit confirmation.

    Use the submission_id from contoso_review_request. The four required fields
    must be nonblank; systems/constraints and the customer's deadline are optional.
    This is the first storage write. Returns the saved request ID and status.
    Reusing the same submission_id and brief returns the existing request without
    creating another; different details with an already-used ID are rejected.
    """
    partition = _customer_partition()
    brief = {
        "requested_software": requested_software,
        "business_objective": business_objective,
        "capabilities": capabilities,
        "systems_and_constraints": systems_and_constraints,
        "decision_maker": decision_maker,
        "preferred_deadline": preferred_deadline,
    }
    missing = _missing_intake_fields(brief)
    if missing:
        raise ToolError(
            "Complete the required fields before submitting: " + ", ".join(missing)
        )
    entity = {
        "PartitionKey": partition,
        "RowKey": "REQUEST-" + submission_id,
        "DemoId": "contoso-demo",
        "Status": "submitted",
        **{INTAKE_FIELDS[field]: value for field, value in brief.items()},
    }
    with _storage() as table:
        try:
            result = table.create_entity(entity)
        except ResourceExistsError:
            saved = _owned_request(table, partition, entity["RowKey"])
            if _brief(saved) != brief:
                raise ToolError(
                    "This submission already exists with different details. Open the saved project."
                ) from None
            return _view(saved, saved.metadata["etag"])
        return _view(entity, result["etag"])
