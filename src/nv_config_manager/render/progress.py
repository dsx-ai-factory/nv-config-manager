# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Progress that render work reports to the consumer handling its message.

A consumer can only tell a slow handler from a stuck one if the work itself says
when it moves forward, so long-running steps call ``note_progress()`` as they go.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar


class HandlerProgress:
    """Count of steps completed by the handler of one message."""

    def __init__(self) -> None:
        """Start with no steps completed."""
        self.steps = 0


_current: ContextVar[HandlerProgress | None] = ContextVar("render_handler_progress", default=None)


def note_progress() -> None:
    """Record that the message being handled moved forward, if one is being tracked."""
    progress = _current.get()
    if progress is not None:
        progress.steps += 1


@contextmanager
def track_progress() -> Generator[HandlerProgress]:
    """Collect progress noted by this task and any tasks it starts."""
    progress = HandlerProgress()
    token = _current.set(progress)
    try:
        yield progress
    finally:
        _current.reset(token)
