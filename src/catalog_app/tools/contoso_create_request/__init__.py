"""Create an owned Contoso intake draft without submitting it."""

from typing import Any
from uuid import uuid4

from ..contoso_open_request import (
    INTAKE_FIELDS,
    IntakeText,
    _customer_partition,
    _storage,
    _view,
)


def contoso_create_request(
    requested_software: IntakeText = "",
    business_objective: IntakeText = "",
    capabilities: IntakeText = "",
    systems_and_constraints: IntakeText = "",
    decision_maker: IntakeText = "",
    preferred_deadline: IntakeText = "",
) -> dict[str, Any]:
    """Create and save a software request draft for the signed-in customer.

    All six fields are flat text and may be omitted or blank. Keep the customer's
    preferred deadline as text, including approximate dates. Returns the saved
    request ID, revision, prefilled editable App, and missing required fields.
    Creation never submits. Reopen that ID with contoso_open_request rather than
    calling creation again; each creation makes a new draft. Submit only after
    the customer's explicit decision using contoso_submit_request.
    """
    partition = _customer_partition()
    fields = {
        "requested_software": requested_software,
        "business_objective": business_objective,
        "capabilities": capabilities,
        "systems_and_constraints": systems_and_constraints,
        "decision_maker": decision_maker,
        "preferred_deadline": preferred_deadline,
    }
    entity = {
        "PartitionKey": partition,
        "RowKey": "CONTOSO-" + uuid4().hex,
        "DemoId": "contoso-demo",
        "Status": "draft",
        **{INTAKE_FIELDS[field]: value for field, value in fields.items()},
    }
    with _storage() as table:
        # Create, never upsert: an ID collision must not overwrite customer data.
        result = table.create_entity(entity)
        return _view(entity, result["etag"])
