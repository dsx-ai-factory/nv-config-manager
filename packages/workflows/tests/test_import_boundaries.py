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
"""Static package-isolation checks for reusable workflow code."""

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "nv_config_manager_workflows"
_CLIENT_ROOT = _PACKAGE_ROOT / "clients"
_ACTIVITIES_ROOT = _PACKAGE_ROOT / "activities"
_DCIM_ROOT = _ACTIVITIES_ROOT / "dcim"
_IB_PKEY_ROOT = _ACTIVITIES_ROOT / "ib_pkey"
_JUNIPER_CLIENT_PATH = Path("clients/device/juniper.py")
_CORE_BOUNDARY_PATHS = (
    _ACTIVITIES_ROOT / "builtin.py",
    *(_ACTIVITIES_ROOT / "config").glob("*.py"),
    *(_ACTIVITIES_ROOT / "hello_world").glob("*.py"),
    *(_ACTIVITIES_ROOT / "nats").glob("*.py"),
    *(_ACTIVITIES_ROOT / "slack").glob("*.py"),
    _PACKAGE_ROOT / "tech_support.py",
)
_IB_PKEY_DCIM_BOUNDARY_PATHS = (
    _PACKAGE_ROOT / "runtime.py",
    _PACKAGE_ROOT / "dcim_session.py",
    _PACKAGE_ROOT / "mixins" / "ib_pkey.py",
    *_DCIM_ROOT.glob("*.py"),
    *(
        _IB_PKEY_ROOT / name
        for name in ("dcim_activities.py", "models.py", "normalization.py", "resolution.py")
    ),
)
_DCIM_DEVICE_INFINIBAND_ACTIVITY_PATHS = (
    *_DCIM_ROOT.glob("*.py"),
    *(_ACTIVITIES_ROOT / "device").glob("*.py"),
    *(_ACTIVITIES_ROOT / "ufm").glob("*.py"),
    *(_ACTIVITIES_ROOT / "ib_pkey").glob("*.py"),
    *(_ACTIVITIES_ROOT / "ib_guid_discovery").glob("*.py"),
)
_DEPLOYMENT_ACTIVITY_PATHS = (
    *(_ACTIVITIES_ROOT / "backup").glob("*.py"),
    *(_ACTIVITIES_ROOT / "deploy").glob("*.py"),
    *(_ACTIVITIES_ROOT / "render").glob("*.py"),
    *(_ACTIVITIES_ROOT / "os").glob("*.py"),
    *(_ACTIVITIES_ROOT / "nvlinkswitch_firmware").glob("*.py"),
)
_DEVICE_OPERATION_ACTIVITY_PATHS = (
    *(_ACTIVITIES_ROOT / "cable_validation").glob("*.py"),
    *(_ACTIVITIES_ROOT / "hardware_validation").glob("*.py"),
    *(_ACTIVITIES_ROOT / "device_password_rotation").glob("*.py"),
    *(_ACTIVITIES_ROOT / "bmc").glob("*.py"),
)
_DIAGNOSTICS_ACTIVITY_PATHS = (
    *(_ACTIVITIES_ROOT / "diagnostics").glob("*.py"),
    *(_ACTIVITIES_ROOT / "ticketing").glob("*.py"),
)
_ALLOWED_IB_PKEY_DCIM_SDK_MODULES = {
    "nv_config_manager_dcim.api",
    "nv_config_manager_dcim.errors",
    "nv_config_manager_dcim.models",
    "nv_config_manager_dcim.workflow_models",
}
_FORBIDDEN_CONFIGURATION_CALLS = {
    "ConfigParser",
    "getenv",
    "load_config",
    "open",
    "read_bytes",
    "read_text",
}
_RUNTIME_PROVIDER_CONSTRUCTORS = {
    "NetworkConnection",
    "UFMClient",
    "create_dcim_client",
}
_DEPLOYMENT_RUNTIME_PROVIDER_CONSTRUCTORS = {
    "ConfigStoreClient",
    "NetworkConnection",
    "RenderClient",
    "ZTPClient",
    "config_store_client",
    "create_dcim_client",
    "get_storage_client",
    "render_client",
    "ztp_client",
}
_DEVICE_OPERATION_RUNTIME_PROVIDER_CONSTRUCTORS = {
    "NetworkConnection",
    "RedfishConnection",
    "create_dcim_client",
    "get_config_manager_connection",
    "get_default_connection",
    "load_config",
}
_DIAGNOSTICS_RUNTIME_PROVIDER_CONSTRUCTORS = {
    "ConfigParser",
    "JiraTicketingProvider",
    "NetworkConnection",
    "RedisClient",
    "load_config",
    "ticketing_client_settings",
}


