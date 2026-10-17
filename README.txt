TANISHUV BOT — GITHUB / RENDER

1. GitHub repository ichiga app.py, requirements.txt va Procfile fayllarini yuklang.
2. Render'da Background Worker yarating va repository'ni ulang.
3. Environment Variables bo'limiga quyidagilarni kiriting:
   BOT_TOKEN = BotFather tokeni
   ADMIN_ID = Telegram raqamingiz emas, Telegram user ID raqamingiz
   CARD_NUMBER = VIP to'lov kartasi (ixtiyoriy)
   CARD_HOLDER = Karta egasining ismi (ixtiyoriy)
   ADMIN_USERNAME = @username (ixtiyoriy)
4. Deploy qiling.

MUHIM:
- Ushbu kodda majburiy kanal/guruh obunasini tekshirish funksiyasi yo'q.
- VIP to'lov chekini admin tasdiqlagach VIP yoqiladi.
- Agar eski bot nusxasi Render'da ishlayotgan bo'lsa, to'g'ri repository/branch ulanganini tekshiring va qayta deploy qiling.
- Render qayta ishga tushganda SQLite fayli yo'qolishi mumkin; doimiy ma'lumotlar uchun persistent disk yoki tashqi PostgreSQL kerak bo'lishi mumkin.
- Bot tokenini ommaga yoki GitHub kodiga yozmang.
