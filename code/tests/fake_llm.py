"""A stand-in for the Anthropic client, for tests.

Mimics only what :mod:`audit.llm` uses: ``client.messages.create(**request)``
returning an object with ``content`` text blocks and a ``usage`` field. Keeping
it this small means a test exercises the gateway's own logic - caching, retry,
accounting - rather than a mock of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FakeTextBlock:
    """One response content block."""

    text: str
    type: str = "text"


@dataclass(frozen=True, slots=True)
class FakeUsage:
    """The response's token counts, as the real API reports them."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class FakeResponse:
    """One response."""

    content: Sequence[FakeTextBlock]
    usage: FakeUsage


@dataclass
class FakeMessages:
    """The ``client.messages`` namespace."""

    replies: list[str]
    requests: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 100
    output_tokens: int = 20
    responder: Callable[[Mapping[str, Any]], str] | None = None
    raises: bool = False

    def create(self, **request: Any) -> FakeResponse:
        """Return the next scripted reply, recording the request."""
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("fake transport failure")
        if self.responder is not None:
            text = self.responder(request)
        elif self.replies:
            text = self.replies.pop(0)
        else:
            text = "{}"
        return FakeResponse(
            content=[FakeTextBlock(text=text)],
            usage=FakeUsage(
                input_tokens=self.input_tokens, output_tokens=self.output_tokens
            ),
        )


@dataclass
class FakeClient:
    """A client whose ``messages.create`` returns scripted text."""

    messages: FakeMessages

    @classmethod
    def with_replies(cls, *replies: str, **kwargs: Any) -> FakeClient:
        """A client that returns each reply in turn."""
        return cls(messages=FakeMessages(replies=list(replies), **kwargs))

    @classmethod
    def with_responder(
        cls, responder: Callable[[Mapping[str, Any]], str], **kwargs: Any
    ) -> FakeClient:
        """A client that computes its reply from the request."""
        return cls(messages=FakeMessages(replies=[], responder=responder, **kwargs))

    @property
    def calls(self) -> int:
        """How many requests reached the client."""
        return len(self.messages.requests)
