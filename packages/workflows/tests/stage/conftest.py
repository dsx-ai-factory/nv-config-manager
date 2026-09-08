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
"""Stand-ins for the Temporal runtime the stage framework reads.

The stage state machine only needs ``workflow.time()`` for history entries and
``workflow.patched()`` for the search-attribute gate, so these tests drive it
directly instead of starting a workflow environment. That keeps the package's
standalone test job free of the test-server download.
"""

from collections.abc import Iterator

import pytest
from temporalio import workflow


class FakeClock:
    """A monotonic stand-in for ``workflow.time()`` under test control."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClock]:
    """Replace ``workflow.time`` with a clock the test can advance."""
    fake = FakeClock()
    monkeypatch.setattr(workflow, "time", fake)
    yield fake


@pytest.fixture
def patched_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report every Temporal patch as applied, as a fresh history would."""
    monkeypatch.setattr(workflow, "patched", lambda _patch_id: True)


@pytest.fixture
def legacy_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report every Temporal patch as absent, as a pre-patch history would."""
    monkeypatch.setattr(workflow, "patched", lambda _patch_id: False)


@pytest.fixture
def upserted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, list[object]]]:
    """Collect the search attributes the mixin upserts."""
    calls: list[dict[str, list[object]]] = []
    monkeypatch.setattr(workflow, "upsert_search_attributes", calls.append)
    return calls
