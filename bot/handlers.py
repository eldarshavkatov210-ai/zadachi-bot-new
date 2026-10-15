"""Telegram hodisalari: checklistlarni kuzatish va admin buyruqlari."""
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import (JOIN_TRANSITION, LEAVE_TRANSITION, BaseFilter,
                             ChatMemberUpdatedFilter, Command, CommandStart)
from aiogram.types import ChatMemberUpdated, Message

from .checker import run_check
from .config import Config
from .db import Database, TaskData
from .settings import Settings

log = logging.getLogger(__name__)
router = Router()

GROUP = F.chat.type.in_({"group", "supergroup"})
PRIVATE = F.chat.type == "private"


class IsAdmin(BaseFilter):
    async def __call__(self, event, settings: Settings) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and settings.is_admin(user.id))


# ---------------- yordamchi funksiyalar ----------------
def to_dt(value) -> datetime | None:
    if value is None or value == 0:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def who(msg: Message) -> str | None:
    if msg.from_user:
        return msg.from_user.full_name
    if msg.sender_chat:
        return msg.sender_chat.title
    return None


def task_rows(checklist) -> list[TaskData]:
    rows = []
    for t in checklist.tasks or []:
        user = getattr(t, "completed_by_user", None)
        chat = getattr(t, "completed_by_chat", None)
        done_at = to_dt(getattr(t, "completion_date", None))
        done = bool(done_at or user or chat)
        by = user.full_name if user else (chat.title if chat else None)
        rows.append(TaskData(task_id=t.id, text=t.text or "", is_done=done, done_by=by, done_at=done_at))
    return rows


async def save_from_message(db: Database, chat_id: int, msg: Message):
    """Checklist xabarini (yoki xizmat xabaridagi checklist_message ni) bazaga yozadi."""
    cl = msg.checklist
    if not cl:
        return
    await db.save_checklist(
        chat_id=chat_id,
        message_id=msg.message_id,
        title=cl.title or "",
        created_at=to_dt(msg.date) or datetime.now(timezone.utc),
        author=who(msg),
        tasks=task_rows(cl),
    )


# ---------------- checklist hodisalari ----------------
@router.message(GROUP, F.migrate_to_chat_id)
async def on_migrate(message: Message, db: Database):
    if await db.is_tracked(message.chat.id):
        await db.migrate_group(message.chat.id, message.migrate_to_chat_id)
        log.info("Guruh ko'chdi: %s -> %s", message.chat.id, message.migrate_to_chat_id)


@router.message(GROUP, F.checklist)
@router.edited_message(GROUP, F.checklist)
async def on_checklist(message: Message, db: Database):
    if await db.is_tracked(message.chat.id):
        await save_from_message(db, message.chat.id, message)


@router.message(GROUP, F.checklist_tasks_done)
async def on_tasks_done(message: Message, db: Database):
    if not await db.is_tracked(message.chat.id):
        return
    ev = message.checklist_tasks_done
    cm = ev.checklist_message
    if cm is None:
        return
    await save_from_message(db, message.chat.id, cm)
    at = to_dt(message.date)
    await db.mark_tasks(message.chat.id, cm.message_id, list(ev.marked_as_done_task_ids or []), True, who(message), at)
    await db.mark_tasks(message.chat.id, cm.message_id, list(ev.marked_as_not_done_task_ids or []), False, None, None)


@router.message(GROUP, F.checklist_tasks_added)
async def on_tasks_added(message: Message, db: Database):
    if not await db.is_tracked(message.chat.id):
        return
    cm = message.checklist_tasks_added.checklist_message
    if cm is not None:
        await save_from_message(db, message.chat.id, cm)


@router.message(GROUP, F.new_chat_title)
async def on_title(message: Message, db: Database):
    if await db.is_tracked(message.chat.id):
        await db.update_title(message.chat.id, message.new_chat_title)


# ---------------- bot guruhga qo'shilganda / chiqarilganda ----------------
@router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_bot_added(event: ChatMemberUpdated, db: Database, settings: Settings, bot: Bot):
    if event.chat.type not in ("group", "supergroup"):
        return
    if event.from_user and settings.is_admin(event.from_user.id):
        await db.add_group(event.chat.id, event.chat.title)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    f"➕ «{escape(event.chat.title or '')}» guruhi kuzatuvga qo'shildi.\n"
                    "Botni shu guruhda <b>admin</b> qiling — aks holda checklistlarni ko'rmaydi.")
            except Exception:
                pass


