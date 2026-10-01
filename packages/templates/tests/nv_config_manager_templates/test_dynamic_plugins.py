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
"""Tests for plugins that are activated dynamically (a directory with a plugin.yaml manifest)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from nv_config_manager_templates.dynamic_plugins import (
    DYNAMIC_PLUGINS_DIR_ENV,
    load_dynamic_plugins,
)
from nv_config_manager_templates.render import Renderer

MANIFEST = """
apiVersion: nvconfigmanager.nvidia.com/v1alpha1
kind: TemplatePlugin
metadata:
  id: {plugin_id}
  displayVersion: "1.2.3"
compatibility:
  templateEngineAPI: "{api}"
templates:
  paths:
    - {package}/templates/
graphql:
  queries:
    {query_name}: graphql/extra.graphql
python:
  entrypoint: {package}.plugin
"""

PLUGIN_PY = """
from pathlib import Path


def get_custom_filters():
    return {{"shout_{package}": lambda value: str(value).upper() + "!"}}
"""


def make_plugin(
    root: Path,
    plugin_id: str,
    *,
    api: str = ">=1,<2",
    plugin_py: str | None = None,
    manifest: str | None = None,
) -> Path:
    """Create one plugin directory under ``root`` and return it."""
    package = plugin_id.replace("-", "_")
    plugin_dir = root / plugin_id
    (plugin_dir / package / "templates").mkdir(parents=True)
    (plugin_dir / "graphql").mkdir()
    (plugin_dir / package / "__init__.py").write_text("")
    (plugin_dir / package / "plugin.py").write_text(
        textwrap.dedent(plugin_py if plugin_py is not None else PLUGIN_PY).format(package=package)
    )
    (plugin_dir / package / "templates" / f"{package}.j2").write_text(
        "hello {{ name | shout_" + package + " }}"
    )
    (plugin_dir / "graphql" / "extra.graphql").write_text(
        "query($id: ID!) { device(id: $id) { name } }"
    )
    (plugin_dir / "plugin.yaml").write_text(
        manifest
        if manifest is not None
        else MANIFEST.format(
            plugin_id=plugin_id, api=api, package=package, query_name=f"{package}_data"
        )
    )
    return plugin_dir


@pytest.fixture
def plugins_root(tmp_path, monkeypatch):
    """A dynamic-plugins directory, wired into the environment like the Helm chart does."""
    root = tmp_path / "_dynamic"
    root.mkdir()
    monkeypatch.setenv(DYNAMIC_PLUGINS_DIR_ENV, str(root))
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    yield root
    sys.path[:] = path_before
    for name in set(sys.modules) - modules_before:
        del sys.modules[name]


def test_nothing_is_loaded_when_the_directory_is_not_configured(monkeypatch):
    monkeypatch.delenv(DYNAMIC_PLUGINS_DIR_ENV, raising=False)

    result = load_dynamic_plugins()

    assert result.plugins == [] and result.errors == []
    assert not any(p.get("dynamic") for p in Renderer().plugins)


def test_a_missing_directory_means_nothing_has_been_activated(tmp_path, monkeypatch):
    monkeypatch.setenv(DYNAMIC_PLUGINS_DIR_ENV, str(tmp_path / "does-not-exist"))

    assert load_dynamic_plugins().plugins == []


def test_a_dynamic_plugin_changes_what_the_renderer_can_do(plugins_root):
    make_plugin(plugins_root, "site-overrides")

    renderer = Renderer()

    assert [p["name"] for p in renderer.plugins if p.get("dynamic")] == ["dynamic:site-overrides"]
    # its template is found and its filter is applied
    assert (
        renderer.environment.get_template("site_overrides.j2").render(name="spine")
        == "hello SPINE!"
    )
    # its GraphQL query is registered as a provider-neutral data requirement (the Nautobot
    # provider understands a "graphql_query" parameter)
    requirement = renderer.plugin_data_requirements["site_overrides_data"]
    assert requirement.parameters == {
        "graphql_query": "query($id: ID!) { device(id: $id) { name } }"
    }
    assert renderer.dynamic_plugin_errors == []


def test_several_plugins_in_one_set_all_load(plugins_root):
    make_plugin(plugins_root, "plugin-a")
    make_plugin(plugins_root, "plugin-b")

    renderer = Renderer()

    assert [p["plugin_id"] for p in renderer.plugins if p.get("dynamic")] == [
        "plugin-a",
        "plugin-b",
    ]
    assert renderer.environment.get_template("plugin_a.j2").render(name="x") == "hello X!"
    assert renderer.environment.get_template("plugin_b.j2").render(name="y") == "hello Y!"


def test_an_incompatible_plugin_is_skipped_and_reported_without_affecting_others(plugins_root):
    make_plugin(plugins_root, "plugin-ok")
    make_plugin(plugins_root, "plugin-future", api=">=2")

    renderer = Renderer()

    assert [p["plugin_id"] for p in renderer.plugins if p.get("dynamic")] == ["plugin-ok"]
    assert len(renderer.dynamic_plugin_errors) == 1
    assert "plugin-future" in renderer.dynamic_plugin_errors[0]
    assert "requires template engine API" in renderer.dynamic_plugin_errors[0]


def test_a_plugin_that_fails_to_import_is_skipped_and_reported(plugins_root):
    make_plugin(plugins_root, "plugin-ok")
    make_plugin(plugins_root, "plugin-broken", plugin_py="raise RuntimeError('boom')\n")

    renderer = Renderer()  # must not raise

    assert [p["plugin_id"] for p in renderer.plugins if p.get("dynamic")] == ["plugin-ok"]
    assert any("plugin-broken" in e and "boom" in e for e in renderer.dynamic_plugin_errors)


@pytest.mark.parametrize(
    "bad_path",
    ["../../etc", "/etc", "templates/../../outside"],
    ids=["parent", "absolute", "embedded-parent"],
)
def test_template_paths_cannot_leave_the_plugin_directory(plugins_root, bad_path):
    good = MANIFEST.format(plugin_id="evil", api=">=1,<2", package="evil", query_name="q")
    make_plugin(plugins_root, "evil", manifest=good.replace("evil/templates/", bad_path))

    result = load_dynamic_plugins()

    assert result.plugins == []
    assert "points outside the plugin directory" in result.errors[0]


def test_graphql_files_cannot_leave_the_plugin_directory(plugins_root):
    good = MANIFEST.format(plugin_id="evil", api=">=1,<2", package="evil", query_name="q")
    make_plugin(
        plugins_root, "evil", manifest=good.replace("graphql/extra.graphql", "../../secret")
    )

    result = load_dynamic_plugins()

    assert result.plugins == []
    assert "points outside the plugin directory" in result.errors[0]


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("kind: Other\napiVersion: nvconfigmanager.nvidia.com/v1alpha1\n", "unsupported kind"),
        ("kind: TemplatePlugin\napiVersion: other/v1\n", "unsupported apiVersion"),
        ("[unterminated", "not valid YAML"),
        ("- just\n- a list\n", "must be a mapping"),
        (
            "kind: TemplatePlugin\n"
            "apiVersion: nvconfigmanager.nvidia.com/v1alpha1\n"
            "metadata: {id: x}\n",
            "missing required field compatibility.templateEngineAPI",
        ),
    ],
)
def test_malformed_manifests_are_skipped_with_a_clear_reason(plugins_root, manifest, message):
    make_plugin(plugins_root, "bad", manifest=manifest)

    result = load_dynamic_plugins()

    assert result.plugins == []
    assert message in result.errors[0]


def test_a_directory_without_a_manifest_is_not_a_plugin_and_is_not_an_error(plugins_root):
    (plugins_root / "just-a-folder").mkdir()

    result = load_dynamic_plugins()

    assert result.plugins == [] and result.errors == []


def test_installed_plugins_keep_precedence_over_dynamic_ones(plugins_root, monkeypatch):
    make_plugin(plugins_root, "site-overrides")
    static = {
        "name": "static",
        "module": None,
        "template_paths": [],
        "filters": {},
        "data_requirements": {},
    }
    monkeypatch.setattr(Renderer, "_discover_plugins", lambda self: [static])

    renderer = Renderer()

    assert [p["name"] for p in renderer.plugins] == ["static", "dynamic:site-overrides"]


def test_a_plugin_cannot_shadow_a_standard_library_module(plugins_root):
    # The plugin directory is appended to sys.path, never prepended.
    make_plugin(plugins_root, "site-overrides")

    Renderer()

    assert str(plugins_root / "site-overrides") in sys.path
    assert sys.path.index(str(plugins_root / "site-overrides")) > 0
