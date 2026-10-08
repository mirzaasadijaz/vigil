"""
False-detection control layer.

One stage in the perception writer that every module's event passes through before it
becomes an alert. It can annotate an event with a severity, or decline to write it — it
never changes what a module computes, and with `fd_enabled=false` the writer path is
identical to what it was before this package existed.

See `false-detection-plan.md` for the design and `false-detection-test-cases.md` for the
manual verification procedure.
"""

from backend.services.false_detection.config import (
    DEFAULT_SEVERITY_MAP,
    FALLBACK_SEVERITY,
    SEVERITY_ORDER,
    FDConfig,
)
from backend.services.false_detection.engine import (
    ACCEPT,
    DEMOTE,
    FILTER,
    RULES,
    SUPPRESS,
    FalseDetectionFilter,
    Judgement,
)
from backend.services.false_detection.logging import LOG, RULE_FILES

__all__ = [
    "FDConfig", "SEVERITY_ORDER", "DEFAULT_SEVERITY_MAP", "FALLBACK_SEVERITY",
    "FalseDetectionFilter", "Judgement", "FILTER", "RULES",
    "ACCEPT", "SUPPRESS", "DEMOTE", "LOG", "RULE_FILES",
]
