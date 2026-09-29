from stockwatch.config import Config
from stockwatch.notify.base import LogNotifier, Notifier, format_alerts
from stockwatch.notify.telegram import TelegramNotifier

__all__ = ["LogNotifier", "Notifier", "TelegramNotifier", "build_notifier", "format_alerts"]


def build_notifier(config: Config) -> Notifier:
    if config.telegram_bot_token and config.chat_ids:
        return TelegramNotifier(config.telegram_bot_token, config.chat_ids)
    return LogNotifier()
