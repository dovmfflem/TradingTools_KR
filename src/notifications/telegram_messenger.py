from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import requests
except ModuleNotFoundError:
    requests = None  # type: ignore[assignment]


class TelegramMessageError(Exception):
    """Raised when a telegram message could not be sent after retries."""


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []

    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break

        result.append(ch)

    return "".join(result).rstrip()


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    """
    Minimal YAML mapping parser for nested key-value objects.
    Supports only mappings (no lists/multiline values).
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue

        line = _strip_comment(raw_line)
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[: indent + 1]:
            raise ValueError("tabs are not supported in YAML indentation")

        content = line.strip()
        if ":" not in content:
            continue

        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("invalid YAML indentation")

        current = stack[-1][1]

        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
        else:
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            current[key] = value

    return root


class TelegramMessenger:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        rate_limit_per_minute: int = 20,
        max_message_bytes: int = 4096,
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not token:
            raise ValueError("token is required")
        if not chat_id:
            raise ValueError("chat_id is required")

        self.token = token
        self.chat_id = str(chat_id)
        self.rate_limit_per_minute = rate_limit_per_minute
        self.max_message_bytes = max_message_bytes
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.timeout_seconds = timeout_seconds
        self._send_timestamps: deque[float] = deque()
        self._session = requests.Session() if requests is not None else None

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "TelegramMessenger":
        """Backward-compatible loader using legacy single-bot keys."""
        config = cls._load_config(file_path)
        token = config.get("telegram_token")
        chat_id = config.get("telegram_chat_id")

        if not token or not chat_id:
            raise ValueError(
                "Legacy format requires 'telegram_token' and 'telegram_chat_id'. "
                "For multi-bot setup use from_info_yaml_key(bot_key, file_path)."
            )

        return cls(token=str(token), chat_id=str(chat_id))

    @classmethod
    def from_info_yaml_key(
        cls,
        bot_key: str,
        file_path: str = "info.yaml",
    ) -> "TelegramMessenger":
        """
        Load a bot by key from info.yaml.

        Supported formats:
          1) telegram.bots.<bot_key>.token/chat_id
          2) <bot_key>_token with shared chat_id (or <bot_key>_chat_id)
        """
        if not bot_key:
            raise ValueError("bot_key is required")

        config = cls._load_config(file_path)

        telegram = config.get("telegram")
        bots = telegram.get("bots") if isinstance(telegram, dict) else None
        if not isinstance(bots, dict):
            alt_bots = config.get("telegram_bots")
            bots = alt_bots if isinstance(alt_bots, dict) else None

        if isinstance(bots, dict):
            bot_config = bots.get(bot_key)
            if not isinstance(bot_config, dict):
                available = ", ".join(sorted(bots.keys())) if bots else "none"
                raise KeyError(f"bot_key '{bot_key}' not found. available: {available}")

            token = bot_config.get("token")
            chat_id = bot_config.get("chat_id")
            if not token or not chat_id:
                raise ValueError(
                    f"bot '{bot_key}' must include 'token' and 'chat_id' in info.yaml"
                )
            return cls(token=str(token), chat_id=str(chat_id))

        token = config.get(f"{bot_key}_token")
        chat_id = (
            config.get(f"{bot_key}_chat_id")
            or config.get("chat_id")
            or config.get("telegram_chat_id")
        )

        if not token or not chat_id:
            raise ValueError(
                f"bot_key '{bot_key}' not configured. "
                "Expected either telegram.bots mapping or '<bot_key>_token' + chat_id"
            )

        return cls(token=str(token), chat_id=str(chat_id))

    @staticmethod
    def _load_config(file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        return _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> tuple[int, str, dict[str, Any]]:
        if self._session is not None:
            response = self._session.post(
                endpoint,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response_text = response.text
            try:
                response_json = response.json()
            except ValueError:
                response_json = {}
            return response.status_code, response_text, response_json

        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
                response_text = response.read().decode("utf-8", errors="replace")
                status_code = response.getcode() or 0
        except urllib_error.HTTPError as exc:
            status_code = exc.code
            response_text = exc.read().decode("utf-8", errors="replace")

        try:
            response_json = json.loads(response_text)
            if not isinstance(response_json, dict):
                response_json = {}
        except ValueError:
            response_json = {}

        return status_code, response_text, response_json

    def _enforce_rate_limit(self) -> None:
        now = time.time()
        window_start = now - 60

        while self._send_timestamps and self._send_timestamps[0] < window_start:
            self._send_timestamps.popleft()

        if len(self._send_timestamps) >= self.rate_limit_per_minute:
            wait_for = 60 - (now - self._send_timestamps[0])
            if wait_for > 0:
                time.sleep(wait_for)

            now = time.time()
            window_start = now - 60
            while self._send_timestamps and self._send_timestamps[0] < window_start:
                self._send_timestamps.popleft()

    def _validate_message_size(self, message: str) -> None:
        message_bytes = len(message.encode("utf-8"))
        if message_bytes > self.max_message_bytes:
            raise ValueError(
                f"message is {message_bytes} bytes, limit is {self.max_message_bytes}"
            )

    def send_message(
        self,
        message: str,
        *,
        chat_id: str | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(message, str):
            raise TypeError("message must be a string")

        self._validate_message_size(message)
        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"

        payload: dict[str, Any] = {
            "chat_id": str(chat_id) if chat_id is not None else self.chat_id,
            "text": message,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 2):
            retry_after: float | None = None
            try:
                self._enforce_rate_limit()
                self._send_timestamps.append(time.time())

                status_code, response_text, response_json = self._post_json(endpoint, payload)
                if 200 <= status_code < 300 and response_json.get("ok"):
                    return response_json

                retry_after = (
                    response_json.get("parameters", {}).get("retry_after")
                    if isinstance(response_json, dict)
                    else None
                )
                error_msg = (
                    response_json.get("description")
                    if isinstance(response_json, dict)
                    else None
                ) or response_text
                raise TelegramMessageError(
                    f"Telegram API error(status={status_code}): {error_msg}"
                )

            except (TelegramMessageError, ValueError, urllib_error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt > self.max_retries:
                    break

                if retry_after is not None:
                    sleep_time = float(retry_after)
                else:
                    sleep_time = self.retry_delay_seconds * (2 ** (attempt - 1))
                time.sleep(sleep_time)

        raise TelegramMessageError(
            f"failed to send message after {self.max_retries + 1} attempts"
        ) from last_error


def _self_test(bot_key: str = "default") -> None:
    print(f"[TEST] Loading telegram config from info.yaml with bot_key={bot_key}")
    messenger = TelegramMessenger.from_info_yaml_key(bot_key=bot_key, file_path="info.yaml")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    test_message = f"[TEST] TelegramMessenger({bot_key}) send check at {now}"
    print("[TEST] Sending message...")

    result = messenger.send_message(test_message)
    message_id = result.get("result", {}).get("message_id")
    print(f"[TEST] Success. message_id={message_id}")


if __name__ == "__main__":
    _self_test()
