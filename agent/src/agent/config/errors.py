"""Config validation errors."""

from __future__ import annotations


class ConfigValidationError(ValueError):
    """Raised when raw config input fails validation.

    The message identifies the offending field and the reason.
    """
