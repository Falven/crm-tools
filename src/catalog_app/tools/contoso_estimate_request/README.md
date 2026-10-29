# Estimate latest software request

Call `contoso_estimate_request()` with no arguments. It reads the signed-in
customer's most recently modified saved software request (Azure Tables metadata
`timestamp`), including drafts and saved clarification answers, then asks Azure
Foundry for a preliminary Markdown development estimate. Request IDs break exact
timestamp ties. It never reads another customer's partition or changes storage.
If no request is saved, submit one with the existing intake tools first. Unsaved
conversation or form details are not available to this tool.

## Estimate

The prompt asks for low/likely/high cost and effort, an elapsed timeline, people
and skills, a phase work breakdown, and required hardware/software/cloud/data
assets or materials with quantities and assumed prices. One-time development costs
and recurring monthly costs are separate. Prices, asset availability, currency
(USD by default), and inferred scope are assumptions, not vendor quotes. The
estimate is returned to the caller and is not saved. Each call uses the model
again and can incur a charge or return a different estimate.

## Microsoft Entra authentication

- **Incoming MCP call:** the catalog's existing `MiseAuth` verifies the Entra bearer
  token. The tool requires a delegated customer account with tenant/object IDs,
  matching the request tools' existing access rules. Application-only callers
  cannot read a customer's latest request.
- **Outgoing Foundry call:** `DefaultAzureCredential` and
  `get_bearer_token_provider` request `https://ai.azure.com/.default`; the OpenAI
  SDK's `api_key` parameter receives the token-provider callable, **not an API key**.
  In Azure, the server's managed identity is used (`AZURE_CLIENT_ID` selects a
  user-assigned identity when configured). This is the server identity, not an
  on-behalf-of exchange of the customer's token. Give that identity the appropriate
  model inference role on the resource, typically **Cognitive Services OpenAI
  User**, and ensure network access to the endpoint.

Endpoint: `https://aif-datacontrol-dev-eus2-primary.services.ai.azure.com/openai/v1`

Deployment: `gpt-6.1-sol`

The endpoint, deployment, and scope follow the supplied starter template. Existing
`CONTOSO_STORAGE_CONNECTION_STRING` and `CONTOSO_REQUESTS_TABLE` settings remain
required for request storage. No new secret or API-key setting is introduced.
Credentials and the OpenAI client close after every invocation. Responses are
requested with `store=False`, a 120-second client timeout, and no automatic retries.
Empty/incomplete responses and Azure authentication/API failures return safe errors.
