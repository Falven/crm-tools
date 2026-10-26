"""Contoso's public MCP boundary with controlled caller and storage fixtures.

These checks do not establish live Azure access or ToolForge/Foundry integration.
"""

import os
import json
import re
from contextlib import contextmanager
from copy import deepcopy
import unittest
from unittest.mock import patch

from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.data.tables import UpdateMode
from mcp.client import Client
from mcp.server import MCPServer
from mcp.server.apps import Apps
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from tests.fixtures.catalog import register_catalog_tool

TENANT = "22222222-2222-2222-2222-222222222222"
CUSTOMER = "33333333-3333-3333-3333-333333333333"
OTHER_CUSTOMER = "44444444-4444-4444-4444-444444444444"
REQUEST_ID = "CONTOSO-DEMO-001"
CLARIFICATION_ID = "CONTOSO-DEMO-002"
QUESTIONS = [
    {
        "id": "approval_owner",
        "type": "text",
        "prompt": "Who should approve contractor timesheets?",
        "required": True,
    },
    {
        "id": "reminder_schedule",
        "type": "choice",
        "prompt": "When should reminders be sent?",
        "required": True,
        "choices": ["Daily", "Weekly"],
    },
    {
        "id": "notify_contractors",
        "type": "yes_no",
        "prompt": "Should contractors receive reminders?",
        "required": True,
    },
    {
        "id": "notes",
        "type": "text",
        "prompt": "Anything else we should know?",
        "required": False,
    },
]
ENVIRONMENT = {
    "CONTOSO_STORAGE_CONNECTION_STRING": "synthetic-secret-not-for-output",
    "CONTOSO_REQUESTS_TABLE": "ContosoRequests",
}


@contextmanager
def verified(claims):
    context = auth_context_var.set(
        AuthenticatedUser(
            AccessToken(
                token="synthetic-bearer-not-for-output",
                client_id="controlled-client",
                scopes=["ToolForge.MCP.Access"],
                claims=claims,
            )
        )
    )
    try:
        yield
    finally:
        auth_context_var.reset(context)


class Row(dict):
    def __init__(self, values, etag='"1"'):
        super().__init__(values)
        self.metadata = {"etag": etag}


