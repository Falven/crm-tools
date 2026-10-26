# ToolForge repository

## Layout and Tool contract

Each Tool lives in `src/catalog_app/tools/<tool_id>/`, with its entry module
in `__init__.py` and supporting code and data alongside it. The package name
is its Tool ID; renaming the package changes that identity. A simple Tool is a
plain typed function in that module. `src/catalog_app/server.py` explicitly
imports and registers it with `server.add_tool()` after constructing the server;
source files alone are not discovered. Keep the import and registration inside
`with tool_directory("<tool_id>"):`. The `server.tool_directory` binding supports
both standalone and supervised execution.
This ToolForge scope attributes calls and startup failures to the Tool ID and lets a faulty
Tool fail without hiding healthy siblings.

For a Tool that also registers resources or App Handlers, the entry module may
expose `register(server: MCPServer) -> None`, with `MCPServer` imported from
`mcp.server`. Call it explicitly inside the same `tool_directory` scope in
`server.py`. Each managed directory registers exactly one model-facing Tool;
App Handlers use `meta={"ui": {"visibility": ["app"]}}`.

An [MCP App](https://modelcontextprotocol.io/extensions/apps/overview) requires
`extensions=[Apps()]` in `src/catalog_app/server.py`.
Its registration can add a unique `ui://` HTML resource on the supplied server
with `mime_type="text/html;profile=mcp-app"`. The main Tool's
`meta["ui"]["resourceUri"]` points to that URI. HTML, JavaScript, and CSS live in
the Tool package; paths relative to `__file__` locate these assets independently
of the working directory.

Text or structured return data supports clients that cannot render Apps.
An App's assets and handlers are published with its Tool. A successful App result
exposes **Open App** on the completed reply and an entry in ToolForge's
**View App** sidebar panel for that session.

The naming convention for Tool IDs and tool names is lowercase snake_case.
The registered function's name is its MCP name even when re-exported from a
package; `tool_directory` sets only Tool ID and never renames the MCP Tool.
An explicit registration name can override it. MCP tool names must be unique
across the server; package paths do not prefix them. Disabled
Tools can introduce name collisions when re-enabled.

The signature defines the input schema. The docstring supplies the default Tool
description of inputs, outputs, and side effects for calling agents. The return
value is sent back to the caller.

## Copilot Skills

The Sandbox’s preinstalled `skills` CLI supports project installs from the
repository root: `skills add <source> --agent universal -y`.
Usage is available through `skills --help`.
Global installs (`--global`) live in the ephemeral Sandbox filesystem and will
not persist across sessions.
The Sandbox provides a preconfigured CLI and browser for `agent-browser`.

## Python environment and dependencies

The repository-root `pyproject.toml` defines shared Python configuration.
`[project].requires-python` selects Python. Additional dependencies are declared
there or in workspace package manifests. The installable `catalog-app` project
owns the complete MCP execution application and its dependencies under
`src/catalog_app/`, including the SDK, transport, authentication, and telemetry.
Management runs in a separate environment and supplies no execution dependencies.

`uv.lock` is the dependency lock. In ToolForge sandboxes, the Git pre-commit
hook runs `uv sync --all-packages` and stages the resulting lock when a
fully staged dependency manifest changes.

Outside the hook, `uv sync --all-packages` from the repository root updates the
environment and lock. Dependency updates include the changed manifests and
generated lock in the same commit.

## MCP runtime and authentication

`python -m catalog_app` runs the Catalog-owned execution application.
`src/catalog_app/__main__.py` owns transport and private supervision controls;
`hosting.py` owns SDK integration, Tool scopes, and invocation attribution.
`src/catalog_app/server.py` constructs its own MCP Python SDK v2 server class
and composes Catalog authentication.
`name`, `title`, `description`, and `instructions` are constructor arguments;
`**kwargs` supplies hosting options. The **Server** page edits
literal `title` and `instructions` values through an explicit **Commit** action
that commits and pushes the source change.

`src/catalog_app/auth.py` defines the `CatalogAuth` provider. Import and
construct the chosen provider in `src/catalog_app/server.py`. It implements
the official Model Context Protocol Python SDK (`mcp`
package) `TokenVerifier`, exposes SDK `AuthSettings`, and optionally supplies
an Azure Core `TokenCredential` for a delegated caller. This Catalog’s `MiseAuth`
accepts user and application tokens through MISE. Keep its wheel in `wheelhouse/`
and its declared Linux ICU dependency. MISE errors deny access.
`server.py` reads its settings from the
environment at startup and passes them to the constructor. MISE requires the client ID at startup. Other OBO settings remain
optional until a Tool requests a delegated credential. A custom provider can
return `None` for delegation.
Changing this bearer provider does not change web or Copilot token acquisition
or add a nonbearer HTTP authentication flow.

During an authenticated Tool call,
`mcp.server.auth.middleware.auth_context.get_access_token()` provides the
caller's token and verified claims.

For delegated user calls, `catalog_app.credentials.get_caller_credential()`
provides the provider's invocation-scoped Azure Core `TokenCredential` for
downstream APIs. `catalog_app.credentials` handles the generic call lifecycle;
it closes the credential when the Tool call ends. Do not retain that credential.
`MiseAuth` owns OBO exchange and reauthorization. `get_token()` requests the
target API's authorized scopes. Application-only calls cannot use the default
OBO credential. Terminal execution has no MCP caller context.

Shared Tool credentials are configured under **Server → Environment variables**.
These values are available to all Tools on the server and kept out of Git.

## Publication

Changes publish when their commit reaches the configured remote branch.
ToolForge's Source Control pushes after committing; terminal commits require
an explicit push. ToolForge automatically pulls that branch and restarts the
MCP application, which can take a few seconds. **Server → Tools** shows
activation; a successful push does not establish that the changed Tool is being
served.

Enable and Disable remove or add one comment layer around the Tool's
registration block in `server.py`; the implementation remains editable.
A manually authored Tool with an active registration block publishes normally
without a separate Enable action. UI New Tool creates a commented block in a
general repository Session. Edit the Tool and uncomment its `server.py` block
before publishing, or use **Tools → Enable**. Editing only the implementation
leaves a disabled Tool disabled. Delete removes the registration block and Tool
folder. Each UI action commits and
pushes immediately.
