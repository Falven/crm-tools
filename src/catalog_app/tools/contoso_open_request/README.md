# Software request Tools and App

The ToolForge App and Foundry conversation use the same customer-scoped Tools
and saved requests: create an incomplete intake draft, edit its six flat text
fields, explicitly submit the saved brief, and save or submit clarification
answers. Creation and saving never submit a request.

## Runtime configuration

Set these values in **ToolForge → Server → Environment variables**:

| Variable | Value |
| --- | --- |
| `CONTOSO_STORAGE_CONNECTION_STRING` | Connection string for the approved demo storage account. |
| `CONTOSO_REQUESTS_TABLE` | Existing Azure Table containing the demo request. |

The MCP service uses the Azure Tables SDK. It does not create the table or seed
operator fixtures. Intake creation inserts a new customer-owned draft using
`create_entity`, never an upsert. Storage configuration is read when a Tool runs; Tool discovery and App
assets remain available before configuration. Credentials stay in the server
environment and are never sent to the App.

## Operator fixture

Use the customer's verified Entra tenant ID and object ID as canonical,
lowercase UUIDs. Existing draft rows remain valid with these properties:

| Property | Value |
| --- | --- |
| `PartitionKey` | `contoso:<tenant-id>:<customer-object-id>` |
| `RowKey` | `CONTOSO-DEMO-001` |
| `DemoId` | `contoso-demo` |
| `Title` | `Contractor timesheet approvals` |
| `BusinessObjective` | `Reduce manual timesheet follow-up.` |
| `Status` | `draft` |

Azure Table Storage supplies the ETag. Do not create a separate revision
property. Each response returns the ETag as `revision`.

A needs-clarification fixture uses `RowKey=CONTOSO-DEMO-002`, the same partition
and demo marker, and `Status=needs_clarification`. It adds:

| Property | Type and value |
| --- | --- |
| `Capabilities` | Optional saved brief string. |
| `SystemsAndConstraints` | Optional saved brief string. |
| `DecisionMaker` | Optional saved brief string. |
| `PreferredDeadline` | Optional saved brief string. |
| `QuestionSetRevision` | Nonempty string, such as `clarification-1`. |
| `Questions` | JSON array serialized as an Azure Table string. |
| `Answers` | JSON object serialized as an Azure Table string. |

Each question has a unique `id`, a `type` of `text`, `choice`, or `yes_no`, a
nonempty `prompt`, and a boolean `required`. IDs use 1–128 letters, digits,
underscores, or hyphens. Choice questions also have a nonempty `choices` array
of distinct, nonempty strings; other types omit `choices`. For example:

```json
[
  {"id":"approval_owner","type":"text","prompt":"Who should approve contractor timesheets?","required":true},
  {"id":"reminder_schedule","type":"choice","prompt":"When should reminders be sent?","required":true,"choices":["Daily","Weekly"]},
  {"id":"notify_contractors","type":"yes_no","prompt":"Should contractors receive reminders?","required":true},
  {"id":"notes","type":"text","prompt":"Anything else we should know?","required":false}
]
```

An initial `Answers` value can serialize `{"approval_owner":"Engagement lead"}`.
Stored text and choice answers are strings; yes/no answers are actual booleans.
`false` is a completed answer. Unanswered questions are absent from the object.
Keep questions and answers consistent, and change `QuestionSetRevision` whenever
questions change. Each answer string is limited to 4,000 characters.

Submission retains the questions and answers, changes `Status` to `in_review`,
and stores `SubmittedFromRevision` and `SubmittedQuestionSetRevision`. These
fields make a retry of that submission safe. Do not seed them on a request
that still needs clarification. Normal preparation must preserve existing rows;
replacing rehearsal data is an explicit reset.

## MCP interface

- `contoso_list_requests(needs_attention=False)` lists the signed-in customer's
  requests with status and next action. The attention filter includes `draft`
  and `needs_clarification` requests.
- `contoso_open_request(request_id)` reads the current brief, status, questions,
  saved answers, record revision, question-set revision, and missing required
  answers. It attaches `ui://contoso-request/app.html`.
