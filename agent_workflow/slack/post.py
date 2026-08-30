"""Small alert transport seam; console mode keeps local demos dependency-free."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.request import Request, urlopen


class AlertPoster(Protocol):
    def post_root(self, text: str) -> str | None: ...
    def post_thread(self, root_message_id: str | None, text: str) -> None: ...


@dataclass
class ConsolePoster:
    """Captures messages for tests and prints them for a terminal demo."""
    messages: list[tuple[str, str | None, str]] = field(default_factory=list)

    def post_root(self, text: str) -> str:
        message_id = f"local-{len(self.messages) + 1}"
        self.messages.append(("root", message_id, text))
        print(text)
        return message_id

    def post_thread(self, root_message_id: str | None, text: str) -> None:
        self.messages.append(("thread", root_message_id, text))
        print(text)


@dataclass
class WebhookPoster:
    """Slack incoming-webhook transport for production-like local demos.

    Incoming webhooks acknowledge with ``ok`` rather than a message timestamp, so they
    cannot supply the root ``thread_ts`` themselves.  ConsolePoster is the fully
    thread-capable local/demo transport; a future Slack app token can replace this seam
    when thread replies are enabled.
    """
    webhook_url: str
    timeout_seconds: float = 5.0

    def _post(self, payload: dict) -> None:
        request = Request(
            self.webhook_url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Slack webhook returned HTTP {response.status}")

    def post_root(self, text: str) -> None:
        self._post({"text": text})
        return None

    def post_thread(self, root_message_id: str | None, text: str) -> None:
        payload = {"text": text}
        if root_message_id:
            payload["thread_ts"] = root_message_id
        self._post(payload)
