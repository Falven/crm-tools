"""Save partial clarification answers through the App or conversation."""

import json
from typing import Any

from azure.core import MatchConditions
from azure.data.tables import UpdateMode
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import StrictBool

from ..contoso_open_request import (
    AnswerText,
    QuestionSetRevision,
    RequestId,
    Revision,
    _clarification,
    _customer_partition,
    _owned_request,
    _storage,
    _validate_answers,
    _view,
)


def contoso_save_answers(
    request_id: RequestId,
    answers: dict[str, AnswerText | StrictBool | None],
    revision: Revision,
    question_set_revision: QuestionSetRevision,
) -> dict[str, Any]:
    """Save partial answers without submitting. Text/choice use strings; yes/no uses booleans.

    Omitted answers stay unchanged; null clears one answer. Use both revisions from
    the current open result. A conflict requires reopening and reviewing the request.
    """
    partition = _customer_partition()
    with _storage() as table:
        entity = _owned_request(table, partition, request_id)
        if entity["Status"] != "needs_clarification":
            raise ToolError("Only requests needing clarification accept answers.")
        questions, current, _ = _clarification(entity, question_set_revision)
        _validate_answers(questions, answers)
        merged = {
            key: value
            for key, value in {**current, **answers}.items()
            if value is not None
        }
        update = {
            "PartitionKey": partition,
            "RowKey": request_id,
            "Answers": json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        }
        result = table.update_entity(
            update,
            mode=UpdateMode.MERGE,
            etag=revision,
            match_condition=MatchConditions.IfNotModified,
        )
        return _view({**entity, **update}, result["etag"])
