# NVIDIA Config Manager Go Bindings

This module contains generated clients for every NVIDIA Config Manager OpenAPI
service. The packages are generated with OpenAPI Generator and committed so API
changes are reviewable.

Install a specific platform release after it is published:

```bash
go get github.com/dsx-ai-factory/nv-config-manager/bindings/go@v1.4.0
```

For a release candidate containing the corrected module path, replace `N` below
with its published candidate number:

```bash
go get github.com/dsx-ai-factory/nv-config-manager/bindings/go@v1.4.0-rc.N
```

The repository tag is `bindings/go/v1.4.0-rc.N`; the Go module version used by
`go get` is `v1.4.0-rc.N`.

When migrating from `github.com/nvidia/nv-config-manager/bindings/go`, update
all service imports to `github.com/dsx-ai-factory/nv-config-manager/bindings/go`
with the same service suffix, then run the install command above for your chosen
version and `go mod tidy` in your consuming project. Review and commit the
resulting `go.mod` and `go.sum` changes. For example, replace
`github.com/nvidia/nv-config-manager/bindings/go/temporal` with
`github.com/dsx-ai-factory/nv-config-manager/bindings/go/temporal`.

Choose a release containing the module-path correction. Older tags, including
`1.4.0-rc.9`, still declare the previous module path and cannot be installed
using the new module path.

Import the service package you need. Each package uses its service name
(`configstore`, `dhcp`, `render`, `temporal`, or `ztp`), so multiple clients
can be imported without aliases:

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

CLI and machine clients use a bearer JWT by default. Health, readiness, metrics,
and Temporal codec endpoints are explicitly public. ZTP device endpoints also
support device-IP authorization.
Deployments can disable authentication enforcement with `[auth] required = false`.

Regenerate every OpenAPI specification and Go client from the repository root:

```bash
make api-generate
```

Generation requires Docker; the command uses a version-and-digest-pinned OpenAPI
Generator image.

The Temporal `WorkflowFormUiSchema` model exposes `AdditionalProperties` for form
entries keyed by workflow input field. These dynamic entries are part of that
schema; generated models do not enable additional properties globally.

The workflow API's legacy metadata response remains available without a query
parameter. Request `GET /v1/workflow/metadata?include=form` to discover browser
forms, then use the returned `form_id` with `GET /v1/workflow/{form_id}/form`.
Form-only field projections do not change the workflow execution request
models. The API replaces a `ServerOwned` user field with authenticated identity,
even when a client supplies another value.

Do not edit generated service directories by hand.
