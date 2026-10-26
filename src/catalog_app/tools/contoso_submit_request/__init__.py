"""Submit a saved intake draft after the customer's explicit decision."""

from typing import Any

from azure.core import MatchConditions
from azure.data.tables import UpdateMode
from mcp.server.mcpserver.exceptions import ToolError

from ..contoso_open_request import (
    RequestId,
    Revision,
    _check_revision,
    _customer_partition,
    _missing_intake_fields,
    _owned_request,
    _storage,
    _view,
)


def contoso_submit_request(
    request_id: RequestId, revision: Revision
) -> dict[str, Any]:
    """Submit a saved draft only after the customer explicitly confirms submission.

    Takes the request ID and current revision, not new field values. Requested
    software, business objective, capabilities, and decision maker must be saved
    and nonblank. Systems/constraints and preferred deadline are optional. Moves
    a valid draft to submitted; creation and saving never submit. A conflict
    requires reopening and reviewing. Repeating the completed submission with
    its original or resulting revision returns the saved result without writing.
    """
    partition = _customer_partition()
    with _storage() as table:
        entity = _owned_request(table, partition, request_id)
        if entity["Status"] == "submitted":
            if entity.get("IntakeSubmittedFromRevision") and revision in (
                entity["IntakeSubmittedFromRevision"],
                entity.metadata["etag"],
            ):
                return _view(entity, entity.metadata["etag"])
            raise ToolError("This request changed. Reopen it before submitting.")
        if entity["Status"] != "draft":
            raise ToolError("Only draft requests can be submitted.")
        _check_revision(entity, revision)
        missing = _missing_intake_fields(entity)
        if missing:
            raise ToolError(
                "Complete and save the required fields before submitting: "
                + ", ".join(missing)
            )
        update = {
            "PartitionKey": partition,
            "RowKey": request_id,
            "Status": "submitted",
            # Keep intake retry metadata separate from invoice clarification.
            "IntakeSubmittedFromRevision": revision,
        }
        result = table.update_entity(
            update,
            mode=UpdateMode.MERGE,
            etag=revision,
            match_condition=MatchConditions.IfNotModified,
        )
        return _view({**entity, **update}, result["etag"])
