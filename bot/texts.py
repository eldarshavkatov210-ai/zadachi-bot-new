"""Xabar matnlari: guruhga eslatma (o'zbekcha kirill) va admin uchun hisobot."""
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape

MONTHS_CYR = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
              "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_LAT = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul",
              "avgust", "sentyabr", "oktabr", "noyabr", "dekabr"]

TG_LIMIT = 3900  # Telegram chegarasi 4096, zaxira bilan


@dataclass
class Item:
    """Bitta checklist va undagi bajarilmagan bandlar."""
    message_id: int
    day: date
    title: str
    undone: list[str]
    total: int


@dataclass
class GroupResult:
    chat_id: int
    title: str
    items: list[Item] = field(default_factory=list)
    sent: bool | None = None       # None — yuborilmadi (rejim), True/False — natija
    error: str | None = None

    @property
    def undone_count(self) -> int:
        return sum(len(i.undone) for i in self.items)


def day_cyr(d: date) -> str:
    return f"{d.day}-{MONTHS_CYR[d.month - 1]}"


def day_lat(d: date) -> str:
    return f"{d.day}-{MONTHS_LAT[d.month - 1]}"


def clean_title(title: str | None, group_title: str | None) -> str:
    """'MONDI\\nБанк кирим- чиким' -> 'Банк кирим- чиким'
    (birinchi qator guruh nomi bilan bir xil bo'lsa olib tashlanadi)."""
    lines = [ln.strip() for ln in (title or "").splitlines() if ln.strip()]
    if len(lines) > 1 and group_title:
        brand = group_title.split()[0].lower()
        if lines[0].lower() == brand or lines[0].lower() in group_title.lower():
            lines = lines[1:]
    return " ".join(lines)


def item_text(item: Item) -> str:
    title = item.title
    if not title:
        return ", ".join(item.undone)
    if item.total > 1:
        return f"{title}: " + ", ".join(item.undone)
    return title


def message_link(chat_id: int, message_id: int) -> str | None:
    s = str(chat_id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{message_id}"
    return None  # oddiy guruhlarda xabarga havola bo'lmaydi


def group_by_day(items: list[Item]) -> dict[date, list[Item]]:
    out: dict[date, list[Item]] = {}
    for it in sorted(items, key=lambda i: (i.day, i.message_id)):
        out.setdefault(it.day, []).append(it)
    return out


def split_message(lines: list[str]) -> list[str]:
    chunks, cur = [], ""
    for line in lines:
        add = line + "\n"
        if len(cur) + len(add) > TG_LIMIT and cur:
            chunks.append(cur.rstrip())
            cur = ""
        cur += add
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks


# ---------------- guruhga eslatma ----------------
def build_reminder(chat_id: int, items: list[Item], today: date) -> list[str]:
    days = group_by_day(items)
    has_today = today in days
    has_past = any(d < today for d in days)
    if has_today and has_past:
        intro = "Ўтган кунлардаги ва бугунги баъзи вазифалар (checklist) ҳали белгиланмаган:"
    elif has_today:
        intro = "Бугунги баъзи вазифалар (checklist) ҳали белгиланмаган:"
    else:
        intro = "Ўтган кунлардаги баъзи вазифалар (checklist) ҳали белгиланмаган:"

    lines = ["Ассалому алейкум!", "", intro, ""]
    for d, day_items in days.items():
        suffix = " (бугун)" if d == today else ""
        lines.append(f"<b>{day_cyr(d)}{suffix}:</b>")
        for it in day_items:
            text = escape(item_text(it))
            link = message_link(chat_id, it.message_id)
            lines.append(f'• <a href="{link}">{text}</a>' if link else f"• {text}")
        lines.append("")
    lines.append("Илтимос, буларни текшириб чиқиб, checklist'да белгилаб қўйинг. Раҳмат!")
    return split_message(lines)


# ---------------- admin uchun hisobot ----------------
def build_report(results: list[GroupResult], now: datetime, mode: str) -> list[str]:
    """mode: 'sent' — guruhlarga yuborildi, 'preview' — faqat ko'rish."""
    lines = [f"📊 <b>Checklist hisoboti</b> — {now:%d.%m.%Y %H:%M}"]
    if mode == "preview":
        lines.append("<i>(oldindan ko'rish — guruhlarga hech narsa yuborilmadi)</i>")
    lines.append("")

    total = 0
    sent_count = 0
    for r in results:
        name = escape(r.title or str(r.chat_id))
        if not r.items:
            lines.append(f"✅ <b>{name}</b> — hammasi bajarilgan")
            lines.append("")
            continue
        total += r.undone_count
        status = ""
        if r.sent is True:
            status = " · eslatma yuborildi ✉️"
            sent_count += 1
        elif r.sent is False:
            status = f" · ⚠️ yuborilmadi: {escape(r.error or 'xato')}"
        lines.append(f"❗ <b>{name}</b> — {r.undone_count} ta bajarilmagan{status}")
        for d, day_items in group_by_day(r.items).items():
            suffix = " (bugun)" if d == now.date() else ""
            lines.append(f"  <u>{day_lat(d)}{suffix}</u>")
            for it in day_items:
                text = escape(item_text(it))
                link = message_link(r.chat_id, it.message_id)
                lines.append(f'   ▫️ <a href="{link}">{text}</a>' if link else f"   ▫️ {text}")
        lines.append("")

    if not results:
        lines.append("Kuzatilayotgan guruh yo'q. Guruhda /ulash buyrug'ini yuboring.")
    else:
        lines.append(f"Jami bajarilmagan bandlar: <b>{total}</b>")
        if mode == "sent":
            lines.append(f"Eslatma yuborilgan guruhlar: <b>{sent_count}</b>")
    return split_message(lines)
