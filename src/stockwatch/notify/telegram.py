"""Telegram Bot API notifier (plain HTTPS, no SDK)."""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on line boundaries into chunks of at most `limit` chars; hard-split overlong lines."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            parts.append(current)
            current = line
    if current:
        parts.append(current)
    return parts


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_ids: list[str],
        *,
        client: httpx.AsyncClient | None = None,
        retries: int = 5,
        retry_delay: float = 5.0,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_ids = chat_ids
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        self._retries = retries
        self._retry_delay = retry_delay

    async def send(self, text: str) -> None:
        """Deliver to every chat; raises if any chat could not be reached after retries."""
        failures = []
        for chat_id in self._chat_ids:
            for chunk in split_message(text):
                try:
                    await self._send_one(chat_id, chunk)
                except Exception as exc:  # keep delivering to the other chats
                    log.error("Telegram delivery to %s failed: %s", chat_id, exc)
                    failures.append(f"{chat_id}: {exc}")
                    break
        if failures:
            raise RuntimeError("; ".join(failures))

    async def _send_one(self, chat_id: str, text: str) -> None:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.post(self._url, json=payload)
            except httpx.HTTPError as exc:
                error, delay = repr(exc), self._retry_delay * attempt
            else:
                if response.status_code == 200:
                    return
                body = _json(response)
                error = f"HTTP {response.status_code}: {body.get('description', response.text[:200])}"
                if response.status_code == 429:
                    delay = float(body.get("parameters", {}).get("retry_after", self._retry_delay))
                elif response.status_code >= 500:
                    delay = self._retry_delay * attempt
                else:
                    raise RuntimeError(error)  # 4xx other than 429 won't fix itself
            if attempt == self._retries:
                raise RuntimeError(error)
            log.warning(
                "Telegram send failed (%s); retry %d/%d in %.0fs", error, attempt, self._retries, delay
            )
            await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._client.aclose()


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
