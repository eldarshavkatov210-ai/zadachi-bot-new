"""Admin paneldan o'zgartiriladigan sozlamalar (bazada saqlanadi, .env — boshlang'ich qiymat)."""
from dataclasses import dataclass, field

from .config import Config, _bool, _times
from .db import Database


def fmt_times(times: list[tuple[int, int]]) -> str:
    return ", ".join(f"{h:02d}:{m:02d}" for h, m in times) or "—"


@dataclass
class Settings:
    remind_times: list[tuple[int, int]]
    lookback_days: int
    send_to_groups: bool
    paused: bool
    super_admins: list[int]                  # .env dagi ADMIN_IDS — o'chirib bo'lmaydi
    extra_admins: dict[int, str | None] = field(default_factory=dict)  # paneldan qo'shilganlar

    @property
    def admin_ids(self) -> list[int]:
        return list(dict.fromkeys([*self.super_admins, *self.extra_admins]))

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.super_admins or user_id in self.extra_admins

    def is_super(self, user_id: int) -> bool:
        return user_id in self.super_admins

    @property
    def times_text(self) -> str:
        return fmt_times(self.remind_times)

    @classmethod
    async def load(cls, db: Database, config: Config) -> "Settings":
        saved = await db.get_settings()
        times = _times(saved["remind_times"]) if "remind_times" in saved else config.remind_times
        s = cls(
            remind_times=sorted(set(times)),
            lookback_days=int(saved.get("lookback_days", config.lookback_days)),
            send_to_groups=_bool(saved.get("send_to_groups", ""), config.send_to_groups),
            paused=_bool(saved.get("paused", ""), False),
            super_admins=list(config.admin_ids),
        )
        s.extra_admins = {r["user_id"]: r["name"] for r in await db.list_admins()}
        return s

    async def save(self, db: Database):
        self.remind_times = sorted(set(self.remind_times))
        await db.set_setting("remind_times", fmt_times(self.remind_times).replace("—", ""))
        await db.set_setting("lookback_days", str(self.lookback_days))
        await db.set_setting("send_to_groups", "true" if self.send_to_groups else "false")
        await db.set_setting("paused", "true" if self.paused else "false")
