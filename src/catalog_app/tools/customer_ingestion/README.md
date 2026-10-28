# Customer intake — no storage

`customer_ingestion()` opens a blank MCP App with first name, last name, age,
and description. It accepts no arguments and never receives customer details.
It returns a useful static field description for clients that cannot show Apps;
do not collect the details in chat as a fallback.

## Interaction

- First and last names are required, trimmed, and limited to 100 characters each.
- Age is optional; when entered, it must be a whole number from 0 to 150.
- Description is optional and limited to 2,000 characters.
- **Review details** displays an on-screen preview, not a submission.
- **Edit details** returns to the form and removes the preview.
- **Clear form** wipes the inputs and preview.

## Privacy contract

Entered values exist only in the currently open App's DOM/memory. There are no
App Handlers, storage APIs, cookies, analytics, fetches containing form data,
server calls, model-context updates, messages to chat, or URL-based state.
Reopening the App loads only static recorded tool output and starts blank.
Autocomplete is disabled and the form cannot perform a native network submit.
No credentials, environment variables, or storage configuration are required.

The pinned official MCP App SDK is loaded from jsDelivr. This request contains
no form values. The resource permits that asset origin but no direct network
connections. Form review still works if the SDK cannot load; host theme and
size/fullscreen integration require the SDK bridge. The host's manually invoked
Copy as image control may copy visible details if the user explicitly chooses it;
this App never invokes it or exports details itself. Browser extensions and
user-initiated screenshots are outside this App's control.

## Files

- `__init__.py`: no-input Tool and static `ui://customer-ingestion/app.html` resource.
- `app.html`: responsive, theme-aware form.
- `app.js`: local validation/review/clear and official MCP App bridge lifecycle.
- `tests/test_customer_ingestion.py`: catalog registration and privacy regressions.