class Table:
    """Controlled Azure SDK boundary; it does not emulate Azure authentication."""

    def __init__(self):
        self.race_before_update = False
        self.question_race_before_update = False
        self.rows = {
            (f"contoso:{TENANT}:{CUSTOMER}", REQUEST_ID): Row(
                {
                    "PartitionKey": f"contoso:{TENANT}:{CUSTOMER}",
                    "RowKey": REQUEST_ID,
                    "DemoId": "contoso-demo",
                    "Title": "Contractor timesheet approvals",
                    "BusinessObjective": "Reduce manual timesheet follow-up.",
                    "Status": "draft",
                }
            ),
        }
        self.rows[(f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)] = Row(
            {
                "PartitionKey": f"contoso:{TENANT}:{CUSTOMER}",
                "RowKey": CLARIFICATION_ID,
                "DemoId": "contoso-demo",
                "Title": "Contractor timesheet approvals",
                "BusinessObjective": "Reduce manual timesheet follow-up.",
                "Status": "needs_clarification",
                "Capabilities": "Submit weekly timesheets and route approvals.",
                "SystemsAndConstraints": "Microsoft Teams and the existing project list.",
                "DecisionMaker": "Customer operations lead",
                "PreferredDeadline": "Pilot next month",
                "QuestionSetRevision": "clarification-1",
                "Questions": json.dumps(QUESTIONS),
                "Answers": json.dumps({"approval_owner": "Engagement lead"}),
            }
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_entity(self, partition_key, row_key):
        try:
            return deepcopy(self.rows[(partition_key, row_key)])
        except KeyError:
            raise ResourceNotFoundError("Not found") from None

    def create_entity(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self.rows:
            raise ResourceExistsError("Already exists")
        self.rows[key] = Row(deepcopy(entity))
        return {"etag": '"1"'}

    def query_entities(self, query_filter, *, parameters):
        filters = re.findall(r"(\w+) eq @(\w+)", query_filter)
        return [
            deepcopy(row)
            for row in self.rows.values()
            if all(row.get(field) == parameters[value] for field, value in filters)
        ]

    def update_entity(
        self, entity, mode=UpdateMode.MERGE, *, etag=None, match_condition=None
    ):
        key = (entity["PartitionKey"], entity["RowKey"])
        current = self.rows[key]
        if self.question_race_before_update:
            self.question_race_before_update = False
            current["QuestionSetRevision"] = "clarification-2"
            current.metadata["etag"] = '"2"'
        if self.race_before_update:
            self.race_before_update = False
            current["BusinessObjective"] = "A newer saved objective."
            current.metadata["etag"] = '"2"'
        if match_condition == MatchConditions.IfNotModified and etag not in (
            "*",
            current.metadata["etag"],
        ):
            raise ResourceModifiedError("Changed")
        revision = int(current.metadata["etag"][1:-1]) + 1
        values = {**current, **entity} if mode == UpdateMode.MERGE else dict(entity)
        self.rows[key] = Row(values, f'"{revision}"')
        return {"etag": f'"{revision}"'}


TOOLS = (
    "contoso_review_request",
    "contoso_submit_request",
    "contoso_list_requests",
    "contoso_open_request",
    "contoso_submit_answers",
)
BRIEF = {
    "requested_software": "Contractor timesheet approvals",
    "business_objective": "Reduce weekly timesheet follow-up.",
    "capabilities": "Collect timesheets, route approvals, and show missing submissions.",
    "systems_and_constraints": "Use Teams for reminders.",
    "decision_maker": "Operations lead",
    "preferred_deadline": "Before the next quarterly contractor intake",
}
SUBMISSION_ID = "a" * 32
ANSWERS = {"reminder_schedule": "Weekly", "notify_contractors": False}


class ContosoRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = self.new_server()
        self.table = Table()

    def new_server(self):
        server = MCPServer("software-request-check", extensions=[Apps()])
        for name in TOOLS:
            register_catalog_tool(server, name)
        return server

    async def call(self, name, arguments, claims=None):
        if claims is None:
            claims = {"tid": TENANT, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"}
        with (
            verified(claims),
            patch.dict(os.environ, ENVIRONMENT),
            patch(
                "azure.data.tables.TableClient.from_connection_string",
                return_value=self.table,
            ),
        ):
            async with Client(self.server) as client:
                result = await client.call_tool(name, arguments)
        self.assertNotIn("synthetic-secret-not-for-output", result.model_dump_json())
        self.assertNotIn("synthetic-bearer-not-for-output", result.model_dump_json())
        return result

    def clarification_arguments(self, **changes):
        return {
            "request_id": CLARIFICATION_ID,
            "answers": ANSWERS,
            "revision": '"1"',
            "question_set_revision": "clarification-1",
            **changes,
        }

    async def test_discovery_has_five_tools_and_only_two_app_entry_points(self):
        with patch.dict(os.environ, {}, clear=True):
            async with Client(self.server) as client:
                tools = (await client.list_tools()).tools
        self.assertCountEqual([tool.name for tool in tools], TOOLS)
        apps = [
            tool for tool in tools if (tool.meta or {}).get("ui", {}).get("resourceUri")
        ]
        self.assertCountEqual(
            [tool.name for tool in apps],
            ["contoso_review_request", "contoso_open_request"],
        )
        self.assertCountEqual(
            [tool.title for tool in apps],
            ["Review software request", "Open software project"],
        )
        descriptors = {tool.name: tool for tool in tools}
        self.assertTrue(
            descriptors["contoso_review_request"].annotations.read_only_hint
        )
        for name in ("contoso_submit_request", "contoso_submit_answers"):
            self.assertEqual(
                descriptors[name].meta["ui"]["visibility"], ["model", "app"]
            )
        self.assertNotIn(
            "revision", descriptors["contoso_submit_request"].input_schema["properties"]
        )
        self.assertIn(
            "answers", descriptors["contoso_submit_answers"].input_schema["required"]
        )

    async def test_review_is_prefilled_without_storage_access_or_a_saved_draft(self):
        before = deepcopy(self.table.rows)
        with (
            verified({"tid": TENANT, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"}),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "azure.data.tables.TableClient.from_connection_string",
                side_effect=AssertionError("Review accessed storage"),
            ),
        ):
            async with Client(self.server) as client:
                result = await client.call_tool(
                    "contoso_review_request",
                    {
                        "requested_software": BRIEF["requested_software"],
                        "preferred_deadline": BRIEF["preferred_deadline"],
                    },
                )
        self.assertFalse(result.is_error, result.content)
        review = result.structured_content
        self.assertEqual(review["view"], "review")
        self.assertRegex(review["submission_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("request_id", review)
        self.assertEqual(
            review["brief"]["requested_software"], BRIEF["requested_software"]
        )
        self.assertEqual(
            review["brief"]["preferred_deadline"], BRIEF["preferred_deadline"]
        )
        self.assertEqual(review["brief"]["decision_maker"], "")
        self.assertEqual(
            review["missing_required_fields"],
            ["business_objective", "capabilities", "decision_maker"],
        )
        self.assertEqual(self.table.rows, before)

    async def test_submission_creates_the_first_record_with_the_reviewed_brief(self):
        review = await self.call(
            "contoso_review_request", {**BRIEF, "decision_maker": ""}
        )
        before = deepcopy(self.table.rows)
        self.assertEqual(len(before), 2)
        result = await self.call(
            "contoso_submit_request",
            {
                "submission_id": review.structured_content["submission_id"],
                **BRIEF,
            },
        )
        self.assertFalse(result.is_error, result.content)
        saved = result.structured_content
        self.assertEqual(saved["status"], "submitted")
        self.assertEqual(saved["view"], "status")
        self.assertEqual(saved["brief"], BRIEF)
        self.assertEqual(
            saved["request_id"], "REQUEST-" + review.structured_content["submission_id"]
        )
        self.assertEqual(len(self.table.rows), len(before) + 1)
        self.server = self.new_server()
        opened = await self.call(
            "contoso_open_request", {"request_id": saved["request_id"]}
        )
        self.assertEqual(opened.structured_content, saved)

    async def test_invalid_briefs_do_not_create_records(self):
        before = deepcopy(self.table.rows)
        for field in (
            "requested_software",
            "business_objective",
            "capabilities",
            "decision_maker",
        ):
            for value in ("", "  ", None, 42, "x" * 4001):
                with self.subTest(field=field, value=str(value)[:20]):
                    result = await self.call(
                        "contoso_submit_request",
                        {
                            "submission_id": SUBMISSION_ID,
                            **BRIEF,
                            field: value,
                        },
                    )
                    self.assertTrue(result.is_error)
        for value in ("", "*", "../other", "a" * 31, "A" * 32):
            result = await self.call(
                "contoso_submit_request", {"submission_id": value, **BRIEF}
            )
            self.assertTrue(result.is_error)
        for extra in ({"customer_id": OTHER_CUSTOMER}, {"revision": '"1"'}):
            result = await self.call(
                "contoso_submit_request",
                {"submission_id": SUBMISSION_ID, **BRIEF, **extra},
            )
            self.assertTrue(result.is_error)
        self.assertEqual(self.table.rows, before)

    async def test_repeated_submission_returns_one_record_and_rejects_changed_payload(
        self,
    ):
        args = {"submission_id": SUBMISSION_ID, **BRIEF}
        first = await self.call("contoso_submit_request", args)
        self.assertFalse(first.is_error, first.content)
        row_id = (
            f"contoso:{TENANT}:{CUSTOMER}",
            first.structured_content["request_id"],
        )
        etag = self.table.rows[row_id].metadata["etag"]
        self.server = self.new_server()
        repeated = await self.call("contoso_submit_request", args)
        self.assertEqual(repeated.structured_content, first.structured_content)
        self.assertEqual(len(self.table.rows), 3)
        self.assertEqual(self.table.rows[row_id].metadata["etag"], etag)
        changed = await self.call(
            "contoso_submit_request", {**args, "business_objective": "Different work"}
        )
        self.assertTrue(changed.is_error)
        self.assertEqual(self.table.rows[row_id].metadata["etag"], etag)
        current = await self.call(
            "contoso_open_request",
            {"request_id": first.structured_content["request_id"]},
        )
        self.assertEqual(current.structured_content, first.structured_content)

    async def test_submission_collision_cannot_overwrite_an_unrelated_record(self):
        row_id = (f"contoso:{TENANT}:{CUSTOMER}", "REQUEST-" + SUBMISSION_ID)
        self.table.rows[row_id] = Row(
            {
                "PartitionKey": row_id[0],
                "RowKey": row_id[1],
                "DemoId": "another-demo",
                "Title": "Private unrelated data",
                "BusinessObjective": "Do not expose",
                "Status": "submitted",
            }
        )
        before = deepcopy(self.table.rows)
        result = await self.call(
            "contoso_submit_request", {"submission_id": SUBMISSION_ID, **BRIEF}
        )
        self.assertTrue(result.is_error)
        self.assertNotIn("Private unrelated data", result.model_dump_json())
        self.assertEqual(self.table.rows, before)

    async def test_customer_identity_scopes_creation_reads_and_lists(self):
        first = await self.call(
            "contoso_submit_request", {"submission_id": SUBMISSION_ID, **BRIEF}
        )
        self.assertFalse(first.is_error, first.content)
        for claims in (
            {"tid": TENANT, "oid": OTHER_CUSTOMER, "scp": "ToolForge.MCP.Access"},
            {"tid": OTHER_CUSTOMER, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"},
        ):
            with self.subTest(claims=claims):
                opened = await self.call(
                    "contoso_open_request",
                    {"request_id": first.structured_content["request_id"]},
                    claims,
                )
                self.assertTrue(opened.is_error)
                listed = await self.call("contoso_list_requests", {}, claims)
                self.assertEqual(listed.structured_content["requests"], [])
                answers = await self.call(
                    "contoso_submit_answers", self.clarification_arguments(), claims
                )
                self.assertTrue(answers.is_error)
                own = await self.call(
                    "contoso_submit_request",
                    {
                        "submission_id": SUBMISSION_ID,
                        **BRIEF,
                        "requested_software": "Another project",
                    },
                    claims,
                )
                self.assertFalse(own.is_error, own.content)
                self.assertEqual(own.structured_content["title"], "Another project")
        original = await self.call(
            "contoso_open_request",
            {"request_id": first.structured_content["request_id"]},
        )
        self.assertEqual(original.structured_content, first.structured_content)

    async def test_application_and_incomplete_user_identities_cannot_use_the_workflow(
        self,
    ):
        before = deepcopy(self.table.rows)
        calls = {
            "contoso_review_request": BRIEF,
            "contoso_submit_request": {"submission_id": SUBMISSION_ID, **BRIEF},
            "contoso_list_requests": {},
            "contoso_open_request": {"request_id": CLARIFICATION_ID},
            "contoso_submit_answers": self.clarification_arguments(),
        }
        for claims in (
            {
                "tid": TENANT,
                "oid": CUSTOMER,
                "roles": ["ToolForge.MCP.Access.Application"],
            },
            {
                "tid": TENANT,
                "oid": CUSTOMER,
                "scp": "ToolForge.MCP.Access",
                "idtyp": "app",
            },
            {"tid": TENANT, "oid": CUSTOMER, "scp": " "},
            {"tid": TENANT, "scp": "ToolForge.MCP.Access"},
            {"oid": CUSTOMER, "scp": "ToolForge.MCP.Access"},
        ):
            for name, arguments in calls.items():
                with self.subTest(name=name, claims=claims):
                    result = await self.call(name, arguments, claims)
                    self.assertTrue(result.is_error)
        self.assertEqual(self.table.rows, before)

    async def test_existing_drafts_remain_readable_without_being_changed(self):
        before = deepcopy(self.table.rows)
        result = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.structured_content["status"], "draft")
        self.assertEqual(result.structured_content["view"], "status")
        self.assertEqual(
            result.structured_content["brief"]["requested_software"],
            "Contractor timesheet approvals",
        )
        self.assertEqual(result.structured_content["brief"]["capabilities"], "")
        self.assertEqual(self.table.rows, before)

    async def test_list_returns_owned_projects_and_attention(self):
        row = deepcopy(self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", REQUEST_ID)])
        row.update(RowKey="CONTOSO-DEMO-003", Status="submitted")
        self.table.rows[(row["PartitionKey"], row["RowKey"])] = row
        unrelated = deepcopy(row)
        unrelated.update(RowKey="UNRELATED", DemoId="another-demo")
        self.table.rows[(unrelated["PartitionKey"], unrelated["RowKey"])] = unrelated
        listed = await self.call("contoso_list_requests", {})
        self.assertEqual(
            [row["request_id"] for row in listed.structured_content["requests"]],
            [REQUEST_ID, CLARIFICATION_ID, "CONTOSO-DEMO-003"],
        )
        attention = await self.call("contoso_list_requests", {"needs_attention": True})
        self.assertEqual(
            [row["request_id"] for row in attention.structured_content["requests"]],
            [REQUEST_ID, CLARIFICATION_ID],
        )

    async def test_open_returns_current_questions_and_saved_answers(self):
        result = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertFalse(result.is_error, result.content)
        current = result.structured_content
        self.assertEqual(current["view"], "clarification")
        self.assertEqual(current["questions"], QUESTIONS)
        self.assertEqual(current["answers"], {"approval_owner": "Engagement lead"})
        self.assertEqual(
            current["missing_required_answers"],
            ["reminder_schedule", "notify_contractors"],
        )
        self.assertEqual(current["question_set_revision"], "clarification-1")

    async def test_incomplete_or_invalid_clarification_never_saves_partial_answers(
        self,
    ):
        before = deepcopy(self.table.rows)
        for answers in (
            {"reminder_schedule": "Weekly"},
            {**ANSWERS, "approval_owner": " "},
            {**ANSWERS, "reminder_schedule": "Sometimes"},
            {**ANSWERS, "notify_contractors": "false"},
            {**ANSWERS, "notify_contractors": 0},
            {**ANSWERS, "notify_contractors": None},
            {**ANSWERS, "approval_owner": False},
            {**ANSWERS, "unknown": "No such question"},
        ):
            with self.subTest(answers=answers):
                result = await self.call(
                    "contoso_submit_answers",
                    self.clarification_arguments(answers=answers),
                )
                self.assertTrue(result.is_error)
                self.assertEqual(self.table.rows, before)
                self.assertEqual(
                    self.table.rows[
                        (f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)
                    ].metadata["etag"],
                    '"1"',
                )

    async def test_clarification_saves_answers_and_submits_together(self):
        self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)][
            "Answers"
        ] = json.dumps(
            {
                "approval_owner": "Engagement lead",
                "notes": "Remove this optional note",
            }
        )
        result = await self.call(
            "contoso_submit_answers",
            self.clarification_arguments(answers={**ANSWERS, "notes": None}),
        )
        self.assertFalse(result.is_error, result.content)
        current = result.structured_content
        self.assertEqual(current["status"], "in_review")
        self.assertEqual(current["view"], "status")
        self.assertEqual(
            current["answers"], {"approval_owner": "Engagement lead", **ANSWERS}
        )
        self.assertIs(current["answers"]["notify_contractors"], False)
        self.assertEqual(current["missing_required_answers"], [])
        self.server = self.new_server()
        opened = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(opened.structured_content, current)
        attention = await self.call("contoso_list_requests", {"needs_attention": True})
        self.assertEqual(
            [row["request_id"] for row in attention.structured_content["requests"]],
            [REQUEST_ID],
        )

    async def test_clarification_retry_preserves_one_outcome_and_rejects_changed_answers(
        self,
    ):
        first = await self.call(
            "contoso_submit_answers", self.clarification_arguments()
        )
        self.assertFalse(first.is_error, first.content)
        current = first.structured_content
        for revision in ('"1"', current["revision"]):
            repeat = await self.call(
                "contoso_submit_answers",
                self.clarification_arguments(revision=revision),
            )
            self.assertEqual(repeat.structured_content, current)
        changed = await self.call(
            "contoso_submit_answers",
            self.clarification_arguments(
                answers={**ANSWERS, "notify_contractors": True}
            ),
        )
        self.assertTrue(changed.is_error)
        opened = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(opened.structured_content, current)

    async def test_stale_record_or_question_revisions_cannot_overwrite_answers(self):
        before = deepcopy(self.table.rows)
        for revision in ('"old"', "*", " * ", "", '""', '"old", *'):
            result = await self.call(
                "contoso_submit_answers",
                self.clarification_arguments(revision=revision),
            )
            self.assertTrue(result.is_error)
            self.assertEqual(self.table.rows, before)
        result = await self.call(
            "contoso_submit_answers",
            self.clarification_arguments(question_set_revision="old"),
        )
        self.assertTrue(result.is_error)
        self.assertEqual(self.table.rows, before)
        for race in ("race_before_update", "question_race_before_update"):
            self.table = Table()
            setattr(self.table, race, True)
            result = await self.call(
                "contoso_submit_answers", self.clarification_arguments()
            )
            self.assertTrue(result.is_error)
            row = self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)]
            self.assertEqual(
                json.loads(row["Answers"]), {"approval_owner": "Engagement lead"}
            )
            self.assertEqual(row["Status"], "needs_clarification")

    async def test_app_resource_can_be_read_without_storage_or_customer_data(self):
        with patch.dict(os.environ, {}, clear=True):
            async with Client(self.server) as client:
                result = await client.read_resource("ui://contoso-request/app.html")
        self.assertEqual(result.contents[0].mime_type, "text/html;profile=mcp-app")
        html = result.contents[0].text
        self.assertIn('id="submit-request"', html)
        self.assertIn('id="submit-answers"', html)
        self.assertNotIn('id="save"', html)
        self.assertNotIn('class="brand"', html)
        self.assertNotIn("synthetic-secret-not-for-output", html)

    async def test_storage_errors_do_not_expose_configuration(self):
        with (
            verified({"tid": TENANT, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"}),
            patch.dict(os.environ, {}, clear=True),
        ):
            async with Client(self.server) as client:
                result = await client.call_tool(
                    "contoso_open_request", {"request_id": REQUEST_ID}
                )
        self.assertTrue(result.is_error)
        self.assertIn("CONTOSO_STORAGE_CONNECTION_STRING", result.content[0].text)
        with (
            verified({"tid": TENANT, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"}),
            patch.dict(os.environ, ENVIRONMENT),
            patch(
                "azure.data.tables.TableClient.from_connection_string",
                side_effect=ValueError("synthetic-secret-not-for-output"),
            ),
        ):
            async with Client(self.server) as client:
                result = await client.call_tool(
                    "contoso_submit_request", {"submission_id": SUBMISSION_ID, **BRIEF}
                )
        self.assertTrue(result.is_error)
        self.assertNotIn("synthetic-secret-not-for-output", result.model_dump_json())


if __name__ == "__main__":
    unittest.main()
