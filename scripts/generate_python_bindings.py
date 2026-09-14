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
"""Generate the public async Python SDK with the pinned OpenAPI Generator."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from add_spdx_headers import PYTHON_HEADER

GENERATOR_IMAGE = (
    "openapitools/openapi-generator-cli:v7.21.0@"
    "sha256:ce308310f3c1f8761e65338b8ab87b651bf4862c6acb80de510f381fffc4510b"
)
SERVICES = ("config-store", "dhcp", "render", "temporal", "ztp")
HEADER = PYTHON_HEADER


def main() -> None:
    """Stage generation before replacing only the generated service directories."""
    root = Path(__file__).resolve().parents[1]
    destination = root / "packages/clients/src/nv_config_manager_clients/generated"
    with tempfile.TemporaryDirectory(prefix="nvcm-python-bindings-") as temporary:
        staging = Path(temporary)
        templates = staging / "templates"
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--volume",
                f"{staging}:/out",
                GENERATOR_IMAGE,
                "author",
                "template",
                "--generator-name",
                "python",
                "--output",
                "/out/templates",
            ],
            check=True,
        )
        # OpenAPI 3.1 nullable enum parameters are sometimes emitted as StrictStr.
        # Let ApiClient.sanitize_for_serialization handle both enums and strings.
        with (root / "scripts/openapi-generator-python.patch").open() as patch:
            subprocess.run(
                ["patch", "--directory", str(templates), "--strip", "1"], stdin=patch, check=True
            )
        for service in SERVICES:
            package = service.replace("-", "_")
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--volume",
                    f"{root}:/repo:ro",
                    "--volume",
                    f"{staging}:/out",
                    GENERATOR_IMAGE,
                    "generate",
                    "--input-spec",
                    f"/repo/docs/api-specs/{service}.openapi.json",
                    "--generator-name",
                    "python",
                    "--output",
                    f"/out/{service}",
                    "--additional-properties",
                    f"packageName=nv_config_manager_clients.generated.{package},"
                    "library=asyncio,generateSourceCodeOnly=true,hideGenerationTimestamp=true,"
                    "disallowAdditionalPropertiesIfNotPresent=false",
                    "--template-dir",
                    "/out/templates",
                    "--global-property",
                    "apiDocs=false,apiTests=false,modelDocs=false,modelTests=false",
                ],
                check=True,
            )
        for service in SERVICES:
            package = service.replace("-", "_")
            source = staging / service / "nv_config_manager_clients/generated" / package
            for path in source.rglob("*.py"):
                path.write_text(HEADER + path.read_text())
            target = destination / package
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        # Runtime-installed workflow plugins may add endpoints beyond this SDK.
        # For built-ins, generate a dispatch table to the typed operations rather
        # than duplicating their paths and request models in the wrapper.
        spec = json.loads((root / "docs/api-specs/temporal.openapi.json").read_text())
        api = destination / "temporal/api/workflow_api.py"
        tree = ast.parse(api.read_text())
        methods = {
            node.name: node
            for cls in tree.body
            if isinstance(cls, ast.ClassDef)
            for node in cls.body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        rows = []
        for path, item in spec["paths"].items():
            operation = item.get("post", {})
            if "_endpoint_" not in operation.get("operationId", ""):
                continue
            name = "_".join(filter(None, operation["operationId"].lower().split("_")))
            method = methods[name + "_without_preload_content"]
            argument = method.args.args[1]
            model = ast.unparse(argument.annotation)
            rows.append(
                f"    {path.removeprefix('/v1/workflow')!r}: ({name!r}, {argument.arg!r}, models.{model}),"
            )
        (destination / "workflow_starts.py").write_text(
            HEADER
            + '\n"""Generated workflow-start dispatch; do not edit."""\n\n'
            + "from nv_config_manager_clients.generated.temporal import models\n\n"
            + "WORKFLOW_STARTS = {\n"
            + "\n".join(rows)
            + "\n}\n"
        )


if __name__ == "__main__":
    main()
