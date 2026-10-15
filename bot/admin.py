"""Admin panel: hamma boshqaruv inline tugmalar orqali (/admin yoki /start)."""
import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardMarkup,
                           Message, MessageOriginUser)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .checker import apply_schedule, collect, next_run, run_check
from .config import Config
from .db import Database
from .handlers import PRIVATE, IsAdmin
from .settings import Settings
from .texts import message_link

log = logging.getLogger(__name__)
router = Router()
router.message.filter(PRIVATE, IsAdmin())
router.callback_query.filter(IsAdmin())


# ---------------- callback ma'lumotlari va holatlar ----------------
class Nav(CallbackData, prefix="nav"):
    to: str


class Grp(CallbackData, prefix="grp"):
    act: str
    chat_id: int


class Cfg(CallbackData, prefix="cfg"):
    act: str
    val: str = ""


class Adm(CallbackData, prefix="adm"):
    act: str
    user_id: int = 0


class Bc(CallbackData, prefix="bc"):
    act: str


class Form(StatesGroup):
    time = State()
    days = State()
    admin = State()
    broadcast = State()


# ---------------- yordamchilar ----------------
async def show(event: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None):
    """Callback bo'lsa — xabarni tahrirlaydi, aks holda yangi xabar yuboradi."""
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await event.message.answer(text, reply_markup=kb)
        try:
            await event.answer()
        except TelegramBadRequest:
            pass
    else:
        await event.answer(text, reply_markup=kb)


def kb_back(to: str = "menu", text: str = "⬅️ Orqaga") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=Nav(to=to))
    return b.as_markup()


def pct(done: int, total: int) -> int:
    return round(done * 100 / total) if total else 0


def bar(p: int, width: int = 10) -> str:
    full = round(p * width / 100)
    return "▓" * full + "░" * (width - full)


def fmt_dt(dt: datetime | None, config: Config) -> str:
    return dt.astimezone(config.tz).strftime("%d.%m %H:%M") if dt else "—"


def group_name(g) -> str:
    return escape(g["title"] or str(g["chat_id"]))


async def bot_role(bot: Bot, chat_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, (await bot.me()).id)
    except Exception:
        return "guruhga kira olmadi ❌"
    return "admin ✅" if member.status in ("administrator", "creator") else "admin emas ⚠️"


# ---------------- asosiy menyu ----------------
async def menu_view(db: Database, config: Config, settings: Settings,
                    scheduler: AsyncIOScheduler) -> tuple[str, InlineKeyboardMarkup]:
    results = await collect(db, settings, config, datetime.now(config.tz))
    undone = sum(r.undone_count for r in results)
    nxt = next_run(scheduler)
    status = "⏸ <b>pauzada</b> (avtomatik tekshiruv o'chiq)" if settings.paused else "▶️ ishlayapti"
    lines = [
        "🛠 <b>Admin panel</b>",
        "",
        f"Holat: {status}",
        f"Tekshiruv vaqti: <b>{settings.times_text}</b> ({config.tz.key})",
        f"Keyingi tekshiruv: {fmt_dt(nxt, config) if nxt and not settings.paused else '—'}",
        f"Guruhlarga eslatma: {'🔔 yoqilgan' if settings.send_to_groups else '🔕 faqat sizga hisobot'}",
        f"Kuzatuvdagi guruhlar: <b>{len(results)}</b>",
        f"Bajarilmagan bandlar (oxirgi {settings.lookback_days} kun): <b>{undone}</b>",
    ]
    b = InlineKeyboardBuilder()
    b.button(text="📊 Hisobot", callback_data=Nav(to="report"))
    b.button(text="🔔 Hoziroq eslatma", callback_data=Nav(to="check"))
    b.button(text="👥 Guruhlar", callback_data=Nav(to="groups"))
    b.button(text="📈 Statistika", callback_data=Nav(to="stats"))
    b.button(text="📣 Xabar yuborish", callback_data=Nav(to="broadcast"))
    b.button(text="📜 Eslatmalar tarixi", callback_data=Nav(to="log"))
    b.button(text="⚙️ Sozlamalar", callback_data=Nav(to="settings"))
    b.button(text="👮 Adminlar", callback_data=Nav(to="admins"))
    b.button(text="📥 Excel eksport", callback_data=Nav(to="export"))
    b.button(text="🔄 Yangilash", callback_data=Nav(to="menu"))
    b.adjust(2)
    return "\n".join(lines), b.as_markup()