- `contoso_create_request(requested_software="", business_objective="",
  capabilities="", systems_and_constraints="", decision_maker="",
  preferred_deadline="")` saves a new draft and returns its identifier, current
  revision, six flat text fields, missing required fields, and prefilled App.
  All inputs may be incomplete or blank. Reopen that identifier; do not create
  again to resume a saved request.
- `contoso_save_request(request_id, revision, ...)` accepts any supplied subset
  of those same six flat fields. Unknown input fields are rejected, not silently
  ignored. Omitted fields stay unchanged; an empty string clears a field
  (explicit `null` is rejected). It only edits drafts and never
  submits. The App's **Save draft** action and conversational saves call this
  same Tool. Updates use a conditional Table Merge; empty, wildcard, and stale
  revisions are rejected, including a save with no supplied fields.
- `contoso_submit_request(request_id, revision)` requires the customer's explicit
  submission decision and submits only already-saved fields. Requested software,
  business objective, capabilities, and decision maker must be nonblank. Systems
  and constraints and the preferred deadline are optional. Deadline values remain
  the customer's text, including approximate dates; no date parsing occurs.
  Submission changes `draft` to `submitted` and stores
  `IntakeSubmittedFromRevision`, separately from clarification retry metadata.
  Repeats with the original or resulting revision return the saved submitted
  record without another write. The App requires a separate **Submit request**
  action after saving a complete brief, with no unsaved edits.
- `contoso_save_answers(request_id, answers, revision, question_set_revision)`
  merges partial answers without submitting. Omitted answers remain unchanged;
  `null` clears one answer. Unknown question IDs, wrong types, invalid choices,
  and stale revisions reject the entire change.
- `contoso_submit_answers(request_id, revision, question_set_revision)` submits
  already-saved answers after the customer's explicit decision. All required
  answers must be present; blank text is incomplete. Configure client approval
  for this Tool. The App requires a separate **Submit answers** action after
  saving complete answers.

Each operation has one model-facing Tool Directory. App actions call the same
operations. Keep their registrations active together: Open publishes the shared
App resource used by the mutation results. The App chooses its view from the
saved state: `draft` shows the intake editor, `needs_clarification` shows the
question form, and `submitted` or `in_review` shows a read-only brief and answers.

Every operation derives ownership from verified `tid` and `oid` claims and
requires a nonempty delegated `scp` claim. Application-only callers are rejected.
Client-supplied owner fields cannot grant access. Rows without the `contoso-demo`
marker are hidden.

The form bundles the official MCP App SDK. On opening, it takes only the request
ID from the retained result and fetches the current record through the host.
Reloading or reopening performs a read; it never replays a save.

Answer writes check the active question-set revision before a conditional Table
Merge using the supplied ETag. A conflict requires reopening and review before
another change. An exact submission retry, or a repeat using the current
revision of the same submitted question set, returns the saved outcome without
another write.

## Build and check

From this Tool's `frontend` directory:

```sh
npm ci --ignore-scripts
npm run build
```

Commit `app.js` and `THIRD_PARTY_NOTICES.txt` with the frontend sources and lock.
From the repository root:

```sh
uv sync --frozen
uv run --frozen python -m unittest tests.test_contoso_request -v
```

These tests use the public MCP client with controlled verified-principal and
Azure SDK fixtures. They establish Tool discovery, ownership, conditional writes,
and read/save/reopen behavior, including clarification types, required answers,
revision conflicts, and submission retries. Live Azure access and integration
with ToolForge and Foundry must be checked separately.

For that live gate, open `CONTOSO-DEMO-001` in ToolForge, change the business
objective, and select **Save draft**. In the Foundry playground, have the customer
agent read that same changed request and save another customer-requested edit
using the current revision. Reopen the ToolForge App and confirm that it shows
the second saved value. The row's ETag must change only for saves. Both surfaces
must resolve to the same delegated customer. Also verify that a different
customer and an application-only caller cannot read or edit the request.

For clarification, list requests needing attention and open `CONTOSO-DEMO-002`.
Save partial answers, including a **No** answer, and confirm the same values
through the other interface. Complete and save the remaining required answers.
Verify conversational submission approval, explicitly submit, and reopen the
App to see the retained answers and **In review** state. A repeated submission
must not advance the ETag again.
