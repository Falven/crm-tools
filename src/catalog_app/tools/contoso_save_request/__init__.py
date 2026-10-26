"""Save a Contoso request through conversation or its MCP App."""

from typing import Any

from azure.core import MatchConditions
from azure.data.tables import UpdateMode
from mcp.server.mcpserver.exceptions import ToolError

from ..contoso_open_request import (
    INTAKE_FIELDS,
    IntakeText,
    RequestId,
    Revision,
    _check_revision,
    _customer_partition,
    _owned_request,
    _storage,
    _view,
)


def contoso_save_request(
    request_id: RequestId,
    revision: Revision,
    requested_software: IntakeText = None,
    business_objective: IntakeText = None,
    capabilities: IntakeText = None,
    systems_and_constraints: IntakeText = None,
    decision_maker: IntakeText = None,
    preferred_deadline: IntakeText = None,
) -> dict[str, Any]:
    """Save supplied fields on the signed-in customer's draft; never submit it.

    Use the last-read revision. Omitted fields stay unchanged; an empty string
    clears a field. Partial drafts are allowed. All six fields are flat text
    values (not null); preserve the customer's deadline text, even approximate
    dates. A conflict requires reopening and reviewing the saved request.
    """
    # None is only the internal omitted-argument default. The strict string
    # schema rejects explicit null; no omitted column is included in the merge.
    supplied = {
        "requested_software": requested_software,
        "business_objective": business_objective,
        "capabilities": capabilities,
        "systems_and_constraints": systems_and_constraints,
        "decision_maker": decision_maker,
        "preferred_deadline": preferred_deadline,
    }
    partition = _customer_partition()
    with _storage() as table:
        entity = _owned_request(table, partition, request_id)
        if entity["Status"] != "draft":
            raise ToolError("Only draft requests can be edited.")
        _check_revision(entity, revision)
        changes = {
            INTAKE_FIELDS[field]: value
            for field, value in supplied.items()
            if value is not None
        }
        if not changes:
            return _view(entity, entity.metadata["etag"])
        update = {"PartitionKey": partition, "RowKey": request_id, **changes}
        result = table.update_entity(
            update,
            mode=UpdateMode.MERGE,
            etag=revision,
            match_condition=MatchConditions.IfNotModified,
        )
        return _view({**entity, **update}, result["etag"])