@router.message(CommandStart())
@router.message(Command("admin", "panel"))
async def cmd_admin(message: Message, state: FSMContext, db: Database, config: Config,
                    settings: Settings, scheduler: AsyncIOScheduler):
    await state.clear()
    await show(message, *await menu_view(db, config, settings, scheduler))


@router.message(Command("bekor", "cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db: Database, config: Config,
                     settings: Settings, scheduler: AsyncIOScheduler):
    await state.clear()
    await message.answer("Bekor qilindi.")
    await show(message, *await menu_view(db, config, settings, scheduler))


@router.callback_query(Nav.filter(F.to == "menu"))
async def nav_menu(cb: CallbackQuery, state: FSMContext, db: Database, config: Config,
                   settings: Settings, scheduler: AsyncIOScheduler):
    await state.clear()
    await show(cb, *await menu_view(db, config, settings, scheduler))


# ---------------- hisobot va tekshiruv ----------------
@router.callback_query(Nav.filter(F.to == "report"))
async def nav_report(cb: CallbackQuery, bot: Bot, db: Database, config: Config, settings: Settings):
    await cb.answer("Hisobot tayyorlanmoqda...")
    await run_check(bot, db, config, settings, send_to_groups=False, report_to=[cb.from_user.id])


@router.callback_query(Nav.filter(F.to == "check"))
async def nav_check(cb: CallbackQuery, db: Database, config: Config, settings: Settings):
    results = await collect(db, settings, config, datetime.now(config.tz))
    targets = [r for r in results if r.items and r.reminders_enabled]
    if not targets:
        await cb.answer("Eslatma yuboriladigan guruh yo'q — hammasi bajarilgan ✅", show_alert=True)
        return
    names = "\n".join(f"• {escape(r.title)} — {r.undone_count} ta band" for r in targets)
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ha, yuborish", callback_data=Nav(to="check_ok"))
    b.button(text="⬅️ Bekor", callback_data=Nav(to="menu"))
    b.adjust(2)
    await show(cb, f"🔔 <b>Quyidagi guruhlarga hoziroq eslatma yuborilsinmi?</b>\n\n{names}", b.as_markup())


@router.callback_query(Nav.filter(F.to == "check_ok"))
async def nav_check_ok(cb: CallbackQuery, bot: Bot, db: Database, config: Config, settings: Settings):
    await show(cb, "⏳ Eslatmalar yuborilmoqda...", None)
    await run_check(bot, db, config, settings, send_to_groups=True, report_to=[cb.from_user.id])
    await cb.message.answer("✅ Tayyor.", reply_markup=kb_back(text="⬅️ Menyu"))


# ---------------- guruhlar ----------------
@router.callback_query(Nav.filter(F.to == "groups"))
async def nav_groups(cb: CallbackQuery, db: Database, config: Config, settings: Settings):
    groups = await db.all_groups()
    undone = {r.chat_id: r.undone_count
              for r in await collect(db, settings, config, datetime.now(config.tz))}
    b = InlineKeyboardBuilder()
    for g in groups:
        icon = "⏸" if not g["is_active"] else ("🟢" if g["reminders_enabled"] else "🔕")
        n = undone.get(g["chat_id"], 0)
        b.button(text=f"{icon} {g['title'] or g['chat_id']}" + (f" · {n}❗" if n else ""),
                 callback_data=Grp(act="open", chat_id=g["chat_id"]))
    b.button(text="⬅️ Orqaga", callback_data=Nav(to="menu"))
    b.adjust(1)
    text = ("👥 <b>Guruhlar</b>\n\n"
            "🟢 kuzatuvda · 🔕 eslatma o'chirilgan · ⏸ kuzatuvdan olingan\n"
            "❗ — bajarilmagan bandlar soni\n\n")
    text += "Guruhni tanlang:" if groups else (
        "Hali guruh yo'q. Botni guruhga qo'shib admin qiling yoki guruhda /ulash yozing.")
    await show(cb, text, b.as_markup())


async def group_view(chat_id: int, bot: Bot, db: Database, config: Config,
                     settings: Settings) -> tuple[str, InlineKeyboardMarkup] | None:
    g = await db.get_group(chat_id)
    if g is None:
        return None
    since = datetime.now(config.tz) - timedelta(days=settings.lookback_days)
    st = await db.group_stats(chat_id, since)
    res = await collect(db, settings, config, datetime.now(config.tz), only_chat=chat_id)
    undone = res[0].undone_count if res else 0
    last = await db.recent_reminders(1, chat_id)
    last_txt = (f"{fmt_dt(last[0]['sent_at'], config)} {'✅' if last[0]['ok'] else '❌'}"
                if last else "hali yuborilmagan")
    lines = [
        f"👥 <b>{group_name(g)}</b>",
        f"ID: <code>{chat_id}</code>",
        "",
        f"Bot huquqi: {await bot_role(bot, chat_id)}",
        "Kuzatuv: " + ("✅ yoqilgan" if g["is_active"] else "⏸ to'xtatilgan"),
        "Eslatmalar: " + ("🔔 yoqilgan" if g["reminders_enabled"] else "🔕 o'chirilgan"),
        "",
        f"<b>Oxirgi {settings.lookback_days} kun:</b>",
        f"checklist: {st['checklists']} · band: {st['tasks']} · bajarilgan: {st['done']}",
        f"{bar(pct(st['done'], st['tasks']))} {pct(st['done'], st['tasks'])}%",
        f"Hozir bajarilmagan: <b>{undone}</b>",
        f"Oxirgi eslatma: {last_txt}",
    ]
    b = InlineKeyboardBuilder()
    b.button(text="📊 Hisobot", callback_data=Grp(act="report", chat_id=chat_id))
    b.button(text="✉️ Eslatma yuborish", callback_data=Grp(act="remind", chat_id=chat_id))
    b.button(text="🔕 Eslatmalarni o'chirish" if g["reminders_enabled"] else "🔔 Eslatmalarni yoqish",
             callback_data=Grp(act="rem", chat_id=chat_id))
    b.button(text="⏸ Kuzatuvni to'xtatish" if g["is_active"] else "▶️ Kuzatuvga olish",
             callback_data=Grp(act="track", chat_id=chat_id))
    b.button(text="📣 Xabar yuborish", callback_data=Grp(act="bc", chat_id=chat_id))
    b.button(text="🗑 O'chirish", callback_data=Grp(act="delete", chat_id=chat_id))
    b.button(text="⬅️ Guruhlar", callback_data=Nav(to="groups"))
    b.adjust(2, 1, 1, 2, 1)
    return "\n".join(lines), b.as_markup()


async def show_group(cb: CallbackQuery, chat_id: int, bot: Bot, db: Database,
                     config: Config, settings: Settings):
    view = await group_view(chat_id, bot, db, config, settings)
    if view is None:
        await cb.answer("Guruh topilmadi", show_alert=True)
        return
    await show(cb, *view)


@router.callback_query(Grp.filter(F.act == "open"))
async def grp_open(cb: CallbackQuery, callback_data: Grp, bot: Bot, db: Database,
                   config: Config, settings: Settings):
    await show_group(cb, callback_data.chat_id, bot, db, config, settings)


@router.callback_query(Grp.filter(F.act == "rem"))
async def grp_toggle_reminders(cb: CallbackQuery, callback_data: Grp, bot: Bot, db: Database,
                               config: Config, settings: Settings):
    g = await db.get_group(callback_data.chat_id)
    if g:
        await db.set_group_reminders(g["chat_id"], not g["reminders_enabled"])
    await show_group(cb, callback_data.chat_id, bot, db, config, settings)


@router.callback_query(Grp.filter(F.act == "track"))
async def grp_toggle_tracking(cb: CallbackQuery, callback_data: Grp, bot: Bot, db: Database,
                              config: Config, settings: Settings):
    g = await db.get_group(callback_data.chat_id)
    if g:
        await db.set_group_active(g["chat_id"], not g["is_active"])
    await show_group(cb, callback_data.chat_id, bot, db, config, settings)


@router.callback_query(Grp.filter(F.act == "report"))
async def grp_report(cb: CallbackQuery, callback_data: Grp, bot: Bot, db: Database,
                     config: Config, settings: Settings):
    g = await db.get_group(callback_data.chat_id)
    if not g or not g["is_active"]:
        await cb.answer("Guruh kuzatuvda emas", show_alert=True)
        return
    await cb.answer("Hisobot tayyorlanmoqda...")
    await run_check(bot, db, config, settings, send_to_groups=False,
                    report_to=[cb.from_user.id], only_chat=callback_data.chat_id)


@router.callback_query(Grp.filter(F.act == "remind"))
async def grp_remind(cb: CallbackQuery, callback_data: Grp, db: Database, config: Config,
                     settings: Settings):
    res = await collect(db, settings, config, datetime.now(config.tz), only_chat=callback_data.chat_id)
    if not res or not res[0].items:
        await cb.answer("Bu guruhda bajarilmagan band yo'q ✅", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ha, yuborish", callback_data=Grp(act="remind_ok", chat_id=callback_data.chat_id))
    b.button(text="⬅️ Bekor", callback_data=Grp(act="open", chat_id=callback_data.chat_id))
    b.adjust(2)
    await show(cb, f"✉️ <b>{escape(res[0].title)}</b> guruhiga {res[0].undone_count} ta "
                   "bajarilmagan band bo'yicha eslatma yuborilsinmi?", b.as_markup())


@router.callback_query(Grp.filter(F.act == "remind_ok"))
async def grp_remind_ok(cb: CallbackQuery, callback_data: Grp, bot: Bot, db: Database,
                        config: Config, settings: Settings):
    await show(cb, "⏳ Eslatma yuborilmoqda...", None)
    await run_check(bot, db, config, settings, send_to_groups=True, report_to=[cb.from_user.id],
                    only_chat=callback_data.chat_id, force=True)
    view = await group_view(callback_data.chat_id, bot, db, config, settings)
    if view:
        text, kb = view
        await cb.message.answer(text, reply_markup=kb)


@router.callback_query(Grp.filter(F.act == "delete"))
async def grp_delete(cb: CallbackQuery, callback_data: Grp, db: Database):
    g = await db.get_group(callback_data.chat_id)
    if not g:
        await cb.answer("Guruh topilmadi", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Ha, o'chirish", callback_data=Grp(act="delete_ok", chat_id=callback_data.chat_id))
    b.button(text="⬅️ Bekor", callback_data=Grp(act="open", chat_id=callback_data.chat_id))
    b.adjust(2)
    await show(cb, f"⚠️ <b>{group_name(g)}</b> guruhi va uning <b>barcha checklist tarixi</b> "
                   "bazadan o'chiriladi. Bu amalni qaytarib bo'lmaydi.\n\n"
                   "Faqat vaqtincha to'xtatmoqchi bo'lsangiz — «Kuzatuvni to'xtatish» tugmasidan foydalaning.",
               b.as_markup())


@router.callback_query(Grp.filter(F.act == "delete_ok"))
async def grp_delete_ok(cb: CallbackQuery, callback_data: Grp, db: Database, config: Config,
                        settings: Settings):
    await db.delete_group(callback_data.chat_id)
    await cb.answer("Guruh o'chirildi")
    await nav_groups(cb, db, config, settings)


# ---------------- xabar yuborish (broadcast) ----------------
async def start_broadcast(cb: CallbackQuery, state: FSMContext, target: int, label: str):
    await state.set_state(Form.broadcast)
    await state.update_data(target=target, src_chat=None, src_msg=None)
    await show(cb, f"📣 <b>Xabar yuborish — {label}</b>\n\n"
                   "Yubormoqchi bo'lgan xabaringizni shu yerga yozing (matn, rasm, fayl, video — "
                   "istalgan turi). Yuborishdan oldin tasdiqlash so'raladi.",
               kb_back(text="❌ Bekor qilish"))


@router.callback_query(Nav.filter(F.to == "broadcast"))
async def nav_broadcast(cb: CallbackQuery, state: FSMContext, db: Database):
    n = len(await db.active_groups())
    if not n:
        await cb.answer("Kuzatuvdagi guruh yo'q", show_alert=True)
        return
    await start_broadcast(cb, state, 0, f"barcha guruhlarga ({n} ta)")


@router.callback_query(Grp.filter(F.act == "bc"))
async def grp_broadcast(cb: CallbackQuery, callback_data: Grp, state: FSMContext, db: Database):
    g = await db.get_group(callback_data.chat_id)
    if not g:
        await cb.answer("Guruh topilmadi", show_alert=True)
        return
    await start_broadcast(cb, state, g["chat_id"], f"«{group_name(g)}» guruhiga")


NOT_COMMAND = ~F.text.startswith("/")


@router.message(Form.broadcast, NOT_COMMAND)
async def broadcast_got(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    await state.update_data(src_chat=message.chat.id, src_msg=message.message_id)
    if data["target"]:
        g = await db.get_group(data["target"])
        where = f"«{group_name(g)}» guruhiga" if g else "guruhga"
    else:
        where = f"{len(await db.active_groups())} ta guruhga"
    b = InlineKeyboardBuilder()
    b.button(text="✅ Yuborish", callback_data=Bc(act="send"))
    b.button(text="❌ Bekor", callback_data=Nav(to="menu"))
    b.adjust(2)
    await message.reply(f"Shu xabar {where} yuborilsinmi?\n"
                        "<i>(boshqa xabar yozsangiz — u almashtiriladi)</i>", reply_markup=b.as_markup())


@router.callback_query(Bc.filter(F.act == "send"), Form.broadcast)
async def broadcast_send(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Database):
    data = await state.get_data()
    await state.clear()
    if not data.get("src_msg"):
        await cb.answer("Xabar topilmadi", show_alert=True)
        return
    targets = [data["target"]] if data["target"] else [g["chat_id"] for g in await db.active_groups()]
    await show(cb, f"⏳ {len(targets)} ta guruhga yuborilmoqda...", None)
    ok, failed = 0, []
    for chat_id in targets:
        try:
            await bot.copy_message(chat_id, data["src_chat"], data["src_msg"])
            ok += 1
        except Exception as e:
            g = await db.get_group(chat_id)
            failed.append(f"• {group_name(g) if g else chat_id}: {escape(str(e)[:120])}")
        await asyncio.sleep(0.1)
    text = f"📣 Yuborildi: <b>{ok}</b> / {len(targets)}"
    if failed:
        text += "\n\n⚠️ Yuborilmadi:\n" + "\n".join(failed)
    await cb.message.answer(text, reply_markup=kb_back(text="⬅️ Menyu"))


@router.callback_query(Bc.filter())
async def broadcast_expired(cb: CallbackQuery):
    await cb.answer("Bu so'rov eskirgan. Qaytadan boshlang.", show_alert=True)


# ---------------- statistika ----------------
@router.callback_query(Nav.filter(F.to == "stats"))
async def nav_stats(cb: CallbackQuery, db: Database, config: Config, settings: Settings):
    now = datetime.now(config.tz)
    lines = ["📈 <b>Statistika</b>", ""]
    for days in (1, 7, 30):
        st = await db.overall_stats(now - timedelta(days=days))
        p = pct(st["done"], st["tasks"])
        label = "Oxirgi 24 soat" if days == 1 else f"Oxirgi {days} kun"
        lines.append(f"<b>{label}:</b> checklist {st['checklists']} · band {st['tasks']} · "
                     f"bajarilgan {st['done']} ({p}%)")

    since = now - timedelta(days=settings.lookback_days)
    groups = await db.active_groups()
    if groups:
        lines += ["", f"<b>Guruhlar bo'yicha bajarilish</b> (oxirgi {settings.lookback_days} kun):"]
        rows = []
        for g in groups:
            st = await db.group_stats(g["chat_id"], since)
            rows.append((pct(st["done"], st["tasks"]), st, g))
        for p, st, g in sorted(rows, key=lambda r: -r[0]):
            lines.append(f"{bar(p)} {p:>3}% · {group_name(g)} ({st['done']}/{st['tasks']})")

    top = await db.top_performers(now - timedelta(days=30))
    if top:
        lines += ["", "🏆 <b>Eng faol ijrochilar</b> (30 kun):"]
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(top):
            mark = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{mark} {escape(r['name'])} — {r['done']} ta")
    await show(cb, "\n".join(lines), kb_back())


# ---------------- eslatmalar tarixi ----------------
@router.callback_query(Nav.filter(F.to == "log"))
async def nav_log(cb: CallbackQuery, db: Database, config: Config):
    rows = await db.recent_reminders(20)
    lines = ["📜 <b>Oxirgi eslatmalar</b>", ""]
    if not rows:
        lines.append("Hali eslatma yuborilmagan.")
    for r in rows:
        name = escape(r["title"] or str(r["chat_id"]))
        if r["ok"]:
            lines.append(f"✅ {fmt_dt(r['sent_at'], config)} · {name} · {r['undone']} ta band")
        else:
            lines.append(f"❌ {fmt_dt(r['sent_at'], config)} · {name} · "
                         f"<i>{escape((r['error'] or 'xato')[:80])}</i>")
    await show(cb, "\n".join(lines), kb_back())


# ---------------- sozlamalar ----------------
def settings_view(config: Config, settings: Settings,
                  scheduler: AsyncIOScheduler) -> tuple[str, InlineKeyboardMarkup]:
    nxt = next_run(scheduler)
    lines = [
        "⚙️ <b>Sozlamalar</b>",
        "",
        f"🕗 Tekshiruv vaqtlari: <b>{settings.times_text}</b> ({config.tz.key})",
        f"⏭ Keyingi tekshiruv: {fmt_dt(nxt, config) if nxt and not settings.paused else '—'}",
        f"📅 Tekshiriladigan davr: oxirgi <b>{settings.lookback_days}</b> kun",
        "📣 Guruhlarga eslatma: " + ("🔔 yoqilgan" if settings.send_to_groups
                                     else "🔕 o'chiq (faqat sizga hisobot)"),
        f"⏯ Avtomatik tekshiruv: {'⏸ pauzada' if settings.paused else '▶️ ishlayapti'}",
        "",
        "<i>Vaqtni o'chirish uchun ❌ tugmasini bosing.</i>",
    ]
    b = InlineKeyboardBuilder()
    for h, m in settings.remind_times:
        b.button(text=f"❌ {h:02d}:{m:02d}", callback_data=Cfg(act="time_del", val=f"{h:02d}{m:02d}"))
    b.button(text="➕ Vaqt qo'shish", callback_data=Cfg(act="time_add"))
    b.button(text=f"📅 Davr: {settings.lookback_days} kun", callback_data=Cfg(act="days"))
    b.button(text="🔕 Guruhlarga eslatmani o'chirish" if settings.send_to_groups
             else "🔔 Guruhlarga eslatmani yoqish", callback_data=Cfg(act="send"))
    b.button(text="▶️ Davom ettirish" if settings.paused else "⏸ Pauza qilish",
             callback_data=Cfg(act="pause"))
    b.button(text="⬅️ Orqaga", callback_data=Nav(to="menu"))
    n = len(settings.remind_times)
    b.adjust(*([3] * (n // 3) + ([n % 3] if n % 3 else [])), 2, 1, 1, 1)
    return "\n".join(lines), b.as_markup()


async def save_settings(db: Database, bot: Bot, config: Config, settings: Settings,
                        scheduler: AsyncIOScheduler):
    await settings.save(db)
    apply_schedule(scheduler, bot, db, config, settings)


@router.callback_query(Nav.filter(F.to == "settings"))
async def nav_settings(cb: CallbackQuery, state: FSMContext, config: Config, settings: Settings,
                       scheduler: AsyncIOScheduler):
    await state.clear()
    await show(cb, *settings_view(config, settings, scheduler))


@router.callback_query(Cfg.filter(F.act == "time_del"))
async def cfg_time_del(cb: CallbackQuery, callback_data: Cfg, bot: Bot, db: Database,
                       config: Config, settings: Settings, scheduler: AsyncIOScheduler):
    t = (int(callback_data.val[:2]), int(callback_data.val[2:]))
    if t in settings.remind_times:
        settings.remind_times.remove(t)
        await save_settings(db, bot, config, settings, scheduler)
    if not settings.remind_times:
        await cb.answer("⚠️ Birorta ham vaqt qolmadi — avtomatik tekshiruv bo'lmaydi.", show_alert=True)
    await show(cb, *settings_view(config, settings, scheduler))


@router.callback_query(Cfg.filter(F.act == "time_add"))
async def cfg_time_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Form.time)
    await show(cb, "🕗 Yangi tekshiruv vaqtini <b>SS:DD</b> ko'rinishida yuboring.\n"
                   "Masalan: <code>13:00</code> yoki bir nechtasi: <code>09:00, 18:30</code>",
               kb_back("settings", "❌ Bekor qilish"))


@router.message(Form.time, F.text)
async def form_time(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config,
                    settings: Settings, scheduler: AsyncIOScheduler):
    found = re.findall(r"\b(\d{1,2})[:.](\d{2})\b", message.text)
    times = [(int(h), int(m)) for h, m in found if int(h) < 24 and int(m) < 60]
    if not times:
        await message.answer("❗ Vaqt tushunilmadi. Masalan: <code>20:00</code>",
                             reply_markup=kb_back("settings", "❌ Bekor qilish"))
        return
    settings.remind_times.extend(times)
    await save_settings(db, bot, config, settings, scheduler)
    await state.clear()
    await message.answer("✅ Saqlandi.")
    await show(message, *settings_view(config, settings, scheduler))


@router.callback_query(Cfg.filter(F.act == "days"))
async def cfg_days(cb: CallbackQuery, state: FSMContext, settings: Settings):
    await state.set_state(Form.days)
    b = InlineKeyboardBuilder()
    for d in (3, 7, 14, 30):
        b.button(text=f"{'• ' if d == settings.lookback_days else ''}{d} kun",
                 callback_data=Cfg(act="days_set", val=str(d)))
    b.button(text="❌ Bekor qilish", callback_data=Nav(to="settings"))
    b.adjust(4, 1)
    await show(cb, "📅 Necha kun oldingi checklistlar tekshirilsin?\n"
                   "Tugmani tanlang yoki 1 dan 90 gacha son yuboring.", b.as_markup())


async def set_days(days: int, bot: Bot, db: Database, config: Config, settings: Settings,
                   scheduler: AsyncIOScheduler):
    settings.lookback_days = days
    await save_settings(db, bot, config, settings, scheduler)


@router.callback_query(Cfg.filter(F.act == "days_set"))
async def cfg_days_set(cb: CallbackQuery, callback_data: Cfg, state: FSMContext, bot: Bot,
                       db: Database, config: Config, settings: Settings, scheduler: AsyncIOScheduler):
    await state.clear()
    await set_days(int(callback_data.val), bot, db, config, settings, scheduler)
    await show(cb, *settings_view(config, settings, scheduler))


@router.message(Form.days, F.text)
async def form_days(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config,
                    settings: Settings, scheduler: AsyncIOScheduler):
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 90:
        await message.answer("❗ 1 dan 90 gacha son yuboring.",
                             reply_markup=kb_back("settings", "❌ Bekor qilish"))
        return
    await state.clear()
    await set_days(int(text), bot, db, config, settings, scheduler)
    await message.answer("✅ Saqlandi.")
    await show(message, *settings_view(config, settings, scheduler))


@router.callback_query(Cfg.filter(F.act == "send"))
async def cfg_send(cb: CallbackQuery, bot: Bot, db: Database, config: Config, settings: Settings,
                   scheduler: AsyncIOScheduler):
    settings.send_to_groups = not settings.send_to_groups
    await save_settings(db, bot, config, settings, scheduler)
    await show(cb, *settings_view(config, settings, scheduler))


@router.callback_query(Cfg.filter(F.act == "pause"))
async def cfg_pause(cb: CallbackQuery, bot: Bot, db: Database, config: Config, settings: Settings,
                    scheduler: AsyncIOScheduler):
    settings.paused = not settings.paused
    await save_settings(db, bot, config, settings, scheduler)
    await show(cb, *settings_view(config, settings, scheduler))


# ---------------- adminlar ----------------
def admins_view(user_id: int, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["👮 <b>Adminlar</b>", "", "<b>Asosiy</b> (.env dagi ADMIN_IDS, o'chirib bo'lmaydi):"]
    lines += [f"• <code>{uid}</code>" + (" (siz)" if uid == user_id else "") for uid in settings.super_admins]
    lines += ["", "<b>Paneldan qo'shilganlar:</b>"]
    if settings.extra_admins:
        lines += [f"• {escape(name or '—')} — <code>{uid}</code>" + (" (siz)" if uid == user_id else "")
                  for uid, name in settings.extra_admins.items()]
    else:
        lines.append("—")
    b = InlineKeyboardBuilder()
    if settings.is_super(user_id):
        for uid, name in settings.extra_admins.items():
            b.button(text=f"❌ {name or uid}", callback_data=Adm(act="del", user_id=uid))
        b.button(text="➕ Admin qo'shish", callback_data=Adm(act="add"))
    else:
        lines += ["", "<i>Adminlarni faqat asosiy admin boshqara oladi.</i>"]
    b.button(text="⬅️ Orqaga", callback_data=Nav(to="menu"))
    b.adjust(1)
    return "\n".join(lines), b.as_markup()


@router.callback_query(Nav.filter(F.to == "admins"))
async def nav_admins(cb: CallbackQuery, state: FSMContext, settings: Settings):
    await state.clear()
    await show(cb, *admins_view(cb.from_user.id, settings))


@router.callback_query(Adm.filter(F.act == "add"))
async def adm_add(cb: CallbackQuery, state: FSMContext, settings: Settings):
    if not settings.is_super(cb.from_user.id):
        await cb.answer("Faqat asosiy admin uchun", show_alert=True)
        return
    await state.set_state(Form.admin)
    await show(cb, "➕ <b>Yangi admin</b>\n\n"
                   "Uning Telegram <b>ID</b> raqamini yuboring yoki uning biror xabarini shu yerga "
                   "<b>forward</b> qiling.\n\n"
                   "ID ni bilish uchun u botga /start yozsin — bot ID ni ko'rsatadi.",
               kb_back("admins", "❌ Bekor qilish"))


@router.message(Form.admin, NOT_COMMAND)
async def form_admin(message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings):
    uid, name = None, None
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        uid, name = origin.sender_user.id, origin.sender_user.full_name
    elif origin is not None:
        await message.answer("❗ Bu foydalanuvchi forward'ni yashirgan. Uning ID raqamini yuboring.")
        return
    elif message.text and re.fullmatch(r"\s*\d{5,15}\s*", message.text):
        uid = int(message.text)
        try:
            name = (await bot.get_chat(uid)).full_name
        except Exception:
            name = None
    if not uid:
        await message.answer("❗ ID raqam (faqat sonlar) yuboring yoki xabarni forward qiling.",
                             reply_markup=kb_back("admins", "❌ Bekor qilish"))
        return
    if settings.is_admin(uid):
        await message.answer("Bu foydalanuvchi allaqachon admin.")
    else:
        await db.add_admin(uid, name, message.from_user.id)
        settings.extra_admins[uid] = name
        await message.answer(f"✅ {escape(name or str(uid))} admin qilindi.")
        try:
            await bot.send_message(uid, "👮 Siz checklist botiga admin qilib tayinlandingiz. Panel: /admin")
        except Exception:
            await message.answer("ℹ️ Unga xabar yuborib bo'lmadi — u botga /start yozishi kerak.")
    await state.clear()
    await show(message, *admins_view(message.from_user.id, settings))


@router.callback_query(Adm.filter(F.act == "del"))
async def adm_del(cb: CallbackQuery, callback_data: Adm, settings: Settings):
    if not settings.is_super(cb.from_user.id):
        await cb.answer("Faqat asosiy admin uchun", show_alert=True)
        return
    name = settings.extra_admins.get(callback_data.user_id) or callback_data.user_id
    b = InlineKeyboardBuilder()
    b.button(text="✅ Ha, olib tashlash", callback_data=Adm(act="del_ok", user_id=callback_data.user_id))
    b.button(text="⬅️ Bekor", callback_data=Nav(to="admins"))
    b.adjust(2)
    await show(cb, f"<b>{escape(str(name))}</b> adminlikdan olinsinmi?", b.as_markup())


@router.callback_query(Adm.filter(F.act == "del_ok"))
async def adm_del_ok(cb: CallbackQuery, callback_data: Adm, db: Database, settings: Settings):
    if not settings.is_super(cb.from_user.id):
        await cb.answer("Faqat asosiy admin uchun", show_alert=True)
        return
    await db.remove_admin(callback_data.user_id)
    settings.extra_admins.pop(callback_data.user_id, None)
    await show(cb, *admins_view(cb.from_user.id, settings))


# ---------------- eksport ----------------
@router.callback_query(Nav.filter(F.to == "export"))
async def nav_export(cb: CallbackQuery, db: Database, config: Config, settings: Settings):
    await cb.answer("Fayl tayyorlanmoqda...")
    now = datetime.now(config.tz)
    rows = await db.export_tasks(now - timedelta(days=settings.lookback_days))
    if not rows:
        await cb.message.answer(f"Oxirgi {settings.lookback_days} kunda checklist yo'q.")
        return
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # Excel (ru/uz) uchun ; ajratuvchi
    w.writerow(["Guruh", "Sana", "Checklist", "Band", "Holat", "Bajardi", "Bajarilgan vaqt",
                "Muallif", "Havola"])
    for r in rows:
        w.writerow([
            r["group_title"] or r["chat_id"],
            r["created_at"].astimezone(config.tz).strftime("%d.%m.%Y %H:%M"),
            " ".join((r["title"] or "").split()),
            r["text"],
            "bajarilgan" if r["is_done"] else "BAJARILMAGAN",
            r["done_by"] or "",
            r["done_at"].astimezone(config.tz).strftime("%d.%m.%Y %H:%M") if r["done_at"] else "",
            r["author"] or "",
            message_link(r["chat_id"], r["message_id"]) or "",
        ])
    data = buf.getvalue().encode("utf-8-sig")
    done = sum(1 for r in rows if r["is_done"])
    await cb.message.answer_document(
        BufferedInputFile(data, filename=f"checklist_{now:%Y-%m-%d}.csv"),
        caption=f"📥 Oxirgi {settings.lookback_days} kun: {len(rows)} ta band, "
                f"{done} bajarilgan, {len(rows) - done} bajarilmagan.\nExcel'da ochiladi.")
