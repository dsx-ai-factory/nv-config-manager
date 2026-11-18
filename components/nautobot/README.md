# NVIDIA Config Manager Nautobot Image

This component builds the Nautobot image used by the bundled deployment. It includes Nautobot configuration, installed plugins, and bootstrap job content used by the installer.

## Directory Structure

```text
components/nautobot/
├── nautobot_config.py
├── nautobot-app-overlays/
├── nautobot-nv-config-manager/
├── pyproject.toml
├── uv.lock
└── nv_config_manager_jobs/
```

## Building Images

Use the repository Makefile for local builds:

```bash
make docker-build-nb
make docker-build-nats-ready
```

The installer can also build and load the images during deployment:

```bash
cd installer
uv run nv-config-manager-installer deploy ../deploy/configs/local-superpod.yaml \
  --image-source local \
  --build-images \
  --load-kind
```

Manual build commands:

```bash
docker build -t nv-config-manager-nautobot:local -f build/nautobot.Dockerfile components/nautobot/
docker build -t nv-config-manager-nats-ready:local -f build/nats-ready.Dockerfile components/nats-ready/
```

## Jobs

`nv_config_manager_jobs/` contains bootstrap and development jobs. Bootstrap jobs are
always available from the Nautobot image; the installer stages only custom job paths
configured through `content.jobs` in `nv-config-manager-install.yaml`.

## Plugins

The image includes the Nautobot dependencies required by NVIDIA Config Manager, including the bundled `nautobot-app-overlays` app, the NVIDIA Config Manager Nautobot plugin, and the NATS broker plugin.

`nautobot-app-overlays/` and `nautobot-nv-config-manager/` are kept as standalone Python projects so they can be extracted back into their own repositories later. The Nautobot image consumes both through local path sources declared in `pyproject.toml`.
