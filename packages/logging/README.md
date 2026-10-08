# NVIDIA Config Manager Logging

Reusable structured logging for NVIDIA Config Manager services and libraries.
The package provides category-tagged, newline-safe loggers and optional
process-wide JSON/text logging configuration without depending on the core
application package.

```python
from nv_config_manager_logging import LogCategory, get_logger

logger = get_logger(__name__, category=LogCategory.NATS)
```
