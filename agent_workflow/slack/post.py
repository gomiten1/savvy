"""Small alert transport seam; console mode keeps local demos dependency-free."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError
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


@dataclass
class SlackAppPoster:
    """`chat.postMessage` transport with real threading (D29/D45).

    Needs a bot token with `chat:write` and the bot invited to the channel.
    `post_root` returns the message `ts`; `post_thread` passes it back as
    `thread_ts`, so the agent's diagnosis lands under the deterministic root
    alert instead of as a sibling message.
    """
    bot_token: str
    channel: str
    timeout_seconds: float = 5.0
    api_url: str = "https://slack.com/api/chat.postMessage"

    @classmethod
    def from_env(cls) -> "SlackAppPoster | None":
        token, channel = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("SLACK_CHANNEL")
        return cls(token, channel) if token and channel else None

    def _post(self, payload: dict) -> dict:
        request = Request(
            self.api_url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json; charset=utf-8",
                     "Authorization": f"Bearer {self.bot_token}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode())
        except HTTPError as error:  # 429 / 5xx — never let transport break the loop
            raise RuntimeError(f"Slack chat.postMessage HTTP {error.code}") from error
        if not body.get("ok"):
            raise RuntimeError(f"Slack chat.postMessage error: {body.get('error', 'unknown')}")
        return body

    def post_root(self, text: str) -> str | None:
        return self._post({"channel": self.channel, "text": text}).get("ts")

    def post_thread(self, root_message_id: str | None, text: str) -> None:
        payload = {"channel": self.channel, "text": text}
        if root_message_id:
            payload["thread_ts"] = root_message_id
        self._post(payload)


def poster_from_env() -> AlertPoster:
    """Pick the alert transport: Slack app (threaded) > webhook > console."""
    app = SlackAppPoster.from_env()
    if app is not None:
        return app
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        return WebhookPoster(webhook)
    return ConsolePoster()
