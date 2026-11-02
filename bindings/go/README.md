# NVIDIA Config Manager Go Bindings

This module contains generated clients for every NVIDIA Config Manager OpenAPI service. The
packages are generated with OpenAPI Generator and committed so API changes are reviewable.

Install a specific platform release:

```bash
go get github.com/dsx-ai-factory/nv-config-manager/bindings/go@v1.4.0
```

Import the service package you need. Each package uses its service name (`configstore`, `dhcp`,
`render`, `temporal`, or `ztp`), so multiple clients can be imported without aliases:

```go
import (
    "context"

    "github.com/dsx-ai-factory/nv-config-manager/bindings/go/temporal"
)

ctx := context.WithValue(context.Background(), temporal.ContextAccessToken, accessToken)
configuration := temporal.NewConfiguration()
client := temporal.NewAPIClient(configuration)
request := client.WorkflowAPI.GetWorkflowsV1WorkflowGet(ctx)
```

CLI and machine clients use a bearer JWT by default. Health, readiness, metrics, and Temporal
codec endpoints are explicitly public. ZTP device endpoints also support device-IP authorization.
Deployments can disable authentication enforcement with `[auth] required = false`.

Regenerate every OpenAPI specification and Go client from the repository root:

```bash
make api-generate
```

Generation requires Docker; the command uses a version-and-digest-pinned OpenAPI Generator image.

The Temporal `WorkflowFormUiSchema` model exposes `AdditionalProperties` for form entries keyed by
workflow input field. These dynamic entries are part of that schema; generated models do not enable
additional properties globally.

The workflow API's legacy metadata response remains available without a query
parameter. Request `GET /v1/workflow/metadata?include=form` to discover browser
forms, then use the returned `form_id` with `GET /v1/workflow/{form_id}/form`.
Form-only field projections do not change the workflow execution request
models. The API replaces a `ServerOwned` user field with authenticated identity,
even when a client supplies another value.

Do not edit generated service directories by hand.
