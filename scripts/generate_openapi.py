#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Generate OpenAPI specifications for all NVIDIA Config Manager FastAPI services.

This script imports each FastAPI application and exports its OpenAPI schema
to JSON files in the docs/api-specs/ directory.

Usage:
    uv run python scripts/generate_openapi.py
    # Or with custom output directory:
    uv run python scripts/generate_openapi.py --output-dir ./api-specs
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

from fastapi import FastAPI

# Service definitions: (module_path, app_name, output_filename, display_name)
SERVICES = [
    ("nv_config_manager.config_store.api.main", "app", "config-store", "Config Store API"),
    ("nv_config_manager.temporal.api.main", "app", "temporal", "Temporal Workflow API"),
    ("nv_config_manager.ztp.api.main", "app", "ztp", "ZTP API"),
    ("nv_config_manager.render.api.main", "app", "render", "Render API"),
    ("nv_config_manager.dhcp.api", "app", "dhcp", "DHCP API"),
]

# Mock INI config for services that require configuration at import time
MOCK_CONFIG = """
[config_store]
database_host = localhost
database_port = 5432
database = nv-config-manager
database_user = nv-config-manager
database_password = mock

[redis]
host = localhost
port = 6379
db = 0
password = mock
ssl = false
socket_timeout = 5
socket_connect_timeout = 5

[nautobot]
server = http://localhost:8080
token = mock
ca_cert_file =
cache_refresh_interval = 300
cache_ttl = 600

[dhcp.lease_db]
local = true
"""


def setup_mock_config() -> str:
    """Create a temporary mock config file for services that need it.

    Returns:
        Path to the temporary config file
    """
    fd, config_path = tempfile.mkstemp(suffix=".ini", prefix="nv_config_manager_openapi_")
    with os.fdopen(fd, "w") as f:
        f.write(MOCK_CONFIG)
    return config_path


def get_app(module_path: str, app_name: str) -> FastAPI:
    """Import and return a FastAPI app from a module."""
    module = importlib.import_module(module_path)
    return cast(FastAPI, getattr(module, app_name))


def generate_openapi_spec(app: FastAPI, title: str | None = None) -> dict:
    """Generate OpenAPI spec from a FastAPI app."""
    # Update title if provided
    if title:
        app.title = title

    spec = app.openapi()
    validate_unique_operation_tags(spec)
    validate_unique_operation_ids(spec)
    validate_bearer_auth(spec)
    return spec


def validate_unique_operation_tags(spec: dict) -> None:
    """Reject duplicate operation tags before they reach client generators."""
    http_methods = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
    duplicate_operations: list[str] = []

    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in http_methods:
                continue
            tags = operation.get("tags", [])
            if len(tags) != len(set(tags)):
                duplicate_operations.append(f"{method.upper()} {path}: {tags}")

    if duplicate_operations:
        details = "\n  ".join(duplicate_operations)
        raise ValueError(f"OpenAPI operations contain duplicate tags:\n  {details}")


def validate_unique_operation_ids(spec: dict) -> None:
    """Reject missing or duplicate operation IDs before client generation."""
    http_methods = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
    owners: dict[str, str] = {}
    problems: list[str] = []

    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in http_methods:
                continue
            owner = f"{method.upper()} {path}"
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                problems.append(f"{owner}: missing operationId")
                continue
            previous = owners.setdefault(operation_id, owner)
            if previous != owner:
                problems.append(
                    f"{owner}: operationId {operation_id!r} is already used by {previous}"
                )

    if problems:
        details = "\n  ".join(problems)
        raise ValueError(f"OpenAPI operations must have unique operation IDs:\n  {details}")


def validate_bearer_auth(spec: dict) -> None:
    """Require the shared default bearer policy used by generated clients."""
    bearer_scheme = spec.get("components", {}).get("securitySchemes", {}).get("BearerAuth")
    if not isinstance(bearer_scheme, dict):
        raise ValueError("OpenAPI schema does not define components.securitySchemes.BearerAuth")
    if bearer_scheme.get("type") != "http" or bearer_scheme.get("scheme") != "bearer":
        raise ValueError("OpenAPI BearerAuth must use the HTTP bearer security scheme")

    http_methods = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
    missing_security: list[str] = []
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method in http_methods and "security" not in operation:
                missing_security.append(f"{method.upper()} {path}")

    if missing_security:
        details = "\n  ".join(missing_security)
        raise ValueError(
            f"OpenAPI operations must declare their authentication policy:\n  {details}"
        )


def main() -> int:
    """Generate OpenAPI specs for all services."""
    parser = argparse.ArgumentParser(
        description="Generate OpenAPI specifications for NVIDIA Config Manager FastAPI services"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/api-specs"),
        help="Output directory for OpenAPI specs (default: docs/api-specs)",
    )
    parser.add_argument(
        "--service",
        type=str,
        choices=[s[2] for s in SERVICES],
        help="Generate spec for a specific service only",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if specs are up-to-date (exit 1 if changes detected)",
    )
    args = parser.parse_args()

    # Set up mock config for services that need it at import time
    # This must happen BEFORE importing any nv-config-manager modules
    mock_config_path = setup_mock_config()
    os.environ["NV_CONFIG_MANAGER_INI"] = mock_config_path
    print(f"Using mock config: {mock_config_path}")

    try:
        return _generate_specs(args, mock_config_path)
    finally:
        # Clean up the mock config file
        try:
            os.unlink(mock_config_path)
        except OSError:
            pass


def _generate_specs(args: argparse.Namespace, mock_config_path: str) -> int:
    """Generate specs with mock config already set up."""
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    services_to_generate = SERVICES
    if args.service:
        services_to_generate = [s for s in SERVICES if s[2] == args.service]

    generated_files: list[Path] = []
    errors: list[str] = []
    changes_detected = False

    for module_path, app_name, filename, display_name in services_to_generate:
        # Include "openapi" in filename so documentation hosts auto-render the spec.
        output_file = output_dir / f"{filename}.openapi.json"
        print(f"Generating OpenAPI spec for {display_name}...")

        try:
            app = get_app(module_path, app_name)
            spec = generate_openapi_spec(app, display_name)

            # Pretty print JSON
            new_content = json.dumps(spec, indent=2, sort_keys=True) + "\n"

            if args.check:
                # Compare with existing file
                if output_file.exists():
                    existing_content = output_file.read_text()
                    if existing_content != new_content:
                        print(f"  ⚠️  {output_file} is out of date")
                        changes_detected = True
                    else:
                        print(f"  ✓  {output_file} is up to date")
                else:
                    print(f"  ⚠️  {output_file} does not exist")
                    changes_detected = True
            else:
                output_file.write_text(new_content)
                generated_files.append(output_file)
                print(f"  ✓  Generated {output_file}")

        except Exception as e:
            error_msg = f"Failed to generate spec for {display_name}: {e}"
            errors.append(error_msg)
            print(f"  ✗  {error_msg}")

    if errors:
        print(f"\n⚠️  {len(errors)} error(s) occurred:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if args.check:
        if changes_detected:
            print("\n❌ OpenAPI specs are out of date. Run 'make api-generate' to update.")
            return 1
        print("\n✅ All OpenAPI specs are up to date.")
        return 0

    print(f"\n✅ Generated {len(generated_files)} OpenAPI spec file(s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
