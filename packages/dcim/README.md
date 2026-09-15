# NVIDIA Config Manager DCIM SDK

`nv-config-manager-dcim` is the provider-neutral contract used by NVIDIA
Config Manager services, template plugins, and DCIM provider implementations.
It defines public models, client protocols, provider discovery, and error
semantics. It deliberately does not read configuration files, initialize
logging, or depend on NVCM services.

Providers return the SDK's Pydantic models and expose one entry point in the
`nv_config_manager.dcim` group. The SDK owns provider-neutral contracts such
as `RenderData` and workflow intent models; providers own native REST,
GraphQL, client-library, and event details. Services select a provider and
pass it explicit settings, but never import a provider implementation.

Until the publishing story is finalized, install it from a sibling checkout:

```bash
uv run --no-project \
  --with ../nv-config-manager/packages/dcim \
  --with ../nv-config-manager/plugins/dcim/nautobot-2x \
  --with ../nv-config-manager/packages/templates \
  --with-editable . template-cli --help
```

Applications select a provider by entry-point name and pass a provider-owned
settings mapping to `create_dcim_client()`. Applications own configuration
file parsing, secrets, logging, and lifecycle policy.

See [Contribute a DCIM Provider](../../docs/development/contributing-dcim-provider.mdx)
for the package, event, render-data, test, and deployment contract. The
The Nautobot 2.x provider in `plugins/dcim/nautobot-2x` is the reference
implementation; its GraphQL transport is not part of this SDK contract.
