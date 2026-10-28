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

## Develop and verify

```sh
uv run --frozen python -m unittest tests.test_contoso_request tests.test_auth -v
```

To rebuild the checked-in App bundle:

```sh
npm ci --prefix src/catalog_app/tools/contoso_open_request/frontend
npm run build --prefix src/catalog_app/tools/contoso_open_request/frontend
```

Commit and push changes to `main`. Confirm the applied commit in ToolForge before
testing. Automated checks use controlled identity and storage fixtures; live
configuration and client behavior must be checked through the deployed service.
