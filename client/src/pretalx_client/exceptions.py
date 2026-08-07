"""Custom exceptions for the pretalx API client."""

from __future__ import annotations


class PretalxAPIError(Exception):
    """Raised when the pretalx API returns an error response."""

    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self.payload = payload
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if isinstance(self.payload, dict):
            parts = []
            for field, errors in self.payload.items():
                if isinstance(errors, list):
                    errors = "; ".join(str(e) for e in errors)
                parts.append(f"{field}: {errors}")
            if parts:
                return f"HTTP {self.status_code}: " + "; ".join(parts)
        return f"HTTP {self.status_code}: {self.payload}"


class ConfigError(Exception):
    """Raised when the client configuration is incomplete or invalid."""
