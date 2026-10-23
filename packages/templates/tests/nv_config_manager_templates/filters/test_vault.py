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
"""Vault filter tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from passlib.hash import cisco_type7, md5_crypt, sha512_crypt

from nv_config_manager_templates.filters import FilterException, vault
from nv_config_manager_templates.filters.vault import encrypt, load_secret


class _FakeVault:
    """Stands in for hvac.Client, recording how it was built and what it read."""

    def __init__(self) -> None:
        self.client_kwargs: dict[str, object] | None = None
        self.reads: list[tuple[str, str]] = []
        self.secrets = SimpleNamespace(kv=SimpleNamespace(read_secret_version=self._read))

    def client(self, **kwargs: object) -> "_FakeVault":
        self.client_kwargs = kwargs
        return self

    def _read(self, path: str, mount_point: str) -> dict:
        self.reads.append((path, mount_point))
        return {"data": {"data": {"bgp_password_r1": "from-vault"}}}


@pytest.fixture
def fake_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeVault:
    """Route Vault lookups to a fake client, with a token in a temporary home."""
    for name in (
        "NV_CONFIG_MANAGER_SKIP_VAULT",
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH",
        "VAULT_ADDR",
        "VAULT_NAMESPACE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".vault-token").write_text("test-token", encoding="utf-8")
    fake = _FakeVault()
    monkeypatch.setattr(vault.hvac, "Client", fake.client)
    return fake


def test_load_secret_requires_vault_address(fake_vault: _FakeVault) -> None:
    """Without a Vault address or another secret source, the lookup fails closed."""
    with pytest.raises(FilterException, match="No Vault address configured"):
        load_secret("bgp_password_r1", site="TEST-SITE")
    assert fake_vault.client_kwargs is None


def test_load_secret_uses_configured_vault(
    fake_vault: _FakeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VAULT_ADDR and VAULT_NAMESPACE select the Vault that load_secret reads."""
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_NAMESPACE", "network")

    assert load_secret("bgp_password_r1", site="TEST-SITE") == "from-vault"
    assert fake_vault.client_kwargs == {
        "url": "https://vault.example.com",
        "verify": True,
        "namespace": "network",
        "token": "test-token",
    }
    assert fake_vault.reads == [("nvdc-net/kiwi/site/test-site/config_secrets", "secrets")]


def test_load_secret_vault_namespace_is_optional(
    fake_vault: _FakeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without VAULT_NAMESPACE the client sends no namespace."""
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:8200")

    assert load_secret("bgp_password_r1", region="AMER") == "from-vault"
    assert fake_vault.client_kwargs is not None
    assert fake_vault.client_kwargs["url"] == "http://127.0.0.1:8200"
    assert fake_vault.client_kwargs["namespace"] is None
    assert fake_vault.reads == [("nvdc-net/kiwi/region/amer/config_secrets", "secrets")]


def test_load_secret_skip_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping Vault returns the rendered path/key placeholder."""
    monkeypatch.setenv("NV_CONFIG_MANAGER_SKIP_VAULT", "1")
    monkeypatch.delenv("NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    assert (
        load_secret("bgp_password_r1", site="TEST-SITE")
        == "nvdc-net/kiwi/site/test-site/config_secrets:bgp_password_r1"
    )
    assert (
        load_secret("bgp_password_r1", region="AMER")
        == "nvdc-net/kiwi/region/amer/config_secrets:bgp_password_r1"
    )

    with pytest.raises(FilterException):
        load_secret("bgp_password_r1")


def test_load_secret_config_file(amend_config_read: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Vault agent config files can provide rendered secrets."""
    monkeypatch.delenv("NV_CONFIG_MANAGER_SKIP_VAULT", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", "/etc/nv-config-manager/secrets/config_secrets"
    )

    assert load_secret("bgp_password_r1", site="TEST-SITE") == "DUMMY"
    assert load_secret("root_password_r1", region="AMER") == "DUMMY"

    with pytest.raises(FilterException, match="Secret bogus not found for TEST-SITE."):
        load_secret("bogus", site="TEST-SITE")


def test_encrypt(amend_config_read: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Supported secret encryption algorithms produce verifiable hashes."""
    secret = "dummy"
    monkeypatch.delenv("NV_CONFIG_MANAGER_SKIP_VAULT", raising=False)
    monkeypatch.setenv(
        "NV_CONFIG_MANAGER_CONFIG_SECRET_PATH", "/etc/nv-config-manager/secrets/config_secrets"
    )

    sha512 = encrypt(secret, "sha512", site="TEST-SITE")
    assert (
        sha512 == "$6$H0QFj2rx$"
        "Zuk5WXJrQosw9bymN.mW1.6bza5btxFGEg6LVLLjiWmKhY35W5.xh7N3ZsbYClYqNKJdq."
        "Ruo1IY9M1wI/Sd.0"
    )
    assert sha512_crypt.verify(secret, sha512)

    md5 = encrypt(secret, "md5", site="TEST-SITE")
    assert md5 == "$1$H0QFj2rx$DtlLRZ2hQCJTffRceFvLt0"
    assert md5_crypt.verify(secret, md5)

    ct7 = encrypt(secret, "ciscot7", site="TEST-SITE")
    assert ct7 == "0000060B0942"
    assert cisco_type7.verify(secret, ct7)

    sha512_no_salt = encrypt(secret, "sha512")
    assert sha512_crypt.verify(secret, sha512_no_salt)
    assert sha512_no_salt != sha512

    with pytest.raises(FilterException):
        encrypt(secret, "sha256")


def test_encrypt_rejects_invalid_ciscot7_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    """ciscot7 salt parsing raises a template filter error."""
    monkeypatch.delenv("NV_CONFIG_MANAGER_SKIP_VAULT", raising=False)
    monkeypatch.setattr(vault, "load_secret", lambda *args, **kwargs: "not-an-int")

    with pytest.raises(FilterException, match="Invalid ciscot7 salt value"):
        vault.encrypt("secret", "ciscot7", site="test-site")
