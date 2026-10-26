"""Submit saved clarification answers after the customer's explicit decision."""

from typing import Any

from azure.core import MatchConditions
from azure.data.tables import UpdateMode
from mcp.server.mcpserver.exceptions import ToolError

from ..contoso_open_request import (
    QuestionSetRevision,
    RequestId,
    Revision,
    _clarification,
    _customer_partition,
    _missing_answers,
    _owned_request,
    _storage,
    _view,
)


def contoso_submit_answers(
    request_id: RequestId,
    revision: Revision,
    question_set_revision: QuestionSetRevision,
) -> dict[str, Any]:
    """Submit saved answers only after the customer explicitly confirms submission.

    Use the current record and question-set revisions. Missing required answers
    prevent submission; a conflict requires reopening and reviewing the request.
    """
    partition = _customer_partition()
    with _storage() as table:
        entity = _owned_request(table, partition, request_id)
        questions, answers, _ = _clarification(entity, question_set_revision)
        if entity["Status"] == "in_review":
            if entity.get(
                "SubmittedQuestionSetRevision"
            ) == question_set_revision and revision in (
                entity.get("SubmittedFromRevision"),
                entity.metadata["etag"],
            ):
                return _view(entity, entity.metadata["etag"])
            raise ToolError("This request changed. Reopen it before submitting.")
        if entity["Status"] != "needs_clarification":
            raise ToolError("Only requests needing clarification can be submitted.")
        missing = _missing_answers(questions, answers)
        if missing:
            raise ToolError(
                "Answer the required questions before submitting: " + ", ".join(missing)
            )
        update = {
            "PartitionKey": partition,
            "RowKey": request_id,
            "Status": "in_review",
            "SubmittedFromRevision": revision,
            "SubmittedQuestionSetRevision": question_set_revision,
        }
        result = table.update_entity(
            update,
            mode=UpdateMode.MERGE,
            etag=revision,
            match_condition=MatchConditions.IfNotModified,
        )
        return _view({**entity, **update}, result["etag"])
