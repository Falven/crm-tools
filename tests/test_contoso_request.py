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


class ContosoRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = MCPServer("contoso-request-check", extensions=[Apps()])
        register_catalog_tool(self.server, "contoso_open_request")
        register_catalog_tool(self.server, "contoso_save_request")
        register_catalog_tool(self.server, "contoso_list_requests")
        register_catalog_tool(self.server, "contoso_save_answers")
        register_catalog_tool(self.server, "contoso_submit_answers")
        register_catalog_tool(self.server, "contoso_create_request")
        register_catalog_tool(self.server, "contoso_submit_request")
        self.table = Table()

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

    async def test_open_and_save_are_model_and_app_visible_without_storage_configuration(
        self,
    ):
        with patch.dict(os.environ, {}, clear=True):
            async with Client(self.server) as client:
                tools = (await client.list_tools()).tools
        visible = [
            tool.name
            for tool in tools
            if "model"
            in (tool.meta or {}).get("ui", {}).get("visibility", ["model", "app"])
        ]
        self.assertCountEqual(
            visible,
            [
                "contoso_open_request",
                "contoso_save_request",
                "contoso_list_requests",
                "contoso_save_answers",
                "contoso_submit_answers",
                "contoso_create_request",
                "contoso_submit_request",
            ],
        )
        descriptors = {tool.name: tool for tool in tools}
        for name in (
            "contoso_save_request",
            "contoso_save_answers",
            "contoso_submit_answers",
        ):
            app_ui = descriptors[name].meta["ui"]
            self.assertIn("app", app_ui["visibility"])
            self.assertEqual(
                app_ui["resourceUri"],
                descriptors["contoso_open_request"].meta["ui"]["resourceUri"],
            )
        for name in ("contoso_create_request", "contoso_submit_request"):
            app_ui = descriptors[name].meta["ui"]
            self.assertIn("app", app_ui.get("visibility", ["model", "app"]))
            self.assertIsInstance(app_ui["resourceUri"], str)

    async def test_intake_draft_is_partially_saved_then_explicitly_submitted(self):
        created = await self.call(
            "contoso_create_request",
            {
                "requested_software": "Contractor timesheet approvals",
                "business_objective": "Reduce manual follow-up on contractor timesheets.",
                "preferred_deadline": "Before our next busy season",
            },
        )
        self.assertFalse(created.is_error, created.content)
        draft = created.structured_content
        self.assertIsInstance(draft["request_id"], str)
        self.assertTrue(draft["request_id"])
        self.assertNotIn(draft["request_id"], (REQUEST_ID, CLARIFICATION_ID))
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["view"], "intake")
        self.assertEqual(
            draft["brief"],
            {
                "requested_software": "Contractor timesheet approvals",
                "business_objective": "Reduce manual follow-up on contractor timesheets.",
                "capabilities": "",
                "systems_and_constraints": "",
                "decision_maker": "",
                "preferred_deadline": "Before our next busy season",
            },
        )
        async with Client(self.server) as client:
            tools = (await client.list_tools()).tools
            entry = next(
                tool for tool in tools if tool.name == "contoso_create_request"
            )
            resource = await client.read_resource(entry.meta["ui"]["resourceUri"])
        self.assertEqual(resource.contents[0].mime_type, "text/html;profile=mcp-app")
        self.assertTrue(resource.contents[0].text)
        self.server = MCPServer("replacement-contoso-server", extensions=[Apps()])
        for tool_id in (
            "contoso_open_request",
            "contoso_list_requests",
            "contoso_save_request",
            "contoso_submit_request",
        ):
            register_catalog_tool(self.server, tool_id)
        for _ in range(2):
            reopened = await self.call(
                "contoso_open_request", {"request_id": draft["request_id"]}
            )
            self.assertFalse(reopened.is_error, reopened.content)
            self.assertEqual(reopened.structured_content, draft)
        listed = await self.call("contoso_list_requests", {})
        self.assertCountEqual(
            [item["request_id"] for item in listed.structured_content["requests"]],
            [REQUEST_ID, CLARIFICATION_ID, draft["request_id"]],
        )

        saved = await self.call(
            "contoso_save_request",
            {
                "request_id": draft["request_id"],
                "revision": draft["revision"],
                "requested_software": "Contractor timesheet approvals and reminders",
                "capabilities": "Submit weekly timesheets and route approvals.",
                "systems_and_constraints": "Microsoft Teams and the current project list.",
            },
        )
        self.assertFalse(saved.is_error, saved.content)
        partial = saved.structured_content
        self.assertEqual(partial["status"], "draft")
        self.assertEqual(partial["view"], "intake")
        self.assertEqual(
            partial["brief"],
            {
                "requested_software": "Contractor timesheet approvals and reminders",
                "business_objective": "Reduce manual follow-up on contractor timesheets.",
                "capabilities": "Submit weekly timesheets and route approvals.",
                "systems_and_constraints": "Microsoft Teams and the current project list.",
                "decision_maker": "",
                "preferred_deadline": "Before our next busy season",
            },
        )
        self.assertNotEqual(partial["revision"], draft["revision"])
        incomplete = await self.call(
            "contoso_submit_request",
            {
                "request_id": draft["request_id"],
                "revision": partial["revision"],
            },
        )
        self.assertTrue(incomplete.is_error)
        still_draft = await self.call(
            "contoso_open_request",
            {
                "request_id": draft["request_id"],
            },
        )
        self.assertEqual(still_draft.structured_content, partial)

        saved = await self.call(
            "contoso_save_request",
            {
                "request_id": draft["request_id"],
                "revision": partial["revision"],
                "business_objective": "Reduce manual chasing with one weekly approval queue.",
                "decision_maker": "Customer operations lead",
                "preferred_deadline": "Before our next busy season, if practical",
            },
        )
        self.assertFalse(saved.is_error, saved.content)
        complete = saved.structured_content
        self.assertEqual(complete["status"], "draft")
        self.assertEqual(
            complete["brief"],
            {
                "requested_software": "Contractor timesheet approvals and reminders",
                "business_objective": "Reduce manual chasing with one weekly approval queue.",
                "capabilities": "Submit weekly timesheets and route approvals.",
                "systems_and_constraints": "Microsoft Teams and the current project list.",
                "decision_maker": "Customer operations lead",
                "preferred_deadline": "Before our next busy season, if practical",
            },
        )
        submitted = await self.call(
            "contoso_submit_request",
            {
                "request_id": draft["request_id"],
                "revision": complete["revision"],
            },
        )
        self.assertFalse(submitted.is_error, submitted.content)
        outcome = submitted.structured_content
        self.assertEqual(outcome["status"], "submitted")
        self.assertEqual(outcome["view"], "status")
        self.assertEqual(outcome["brief"], complete["brief"])
        self.assertNotEqual(outcome["revision"], complete["revision"])
        read_only = await self.call(
            "contoso_save_request",
            {
                "request_id": draft["request_id"],
                "revision": outcome["revision"],
                "business_objective": "An edit after submission",
            },
        )
        self.assertTrue(read_only.is_error)
        reopened = await self.call(
            "contoso_open_request",
            {
                "request_id": draft["request_id"],
            },
        )
        self.assertEqual(reopened.structured_content, outcome)

    async def test_intake_required_fields_can_be_cleared_but_not_submitted(self):
        brief = {
            "requested_software": "Contractor timesheet approvals",
            "business_objective": "Reduce manual follow-up.",
            "capabilities": "Collect timesheets and send approval reminders.",
            "decision_maker": "Customer operations lead",
        }
        created = await self.call("contoso_create_request", brief)
        self.assertFalse(created.is_error, created.content)
        current = created.structured_content
        for field, blank in (
            ("requested_software", ""),
            ("business_objective", " \t"),
            ("capabilities", ""),
            ("decision_maker", ""),
        ):
            with self.subTest(field=field):
                cleared = await self.call(
                    "contoso_save_request",
                    {
                        "request_id": current["request_id"],
                        "revision": current["revision"],
                        field: blank,
                    },
                )
                self.assertFalse(cleared.is_error, cleared.content)
                current = cleared.structured_content
                self.assertEqual(current["brief"][field], blank)
                self.assertEqual(current["missing_required_fields"], [field])
                denied = await self.call(
                    "contoso_submit_request",
                    {
                        "request_id": current["request_id"],
                        "revision": current["revision"],
                    },
                )
                self.assertTrue(denied.is_error)
                reopened = await self.call(
                    "contoso_open_request",
                    {
                        "request_id": current["request_id"],
                    },
                )
                self.assertEqual(reopened.structured_content, current)
                restored = await self.call(
                    "contoso_save_request",
                    {
                        "request_id": current["request_id"],
                        "revision": current["revision"],
                        field: brief[field],
                    },
                )
                self.assertFalse(restored.is_error, restored.content)
                current = restored.structured_content

        self.assertEqual(
            current["brief"],
            {
                **brief,
                "systems_and_constraints": "",
                "preferred_deadline": "",
            },
        )
        self.assertEqual(current["missing_required_fields"], [])
        submitted = await self.call(
            "contoso_submit_request",
            {
                "request_id": current["request_id"],
                "revision": current["revision"],
            },
        )
        self.assertFalse(submitted.is_error, submitted.content)
        self.assertEqual(submitted.structured_content["status"], "submitted")
        self.assertEqual(submitted.structured_content["brief"], current["brief"])

    async def test_repeated_intake_submission_returns_the_saved_outcome(self):
        created = await self.call(
            "contoso_create_request",
            {
                "requested_software": "Contractor timesheet approvals",
                "business_objective": "Reduce manual follow-up.",
                "capabilities": "Collect timesheets and send approval reminders.",
                "decision_maker": "Customer operations lead",
            },
        )
        self.assertFalse(created.is_error, created.content)
        draft = created.structured_content
        submitted = await self.call(
            "contoso_submit_request",
            {
                "request_id": draft["request_id"],
                "revision": draft["revision"],
            },
        )
        self.assertFalse(submitted.is_error, submitted.content)
        outcome = submitted.structured_content
        for revision in (draft["revision"], outcome["revision"]):
            repeated = await self.call(
                "contoso_submit_request",
                {
                    "request_id": draft["request_id"],
                    "revision": revision,
                },
            )
            self.assertFalse(repeated.is_error, repeated.content)
            self.assertEqual(repeated.structured_content, outcome)
        unrelated = await self.call(
            "contoso_submit_request",
            {
                "request_id": draft["request_id"],
                "revision": '"unrelated"',
            },
        )
        self.assertTrue(unrelated.is_error)
        reopened = await self.call(
            "contoso_open_request",
            {
                "request_id": draft["request_id"],
            },
        )
        self.assertEqual(reopened.structured_content, outcome)

    async def test_intake_submission_rejects_stale_revisions_and_racing_edits(self):
        created = await self.call(
            "contoso_create_request",
            {
                "requested_software": "Contractor timesheet approvals",
                "business_objective": "Reduce manual follow-up.",
                "capabilities": "Collect timesheets and send approval reminders.",
                "decision_maker": "Customer operations lead",
            },
        )
        self.assertFalse(created.is_error, created.content)
        draft = created.structured_content
        self.table.race_before_update = True
        raced = await self.call(
            "contoso_submit_request",
            {
                "request_id": draft["request_id"],
                "revision": draft["revision"],
            },
        )
        self.assertTrue(raced.is_error)
        opened = await self.call(
            "contoso_open_request",
            {
                "request_id": draft["request_id"],
            },
        )
        current = opened.structured_content
        self.assertEqual(current["status"], "draft")
        self.assertEqual(
            current["brief"]["business_objective"], "A newer saved objective."
        )
        self.assertNotEqual(current["revision"], draft["revision"])
        for operation in ("contoso_save_request", "contoso_submit_request"):
            for revision in (draft["revision"], "*", "", '"old", *'):
                with self.subTest(operation=operation, revision=revision):
                    denied = await self.call(
                        operation,
                        {
                            "request_id": draft["request_id"],
                            "revision": revision,
                        },
                    )
                    self.assertTrue(denied.is_error)
        reopened = await self.call(
            "contoso_open_request",
            {
                "request_id": draft["request_id"],
            },
        )
        self.assertEqual(reopened.structured_content, current)
        submitted = await self.call(
            "contoso_submit_request",
            {
                "request_id": draft["request_id"],
                "revision": current["revision"],
            },
        )
        self.assertFalse(submitted.is_error, submitted.content)
        self.assertEqual(submitted.structured_content["status"], "submitted")
        self.assertEqual(submitted.structured_content["brief"], current["brief"])

    async def test_open_reads_the_customers_saved_request(self):
        result = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(
            result.structured_content,
            {
                "request_id": REQUEST_ID,
                "title": "Contractor timesheet approvals",
                "requested_software": "Contractor timesheet approvals",
                "business_objective": "Reduce manual timesheet follow-up.",
                "capabilities": "",
                "systems_and_constraints": "",
                "decision_maker": "",
                "preferred_deadline": "",
                "missing_required_fields": ["capabilities", "decision_maker"],
                "status": "draft",
                "next_action": "Review and save your request details, then submit when ready.",
                "revision": '"1"',
                "view": "intake",
                "brief": {
                    "requested_software": "Contractor timesheet approvals",
                    "business_objective": "Reduce manual timesheet follow-up.",
                    "capabilities": "",
                    "systems_and_constraints": "",
                    "decision_maker": "",
                    "preferred_deadline": "",
                },
                "questions": [],
                "answers": {},
                "question_set_revision": None,
                "missing_required_answers": [],
            },
        )

    async def test_list_returns_owned_requests_and_filters_for_customer_attention(self):
        row = deepcopy(self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", REQUEST_ID)])
        row.update(RowKey="CONTOSO-DEMO-003", Status="submitted")
        self.table.rows[(row["PartitionKey"], row["RowKey"])] = row
        other = deepcopy(row)
        other.update(
            PartitionKey=f"contoso:{TENANT}:{OTHER_CUSTOMER}",
            RowKey="OTHER-CUSTOMER",
            Status="needs_clarification",
        )
        self.table.rows[(other["PartitionKey"], other["RowKey"])] = other
        unrelated = deepcopy(row)
        unrelated.update(RowKey="UNRELATED", DemoId="another-demo")
        self.table.rows[(unrelated["PartitionKey"], unrelated["RowKey"])] = unrelated
        listed = await self.call("contoso_list_requests", {})
        self.assertFalse(listed.is_error, listed.content)
        self.assertEqual(
            [row["request_id"] for row in listed.structured_content["requests"]],
            [REQUEST_ID, CLARIFICATION_ID, "CONTOSO-DEMO-003"],
        )
        attention = await self.call("contoso_list_requests", {"needs_attention": True})
        self.assertFalse(attention.is_error, attention.content)
        self.assertEqual(
            [row["request_id"] for row in attention.structured_content["requests"]],
            [REQUEST_ID, CLARIFICATION_ID],
        )
        self.assertEqual(
            attention.structured_content["requests"][1]["status"], "needs_clarification"
        )
        self.assertIn(
            "questions", attention.structured_content["requests"][1]["next_action"]
        )

    async def test_open_returns_saved_brief_questions_and_partial_answers(self):
        result = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertFalse(result.is_error, result.content)
        current = result.structured_content
        self.assertEqual(current["view"], "clarification")
        self.assertEqual(current["question_set_revision"], "clarification-1")
        self.assertEqual(current["questions"], QUESTIONS)
        self.assertEqual(current["answers"], {"approval_owner": "Engagement lead"})
        self.assertEqual(
            current["missing_required_answers"],
            ["reminder_schedule", "notify_contractors"],
        )
        self.assertEqual(
            current["brief"],
            {
                "requested_software": "Contractor timesheet approvals",
                "business_objective": "Reduce manual timesheet follow-up.",
                "capabilities": "Submit weekly timesheets and route approvals.",
                "systems_and_constraints": "Microsoft Teams and the existing project list.",
                "decision_maker": "Customer operations lead",
                "preferred_deadline": "Pilot next month",
            },
        )

    async def test_partial_answers_keep_types_and_can_clear_an_answer(self):
        saved = await self.call(
            "contoso_save_answers",
            {
                "request_id": CLARIFICATION_ID,
                "answers": {
                    "notify_contractors": False,
                    "notes": "Include a weekly summary.",
                },
                "revision": '"1"',
                "question_set_revision": "clarification-1",
            },
        )
        self.assertFalse(saved.is_error, saved.content)
        self.assertEqual(saved.structured_content["status"], "needs_clarification")
        self.assertEqual(
            saved.structured_content["answers"],
            {
                "approval_owner": "Engagement lead",
                "notify_contractors": False,
                "notes": "Include a weekly summary.",
            },
        )
        self.assertEqual(
            saved.structured_content["missing_required_answers"], ["reminder_schedule"]
        )
        cleared = await self.call(
            "contoso_save_answers",
            {
                "request_id": CLARIFICATION_ID,
                "answers": {"reminder_schedule": "Weekly", "notes": None},
                "revision": saved.structured_content["revision"],
                "question_set_revision": "clarification-1",
            },
        )
        self.assertFalse(cleared.is_error, cleared.content)
        opened = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(opened.structured_content, cleared.structured_content)
        self.assertEqual(
            opened.structured_content["answers"],
            {
                "approval_owner": "Engagement lead",
                "notify_contractors": False,
                "reminder_schedule": "Weekly",
            },
        )
        self.assertEqual(opened.structured_content["missing_required_answers"], [])

    async def test_submission_requires_saved_required_answers_and_moves_to_review(self):
        incomplete = await self.call(
            "contoso_submit_answers",
            {
                "request_id": CLARIFICATION_ID,
                "revision": '"1"',
                "question_set_revision": "clarification-1",
            },
        )
        self.assertTrue(incomplete.is_error)
        self.assertIn("reminder_schedule", incomplete.content[0].text)
        saved = await self.call(
            "contoso_save_answers",
            {
                "request_id": CLARIFICATION_ID,
                "answers": {"reminder_schedule": "Weekly", "notify_contractors": False},
                "revision": '"1"',
                "question_set_revision": "clarification-1",
            },
        )
        submitted = await self.call(
            "contoso_submit_answers",
            {
                "request_id": CLARIFICATION_ID,
                "revision": saved.structured_content["revision"],
                "question_set_revision": "clarification-1",
            },
        )
        self.assertFalse(submitted.is_error, submitted.content)
        self.assertEqual(submitted.structured_content["status"], "in_review")
        self.assertEqual(submitted.structured_content["view"], "status")
        self.assertEqual(
            submitted.structured_content["next_action"],
            "Your request is being reviewed.",
        )
        self.assertIs(
            submitted.structured_content["answers"]["notify_contractors"], False
        )
        self.server = MCPServer("replacement-contoso-server", extensions=[Apps()])
        for tool_id in (
            "contoso_open_request",
            "contoso_save_request",
            "contoso_list_requests",
            "contoso_save_answers",
            "contoso_submit_answers",
        ):
            register_catalog_tool(self.server, tool_id)
        reopened = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(reopened.structured_content, submitted.structured_content)
        attention = await self.call("contoso_list_requests", {"needs_attention": True})
        self.assertEqual(
            [row["request_id"] for row in attention.structured_content["requests"]],
            [REQUEST_ID],
        )

    async def test_repeated_submission_returns_one_saved_outcome(self):
        row = self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)]
        row["Answers"] = json.dumps(
            {
                "approval_owner": "Engagement lead",
                "reminder_schedule": "Daily",
                "notify_contractors": False,
            }
        )
        arguments = {
            "request_id": CLARIFICATION_ID,
            "revision": '"1"',
            "question_set_revision": "clarification-1",
        }
        first = await self.call("contoso_submit_answers", arguments)
        self.assertFalse(first.is_error, first.content)
        repeated = await self.call("contoso_submit_answers", arguments)
        self.assertFalse(repeated.is_error, repeated.content)
        self.assertEqual(repeated.structured_content, first.structured_content)
        current_repeat = await self.call(
            "contoso_submit_answers",
            {**arguments, "revision": first.structured_content["revision"]},
        )
        self.assertFalse(current_repeat.is_error, current_repeat.content)
        self.assertEqual(current_repeat.structured_content, first.structured_content)
        unrelated_revision = await self.call(
            "contoso_submit_answers", {**arguments, "revision": '"unrelated"'}
        )
        self.assertTrue(unrelated_revision.is_error)
        current = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(current.structured_content, first.structured_content)

    async def test_invalid_answers_do_not_change_any_saved_answer(self):
        for answers in (
            {"unknown_question": "Invented"},
            {"unknown_question": None},
            {"notify_contractors": "false"},
            {"notify_contractors": 0},
            {"approval_owner": True},
            {"reminder_schedule": "Hourly"},
            {"approval_owner": "Changed", "notify_contractors": "yes"},
        ):
            with self.subTest(answers=answers):
                result = await self.call(
                    "contoso_save_answers",
                    {
                        "request_id": CLARIFICATION_ID,
                        "answers": answers,
                        "revision": '"1"',
                        "question_set_revision": "clarification-1",
                    },
                )
                self.assertTrue(result.is_error)
        current = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(
            current.structured_content["answers"], {"approval_owner": "Engagement lead"}
        )
        self.assertEqual(current.structured_content["revision"], '"1"')

    async def test_record_and_question_revisions_protect_clarification_changes(self):
        row = self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)]
        row["Answers"] = json.dumps(
            {
                "approval_owner": "Engagement lead",
                "reminder_schedule": "Daily",
                "notify_contractors": False,
            }
        )
        row["QuestionSetRevision"] = "clarification-2"
        row.metadata["etag"] = '"2"'
        before = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        for operation in ("contoso_save_answers", "contoso_submit_answers"):
            for revision, question_revision in (
                ('"1"', "clarification-2"),
                ('"2"', "clarification-1"),
                ("*", "clarification-2"),
                ("", "clarification-2"),
            ):
                with self.subTest(
                    operation=operation,
                    revision=revision,
                    question_revision=question_revision,
                ):
                    arguments = {
                        "request_id": CLARIFICATION_ID,
                        "revision": revision,
                        "question_set_revision": question_revision,
                    }
                    if operation == "contoso_save_answers":
                        arguments["answers"] = {"approval_owner": "Stale overwrite"}
                    denied = await self.call(operation, arguments)
                    self.assertTrue(denied.is_error)
        current = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(current.structured_content, before.structured_content)

    async def test_question_changes_during_save_or_submit_cannot_be_overwritten(self):
        for operation in ("contoso_save_answers", "contoso_submit_answers"):
            with self.subTest(operation=operation):
                self.table = Table()
                row = self.table.rows[
                    (f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)
                ]
                row["Answers"] = json.dumps(
                    {
                        "approval_owner": "Engagement lead",
                        "reminder_schedule": "Daily",
                        "notify_contractors": False,
                    }
                )
                self.table.question_race_before_update = True
                arguments = {
                    "request_id": CLARIFICATION_ID,
                    "revision": '"1"',
                    "question_set_revision": "clarification-1",
                }
                if operation == "contoso_save_answers":
                    arguments["answers"] = {"approval_owner": "Racing overwrite"}
                denied = await self.call(operation, arguments)
                self.assertTrue(denied.is_error)
                current = await self.call(
                    "contoso_open_request", {"request_id": CLARIFICATION_ID}
                )
                self.assertEqual(
                    current.structured_content["question_set_revision"],
                    "clarification-2",
                )
                self.assertEqual(
                    current.structured_content["answers"]["approval_owner"],
                    "Engagement lead",
                )
                self.assertEqual(
                    current.structured_content["status"], "needs_clarification"
                )
                self.assertEqual(current.structured_content["revision"], '"2"')

    async def test_save_and_reopen_preserve_the_draft_and_other_fields(self):
        saved = await self.call(
            "contoso_save_request",
            {
                "request_id": REQUEST_ID,
                "business_objective": "Approve contractor timesheets in one place.",
                "revision": '"1"',
            },
        )
        self.assertFalse(saved.is_error, saved.content)
        self.server = MCPServer("replacement-contoso-server", extensions=[Apps()])
        register_catalog_tool(self.server, "contoso_open_request")
        register_catalog_tool(self.server, "contoso_save_request")
        opened = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertFalse(opened.is_error, opened.content)
        self.assertEqual(opened.structured_content, saved.structured_content)
        self.assertEqual(
            opened.structured_content["business_objective"],
            "Approve contractor timesheets in one place.",
        )
        self.assertEqual(
            opened.structured_content["title"], "Contractor timesheet approvals"
        )
        self.assertEqual(opened.structured_content["status"], "draft")
        self.assertEqual(opened.structured_content["revision"], '"2"')

    async def test_other_users_and_application_callers_cannot_read_or_change_a_request(
        self,
    ):
        self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", REQUEST_ID)].update(
            Capabilities="Collect timesheets and send approval reminders.",
            DecisionMaker="Customer operations lead",
        )
        clarification = self.table.rows[
            (f"contoso:{TENANT}:{CUSTOMER}", CLARIFICATION_ID)
        ]
        clarification["Answers"] = json.dumps(
            {
                "approval_owner": "Engagement lead",
                "reminder_schedule": "Daily",
                "notify_contractors": False,
            }
        )
        for claims in (
            {"tid": TENANT, "oid": OTHER_CUSTOMER, "scp": "ToolForge.MCP.Access"},
            {"tid": OTHER_CUSTOMER, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"},
            {
                "tid": TENANT,
                "oid": CUSTOMER,
                "roles": ["ToolForge.MCP.Access.Application"],
            },
            {"tid": TENANT, "oid": CUSTOMER, "scp": " ", "idtyp": "app"},
            {
                "tid": TENANT,
                "oid": CUSTOMER,
                "scp": "ToolForge.MCP.Access",
                "idtyp": "app",
            },
            {"tid": TENANT, "scp": "ToolForge.MCP.Access"},
        ):
            with self.subTest(claims=claims):
                opened = await self.call(
                    "contoso_open_request", {"request_id": REQUEST_ID}, claims
                )
                changed = await self.call(
                    "contoso_save_request",
                    {
                        "request_id": REQUEST_ID,
                        "business_objective": "Unauthorized change",
                        "revision": '"1"',
                    },
                    claims,
                )
                self.assertTrue(opened.is_error)
                self.assertTrue(changed.is_error)
                intake_submission = await self.call(
                    "contoso_submit_request",
                    {"request_id": REQUEST_ID, "revision": '"1"'},
                    claims,
                )
                self.assertTrue(intake_submission.is_error)
                self.assertNotIn("Reduce manual timesheet", opened.model_dump_json())
                listed = await self.call("contoso_list_requests", {}, claims)
                self.assertNotIn(REQUEST_ID, listed.model_dump_json())
                self.assertNotIn(CLARIFICATION_ID, listed.model_dump_json())
                answer_change = await self.call(
                    "contoso_save_answers",
                    {
                        "request_id": CLARIFICATION_ID,
                        "answers": {"approval_owner": "Unauthorized change"},
                        "revision": '"1"',
                        "question_set_revision": "clarification-1",
                        "tenant_id": TENANT,
                        "customer_id": CUSTOMER,
                    },
                    claims,
                )
                submitted = await self.call(
                    "contoso_submit_answers",
                    {
                        "request_id": CLARIFICATION_ID,
                        "revision": '"1"',
                        "question_set_revision": "clarification-1",
                    },
                    claims,
                )
                self.assertTrue(answer_change.is_error)
                self.assertTrue(submitted.is_error)
        current = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertEqual(
            current.structured_content["business_objective"],
            "Reduce manual timesheet follow-up.",
        )
        self.assertEqual(current.structured_content["revision"], '"1"')
        current_answers = await self.call(
            "contoso_open_request", {"request_id": CLARIFICATION_ID}
        )
        self.assertEqual(
            current_answers.structured_content["status"], "needs_clarification"
        )
        self.assertEqual(current_answers.structured_content["revision"], '"1"')
        self.assertEqual(
            current_answers.structured_content["answers"]["approval_owner"],
            "Engagement lead",
        )

    async def test_intake_creation_uses_the_verified_customer_identity(self):
        before = await self.call("contoso_list_requests", {})
        for claims in (
            {"tid": TENANT, "oid": OTHER_CUSTOMER, "scp": "ToolForge.MCP.Access"},
            {"tid": OTHER_CUSTOMER, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"},
        ):
            with self.subTest(claims=claims):
                created = await self.call(
                    "contoso_create_request",
                    {
                        "requested_software": "Another customer's request",
                    },
                    claims,
                )
                self.assertFalse(created.is_error, created.content)
                draft = created.structured_content
                opened = await self.call(
                    "contoso_open_request",
                    {
                        "request_id": draft["request_id"],
                    },
                    claims,
                )
                self.assertEqual(opened.structured_content, draft)
                hidden = await self.call(
                    "contoso_open_request",
                    {
                        "request_id": draft["request_id"],
                    },
                )
                self.assertTrue(hidden.is_error)
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
            with self.subTest(claims=claims):
                denied = await self.call("contoso_create_request", {}, claims)
                self.assertTrue(denied.is_error)
        after = await self.call("contoso_list_requests", {})
        self.assertEqual(after.structured_content, before.structured_content)

    async def test_stale_and_wildcard_revisions_cannot_overwrite_current_data(self):
        for revision in ('"old"', "*", " * ", "", '""', '"old", *'):
            with self.subTest(revision=revision):
                result = await self.call(
                    "contoso_save_request",
                    {
                        "request_id": REQUEST_ID,
                        "business_objective": "Stale overwrite",
                        "revision": revision,
                    },
                )
                self.assertTrue(result.is_error)
        self.table.race_before_update = True
        raced = await self.call(
            "contoso_save_request",
            {
                "request_id": REQUEST_ID,
                "business_objective": "Racing overwrite",
                "revision": '"1"',
            },
        )
        self.assertTrue(raced.is_error)
        current = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertEqual(
            current.structured_content["business_objective"], "A newer saved objective."
        )
        self.assertEqual(current.structured_content["revision"], '"2"')

    async def test_non_draft_and_unrelated_records_are_not_editable(self):
        row = self.table.rows[(f"contoso:{TENANT}:{CUSTOMER}", REQUEST_ID)]
        row["Status"] = "submitted"
        denied = await self.call(
            "contoso_save_request",
            {
                "request_id": REQUEST_ID,
                "business_objective": "Changed",
                "revision": '"1"',
            },
        )
        self.assertTrue(denied.is_error)
        current = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertEqual(current.structured_content["status"], "submitted")
        self.assertEqual(current.structured_content["revision"], '"1"')
        row["DemoId"] = "unrelated-data"
        hidden = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertTrue(hidden.is_error)

    async def test_storage_configuration_and_errors_are_reported_without_secrets(self):
        from azure.core.exceptions import ServiceRequestError

        caller = {"tid": TENANT, "oid": CUSTOMER, "scp": "ToolForge.MCP.Access"}
        with verified(caller), patch.dict(os.environ, {}, clear=True):
            async with Client(self.server) as client:
                result = await client.call_tool(
                    "contoso_open_request", {"request_id": REQUEST_ID}
                )
        self.assertTrue(result.is_error)
        self.assertIn("CONTOSO_STORAGE_CONNECTION_STRING", result.content[0].text)
        with patch.object(
            Table,
            "get_entity",
            side_effect=ServiceRequestError("synthetic-secret-not-for-output"),
        ):
            failed = await self.call("contoso_open_request", {"request_id": REQUEST_ID})
        self.assertTrue(failed.is_error)
        self.assertIn("storage", failed.content[0].text)

    async def test_app_resource_is_available_without_customer_data_or_storage_settings(
        self,
    ):
        with patch.dict(os.environ, {}, clear=True):
            async with Client(self.server) as client:
                tools = (await client.list_tools()).tools
                entry = next(
                    tool for tool in tools if tool.name == "contoso_open_request"
                )
                result = await client.read_resource(entry.meta["ui"]["resourceUri"])
        self.assertEqual(result.contents[0].mime_type, "text/html;profile=mcp-app")
        self.assertTrue(result.contents[0].text)
        self.assertNotIn("Reduce manual timesheet follow-up.", result.contents[0].text)
        self.assertNotIn("synthetic-secret-not-for-output", result.contents[0].text)


if __name__ == "__main__":
    unittest.main()
