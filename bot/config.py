"""Sozlamalar .env (yoki Railway Variables) dan o'qiladi."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int_list(value: str) -> list[int]:
    return [int(x) for x in value.replace(" ", "").split(",") if x]


def _times(value: str) -> list[tuple[int, int]]:
    result = []
    for part in value.replace(" ", "").split(","):
        if part:
            h, m = part.split(":")
            result.append((int(h), int(m)))
    return result


def _bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "ha", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    database_url: str
    admin_ids: list[int]
    group_ids: list[int]
    tz: ZoneInfo
    remind_times: list[tuple[int, int]]
    lookback_days: int
    send_to_groups: bool


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN ko'rsatilmagan (.env yoki Railway Variables)")
    if not db_url:
        raise SystemExit("DATABASE_URL ko'rsatilmagan (.env yoki Railway Variables)")
    # Railway ba'zan postgres:// beradi — asyncpg ikkalasini ham qabul qiladi
    return Config(
        bot_token=token,
        database_url=db_url,
        admin_ids=_int_list(os.getenv("ADMIN_IDS", "")),
        group_ids=_int_list(os.getenv("GROUP_IDS", "")),
        tz=ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent")),
        remind_times=_times(os.getenv("REMIND_TIMES", "20:00")),
        lookback_days=int(os.getenv("LOOKBACK_DAYS", "14")),
        send_to_groups=_bool(os.getenv("SEND_GROUP_REMINDERS", ""), True),
    )
