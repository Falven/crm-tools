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
