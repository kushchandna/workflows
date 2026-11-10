"""Schemas for the JSON files exchanged with harnesses."""

from typing import Literal

from pydantic import BaseModel

from workflows.code_review.models import Comment, Target, ThreadAction


class Message(BaseModel):
    author: str
    body: str


class Thread(BaseModel):
    id: str
    target: Target
    state: Literal["open", "resolved"]
    messages: list[Message]


class PerFileResult(BaseModel):
    path: str
    summary: str
    comments: list[Comment] = []
    thread_actions: list[ThreadAction] = []


class StepResult(BaseModel):
    comments: list[Comment] = []
    thread_actions: list[ThreadAction] = []


class ConsolidationResult(BaseModel):
    status: Literal["approved", "changes_requested", "inconclusive"]
    comments: list[Comment] = []
    thread_actions: list[ThreadAction] = []