def _is_service_module(module: str) -> bool:
    """Return whether an import targets the service package rather than this package."""
    return module == "nv_config_manager" or module.startswith("nv_config_manager.")


def _is_concrete_dcim_provider_module(module: str) -> bool:
    """Return whether an import targets an installed provider implementation."""
    return module.startswith("nv_config_manager_dcim_")


def _forbidden_configuration_call(node: ast.Call, relative_path: Path) -> str | None:
    """Return the forbidden call name, allowing only PyEZ's connection open."""
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
        if (
            name == "open"
            and relative_path == _JUNIPER_CLIENT_PATH
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "device"
        ):
            # PyEZ Device.open() establishes a NETCONF connection.
            return None
    else:
        return None
    return name if name in _FORBIDDEN_CONFIGURATION_CALLS else None


def test_workflows_package_has_no_service_configuration_dependencies() -> None:
    """Reusable workflows must not depend on service-owned configuration access."""
    violations: list[str] = []

    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif node.module is not None:
                    modules = [node.module]
                else:
                    modules = []

                for module in modules:
                    if (
                        _is_service_module(module)
                        or module == "configparser"
                        or module.startswith("configparser.")
                        or _is_concrete_dcim_provider_module(module)
                    ):
                        violations.append(f"{relative_path}:{node.lineno}: {module}")

            if isinstance(node, ast.Call):
                name = _forbidden_configuration_call(node, relative_path)
                if name is not None:
                    violations.append(f"{relative_path}:{node.lineno}: {name}")

    assert violations == []


def test_ib_pkey_dcim_slice_uses_only_provider_neutral_dcim_contracts() -> None:
    """The PKey DCIM slice must not select configuration or provider implementations."""
    violations: list[str] = []

    for path in sorted(_IB_PKEY_DCIM_BOUNDARY_PATHS):
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                lineno = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
                lineno = node.lineno
            else:
                modules = []
                lineno = None

            for module in modules:
                if (
                    _is_service_module(module)
                    or module == "configparser"
                    or module.startswith("configparser.")
                    or _is_concrete_dcim_provider_module(module)
                    or (
                        module.startswith("nv_config_manager_dcim")
                        and module not in _ALLOWED_IB_PKEY_DCIM_SDK_MODULES
                    )
                ):
                    assert lineno is not None
                    violations.append(f"{relative_path}:{lineno}: {module}")

            if isinstance(node, ast.Call):
                name = _forbidden_configuration_call(node, relative_path)
                if name is not None:
                    violations.append(f"{relative_path}:{node.lineno}: {name}")
    assert violations == []


def test_modules_have_no_service_or_configuration_dependencies() -> None:
    """Every core module remains importable without the service application."""
    violations: list[str] = []

    for path in _CORE_BOUNDARY_PATHS:
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                lineno = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
                lineno = node.lineno
            else:
                modules = []
                lineno = None

            for module in modules:
                if _is_service_module(module) or _is_concrete_dcim_provider_module(module):
                    assert lineno is not None
                    violations.append(f"{relative_path}:{lineno}: {module}")

            if isinstance(node, ast.Call):
                name = _forbidden_configuration_call(node, relative_path)
                if name is not None:
                    violations.append(f"{relative_path}:{node.lineno}: {name}")

    assert violations == []


def test_dcim_device_infiniband_activities_do_not_construct_runtime_providers() -> None:
    """DCIM, device, and InfiniBand activities obtain clients from runtime providers."""
    violations: list[str] = []

    for path in sorted(_DCIM_DEVICE_INFINIBAND_ACTIVITY_PATHS):
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name in _RUNTIME_PROVIDER_CONSTRUCTORS:
                violations.append(f"{relative_path}:{node.lineno}: {name}")

    assert violations == []


