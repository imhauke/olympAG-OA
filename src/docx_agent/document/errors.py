"""Structured edit errors. Every refusal carries a machine-readable `code` and a message
written for the LLM, so the agent can recover (pick another block, add context, ask the user)."""

from __future__ import annotations

from typing import Any


class EditError(ValueError):
    """An edit was refused. Nothing was changed."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}
