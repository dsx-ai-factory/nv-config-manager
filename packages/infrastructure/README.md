# NVIDIA Config Manager infrastructure adapters

Shared asynchronous Redis and NATS clients and Redis lock primitives. This is a
separate distribution from the public HTTP SDK and has no dependency on core
services or workflows. Import from `nv_config_manager_infrastructure.redis`,
`.nats`, or `.lock` and pass explicit connection settings.

Applications own configuration discovery, credentials, Redis database selection,
NATS subjects, and connection lifetime. Workflow-specific keys, payloads, and
activities remain with workflow consumers. Legacy service imports retain INI
factory methods as thin subclasses; transport implementations live here only.
