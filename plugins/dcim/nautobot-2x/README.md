# Nautobot 2.x DCIM Provider

This package is the Nautobot 2.x reference implementation of the NVIDIA
Config Manager DCIM provider API. It deliberately owns every Nautobot-specific detail:
HTTP transport, GraphQL and REST queries, changelog event interpretation, and
workflow data operations.

The standalone `nv-config-manager-dcim` SDK supplies the provider contracts and
discovers this package through the `nv_config_manager.dcim` entry-point group.
New DCIM integrations should use this layout as their reference: depend only on
the SDK, expose one provider entry point, and keep backend schemas, NVCM
configuration, logging, metrics, and service models out of the provider.

Nautobot's GraphQL queries, REST endpoints, changelog translation, and optional
Nautobot MCP adapter are implementation details of this package. Other
providers choose their own native transport and must not implement Nautobot MCP
tools merely to satisfy Config Manager. Provider-owned render event handlers
identify the devices affected by each backend event before the provider-neutral
render dispatcher queues work.

## Provider selection

Select this implementation with `[dcim] provider = nautobot-2x`. A versioned
name makes a future Nautobot 3.x implementation an explicit provider change.
Provider settings belong in `[dcim]`, `[dcim.options]`, or the more specific
`[dcim.nautobot-2x]` section.

## GraphQL documents

Provider GraphQL lives in `src/nv_config_manager_dcim_nautobot_2x/graphql/`, grouped
by provider capability (`provider/` and `workflow/`). Documents define named
operations and may import reusable fragments with `# import "relative/path.graphql"`.
The provider validates documents with `graphql-core`, resolves imports, and sends
only the selected operation and its fragment closure with GraphQL's standard
`operationName` field. Keep dynamic field selections in the dedicated
`*_fields.graphql` files; they preserve the legacy `fields=` extension point.

Until the publishing story is finalized, install this provider plugin and the SDK
from sibling Git checkouts. The NVCM host image installs the reference provider
by default; a template plugin can install the same two components directly to
exercise live provider queries without installing core NVCM.

For the complete provider authoring contract, see [Contribute a DCIM Provider](../../../docs/development/contributing-dcim-provider.mdx).
