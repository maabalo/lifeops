"""
LifeOps request/response models (Pydantic v2). Sizes and formats are checked here, before any route runs.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIES = ("bills", "warranty", "renewal", "home", "health", "vehicle", "other")
Category = Literal["bills", "warranty", "renewal", "home", "health", "vehicle", "other"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class SetupIn(Strict):
    """First account (only while there are none, only from this computer)."""

    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]{3,32}$")  # e.g. alex
    password: str = Field(min_length=12, max_length=200)


class CodeIn(Strict):
    """The second step of logging in: a 6-digit code, or a recovery code like k7m2-q9xa."""

    ticket: str = Field(min_length=20, max_length=100)
    code: str = Field(min_length=6, max_length=20)


class PasswordIn(Strict):
    password: str = Field(min_length=1, max_length=200)


class TwoStepConfirmIn(Strict):
    code: str = Field(min_length=6, max_length=10)


class TwoStepOffIn(Strict):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=20)


class InboxIn(Strict):
    content: str = Field(min_length=1, max_length=5000)


class InboxAction(Strict):
    version: int = Field(ge=1)


class TaskIn(Strict):
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=5000)
    category: Category = "other"
    due_date: date | None = None


class TaskFromInbox(Strict):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    category: Category = "other"
    due_date: date | None = None


class TaskPatch(Strict):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    details: str | None = Field(default=None, max_length=5000)
    category: Category | None = None
    due_date: date | None = None
    clear_due_date: bool = False


class TaskStatusIn(Strict):
    version: int = Field(ge=1)
    action: Literal["complete", "reopen", "cancel"]


class SnoozeIn(Strict):
    version: int = Field(ge=1)
    until: date


class SettingsIn(Strict):
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    reminder_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    extractor: Literal["mock", "claude", "local"] | None = None