@router.my_chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def on_bot_removed(event: ChatMemberUpdated, db: Database):
    await db.set_group_active(event.chat.id, False)


# ---------------- guruh ichidagi admin buyruqlari ----------------
@router.message(GROUP, Command("ulash", "register"), IsAdmin())
async def cmd_register(message: Message, db: Database):
    await db.add_group(message.chat.id, message.chat.title)
    await message.reply("✅ Guruh kuzatuvga olindi. Har kuni belgilanmagan checklistlar tekshiriladi.")


@router.message(GROUP, Command("uzish", "unregister"), IsAdmin())
async def cmd_unregister(message: Message, db: Database):
    await db.set_group_active(message.chat.id, False)
    await message.reply("⏸ Guruh kuzatuvdan olindi.")


# ---------------- shaxsiy chatdagi admin buyruqlari ----------------
HELP = (
    "👋 <b>Checklist nazorati boti</b>\n\n"
    "Bot guruhlardagi checklistlarni doimiy kuzatib boradi va har kuni "
    "{times} da belgilanmagan vazifalar bo'yicha guruhlarga eslatma, "
    "sizga esa hisobot yuboradi.\n\n"
    "<b>Buyruqlar:</b>\n"
    "/admin — admin panel (hamma narsa tugmalar orqali)\n"
    "/hisobot — hozirgi holat (guruhlarga hech narsa yuborilmaydi)\n"
    "/tekshir — hoziroq tekshirib, guruhlarga eslatma yuborish\n"
    "/guruhlar — kuzatilayotgan guruhlar va statistika\n\n"
    "<b>Guruh ichida:</b>\n"
    "/ulash — guruhni kuzatuvga qo'shish\n"
    "/uzish — kuzatuvdan olish"
)


@router.message(PRIVATE, Command("help", "yordam"), IsAdmin())
async def cmd_help(message: Message, settings: Settings):
    await message.answer(HELP.format(times=settings.times_text))


@router.message(PRIVATE, CommandStart())
async def cmd_start_other(message: Message):
    await message.answer(f"Bu bot faqat administrator uchun. Sizning ID: <code>{message.from_user.id}</code>")


@router.message(PRIVATE, Command("hisobot", "report"), IsAdmin())
async def cmd_report(message: Message, bot: Bot, db: Database, config: Config, settings: Settings):
    await run_check(bot, db, config, settings, send_to_groups=False, report_to=[message.chat.id])


@router.message(PRIVATE, Command("tekshir", "check"), IsAdmin())
async def cmd_check(message: Message, bot: Bot, db: Database, config: Config, settings: Settings):
    await message.answer("⏳ Tekshirilmoqda va guruhlarga eslatma yuborilmoqda...")
    await run_check(bot, db, config, settings, send_to_groups=True, report_to=[message.chat.id])


@router.message(PRIVATE, Command("guruhlar", "groups"), IsAdmin())
async def cmd_groups(message: Message, bot: Bot, db: Database, config: Config, settings: Settings):
    since = datetime.now(config.tz) - timedelta(days=settings.lookback_days)
    groups = await db.active_groups()
    if not groups:
        await message.answer("Kuzatilayotgan guruh yo'q. Guruhga botni qo'shing yoki guruhda /ulash yozing.")
        return
    me = await bot.me()
    lines = [f"📋 <b>Kuzatilayotgan guruhlar</b> (oxirgi {settings.lookback_days} kun):", ""]
    for g in groups:
        st = await db.group_stats(g["chat_id"], since)
        try:
            member = await bot.get_chat_member(g["chat_id"], me.id)
            role = "admin ✅" if member.status in ("administrator", "creator") else "admin emas ⚠️"
        except Exception:
            role = "guruhga kira olmadi ❌"
        lines.append(
            f"• <b>{escape(g['title'] or str(g['chat_id']))}</b> — bot: {role}\n"
            f"  checklist: {st['checklists']}, band: {st['tasks']}, bajarilgan: {st['done']}"
        )
    await message.answer("\n".join(lines))
