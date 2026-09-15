# Nautobot Overlays App

A Nautobot app that provides data models and UI for network segregation and multi-tenancy through overlays.

## Overview

This app enables logical separation of network resources by tenant, allowing downstream systems (Temporal workflows, config rendering) to enforce tenant boundaries. It provides:

- **Overlay** - Central object representing a tenant's network overlay (VRF + VLAN + VXLAN + interfaces)
- **OverlayAssignment** - Associates devices, interfaces, and other resources with overlays
- **VXLAN** - Tracks VXLAN Network Identifiers (VNIs), including optional BGP EVPN import/export route targets for L2 segments
- **InfiniBandPKey** - Tracks InfiniBand Partition Keys for IB fabric isolation

## Features

- Unified overlay object to represent a tenant's network overlay
- Secondary tenancy support - distinguish "owner" from "overlay membership"
- VXLAN/VNID tracking with namespace scoping and per-VNI import/export route targets
- Single pane of glass view for all resources allocated to a tenant overlay
- Bulk allocation UI to assign racks/interfaces to an overlay in one action
- REST and GraphQL API support
- Integration with existing Nautobot views (Device, Interface, VRF, VLAN)

## Screenshots

### Overlays List

![Overlays List View](docs/images/fabric-partition-view.png)

### Single Overlay Detail

![Single Overlay Detail View](docs/images/single-partition-view.png)

### Overlay Assignments

![Overlay Assignments View](docs/images/partition-member-view.png)

### VXLANs

![VXLANs View](docs/images/vlxans-view.png)

### InfiniBand PKeys

![InfiniBand PKeys View](docs/images/infiniband-pkeys-view.png)

## Requirements

- Nautobot >= 2.4.20, < 2.5.0
- Python >= 3.10, < 3.13

## Installation

This app is currently bundled in with the NVIDIA Config Manager. It will be published as a standalone Nautobot app in the future if there is community interest.

If you want only the overlays plugin without deploying the full NVIDIA Config Manager suite, install it directly from a tagged `nv-config-manager` release:

```bash
pip install "nautobot-app-overlays @ git+https://github.com/dsx-ai-factory/nv-config-manager.git@<release-tag>#subdirectory=components/nautobot/nautobot-app-overlays"
```

Replace `<release-tag>` with a released tag such as `1.2.2`. Use a tag rather than a branch for repeatable installs.

Add the app to your `nautobot_config.py`:

```python
PLUGINS = [
    "nautobot_app_overlays",
]
```

Run migrations:

```bash
nautobot-server migrate
```

For managed Nautobot deployments, run the same restart or `post_upgrade` flow you normally use after adding a Nautobot app.

## Development Environment Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.10+
- uv (recommended) or pip

### Setup

1. Clone the NVIDIA Config Manager repository:

   ```bash
   git clone https://github.com/dsx-ai-factory/nv-config-manager.git
   cd nv-config-manager/components/nautobot/nautobot-app-overlays
   ```

2. Create credentials file:

   ```bash
   cp development/creds.example.env development/creds.env
   ```

3. (Optional) Create local virtual environment for invoke commands:

   ```bash
   uv sync --frozen --all-extras
   ```

4. Build the Docker image:

   ```bash
   invoke build
   ```

5. Start containers:

   ```bash
   invoke start
   ```

6. Access Nautobot at http://localhost:8080

The default credentials are `admin` / `admin`.

### Common Commands

```bash
invoke start          # Start containers in detached mode
invoke stop           # Stop containers
invoke destroy        # Stop and remove containers and volumes
invoke logs -f        # Follow container logs
invoke cli            # Open bash shell in nautobot container
invoke nbshell        # Open Nautobot shell
invoke unittest       # Run unit tests
invoke tests          # Run all tests (lint + unit)
invoke makemigrations # Create new migrations
invoke migrate        # Apply migrations
invoke lock           # Generate/update uv.lock file
invoke autoformat     # Auto-format code with ruff
invoke ruff           # Run ruff linting and formatting checks
invoke docs           # Serve documentation locally
invoke build_and_check_docs # Build documentation to be available within Nautobot
```

### Running Tests

```bash
# Run all tests (linting + unit tests)
invoke tests

# Run only linting
invoke tests --lint-only

# Run only unit tests
invoke unittest

# Run unit tests with coverage
invoke unittest --coverage
invoke unittest-coverage
```

### Updating Dependencies

To update the `uv.lock` file after modifying `pyproject.toml`:

```bash
uv lock
```

Or inside the container:

```bash
invoke lock
```

## Documentation

To serve documentation locally, run one of the following commands:

```bash
# If running in development environment
invoke docs

# To run just the docs server, no Docker
uv run mkdocs serve
```

The mkdocs output lists the URL to access the documentation.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.
