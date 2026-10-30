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
"""Tests for the SPIFFE credential health metrics collector."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from prometheus_client import CollectorRegistry, generate_latest

import nv_config_manager.common.metrics as metrics_mod
from nv_config_manager.common.auth import AuthConfig, SpiffeConfig
from nv_config_manager.common.metrics import (
    SpiffeCredentialCollector,
    install_spiffe_bundle_metrics,
)

_BUNDLE = "nv_config_manager_spiffe_trust_bundle"
_SVID = "nv_config_manager_spiffe_jwt_svid"


@pytest.fixture(autouse=True)
def _reset_collector():
    """Reset the module-level registration guard between tests."""
    metrics_mod._collector = None
    yield
    metrics_mod._collector = None


def _use_spiffe_config(monkeypatch: pytest.MonkeyPatch, spiffe: SpiffeConfig | None) -> None:
    """Force ``metrics.collect()`` to see the given SPIFFE config."""
    monkeypatch.setattr(
        metrics_mod,
        "load_auth_config",
        lambda *a, **k: AuthConfig(spiffe=spiffe),
    )


def _samples(collector: SpiffeCredentialCollector) -> dict[str, float]:
    """Flatten a collect() run into a ``{metric_name: value}`` map."""
    out: dict[str, float] = {}
    for family in collector.collect():
        for sample in family.samples:
            out[sample.name] = sample.value
    return out


def _sample_labels(collector: SpiffeCredentialCollector, name: str) -> dict[str, str]:
    """Return the labels of the (single) sample named ``name``."""
    for family in collector.collect():
        for sample in family.samples:
            if sample.name == name:
                return dict(sample.labels)
    raise AssertionError(f"no sample named {name!r}")


def _raw_jwks(num_keys: int = 2) -> str:
    keys = [{"kty": "RSA", "kid": f"k{i}", "n": "abc", "e": "AQAB"} for i in range(num_keys)]
    return json.dumps({"keys": keys})


def _teleport_bundle(domain: str = "example.org", num_keys: int = 3) -> str:
    inner = json.dumps({"keys": [{"kty": "RSA", "kid": f"k{i}"} for i in range(num_keys)]}).encode()
    return json.dumps({domain: base64.b64encode(inner).decode()})


# ── No-op cases ───────────────────────────────────────────────────────────


def test_no_metrics_when_spiffe_unconfigured(monkeypatch):
    _use_spiffe_config(monkeypatch, None)
    assert _samples(SpiffeCredentialCollector()) == {}


def test_no_bundle_metrics_for_http_jwks(monkeypatch):
    spiffe = SpiffeConfig(jwks_uri="https://spire:8443/keys", audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)
    assert _samples(SpiffeCredentialCollector()) == {}


# ── Trust bundle (inbound validation health) ──────────────────────────────


def test_raw_jwks_bundle_metrics(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_raw_jwks(num_keys=2))
    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert samples[f"{_BUNDLE}_readable"] == 1.0
    assert samples[f"{_BUNDLE}_keys"] == 2.0
    # No x5c chains -> no expiry metric; no jwt_svid_path -> no SVID metrics.
    assert f"{_BUNDLE}_earliest_cert_expiry_timestamp_seconds" not in samples
    assert f"{_SVID}_readable" not in samples
    # The misleading bundle-age metric must not be emitted.
    assert f"{_BUNDLE}_age_seconds" not in samples


def test_teleport_wrapped_bundle_key_count(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_teleport_bundle(num_keys=3))
    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert samples[f"{_BUNDLE}_readable"] == 1.0
    assert samples[f"{_BUNDLE}_keys"] == 3.0


def test_missing_bundle_file_reports_unreadable(monkeypatch, tmp_path):
    spiffe = SpiffeConfig(jwks_uri=str(tmp_path / "absent.json"), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert samples[f"{_BUNDLE}_readable"] == 0.0
    assert f"{_BUNDLE}_keys" not in samples


def test_malformed_bundle_reports_unreadable(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text("not json {")
    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert samples[f"{_BUNDLE}_readable"] == 0.0


def _make_jwks_with_cert(not_after: datetime) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    x5c = base64.b64encode(cert.public_bytes(encoding=serialization.Encoding.DER)).decode()
    return json.dumps({"keys": [{"kty": "RSA", "kid": "k0", "x5c": [x5c]}]})


def test_earliest_cert_expiry_emitted(monkeypatch, tmp_path):
    not_after = datetime.now(UTC) + timedelta(days=30)
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_make_jwks_with_cert(not_after))
    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    emitted = samples[f"{_BUNDLE}_earliest_cert_expiry_timestamp_seconds"]
    assert emitted == pytest.approx(not_after.timestamp(), abs=1.0)


# ── JWT-SVID (outbound credential freshness) ──────────────────────────────


def _write_svid(path, exp: datetime, sub: str = "spiffe://example.org/ns/x/sa/y") -> None:
    token = pyjwt.encode(
        {"sub": sub, "exp": int(exp.timestamp())},
        "x" * 32,
        algorithm="HS256",
    )
    path.write_text(token)


def test_jwt_svid_expiry_emitted(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_raw_jwks(num_keys=1))
    svid = tmp_path / "jwt-svid"
    exp = datetime.now(UTC) + timedelta(minutes=30)
    _write_svid(svid, exp, sub="spiffe://example.org/ns/x/sa/y")

    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"], jwt_svid_path=str(svid))
    _use_spiffe_config(monkeypatch, spiffe)

    collector = SpiffeCredentialCollector()
    samples = _samples(collector)
    assert samples[f"{_SVID}_readable"] == 1.0
    assert samples[f"{_SVID}_expiry_timestamp_seconds"] == pytest.approx(exp.timestamp(), abs=1.0)
    # trust_domain label carries only the domain, never the workload path.
    assert _sample_labels(collector, f"{_SVID}_expiry_timestamp_seconds") == {
        "trust_domain": "example.org"
    }


def test_jwt_svid_trust_domain_unknown_for_malformed_sub(monkeypatch, tmp_path):
    svid = tmp_path / "jwt-svid"
    exp = datetime.now(UTC) + timedelta(minutes=30)
    _write_svid(svid, exp, sub="not-a-spiffe-id")
    spiffe = SpiffeConfig(
        jwks_uri="https://spire:8443/keys", audiences=["spiffe://td"], jwt_svid_path=str(svid)
    )
    _use_spiffe_config(monkeypatch, spiffe)

    labels = _sample_labels(SpiffeCredentialCollector(), f"{_SVID}_expiry_timestamp_seconds")
    assert labels == {"trust_domain": "unknown"}


def test_jwt_svid_unreadable(monkeypatch, tmp_path):
    svid = tmp_path / "jwt-svid"
    svid.write_text("not-a-jwt")
    spiffe = SpiffeConfig(
        jwks_uri="https://spire:8443/keys", audiences=["spiffe://td"], jwt_svid_path=str(svid)
    )
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert samples[f"{_SVID}_readable"] == 0.0
    assert f"{_SVID}_expiry_timestamp_seconds" not in samples


def test_jwt_svid_missing_file(monkeypatch, tmp_path):
    spiffe = SpiffeConfig(
        jwks_uri="https://spire:8443/keys",
        audiences=["spiffe://td"],
        jwt_svid_path=str(tmp_path / "absent"),
    )
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert samples[f"{_SVID}_readable"] == 0.0


def test_no_svid_metrics_when_path_unset(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_raw_jwks(num_keys=1))
    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    samples = _samples(SpiffeCredentialCollector())
    assert f"{_SVID}_readable" not in samples
    assert f"{_SVID}_expiry_timestamp_seconds" not in samples


# ── Registration ──────────────────────────────────────────────────────────


def test_install_is_idempotent_and_scrapeable(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_raw_jwks(num_keys=1))
    spiffe = SpiffeConfig(jwks_uri=str(bundle), audiences=["spiffe://td"])
    _use_spiffe_config(monkeypatch, spiffe)

    registry = CollectorRegistry()
    install_spiffe_bundle_metrics(registry)
    first = metrics_mod._collector
    install_spiffe_bundle_metrics(registry)
    assert metrics_mod._collector is first  # not re-registered

    output = generate_latest(registry).decode()
    assert f"{_BUNDLE}_readable" in output
    assert f"{_BUNDLE}_keys" in output
