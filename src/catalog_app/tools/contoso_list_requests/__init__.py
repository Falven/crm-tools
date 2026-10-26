"""List the signed-in customer's saved Contoso requests."""

from typing import Annotated, Any

from pydantic import Field

from ..contoso_open_request import (
    _customer_partition,
    _next_action,
    _storage,
    _validate_request,
)


def contoso_list_requests(
    needs_attention: Annotated[bool, Field(strict=True)] = False,
) -> dict[str, Any]:
    """List your requests; needs_attention limits results to drafts and clarification."""
    partition = _customer_partition()
    with _storage() as table:
        entities = table.query_entities(
            "PartitionKey eq @partition and DemoId eq @demo",
            parameters={"partition": partition, "demo": "contoso-demo"},
        )
        requests = []
        for entity in entities:
            _validate_request(entity)
            if needs_attention and entity["Status"] not in (
                "draft",
                "needs_clarification",
            ):
                continue
            requests.append(
                {
                    "request_id": entity["RowKey"],
                    "title": entity["Title"],
                    "status": entity["Status"],
                    "next_action": _next_action(entity["Status"]),
                }
            )
    return {"requests": sorted(requests, key=lambda request: request["request_id"])}
