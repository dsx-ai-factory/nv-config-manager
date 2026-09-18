# NVIDIA Config Manager infrastructure adapters

Shared asynchronous Redis and NATS clients and Redis lock primitives. This is a
separate distribution from the public HTTP SDK and has no dependency on core
services or workflows. Import from `nv_config_manager_infrastructure.redis`,
`.nats`, or `.lock` and pass explicit connection settings.

Applications own configuration discovery, credentials, Redis database selection,
NATS subjects, and connection lifetime. Workflow-specific keys, payloads, and
activities remain with workflow consumers. Legacy service imports retain INI
factory methods as thin subclasses; transport implementations live here only.

NATS is split by responsibility under `nv_config_manager_infrastructure.nats`:
`client`, `producer`, `consumer`, and `admin`. The package root re-exports the
three client classes for compatibility.

External password- or JWT-authenticated NATS connections support `tls://` and
`wss://` endpoints. Secure WebSockets establish TLS before the NATS handshake.
Native `tls://` connections require a server configured with
`handshake_first: true` in its `tls` block. The client performs the TLS handshake
before reading the server's initial `INFO`
message and does not fall back to INFO-first negotiation. A server using the
default INFO-first order can fail with a TLS error or timeout. See the
[external NATS setup guide](../../docs/render/render-service.mdx#external-nats-administration)
for a server configuration example. The explicitly `local` bundled
deployment retains its server-negotiated connection behavior.

## Local transport tests

Run the adapter tests and the socket-level downgrade regression from the
repository root:

```sh
uv run pytest packages/infrastructure/tests/ -v
```

The downgrade test sends unauthenticated `INFO` with `tls_required` omitted or
false and verifies that the client sends a TLS handshake, never a plaintext
`CONNECT`. It requires permission to open loopback sockets, but no NATS server.

To also exercise real NATS servers, start Docker and run:

```sh
docker pull nats:2.10.26-alpine
NVCM_TEST_NATS_DOCKER=1 uv run pytest packages/infrastructure/tests/ -v
```

These opt-in tests generate temporary certificates and operator/account/user JWT
credentials. They check password and JWT authentication over native TLS-first
and WSS, publish/consume before and after a server restart, and rejection of an
untrusted server certificate. Containers bind only to loopback and are removed
after the tests. No deployment or production credentials are needed. Test-only
timeouts, heartbeat intervals, and reconnect delays are shortened; authentication,
certificate verification, and transport I/O use the real client library.

To verify the minimum supported nats-py version in an isolated dependency overlay:

```sh
NVCM_TEST_NATS_DOCKER=1 uv run --with 'nats-py[nkeys]==2.9.0' pytest packages/infrastructure/tests/ -v
```
