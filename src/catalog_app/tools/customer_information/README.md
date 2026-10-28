# Customer information

A blank, optional-fields MCP App for full name, company, email, phone, address,
and notes. The model-facing `customer_information()` tool returns a blank field
map and opens `ui://customer-information/app.html`.

## Memory-only behavior

Edits update a JavaScript object synchronously on each input/change event. Each
open App has its own object. The form has no database, file writes, browser
storage, customer-data network requests, server handlers, model-context updates,
or submit-to-assistant action. Review details shows a local live preview;
Clear form asks for confirmation before resetting all fields. Browser email
validation runs on Review details; all fields remain optional.

Closing, reloading, or reopening the App loses edits. Page-hide also clears
values to avoid retaining them through the browser back/forward cache. The host
bridge handles theme, natural content size, and fullscreen only, not customer
values. As with any interface, the host's screenshot/copy-as-image control can
capture visible values when deliberately used; memory-only is not a secure
vault. Avoid entering credentials or other highly sensitive data.

The official MCP App SDK is bundled into the single HTML resource. No CDN,
external fonts, or runtime dependencies need network access. The form remains
usable if the host bridge is unavailable.

## Rebuild and test

From this directory:

```sh
(cd frontend && npm ci && npm run build)
```

From the repository root:

```sh
uv run --locked python -m unittest discover -s tests -v
```

Register the Tool/resource within `tool_directory("customer_information")` in
`src/catalog_app/server.py`. Publish through Git, wait for MCP synchronization,
then call `customer_information` and inspect its returned resource. No environment
variables or customer-storage credentials are required.
