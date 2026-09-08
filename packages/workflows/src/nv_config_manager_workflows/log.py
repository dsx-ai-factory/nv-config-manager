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
"""Category-tagged loggers, built on the standard library alone.

The host service labels every record with a ``category`` field that log
filters and dashboards select on. This package cannot import the service's
logging module, so it attaches the same field through a plain
:class:`logging.LoggerAdapter`: the record reaches the host's handlers carrying
``category`` exactly as a service-side logger would.

``WORKFLOW_LOG_CATEGORY`` intentionally duplicates the host's
``LogCategory.TEMPORAL_WORKFLOW``. It is a label consumed by dashboards rather
than a type anything compares by identity, so a second definition is safe; a
test in the host asserts the two agree, which is what keeps them from drifting.
"""

from __future__ import annotations

import logging

WORKFLOW_LOG_CATEGORY = "temporal.workflow"


def get_workflow_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """Return a logger whose records carry the workflow log category.

    Args:
        name: Logger name, conventionally the calling module's ``__name__``.

    Returns:
        An adapter that stamps ``category`` on every record and merges, rather
        than replaces, any ``extra`` a call site supplies.
    """
    return logging.LoggerAdapter(
        logging.getLogger(name),
        extra={"category": WORKFLOW_LOG_CATEGORY},
        merge_extra=True,
    )
