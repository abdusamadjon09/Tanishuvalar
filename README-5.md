# Tanishuv Telegram Bot

## GitHub'ga yuklanadigan fayllar
- `app-22.py` — bot kodi
- `requirements.txt` — Python kutubxonalari
- `Procfile` — worker sifatida ishga tushirish sozlamasi
- `.gitignore` — maxfiy va vaqtinchalik fayllarni GitHub'ga yuklamaslik
- `.env.example` — kerakli muhit o'zgaruvchilari namunasi

## 1. GitHub'ga yuklash
Ushbu papkadagi fayllarni yangi GitHub repository ichiga yuklang.
`BOT_TOKEN`, haqiqiy karta ma'lumotlari yoki `.env` faylini GitHub'ga yuklamang.

## 2. Kerakli muhit o'zgaruvchilari
Hosting platformasining Environment Variables bo'limida quyidagilarni kiriting:
- `BOT_TOKEN` — BotFather bergan bot tokeni
- `ADMIN_ID` — administratorning Telegram raqamli ID raqami
- `CARD_NUMBER` — to'lov kartasi
- `CARD_HOLDER` — karta egasining ismi
- `ADMIN_USERNAME` — administrator username'i, masalan `@admin`

## 3. Ishga tushirish
Python 3.10 yoki undan yangiroq versiya tavsiya qilinadi.

Lokal ishga tushirish:
```bash
pip install -r requirements.txt
python app-22.py
```

Hostingda `Procfile` qo'llab-quvvatlansa, start command:
```bash
python app-22.py
```

## Muhim
- Bot doimiy ishlashi uchun polling jarayonini qo'llab-quvvatlaydigan worker/background service kerak.
- SQLite ma'lumotlar bazasi `tanishuvbot.db` nomi bilan yaratiladi. Hosting qayta ishga tushganda disk doimiy saqlanishini tekshiring; aks holda ma'lumotlar yo'qolishi mumkin.
- To'lovlarni faqat haqiqiy pul kelib tushganini tekshirgandan keyin tasdiqlang.
- `ADMIN_ID` ni to'g'ri kiriting; aks holda admin buyruqlari ishlamaydi.
