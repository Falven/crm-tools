"""Validate and save clarification answers in one explicit submission."""

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
    _missing_answers,
    _owned_request,
    _storage,
    _validate_answers,
    _view,
)


def contoso_submit_answers(
    request_id: RequestId,
    answers: dict[str, AnswerText | StrictBool | None],
    revision: Revision,
    question_set_revision: QuestionSetRevision,
) -> dict[str, Any]:
    """Submit clarification answers only after the customer's explicit confirmation.

    Use the current record and question-set revisions from contoso_open_request.
    Text/choice answers are strings; yes/no answers are booleans. Supplied answers
    replace their saved values; omitted answers stay unchanged, and null clears
    an answer. All required answers must be complete. Saves answers and moves the
    project to review in one operation. A failed validation saves nothing.
    A conflict requires reading
    current data and reviewing the intended answers before another attempt.
    """
    partition = _customer_partition()
    with _storage() as table:
        entity = _owned_request(table, partition, request_id)
        questions, current, _ = _clarification(entity, question_set_revision)
        _validate_answers(questions, answers)
        merged = {
            key: value
            for key, value in {**current, **answers}.items()
            if value is not None
        }
        if entity["Status"] == "in_review":
            if (
                entity.get("SubmittedQuestionSetRevision") == question_set_revision
                and revision
                in (
                    entity.get("SubmittedFromRevision"),
                    entity.metadata["etag"],
                )
                and merged == current
            ):
                return _view(entity, entity.metadata["etag"])
            raise ToolError("This request changed. Reopen it before submitting.")
        if entity["Status"] != "needs_clarification":
            raise ToolError("Only requests needing clarification can be submitted.")
        missing = _missing_answers(questions, merged)
        if missing:
            raise ToolError(
                "Answer the required questions before submitting: " + ", ".join(missing)
            )
        update = {
            "PartitionKey": partition,
            "RowKey": request_id,
            "Answers": json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
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
