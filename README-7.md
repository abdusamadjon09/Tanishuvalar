# Tanishuv Telegram Bot

## GitHub fayllari
- `app-22.py` — Telegram bot kodi
- `requirements.txt` — kerakli Python kutubxonalari
- `Procfile` — worker ishga tushirish buyrug'i
- `.gitignore` — vaqtinchalik va maxfiy fayllarni chetga chiqaradi
- `.env.example` — muhit sozlamalari namunasi

## Ishga tushirish
Hostingdagi Environment Variables bo'limiga `BOT_TOKEN`, `ADMIN_ID`, `CARD_NUMBER`, `CARD_HOLDER` va `ADMIN_USERNAME` qiymatlarini kiriting. Start command: `python app-22.py`.

## VIP menyusi
VIP obuna tugmasida ikki asosiy bo'lim mavjud: `VIP obunani sotib olish` va `VIP obuna haqida`. Sotib olishda 1 kun, 1 hafta, 15 kun va 1 oylik tariflar ko'rsatiladi. VIP ma'lumot sahifasida jins/viloyat bo'yicha qidiruv va profil tafsilotlari haqida tushuntirish beriladi.

## Muhim
- Botni bir vaqtda faqat bitta polling instance'da ishlating.
- Haqiqiy bot tokeni va karta ma'lumotlarini GitHub'ga joylamang.
- To'lov cheklarini pul tushganini tekshirgandan keyin tasdiqlang.
- SQLite bazasi doimiy diskda saqlanishi kerak.
