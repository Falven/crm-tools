# Software request Tools and App

The conversation or open form holds the proposed brief. Reviewing it creates no
stored draft. Explicit submission creates the first persistent request. Foundry
conversation and the ToolForge App use the same Tools and customer identity.

| Tool | Behavior | MCP App entry |
| --- | --- | --- |
| `contoso_review_request` | Prepare an incomplete or complete brief without accessing storage. | Review software request |
| `contoso_submit_request` | Validate and create a submitted request from the full brief. | None |
| `contoso_list_requests` | List the customer's saved projects and work needing attention. | None |
| `contoso_open_request` | Read the saved brief, status, questions, answers, and revisions. | Open software project |
| `contoso_submit_answers` | Validate, save, and submit clarification answers in one write. | None |

The two App entries share `ui://contoso-request/app.html`. Submission operations
are model- and App-visible but do not advertise a resource, so they do not appear
as additional Apps. Removed draft and partial-save operations are not registered.

## Review and submission

Review accepts `requested_software`, `business_objective`, `capabilities`,
`systems_and_constraints`, `decision_maker`, and `preferred_deadline` as flat text.
Missing values become blank strings. It returns `view=review`, the brief,
missing required fields, and a random `submission_id`. It requires a verified
customer but does not access storage. The ID is a retry key, not a saved project.

Submit accepts the same complete brief and that `submission_id`. Requested
software, objective, capabilities, and decision maker must be nonblank. Systems
or constraints and the deadline are optional. Approximate deadlines remain text.
The service inserts `RowKey=REQUEST-<submission_id>` in the caller's partition
with `Status=submitted` and `DemoId=contoso-demo`.

The insert is conditional on absence. A repeated ID with the same brief returns
the existing record without a write; conflicting details fail. Do not generate
a new ID to retry an uncertain submission. No idempotency state is kept only in
process memory.

The form submits its current fields directly. There is no Save button. Closing
it loses unsubmitted form edits. Opening a recorded review displays its original
brief and retry key; use Open software project for current submitted data.
Existing saved drafts remain readable as read-only records. Nothing migrates or
deletes them automatically.

## Clarification

Open returns the current questions, saved answers, record `revision` (Table
ETag), and `question_set_revision`. Questions support text, fixed choices, and
yes/no answers; optional questions may remain unanswered.

Submit answers accepts `request_id`, `answers`, and both current revisions.
Supplied answers replace saved values; omitted answers retain their values.
Null clears an answer. Text and choice answers use strings, and yes/no answers use booleans. `false`
is a completed answer. Unknown questions, wrong types, invalid choices, and
missing required answers reject the whole submission without saving anything.

A single ETag-conditional write saves the answers and changes `needs_clarification`
to `in_review`. Stale records or question sets require a fresh read and customer
review; there are no automatic write retries. Repeating the same submission
preserves its outcome. Changing answers after submission is rejected.

## Storage and identity

Set these in ToolForge's Environment Workspace:

| Variable | Value |
| --- | --- |
| `CONTOSO_STORAGE_CONNECTION_STRING` | Connection string for the approved demo storage account. |
| `CONTOSO_REQUESTS_TABLE` | Existing Azure Table containing the demo records. |

Configuration is read when a storage-backed Tool runs. Discovery, review, and
App resource retrieval remain available without storage configuration. Secrets
stay on the server. These Tools do not provision the account or table.

Every customer operation derives `PartitionKey=contoso:<tenant UUID>:<user UUID>`
from verified delegated claims. Caller-supplied identity fields do not authorize
access, and application-only tokens are rejected. Reads also require the demo
marker. A request ID alone never grants access.

The brief uses `Title`, `BusinessObjective`, `Capabilities`,
`SystemsAndConstraints`, `DecisionMaker`, and `PreferredDeadline` columns.
Clarification uses JSON strings in `Questions` and `Answers`, plus
`QuestionSetRevision`. Table ETags govern concurrency. Existing request IDs,
partitions, and fields remain valid.

The product repository's `apps/demo` owns the agent definition, synthetic
`CONTOSO-DEMO-002` clarification fixture, preparation, reset, and recording
instructions. Data reset is separate from source publication.

## Build and verify

```sh
cd src/catalog_app/tools/contoso_open_request/frontend
npm ci
npm run build
```

The build bundles the official MCP App SDK and regenerates `app.js` and the
third-party notices. The form needs no external frontend endpoint.

From the repository root:

```sh
uv run --frozen python -m unittest tests.test_contoso_request -v
```

The checks exercise production Tool registration through an MCP client with
controlled identity and Table SDK fixtures. They establish no-write review,
validated submissions, duplicate protection, ownership, stale revisions,
read-only existing records, and exactly two App entries. They do not establish
Azure network access or client integration.

Rehearse the real ToolForge form submission and a Foundry read of the resulting
record. Also submit through Foundry with an explicit approval and open the saved
project in ToolForge. For clarification, verify incomplete input saves nothing,
then submit complete answers and read the same status from the other interface.
