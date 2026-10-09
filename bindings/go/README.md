# NVIDIA Config Manager Go Bindings

This module contains generated clients for every NVIDIA Config Manager OpenAPI service. The
packages are generated with OpenAPI Generator and committed so API changes are reviewable.

Install a specific platform release:

```bash
go get github.com/nvidia/nv-config-manager/bindings/go@v1.3.0
```

Import the service package you need. Each package uses its service name (`configstore`, `dhcp`,
`render`, `temporal`, or `ztp`), so multiple clients can be imported without aliases:

```go
import (
    "context"

    "github.com/nvidia/nv-config-manager/bindings/go/temporal"
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
Generated object models retain unrecognized JSON properties unless their OpenAPI schema explicitly
sets `additionalProperties: false`, allowing clients to decode additive response fields safely.

Do not edit generated service directories by hand.
