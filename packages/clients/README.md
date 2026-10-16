# NVIDIA Config Manager Python clients

Async generated SDKs and convenience wrappers for the Config Store, DHCP, Render,
Temporal HTTP API, and ZTP services. Requires Python 3.13 or newer.

Install `nv-config-manager-clients`. This distribution does not depend on the
server, Temporal SDK, Redis, NATS, or workflow package.

```python
from nv_config_manager_clients import ConfigStoreClient


async def read_config(token: str) -> str:
    async with ConfigStoreClient(
        "https://config.example.com",
        "intended",
        "https://config.example.com",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        config = await client.load_file("device-id", "startup.cfg")
        return config.content
```

Pass a callable as `headers` to refresh credentials for each request. Certificate
paths and TLS verification settings are explicit constructor arguments. Service
INI parsing and SPIFFE/Vault discovery remain in the host application.

Wrappers delegate endpoint selection and request serialization to generated API
operations, while preserving convenient dictionary/model results and service
exceptions. The complete generated SDK is also available under
`nv_config_manager_clients.generated.<service>` (use `config_store` for Config
Store). Generated APIs return their generated Pydantic models.

Regenerate with `make python-bindings`, or regenerate specifications and all
language bindings with `make api-generate`. Never edit generated modules directly.
The pinned generator and reusable generation changes live in
`scripts/generate_python_bindings.py` and `scripts/openapi-generator-python.patch`.

For the full generated API, manage its connection lifetime explicitly:

```python
from nv_config_manager_clients.generated.config_store import ApiClient, Configuration
from nv_config_manager_clients.generated.config_store.api.config_api import ConfigApi


async def list_configs(token: str):
    settings = Configuration(host="https://config.example.com", access_token=token)
    async with ApiClient(settings) as client:
        return await ConfigApi(client).get_device_configs_v1_config_device_device_uuid_get(
            device_uuid="device-id",
            file_type="intended",
        )
```

`TemporalClient.start_workflow` dispatches built-in workflows through generated
typed operations. For endpoints contributed by plugins installed on the server
after SDK generation, it uses the generated client's generic serializer and
transport, preserving extension support without a server dependency.

ZTP file-existence checks use the metadata-only HEAD endpoint introduced with
this SDK. Older servers without that endpoint should be upgraded before using
`check_file_exists`.
