# OpenAPI Specifications

This directory contains generated OpenAPI JSON for the FastAPI services in NVIDIA Config Manager.

| File | Service |
| ---- | ------- |
| `ztp.openapi.json` | ZTP API |
| `dhcp.openapi.json` | DHCP API |
| `temporal.openapi.json` | Temporal workflow API |
| `render.openapi.json` | Render API |
| `config-store.openapi.json` | Config Store API |

Regenerate specs from the repository root:

```bash
make openapi
```

Check that committed specs are current:

```bash
make openapi-check
```

Regenerate the specifications and all committed Go and Python clients together:

```bash
make api-generate
```

The public Python SDK and convenience wrappers live in `packages/clients`.
To regenerate only Python bindings from committed specifications, run
`make python-bindings`. Edit generation behavior in
`scripts/generate_python_bindings.py`, never in generated modules.

The specifications describe bearer JWT authentication as the default for CLI and machine clients.
Explicit health, readiness, metrics, and Temporal codec routes are public. ZTP device routes also
support device-IP authorization, and deployments can disable authentication enforcement with
`[auth] required = false`.

API path and method changes should be intentional and reviewed separately from documentation text changes.
