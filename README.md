# CRM tools

Customer software-project requests and clarification forms for ToolForge. This
Repository contains the MCP application, its CRM Tools, and their shared MCP App.
The Foundry agent, synthetic fixture, and rehearsal commands remain in ToolForge's
[`apps/demo`](https://github.com/mcaps-microsoft/ToolForge/tree/main/apps/demo).

## Connect through ToolForge

1. Open **Git** in the ToolForge sidebar.
2. Set **Remote repository URL** to `https://github.com/Falven/crm-tools.git` and
   **Branch** to `main`. Use Git credentials with access to this repository.
3. Choose **Pull repository** for this existing branch. A source switch retires
   the old authoring Sandboxes; preserve unpublished work before switching.
4. Wait for **In sync** and matching desired and applied commits.
5. In **Environment variables**, configure `CONTOSO_STORAGE_CONNECTION_STRING`
   and `CONTOSO_REQUESTS_TABLE` through the masked editor if they are not already
   configured. Storage credentials stay out of Git and chat.
6. Open **Inspector**, connect to ToolForge, and inspect the published Tools and
   Apps. Open an existing software project to verify the real storage connection.

The existing Tool names, delegated identity rules, and storage format are kept so
the configured Foundry agent and saved customer records can continue to work.
See the [request workflow](src/catalog_app/tools/contoso_open_request/README.md)
for Tool contracts, App behavior, and storage requirements.

## Estimate development cost and resources

Call `contoso_estimate_request()` with no arguments for a preliminary estimate of
cost, time, staffing, and assets/materials for your latest saved software request.
Configure `FOUNDRY_ENDPOINT`, `FOUNDRY_DEPLOYMENT`, and `FOUNDRY_SCOPE`.
MCP access and Foundry inference use Entra authentication, not a model API key.

## Interactive MCP Apps

An [MCP App](https://modelcontextprotocol.io/extensions/apps/overview) adds an
interactive interface, such as a form or dashboard, to a Tool result. ToolForge
displays the interface associated with a successful App result and supports its
interactions with the MCP server. Other MCP clients need MCP Apps support to
display the interface; clients without it still receive the Tool's result.

The App's files and handlers are published with its Tool. Change a Tool's
availability by editing its registration and publishing through ordinary Git.

## Publish changes

This Repository owns its complete execution application and locked dependencies.
`src/catalog_app/server.py` constructs the server; `python -m catalog_app` runs
the supervised HTTP application. SDK, authentication, transport, and execution
integration changes publish together with `pyproject.toml` and `uv.lock`.

Push commits to `main` to publish them. ToolForge's Source Control pushes after
a commit. Fully stage dependency manifest changes; the Sandbox's pre-commit
hook checks installation and stages `uv.lock` only on success. Dependency
preparation needs the relevant project files and shared lock within the selected
workspace directory. A narrower directory remains useful for source editing
without a prepared Python environment.

Check MCP Server Status under **MCP**. Open **Inspector**, select the connected
server from Servers, then open Tools to discover and call the advertised Tool.
A connection alone does not prove that the Tool is available.

## Manage tools

Start a native Session in the workspace directory where you want to work. The
agent uses that Session's independent Sandbox to create or change source.
Starting the Session does not generate a Tool or publish files.

Enable or disable a Tool by editing its import and registration block in
`server.py` and publishing through ordinary Git. A commented registration leaves
the implementation and supporting files editable; MCP activation is reported
separately. Editing a disabled Tool's implementation alone leaves it disabled.
Rename or remove Tool source through the editor and Git.

## Authentication and credentials

`authentication=MiseAuth.from_env()` in `src/catalog_app/server.py` verifies Microsoft Entra user and application tokens through MISE before Tool code runs. `src/catalog_app/mise.py` owns verification, on-behalf-of (OBO) exchange, and `MiseMCPServer`, the small server adapter that supplies the HTTP request context MISE needs. The pinned MISE wheel stays in `wheelhouse/`; Linux also uses the declared ICU dependency. Verification failures deny access.

To use another bearer provider, implement the `CatalogAuth` contract from `src/catalog_app/auth.py` in a module such as `my_auth.py`. The provider has four members:

| Member | Responsibility |
| --- | --- |
| `settings` | Native SDK `AuthSettings`: issuer, resource URL, and required scopes. |
| `verify_token(token)` | Validate the bearer token and return a native SDK `AccessToken`, or `None` to reject it. Bind `client_id` to the verified client. |
| `caller_identity(caller)` | Return `CallerIdentity`: an explicit `principalType` of `user` or `application` and the available safe attribution fields. |
| `delegated_credential(caller)` | Return a context manager yielding an Azure Core `TokenCredential`, or `None` when delegation is unavailable. |

`CallerIdentity` supports `userId`, `userName`, `userEmail`, `entraTenantId`, and `entraObjectId`; omit fields your provider does not have. Keep assertions and raw claims in the native token, outside this projection. The provider determines whether its verified token represents a user or an application. Hosting accepts forwarded execution context only from configured trusted client IDs; application callers cannot forward user attribution.

For a custom provider that needs only the bearer token, replace the MISE import in `server.py` with `from .hosting import HostedMCPServer as server_class` and an import of your provider. Replace `authentication=MiseAuth.from_env()` with `authentication=MyAuth()`. The hosted server connects the provider to the SDK and manages each Tool call's reporting and credential lifetime. Tool registrations stay the same. Replacing the bearer provider does not change how ToolForge Web or the agent obtains tokens or add a nonbearer HTTP authentication flow.

`MiseAuth.from_env()` reads the existing environment settings once at startup. Its explicit `MiseAuth(...)` constructor remains available when settings come from elsewhere. MISE requires the client ID at startup. Other OBO settings are optional until a Tool requests a credential. `catalog_app.credentials.get_caller_credential()` returns the invocation-scoped Azure Core credential for downstream APIs; it closes when the Tool call ends, so do not retain it afterward. Application-only calls cannot use the MISE provider's OBO credential.

`TOOLFORGE_MCP_ENTRA_SCOPE` is the full OAuth scope advertised to clients, such as `api://<application-id>/ToolForge.MCP.Access`. `TOOLFORGE_MCP_ENTRA_PERMISSION` is the short permission in the access token's `scp` claim, such as `ToolForge.MCP.Access`. The provider maps that verified permission or its `.Application` role to the full scope required by the MCP SDK.

## Develop and verify

```sh
uv run --frozen python -m unittest discover -s tests -v
```

To rebuild the checked-in App bundle:

```sh
npm ci --prefix src/catalog_app/tools/contoso_open_request/frontend
npm run build --prefix src/catalog_app/tools/contoso_open_request/frontend
```

Automated checks use controlled identity and storage fixtures; live configuration
and client behavior must be checked through the deployed service.