def test_deployment_modules_have_no_service_dependencies() -> None:
    """Deployment modules remain service-independent after activity extraction."""
    violations: list[str] = []

    for path in _DEPLOYMENT_ACTIVITY_PATHS:
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                lineno = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
                lineno = node.lineno
            else:
                modules = []
                lineno = None

            for module in modules:
                if _is_service_module(module) or _is_concrete_dcim_provider_module(module):
                    assert lineno is not None
                    violations.append(f"{relative_path}:{lineno}: {module}")

            if isinstance(node, ast.Call):
                name = _forbidden_configuration_call(node, relative_path)
                if name is not None:
                    violations.append(f"{relative_path}:{node.lineno}: {name}")
                if isinstance(node.func, ast.Name):
                    constructor = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    constructor = node.func.attr
                else:
                    constructor = None
                if constructor in _DEPLOYMENT_RUNTIME_PROVIDER_CONSTRUCTORS:
                    violations.append(f"{relative_path}:{node.lineno}: {constructor}")

    assert violations == []


def test_device_operation_modules_use_only_package_runtime_boundaries() -> None:
    """Device-operation modules avoid service imports and direct provider construction."""
    violations: list[str] = []

    for path in _DEVICE_OPERATION_ACTIVITY_PATHS:
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                lineno = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
                lineno = node.lineno
            else:
                modules = []
                lineno = None

            for module in modules:
                if _is_service_module(module) or _is_concrete_dcim_provider_module(module):
                    assert lineno is not None
                    violations.append(f"{relative_path}:{lineno}: {module}")

            if isinstance(node, ast.Call):
                name = _forbidden_configuration_call(node, relative_path)
                if name is not None:
                    violations.append(f"{relative_path}:{node.lineno}: {name}")
                if isinstance(node.func, ast.Name):
                    constructor = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    constructor = node.func.attr
                else:
                    constructor = None
                if constructor in _DEVICE_OPERATION_RUNTIME_PROVIDER_CONSTRUCTORS:
                    violations.append(f"{relative_path}:{node.lineno}: {constructor}")

    assert violations == []


def test_diagnostics_modules_use_only_package_runtime_boundaries() -> None:
    """Diagnostics and ticketing avoid service configuration and direct clients."""
    violations: list[str] = []

    for path in _DIAGNOSTICS_ACTIVITY_PATHS:
        relative_path = path.relative_to(_PACKAGE_ROOT)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                lineno = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
                lineno = node.lineno
            else:
                modules = []
                lineno = None

            for module in modules:
                if _is_service_module(module) or _is_concrete_dcim_provider_module(module):
                    assert lineno is not None
                    violations.append(f"{relative_path}:{lineno}: {module}")

            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name in _DIAGNOSTICS_RUNTIME_PROVIDER_CONSTRUCTORS:
                violations.append(f"{relative_path}:{node.lineno}: {name}")

    assert violations == []


def test_client_package_initializers_do_not_import_implementations() -> None:
    """Client package imports must not eagerly load implementations or SDKs."""
    violations: list[str] = []

    for path in sorted(_CLIENT_ROOT.rglob("__init__.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                violations.append(f"{path.relative_to(_PACKAGE_ROOT)}:{node.lineno}")

    assert violations == []


def test_configuration_boundary_allows_only_pyez_device_open() -> None:
    """Only the PyEZ connection call may use the otherwise forbidden open name."""

    def parse_call(expression: str) -> ast.Call:
        node = ast.parse(expression, mode="eval").body
        assert isinstance(node, ast.Call)
        return node

    pyez_open = parse_call("device.open()")
    assert _forbidden_configuration_call(pyez_open, _JUNIPER_CLIENT_PATH) is None
    assert _forbidden_configuration_call(pyez_open, Path("other.py")) == "open"

    filesystem_opens = (
        "open(path)",
        "Path(path).open()",
        "path.open()",
        "os.open(path, flags)",
        "io.open(path)",
        "codecs.open(path)",
        "builtins.open(path)",
        "filesystem.open(path)",
    )
    for expression in filesystem_opens:
        call = parse_call(expression)
        assert _forbidden_configuration_call(call, _JUNIPER_CLIENT_PATH) == "open", expression
