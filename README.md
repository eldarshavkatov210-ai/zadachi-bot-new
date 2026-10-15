# Checklist nazorati boti (aiogram 3 + PostgreSQL)

Bot Telegram guruhlardagi **checklist**larni doimiy kuzatadi va har kuni belgilangan vaqtda (standart: 20:00, Toshkent):

1. har bir guruhga bugungi va o'tgan kunlardagi **belgilanmagan vazifalar** ro'yxatini o'zbekcha (kirill) eslatma qilib yuboradi (superguruhlarda har bir band checklistga havola bo'ladi);
2. sizga (admin) barcha guruhlar bo'yicha **hisobot** yuboradi.

## Tuzilma

```
bot/
  __main__.py   ishga tushirish, jadval (APScheduler), polling
  config.py     .env sozlamalari
  db.py         PostgreSQL (asyncpg): groups, checklists, checklist_tasks, reminder_log
  handlers.py   checklist hodisalari + buyruqlar
  checker.py    tekshiruv, eslatma va hisobot yuborish
  texts.py      xabar matnlari
railway.json    Railway sozlamasi
.env            maxfiy ma'lumotlar (GitHub'ga yuklanmaydi)
```

## Buyruqlar

Shaxsiy chatda (faqat admin): `/admin` (yoki `/start`) — tugmali admin panel: hisobot, hoziroq eslatma, guruhlar boshqaruvi (eslatmani o'chirish, kuzatuvni to'xtatish, o'chirish), statistika va eng faol ijrochilar, guruhlarga xabar yuborish, eslatmalar tarixi, sozlamalar (tekshiruv vaqtlari, davr, pauza — qayta ishga tushirmasdan), adminlar, Excel (CSV) eksport.
Paneldagi sozlamalar bazada saqlanadi; `.env` dagi qiymatlar faqat boshlang'ich qiymat.
Buyruqlar: `/hisobot` — holatni ko'rish (guruhlarga yubormaydi), `/tekshir` — hoziroq eslatma yuborish, `/guruhlar` — guruhlar va statistika, `/yordam`.
Guruh ichida (faqat admin): `/ulash` — kuzatuvga qo'shish, `/uzish` — olib tashlash. Admin botni yangi guruhga qo'shsa, guruh avtomatik kuzatuvga olinadi.

## Botni tayyorlash (bir marta)

1. @BotFather → `/revoke` → yangi token oling.
2. @BotFather → `/setprivacy` → botni tanlang → **Disable**.
3. Botni 4 ta guruhga qo'shing va **admin** qiling (aks holda checklistlarni ko'rmaydi).
4. Botga shaxsiy chatda `/start` bosing (hisobot kelishi uchun).

## Railway'ga joylash

1. Loyihani GitHub'ga yuklang (`.env` yuklanmaydi — `.gitignore`).
2. Railway → **New Project** → **Deploy from GitHub repo** → shu repo.
3. Shu loyihada **+ New → Database → PostgreSQL**.
4. Bot servisi → **Variables**: `.env` dagi qiymatlarni kiriting, `DATABASE_URL` uchun `${{Postgres.DATABASE_URL}}` yozing.
5. Deploy. Loglarda `Bot ishga tushdi` chiqadi, sizga ham xabar keladi.

Jadvallar birinchi ishga tushishda avtomatik yaratiladi.

## Lokal ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot
```

## Cheklovlar

- Bot faqat o'zi guruhga qo'shilgandan **keyin** yuborilgan checklistlarni ko'radi. Eski checklistni kimdir belgilasa yoki bekor qilsa, u ham bazaga tushadi.
- Eslatmalar bot nomidan yuboriladi.
- Bitta nusxa ishlashi kerak (Railway'da replicas = 1), aks holda Telegram `Conflict` xatosini beradi.
