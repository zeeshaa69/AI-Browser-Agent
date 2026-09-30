"""Exception hierarchy shared by all components."""

from __future__ import annotations

from enum import Enum


class ErrorKind(str, Enum):
    """Coarse failure categories used to pick a recovery strategy."""

    ELEMENT_NOT_FOUND = "element_not_found"
    INTERCEPTED = "intercepted"
    TIMEOUT = "timeout"
    NAVIGATION = "navigation"
    VERIFICATION = "verification"
    POLICY = "policy"
    LIMIT = "limit"
    CANCELLED = "cancelled"
    OTHER = "other"


class AgentError(Exception):
    kind: ErrorKind = ErrorKind.OTHER

    def __init__(self, message: str, kind: ErrorKind | None = None):
        super().__init__(message)
        if kind is not None:
            self.kind = kind


class PlanningError(AgentError):
    """The goal could not be turned into a valid plan."""


class SafetyViolation(AgentError):
    """An action or URL is forbidden by the safety policy. Never retried."""

    kind = ErrorKind.POLICY


class ConfirmationDenied(SafetyViolation):
    """A sensitive action required confirmation and it was not given."""


class LimitExceeded(AgentError):
    """Maximum actions or overall task timeout reached."""

    kind = ErrorKind.LIMIT


class Cancelled(AgentError):
    kind = ErrorKind.CANCELLED


class ControllerError(AgentError):
    """A browser operation failed."""


class ElementNotFound(ControllerError):
    kind = ErrorKind.ELEMENT_NOT_FOUND


class ElementIntercepted(ControllerError):
    kind = ErrorKind.INTERCEPTED


class ActionTimeout(ControllerError):
    kind = ErrorKind.TIMEOUT


class NavigationError(ControllerError):
    kind = ErrorKind.NAVIGATION


class VerificationFailed(AgentError):
    kind = ErrorKind.VERIFICATION
