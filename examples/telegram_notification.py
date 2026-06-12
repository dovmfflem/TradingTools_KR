from __future__ import annotations

from src.notifications.telegram_messenger import TelegramMessenger


def main() -> None:
    messenger = TelegramMessenger.from_info_yaml_key("default", "info.yaml")
    result = messenger.send_message("TradingTools KR notification test")
    print(result)


if __name__ == "__main__":
    main()
