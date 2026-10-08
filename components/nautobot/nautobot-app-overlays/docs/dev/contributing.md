# Contributing

## Development Setup

```bash
git clone https://github.com/dsx-ai-factory/nv-config-manager.git
cd nv-config-manager/components/nautobot/nautobot-app-overlays

# Copy credentials
cp development/creds.example.env development/creds.env

# Start development environment
docker compose -f development/docker-compose.dev.yml up -d
```

Access Nautobot at http://localhost:8080

## Running Tests

```bash
uv run invoke tests
```

## Code Style

```bash
uv run invoke ruff --fix
```

## Creating Migrations

```bash
uv run invoke makemigrations --name descriptive_name
```

## Pull Requests

1. Fork and create a feature branch
2. Write tests for new functionality
3. Run `invoke tests` to verify
4. Submit PR with clear description
