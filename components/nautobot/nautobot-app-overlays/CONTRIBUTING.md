# Contributing to Nautobot Overlays

Thank you for your interest in contributing to the Nautobot Overlays app!

## Development Environment

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

2. Copy the invoke configuration:

```bash
cp invoke.example.yml invoke.yml
```

3. Copy the credentials file:

```bash
cp development/creds.example.env development/creds.env
```

4. Build the Docker image:

```bash
invoke build
```

5. Start the containers:

```bash
invoke start
```

6. Create a superuser:

```bash
invoke createsuperuser
```

## Development Workflow

### Running Tests

```bash
invoke tests  # Run all tests (lint + unit)
invoke unittest  # Run unit tests only
invoke ruff  # Run linting
```

### Code Formatting

```bash
invoke autoformat  # Auto-format code with ruff
```

### Creating Migrations

```bash
invoke makemigrations
invoke migrate
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Ensure tests pass: `invoke tests`
5. Submit a pull request

## Code Style

- Follow PEP 8 guidelines
- Use meaningful variable and function names
- Add docstrings to all public functions and classes
- Keep functions focused and small

## Commit Messages

- Use clear, descriptive commit messages
- Start with a verb in present tense (e.g., "Add", "Fix", "Update")
- Reference issue numbers when applicable

## Questions?

If you have questions, please open an issue on GitHub.
