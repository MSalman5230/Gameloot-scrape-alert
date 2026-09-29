"""Notifier interface and alert formatting."""

import logging
from typing import Protocol

from stockwatch.core.diff import ChangeSet

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...
    async def aclose(self) -> None: ...


class LogNotifier:
    """Fallback when Telegram isn't configured: alerts go to the log."""

    async def send(self, text: str) -> None:
        log.info("ALERT\n%s", text)

    async def aclose(self) -> None:
        pass


def format_price(price: int) -> str:
    return f"₹{price:,}"


def format_alerts(site_name: str, category_name: str, changes: ChangeSet) -> list[str]:
    """One message for new/back-in-stock items and one for sold-out items (when non-empty)."""
    where = f"{site_name} · {category_name}"
    messages = []
    in_stock = [*changes.new, *(item for _, item in changes.restocked)]
    if in_stock:
        lines = [f"🟢 NEW / BACK IN STOCK — {where} ({len(in_stock)})"]
        lines += [f"\n- {i.name} - {format_price(i.price)} - {i.url}" for i in in_stock]
        messages.append("\n".join(lines))
    if changes.sold_out:
        lines = [f"🔴 NO LONGER IN STOCK — {where} ({len(changes.sold_out)})"]
        lines += [f"\n- {p.name} - {format_price(p.price)} - {p.url}" for p in changes.sold_out]
        messages.append("\n".join(lines))
    return messages
