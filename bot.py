import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta
from html import escape

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ===== SOZLAMALAR =====
# Tokenni kod ichiga yozmang. Android/Pydroid muhitida environment variable qilib qo'ying.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Render Environment Variables orqali kiriting
DB_PATH = "tanishuvbot.db"

# To'lov ma'lumotlarini o'zingiznikiga almashtiring.
VIP_PRICE = "30 000 so'm"
VIP_DAYS = 30
VIP_PLANS = {"1 kun": (1, 2000), "1 hafta": (7, 10000), "15 kun": (15, 18000), "1 oy": (30, 30000)}
CARD_NUMBER = os.getenv("CARD_NUMBER", "KARTA_RAQAMINI_SHU_YERGA_KIRITING")
CARD_HOLDER = os.getenv("CARD_HOLDER", "KARTA_EGASI_ISMI")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@ADMIN_USERNAME")

# Voyaga yetmaganlar faqat 13–17 yoshdagilarni, kattalar esa 18+ guruhini ko'radi.
MIN_AGE, MAX_AGE = 13, 50
ADULT_AGE = 18
NAME, AGE, GENDER, REGION, PHOTO, INTERESTS, CONSENT = range(7)

REGIONS = [
    "Toshkent shahri", "Toshkent viloyati", "Andijon viloyati",
    "Buxoro viloyati", "Farg'ona viloyati", "Jizzax viloyati",
    "Xorazm viloyati", "Namangan viloyati", "Navoiy viloyati",
    "Qashqadaryo viloyati", "Qoraqalpog'iston Respublikasi",
    "Samarqand viloyati", "Sirdaryo viloyati", "Surxondaryo viloyati"
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.utcnow()


def is_admin(uid):
    return ADMIN_ID != 0 and uid == ADMIN_ID


def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            name TEXT, age INTEGER, gender TEXT, region TEXT,
            photo_id TEXT DEFAULT '', interests TEXT DEFAULT '',
            active INTEGER DEFAULT 1, banned INTEGER DEFAULT 0,
            created_at TEXT, vip_until TEXT DEFAULT '',
            share_username INTEGER DEFAULT 0
        )""")
        cols = {row["name"] for row in c.execute("PRAGMA table_info(users)")}
        additions = {
            "username": "TEXT DEFAULT ''", "photo_id": "TEXT DEFAULT ''",
            "interests": "TEXT DEFAULT ''", "active": "INTEGER DEFAULT 1",
            "banned": "INTEGER DEFAULT 0", "created_at": "TEXT DEFAULT ''",
            "vip_until": "TEXT DEFAULT ''", "share_username": "INTEGER DEFAULT 0",
            "balance": "INTEGER DEFAULT 0"
        }
        for col, ddl in additions.items():
            if col not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        c.execute("""CREATE TABLE IF NOT EXISTS blocks(
            user_id INTEGER, blocked_id INTEGER,
            PRIMARY KEY(user_id, blocked_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER,
            reported_id INTEGER, reason TEXT, created_at TEXT,
            status TEXT DEFAULT 'new')""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            created_at TEXT, status TEXT DEFAULT 'pending',
            receipt_file_id TEXT DEFAULT '', amount INTEGER DEFAULT 0,
            payment_type TEXT DEFAULT 'vip', target_user_id INTEGER DEFAULT 0, vip_days INTEGER DEFAULT 30)""")
        pcols = {row["name"] for row in c.execute("PRAGMA table_info(payments)")}
        for col, ddl in {"amount":"INTEGER DEFAULT 0", "payment_type":"TEXT DEFAULT 'vip'", "target_user_id":"INTEGER DEFAULT 0", "vip_days":"INTEGER DEFAULT 30"}.items():
            if col not in pcols: c.execute(f"ALTER TABLE payments ADD COLUMN {col} {ddl}")
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS transfers(id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER, receiver_id INTEGER, amount INTEGER, created_at TEXT)")


def user(uid):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def is_vip(uid):
    if is_admin(uid):
        return True
    r = user(uid)
    if not r or not r["vip_until"]:
        return False
    try:
        return datetime.fromisoformat(r["vip_until"]) > now()
    except (ValueError, TypeError):
        return False


def menu(uid):
    # Bir xil, tartibli menyu: har bir tugma o'z bo'limini ochadi.
    rows = [
        [KeyboardButton("🚀 QIDIRUV")],
        [KeyboardButton("👦 O'g'il bola izlash"), KeyboardButton("👧 Qiz bola izlash")],
        [KeyboardButton("📍 Viloyat bo'yicha izlash"), KeyboardButton("💎 VIP obuna")],
        [KeyboardButton("👤 Profilim"), KeyboardButton("✏️ Profilni tahrirlash")],
        [KeyboardButton("💰 Hisobim"), KeyboardButton("➕ Hisob to'ldirish")],
        [KeyboardButton("💸 Pul o'tkazish"), KeyboardButton("🆘 Shikoyat")],
        [KeyboardButton("ℹ️ Yordam"), KeyboardButton("❌ Bekor qilish")]
    ]
    if is_admin(uid):
        rows.append([KeyboardButton("👑 Admin panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, row_width=2, is_persistent=True)


AGES = ReplyKeyboardMarkup(
    [[str(i) for i in range(13, 19)], [str(i) for i in range(19, 25)],
     [str(i) for i in range(25, 31)], [str(i) for i in range(31, 37)],
     [str(i) for i in range(37, 43)], [str(i) for i in range(43, 51)]],
    resize_keyboard=True, one_time_keyboard=True
)
GENDERS = ReplyKeyboardMarkup([["👦 O'g'il bola", "👧 Qiz bola"]],
                              resize_keyboard=True, one_time_keyboard=True)
REGION_MENU = ReplyKeyboardMarkup([[x] for x in REGIONS],
                                  resize_keyboard=True, one_time_keyboard=True)
CONSENT_KB = ReplyKeyboardMarkup(
    [["✅ Ha, ulashaman", "❌ Yo'q, ulashmayman"]],
    resize_keyboard=True, one_time_keyboard=True
)
SEARCH_KB = ReplyKeyboardMarkup(
    [["➡️ Keyingi profil", "⏹ To'xtatish"], ["💎 VIP obuna"], ["🏠 Menyu"]],
    resize_keyboard=True
)


def profile_text(r, own=False):
    return (
        f"👤 Ism: {escape(str(r['name'] or ''))}\n"
        f"🎂 Yosh: {r['age']}\n"
        f"⚧ Jins: {escape(str(r['gender'] or ''))}\n"
        f"📍 Viloyat: {escape(str(r['region'] or ''))}\n"
        f"💡 Qiziqishlari: {escape(str(r['interests'] or 'Kiritilmagan'))}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context):
        return ConversationHandler.END
    uid = update.effective_user.id
    r = user(uid)
    if r:
        await update.effective_message.reply_text(
            f"🌿 TANISHUVLAR BOTIGA XUSH KELIBSIZ! 🌿\n\nAssalomu alaykum, {r['name']}! 💚\n\n👇 Kerakli bo'limni menyudan tanlang:", reply_markup=menu(uid))
        return ConversationHandler.END
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Assalomu alaykum! Tanishuv botiga xush kelibsiz.\n\nIsmingizni yozing:")
    return NAME


async def name_step(update, context):
    s = (update.message.text or "").strip()
    if not 2 <= len(s) <= 40:
        await update.message.reply_text("Ism 2–40 belgi bo'lsin.")
        return NAME
    context.user_data["name"] = s
    await update.message.reply_text("Yoshingizni tanlang:", reply_markup=AGES)
    return AGE


async def age_step(update, context):
    s = (update.message.text or "").strip()
    if not s.isdigit() or not MIN_AGE <= int(s) <= MAX_AGE:
        await update.message.reply_text("13–50 oralig'idagi yoshni tanlang.", reply_markup=AGES)
        return AGE
    context.user_data["age"] = int(s)
    await update.message.reply_text("Jinsingizni tanlang:", reply_markup=GENDERS)
    return GENDER


async def gender_step(update, context):
    s = (update.message.text or "").strip()
    if s not in ("👦 O'g'il bola", "👧 Qiz bola"):
        await update.message.reply_text("Tugmalardan tanlang.", reply_markup=GENDERS)
        return GENDER
    context.user_data["gender"] = "O'g'il bola" if s.startswith("👦") else "Qiz bola"
    await update.message.reply_text("Viloyatingizni tanlang:", reply_markup=REGION_MENU)
    return REGION


async def region_step(update, context):
    s = (update.message.text or "").strip()
    if s not in REGIONS:
        await update.message.reply_text("Viloyatni tugmalardan tanlang.", reply_markup=REGION_MENU)
        return REGION
    context.user_data["region"] = s
    await update.message.reply_text("Profil uchun rasm yuboring yoki /skip yozing.")
    return PHOTO


async def photo_step(update, context):
    if update.message.photo:
        context.user_data["photo_id"] = update.message.photo[-1].file_id
    elif (update.message.text or "").strip() == "/skip":
        context.user_data["photo_id"] = ""
    else:
        await update.message.reply_text("Rasm yuboring yoki /skip yozing.")
        return PHOTO
    await update.message.reply_text("Qiziqishlaringizni yozing (1–500 belgi).")
    return INTERESTS


async def interests_step(update, context):
    s = (update.message.text or "").strip()
    if not 1 <= len(s) <= 500:
        await update.message.reply_text("1–500 belgi yozing.")
        return INTERESTS
    context.user_data["interests"] = s
    if context.user_data["age"] >= ADULT_AGE:
        await update.message.reply_text(
            "VIP foydalanuvchilarga username/lichka ma'lumotingiz ko'rinishiga rozimisiz?\n"
            "Bu ixtiyoriy; keyin admin orqali o'zgartirishni so'rashingiz mumkin.",
            reply_markup=CONSENT_KB)
        return CONSENT
    return await finish_registration(update, context, 0)


async def consent_step(update, context):
    s = (update.message.text or "").strip()
    if s not in ("✅ Ha, ulashaman", "❌ Yo'q, ulashmayman"):
        await update.message.reply_text("Tugmalardan tanlang.", reply_markup=CONSENT_KB)
        return CONSENT
    return await finish_registration(update, context, int(s.startswith("✅")))


async def finish_registration(update, context, share):
    d = context.user_data
    tg = update.effective_user
    registered_name = d["name"]
    with db() as c:
        c.execute("""
        INSERT OR REPLACE INTO users
        (user_id,username,name,age,gender,region,photo_id,interests,active,banned,
         created_at,vip_until,share_username)
        VALUES(?,?,?,?,?,?,?,?,1,0,?,'',?)
        """, (tg.id, tg.username or "", d["name"], d["age"], d["gender"],
              d["region"], d.get("photo_id", ""), d["interests"],
              now().isoformat(), share))
    context.user_data.clear()
    await update.message.reply_text(
        "✅ Ro'yxatdan o'tish yakunlandi!", reply_markup=menu(tg.id))
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                ADMIN_ID, f"Yangi foydalanuvchi: {tg.id} — {registered_name}")
        except Exception:
            pass
    return ConversationHandler.END


async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi. /start bilan qayta boshlang.",
                                    reply_markup=menu(update.effective_user.id))
    return ConversationHandler.END


async def vip_panel(message, uid):
    """VIP bo'limining asosiy menyusi: sotib olish yoki imkoniyatlar haqida."""
    r = user(uid)
    if r and is_vip(uid) and not is_admin(uid):
        status = f"\n\n✅ Obunangiz faol: {r['vip_until'][:10]} gacha."
    elif is_admin(uid):
        status = "\n\n👑 Siz administrator sifatida VIP imkoniyatlaridan foydalana olasiz."
    else:
        status = "\n\n🔒 Sizda hozircha faol VIP obuna yo'q."
    txt = (
        "💎✨ VIP OBUNA MARKAZI ✨💎\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Profil qidiruvini kengaytiring va qo'shimcha imkoniyatlardan foydalaning!"
        f"{status}\n\n"
        "Quyidan kerakli bo'limni tanlang 👇"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 VIP obunani sotib olish", callback_data="vip_buy_menu")],
        [InlineKeyboardButton("📘 VIP obuna haqida", callback_data="vip_about")],
        [InlineKeyboardButton("🏠 Asosiy menyu", callback_data="vip_main_menu")],
    ])
    await message.reply_text(txt, reply_markup=kb)


async def vip_purchase_menu(message):
    txt = (
        "💳 VIP OBUNANI SOTIB OLISH\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "O'zingizga mos muddatni tanlang. To'lovni balansdan yoki karta orqali qilishingiz mumkin.\n\n"
        "📅 Obuna muddatlari:"
    )
    rows = [[InlineKeyboardButton(f"💎 {name} — {price:,} so'm".replace(',', ' '), callback_data=f"plan:{days}:{price}")]
            for name, (days, price) in VIP_PLANS.items()]
    rows.append([InlineKeyboardButton("⬅️ VIP menyusiga qaytish", callback_data="vip_back")])
    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(rows))


async def vip_about_menu(message, uid):
    txt = (
        "📘💎 VIP OBUNA HAQIDA\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "VIP obuna quyidagi imkoniyatlarni beradi:\n\n"
        "🔎 Jins bo'yicha qidiruvdan foydalanish.\n"
        "📍 Viloyat bo'yicha profil qidirish.\n"
        "👤 Topilgan profilning ismi, yoshi, jinsi va viloyatini ko'rish.\n"
        "💡 Profil egasi kiritgan qiziqishlarini ko'rish.\n"
        "🖼️ Profil egasi rasm yuklagan bo'lsa, rasmni ko'rish.\n"
        "💎 VIP holati faol bo'lgan vaqt davomida ushbu imkoniyatlardan foydalanish.\n\n"
        "🔐 Maxfiylik: profil egasining username'i faqat u ulashishga rozilik bergan bo'lsa ko'rsatiladi.\n"
        "⚠️ VIP obuna boshqa foydalanuvchining roziligisiz uning shaxsiy kontaktini ochib bermaydi.\n\n"
        "💰 Narxlar va muddatlarni ko'rish uchun «VIP obunani sotib olish» tugmasini bosing."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 VIP obunani sotib olish", callback_data="vip_buy_menu")],
        [InlineKeyboardButton("⬅️ VIP menyusiga qaytish", callback_data="vip_back")],
    ])
    await message.reply_text(txt, reply_markup=kb)


async def vip_cmd(update, context):
    await vip_panel(update.effective_message, update.effective_user.id)


async def callback_handler(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "vip_buy_menu":
        await vip_purchase_menu(q.message)
    elif q.data == "vip_about":
        await vip_about_menu(q.message, q.from_user.id)
    elif q.data == "vip_back":
        await vip_panel(q.message, q.from_user.id)
    elif q.data == "vip_main_menu":
        await q.message.reply_text("🏠 ASOSIY MENYU\n━━━━━━━━━━━━━━━━━━\nKerakli bo'limni pastdagi tugmalardan tanlang 👇", reply_markup=menu(q.from_user.id))
    elif q.data.startswith("plan:"):
        _, days, amount = q.data.split(":")
        context.user_data["vip_days"] = int(days)
        context.user_data["payment_amount"] = int(amount)
        context.user_data["payment_type"] = "vip"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Hisobdan to'lash", callback_data=f"vip_wallet:{days}:{amount}"), InlineKeyboardButton("💳 Karta orqali", callback_data=f"vip_card:{days}:{amount}")]])
        await q.message.reply_text(f"💎 Tanlangan obuna: {days} kun, {int(amount):,} so'm. To'lov usulini tanlang.".replace(',', ' '), reply_markup=kb)
    elif q.data.startswith("vip_card:"):
        _, days, amount = q.data.split(":")
        context.user_data["vip_days"] = int(days); context.user_data["payment_amount"] = int(amount)
        context.user_data["payment_type"] = "vip"; context.user_data["await_receipt"] = True
        await q.message.reply_text(f"💳 Karta: {CARD_NUMBER}\nKarta egasi: {CARD_HOLDER}\nSumma: {int(amount):,} so'm. Pul o'tkazgach, chek rasmini yuboring.".replace(',', ' '))
    elif q.data.startswith("vip_wallet:"):
        _, days, amount = q.data.split(":")
        uid = q.from_user.id; days = int(days); amount = int(amount)
        with db() as c:
            row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            if not row or row["balance"] < amount:
                await q.message.reply_text(f"Hisobingizda mablag' yetarli emas. Kerak: {amount:,} so'm. Hisobni to'ldiring.".replace(',', ' '))
                return
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid))
        await grant_vip(context, uid, days)
        await q.message.reply_text(f"✅ VIP {days} kunga yoqildi. Hisobdan {amount:,} so'm yechildi.".replace(',', ' '))
    elif q.data == "pay_start":
        context.user_data["await_receipt"] = True
        await q.message.reply_text("To'lov chekining rasmini yuboring. Chekni tekshirish uchun admin ko'radi.")
    elif q.data.startswith("approve:") or q.data.startswith("reject:"):
        if not is_admin(q.from_user.id):
            await q.message.reply_text("Bu amal faqat administrator uchun.")
            return
        action, raw_uid = q.data.split(":", 1)
        if not raw_uid.isdigit():
            return
        target = int(raw_uid)
        with db() as c:
            payment = c.execute("SELECT * FROM payments WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (target,)).fetchone()
        if not payment:
            await q.message.reply_text("Kutilayotgan to'lov topilmadi.")
            return
        if action == "approve":
            if payment["payment_type"] == "topup":
                with db() as c:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (payment["amount"], target))
                notice = f"💰 Hisobingizga {payment['amount']:,} so'm qo'shildi.".replace(',', ' ')
            elif payment["payment_type"] == "gift_vip":
                await grant_vip(context, payment["target_user_id"], payment["vip_days"])
                notice = f"🎉 Sizga {payment['vip_days']} kunlik VIP sovg'a qilindi!"
            else:
                await grant_vip(context, target, payment["vip_days"] or VIP_DAYS)
                notice = f"🎉 To'lov tasdiqlandi! VIP {payment['vip_days'] or VIP_DAYS} kunga yoqildi."
            with db() as c:
                c.execute("UPDATE payments SET status='approved' WHERE id=?", (payment["id"],))
            await q.message.reply_text(f"✅ To'lov tasdiqlandi: {target}.")
            try: await context.bot.send_message(target, notice)
            except Exception: pass
        else:
            with db() as c:
                c.execute("UPDATE payments SET status='rejected' WHERE id=?", (payment["id"],))
            await q.message.reply_text(f"❌ {target} to'lovi rad etildi.")
            try:
                await context.bot.send_message(target, "To'lov cheki tasdiqlanmadi. Iltimos, admin bilan bog'laning.")
            except Exception:
                pass


async def grant_vip(context, uid, days):
    current = user(uid)
    start_at = now()
    if current and current["vip_until"]:
        try:
            old = datetime.fromisoformat(current["vip_until"])
            if old > start_at:
                start_at = old
        except ValueError:
            pass
    until = (start_at + timedelta(days=days)).isoformat()
    with db() as c:
        c.execute("UPDATE users SET vip_until=? WHERE user_id=?", (until, uid))


async def receipt_handler(update, context):
    if not context.user_data.get("await_receipt"):
        return False
    if not update.message.photo:
        await update.message.reply_text("Iltimos, to'lov chekining rasmini yuboring.")
        return True
    uid = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    amount = int(context.user_data.get("payment_amount", 0))
    ptype = context.user_data.get("payment_type", "vip")
    days = int(context.user_data.get("vip_days", VIP_DAYS))
    target = int(context.user_data.get("gift_target", 0))
    with db() as c:
        c.execute("INSERT INTO payments(user_id,created_at,status,receipt_file_id,amount,payment_type,target_user_id,vip_days) VALUES(?,?,?,?,?,?,?,?)",
                  (uid, now().isoformat(), "pending", file_id, amount, ptype, target, days))
    context.user_data["await_receipt"] = False
    await update.message.reply_text("✅ Chek adminga yuborildi. Tasdiq kutilmoqda.", reply_markup=menu(uid))
    if ADMIN_ID:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"reject:{uid}")
        ]])
        try:
            await context.bot.send_photo(
                ADMIN_ID, file_id,
                caption=f"💳 To'lov cheki\nTuri: {ptype}\nSumma: {amount:,} so'm\nVIP kun: {days}\nMaqsad ID: {target}\nFoydalanuvchi ID: {uid}\nUsername: @{update.effective_user.username or 'yo‘q'}",
                reply_markup=kb)
        except Exception:
            logging.exception("Chekni adminga yuborib bo'lmadi")
    return True


async def find_profile(update, context, gender=None, region=None):
    uid = update.effective_user.id
    own = user(uid)
    if not own:
        await update.effective_message.reply_text("Avval /start orqali ro'yxatdan o'ting.")
        return
    if own["banned"] or not own["active"]:
        await update.effective_message.reply_text("Hisobingiz vaqtincha bloklangan.")
        return
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
        setting = c.execute("SELECT value FROM settings WHERE key='search_enabled'").fetchone()
    if setting and setting["value"] == "0" and not is_admin(uid):
        await update.effective_message.reply_text("Qidiruv vaqtincha o'chirilgan.")
        return
    if (gender or region) and not is_vip(uid):
        await update.effective_message.reply_text(
            "🔒 Bu qidiruv turi faqat VIP obunachilar uchun. VIP obunasini sotib oling 💎")
        await vip_panel(update.effective_message, uid)
        return
    if context.user_data.get("candidate"):
        context.user_data.pop("candidate", None)
    age_group = "age BETWEEN 13 AND 17" if own["age"] < ADULT_AGE else "age >= 18"
    sql = f"""SELECT * FROM users WHERE user_id != ? AND active=1 AND banned=0
              AND {age_group}
              AND user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id=?)
              AND user_id NOT IN (SELECT user_id FROM blocks WHERE blocked_id=?)"""
    args = [uid, uid, uid]
    if gender:
        sql += " AND gender=?"
        args.append(gender)
    if region:
        sql += " AND region=?"
        args.append(region)
    with db() as c:
        rows = c.execute(sql + " ORDER BY RANDOM() LIMIT 50", args).fetchall()
    if not rows:
        await update.effective_message.reply_text("Hozircha mos profil topilmadi.", reply_markup=menu(uid))
        return
    r = random.choice(rows)
    context.user_data["candidate"] = r["user_id"]
    context.user_data["gender_filter"] = gender
    context.user_data["region_filter"] = region
    kb = SEARCH_KB
    if is_vip(uid):
        badge = "💎 VIP profil\n" if is_vip(r["user_id"]) else ""
        txt = badge + profile_text(r) + "\n\n🔒 Shaxsiy username va ID ko'rsatilmaydi."
        if r["photo_id"]:
            await update.effective_message.reply_photo(r["photo_id"], caption=txt, reply_markup=kb)
        else:
            await update.effective_message.reply_text(txt, reply_markup=kb)
    else:
        # VIP bo'lmagan qidiruvchiga profilning ma'lumotlari va rasmi oshkor qilinmaydi.
        txt = ("✅ Foydalanuvchi topildi!\n\n"
               "🔒 Profil rasmi, ismi, yoshi, jinsi, viloyati va qiziqishlari VIP obunachilarga ko'rsatiladi.\n"
               "💎 VIP obunani ochib, profil ma'lumotlarini ko'rishingiz mumkin.")
        await update.effective_message.reply_text(txt, reply_markup=kb)


async def next_profile(update, context):
    if not context.user_data.get("candidate"):
        await update.effective_message.reply_text("Avval qidiruvni boshlang.", reply_markup=menu(update.effective_user.id))
        return
    await find_profile(update, context, context.user_data.get("gender_filter"),
                       context.user_data.get("region_filter"))


async def stop_search(update, context):
    context.user_data.pop("candidate", None)
    context.user_data.pop("gender_filter", None)
    context.user_data.pop("region_filter", None)
    await update.effective_message.reply_text("Qidiruv to'xtatildi.", reply_markup=menu(update.effective_user.id))


async def my_profile(update, context):
    r = user(update.effective_user.id)
    if not r:
        await update.effective_message.reply_text("Avval /start yuboring.")
        return
    txt = profile_text(r, own=True)
    if r["age"] >= ADULT_AGE:
        txt += "\nAloqa ma'lumotini ulashish roziligi: " + ("Ha" if r["share_username"] else "Yo'q")
    txt += "\nVIP: " + ("faol" if is_vip(r["user_id"]) else "faol emas")
    if r["photo_id"]:
        await update.effective_message.reply_photo(r["photo_id"], caption=txt, reply_markup=menu(r["user_id"]))
    else:
        await update.effective_message.reply_text(txt, reply_markup=menu(r["user_id"]))


async def report_start(update, context):
    target = context.user_data.get("candidate")
    if not target:
        await update.effective_message.reply_text("Avval qidiruvda profil tanlang.")
        return
    context.user_data["await_report"] = True
    context.user_data["report_target"] = target
    await update.effective_message.reply_text("Shikoyat sababini yozing.")


async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Faqat administrator uchun.")
        return
    with db() as c:
        total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) n FROM users WHERE active=1 AND banned=0").fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM payments WHERE status='pending'").fetchone()["n"]
        reports = c.execute("SELECT COUNT(*) n FROM reports WHERE status='new'").fetchone()["n"]
    await update.effective_message.reply_text(
        f"👑 ADMIN PANEL\nJami foydalanuvchi: {total}\nFaol: {active}\n"
        f"Kutilayotgan to'lovlar: {pending}\nYangi shikoyatlar: {reports}\n\n"
        "/vip_on USER_ID [kun]\n/gift_vip USER_ID KUN\n/ban USER_ID\n/unban USER_ID\n/search_on\n/search_off\n/set_channel @kanal\n/unset_channel\n/set_card RAQAM ISM\n\nVIP: 1 kun, 7 kun, 15 kun, 30 kun. Admin barcha foydalanuvchilarga VIP bera oladi.")


async def vip_on_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Foydalanish: /vip_on USER_ID [kun]")
        return
    uid = int(context.args[0])
    days = VIP_DAYS
    if len(context.args) > 1 and context.args[1].isdigit():
        days = max(1, min(365, int(context.args[1])))
    await grant_vip(context, uid, days)
    await update.effective_message.reply_text(f"✅ {uid} uchun VIP {days} kunga yoqildi.")
    try:
        await context.bot.send_message(uid, f"🎉 VIP obunangiz {days} kunga yoqildi!")
    except Exception:
        pass


async def ban_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Foydalanish: /ban USER_ID")
        return
    with db() as c:
        c.execute("UPDATE users SET banned=1,active=0 WHERE user_id=?", (int(context.args[0]),))
    await update.effective_message.reply_text("🚫 Foydalanuvchi bloklandi.")


async def unban_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Foydalanish: /unban USER_ID")
        return
    with db() as c:
        c.execute("UPDATE users SET banned=0,active=1 WHERE user_id=?", (int(context.args[0]),))
    await update.effective_message.reply_text("✅ Foydalanuvchi blokdan chiqarildi.")


async def search_toggle(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q.")
        return
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
        value = "0" if update.effective_message.text.startswith("/search_off") else "1"
        c.execute("INSERT OR REPLACE INTO settings VALUES('search_enabled',?)", (value,))
    await update.effective_message.reply_text("Qidiruv " + ("yoqildi." if value == "1" else "o'chirildi."))


async def topup_start(update, context):
    context.user_data["await_topup_amount"] = True
    await update.effective_message.reply_text("Hisobingizga qancha so'm kiritmoqchisiz? Faqat raqam yuboring (masalan 50000).")

async def transfer_start(update, context):
    context.user_data["await_transfer_id"] = True
    await update.effective_message.reply_text("Pul yuboriladigan foydalanuvchining ID raqamini yuboring.")

async def admin_gift_vip(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Faqat administrator uchun."); return
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.effective_message.reply_text("Foydalanish: /gift_vip USER_ID KUN"); return
    target, days = int(context.args[0]), max(1, min(365, int(context.args[1])))
    if not user(target):
        await update.effective_message.reply_text("Bu ID bilan foydalanuvchi topilmadi."); return
    await grant_vip(context, target, days)
    await update.effective_message.reply_text(f"🎁 {target} foydalanuvchisiga {days} kunlik VIP berildi.")
    try: await context.bot.send_message(target, f"🎁 Admin sizga {days} kunlik VIP sovg'a qildi!")
    except Exception: pass

async def set_channel_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q."); return
    if not context.args:
        await update.effective_message.reply_text("Foydalanish: /set_channel @kanal yoki -100... ID"); return
    val = context.args[0]
    with db() as c: c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('required_channel',?)", (val,))
    await update.effective_message.reply_text(f"Majburiy obuna kanali o'rnatildi: {val}. Botni kanalga admin qiling.")

async def unset_channel_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q."); return
    with db() as c: c.execute("DELETE FROM settings WHERE key='required_channel'")
    await update.effective_message.reply_text("Majburiy obuna o'chirildi.")

async def set_card_cmd(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q."); return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Foydalanish: /set_card KARTA_RAQAMI KARTA_EGASI_ISMI"); return
    global CARD_NUMBER, CARD_HOLDER
    CARD_NUMBER = context.args[0]
    CARD_HOLDER = " ".join(context.args[1:])
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('card_number',?)", (CARD_NUMBER,))
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('card_holder',?)", (CARD_HOLDER,))
    await update.effective_message.reply_text("✅ Karta ma'lumotlari yangilandi. Qayta ishga tushganda ham saqlanadi.")


async def check_subscription(update, context):
    uid = update.effective_user.id
    if is_admin(uid): return True
    with db() as c: row = c.execute("SELECT value FROM settings WHERE key='required_channel'").fetchone()
    if not row: return True
    channel = row["value"]
    try:
        member = await context.bot.get_chat_member(channel, uid)
        if member.status in ("member", "administrator", "creator", "restricted") and getattr(member, "is_member", True): return True
    except Exception:
        logging.warning("Majburiy obuna tekshirilmadi; kanal username/ID va bot adminligini tekshiring.")
    await update.effective_message.reply_text(f"🔒 Botdan foydalanish uchun avval {channel} kanaliga obuna bo'ling, so'ng /start bosing.")
    return False


async def router(update, context):
    if not update.message:
        return
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    if not await check_subscription(update, context): return

    # Kutilayotgan matnli amal paytida boshqa menyu tugmasi bosilsa,
    # uni ID/summa sifatida tekshirmaymiz. Eski holat bekor qilinadi va
    # bosilgan tugmaning o'z bo'limi odatdagidek ishlaydi.
    menu_buttons = {
        "🚀 QIDIRUV", "🚀 Qidiruv", "👦 O'g'il bola izlash", "👧 Qiz bola izlash",
        "📍 Viloyat bo'yicha izlash", "💎 VIP obuna", "👤 Profilim",
        "✏️ Profilni tahrirlash", "💰 Hisobim", "➕ Hisob to'ldirish",
        "💸 Pul o'tkazish", "🆘 Shikoyat", "ℹ️ Yordam", "👑 Admin panel",
        "➡️ Keyingi profil", "⏹ To'xtatish", "🏠 Menyu", "❌ Bekor qilish"
    }
    pending_keys = (
        "await_topup_amount", "await_transfer_id", "await_transfer_amount",
        "transfer_target", "await_receipt", "await_region", "await_report",
        "report_target", "payment_amount", "payment_type", "vip_days", "gift_target"
    )
    if text in menu_buttons:
        for key in pending_keys:
            context.user_data.pop(key, None)
        if text == "❌ Bekor qilish":
            await update.message.reply_text(
                "✅ Joriy amal bekor qilindi. Kerakli bo'limni menyudan tanlang.",
                reply_markup=menu(uid))
            return

    if context.user_data.get("await_topup_amount"):
        if not text.isdigit() or not 1000 <= int(text) <= 10000000:
            await update.message.reply_text("1 000 dan 10 000 000 so'mgacha bo'lgan summani raqam bilan yuboring."); return
        context.user_data["await_topup_amount"] = False
        context.user_data["payment_amount"] = int(text); context.user_data["payment_type"] = "topup"; context.user_data["await_receipt"] = True
        await update.message.reply_text(f"💳 {int(text):,} so'm to'lash uchun karta: {CARD_NUMBER}\nKarta egasi: {CARD_HOLDER}\nPul o'tkazgach, chek rasmini yuboring.".replace(',', ' ')); return
    if context.user_data.get("await_transfer_id"):
        if not text.isdigit(): await update.message.reply_text("ID faqat raqamlardan iborat bo'lsin."); return
        target = int(text)
        if target == uid or not user(target):
            await update.message.reply_text("O'zingizga yuborish mumkin emas yoki bu ID topilmadi."); context.user_data["await_transfer_id"] = False; return
        context.user_data["await_transfer_id"] = False; context.user_data["transfer_target"] = target; context.user_data["await_transfer_amount"] = True
        await update.message.reply_text("Qancha so'm yubormoqchisiz? Faqat raqam yuboring."); return
    if context.user_data.get("await_transfer_amount"):
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("Summani musbat raqamda yuboring."); return
        amount = int(text); target = context.user_data.pop("transfer_target"); context.user_data["await_transfer_amount"] = False
        with db() as c:
            sender = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
            if not sender or sender["balance"] < amount:
                await update.message.reply_text("Hisobingizda mablag' yetarli emas."); return
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid)); c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, target))
            c.execute("INSERT INTO transfers(sender_id,receiver_id,amount,created_at) VALUES(?,?,?,?)", (uid,target,amount,now().isoformat()))
        await update.message.reply_text(f"✅ {amount:,} so'm {target} ID ga yuborildi.".replace(',', ' '))
        try: await context.bot.send_message(target, f"💸 Sizga {amount:,} so'm {uid} ID foydalanuvchisidan o'tkazildi.".replace(',', ' '))
        except Exception: pass
        return

    if context.user_data.get("await_receipt"):
        handled = await receipt_handler(update, context)
        if handled:
            return
    if context.user_data.get("await_report") and text:
        target = context.user_data.pop("report_target", None)
        context.user_data.pop("await_report", None)
        with db() as c:
            c.execute("INSERT INTO reports(reporter_id,reported_id,reason,created_at) VALUES(?,?,?,?)",
                      (uid, target, text[:1000], now().isoformat()))
        await update.message.reply_text("✅ Shikoyat adminga saqlandi.", reply_markup=menu(uid))
        if ADMIN_ID:
            try:
                await context.bot.send_message(ADMIN_ID, f"🆘 Shikoyat\nYuboruvchi: {uid}\nProfil: {target}\nSabab: {text[:700]}")
            except Exception:
                pass
        return

    if text in ("🚀 Qidiruv", "🚀 QIDIRUV"):
        await update.message.reply_text("🔎 Qidirilmoqda... mos profil qidiryapman.")
        await find_profile(update, context)
    elif text == "👦 O'g'il bola izlash":
        await update.message.reply_text("🔎 O'g'il bolalar profillari qidirilmoqda...")
        await find_profile(update, context, gender="O'g'il bola")
    elif text == "👧 Qiz bola izlash":
        await update.message.reply_text("🔎 Qiz bolalar profillari qidirilmoqda...")
        await find_profile(update, context, gender="Qiz bola")
    elif text == "📍 Viloyat bo'yicha izlash":
        if not is_vip(uid):
            await update.message.reply_text("🔒 Viloyat bo'yicha izlash uchun VIP obunasini sotib oling 💎")
            await vip_panel(update.message, uid)
        else:
            context.user_data["await_region"] = True
            await update.message.reply_text("Viloyatni tanlang:", reply_markup=REGION_MENU)
    elif context.user_data.get("await_region") and text in REGIONS:
        context.user_data["await_region"] = False
        await find_profile(update, context, region=text)
    elif text == "💎 VIP obuna":
        await vip_panel(update.message, uid)
    elif text == "👤 Profilim":
        await my_profile(update, context)
    elif text == "✏️ Profilni tahrirlash":
        await update.message.reply_text("Profilni o'zgartirish uchun hozircha admin bilan bog'laning: " + ADMIN_USERNAME, reply_markup=menu(uid))
    elif text == "➕ Hisob to'ldirish":
        await topup_start(update, context)
    elif text == "💸 Pul o'tkazish":
        await transfer_start(update, context)
    elif text == "💰 Hisobim":
        r = user(uid)
        if not r:
            await update.message.reply_text("Avval /start yuboring.")
        else:
            await update.message.reply_text(
                f"💰 HISOBIM\nID: {uid}\nBalans: {r['balance']:,} so'm\nVIP: {'faol' if is_vip(uid) else 'faol emas'}\n"
                f"Ro'yxatdan o'tgan: {(r['created_at'] or '')[:10]}".replace(',', ' '), reply_markup=menu(uid))
    elif text == "🆘 Shikoyat":
        await report_start(update, context)
    elif text == "ℹ️ Yordam":
        await update.message.reply_text(
            "🚀 Qidiruv — umumiy profil qidirish\n"
            "👦/👧 va viloyat bo'yicha qidiruv — VIP\n"
            "💎 VIP obuna — narx va to'lov\n"
            "Shaxsiy ma'lumot, parol, uy manzili yoki pul yubormang.\n"
            f"Yordam uchun: {ADMIN_USERNAME}",
            reply_markup=menu(uid))
    elif text == "👑 Admin panel":
        await admin_panel(update, context)
    elif text == "➡️ Keyingi profil":
        await next_profile(update, context)
    elif text == "⏹ To'xtatish":
        await stop_search(update, context)
    elif text == "🏠 Menyu":
        await update.message.reply_text("🌿 ASOSIY MENYU 🌿\n━━━━━━━━━━━━━━━━━━\nKerakli bo'limni tanlang 👇", reply_markup=menu(uid))


async def start_cmd(update, context):
    await start(update, context)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "BOTFATHER_TOKENINI_SHU_YERGA_QOYING":
        raise RuntimeError("BOT_TOKEN qatoriga yangi BotFather tokenini kiriting.")
    init_db()
    global CARD_NUMBER, CARD_HOLDER
    with db() as c:
        saved = dict(c.execute("SELECT key,value FROM settings WHERE key IN ('card_number','card_holder')").fetchall())
    CARD_NUMBER = saved.get("card_number", CARD_NUMBER)
    CARD_HOLDER = saved.get("card_holder", CARD_HOLDER)
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_step)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, age_step)],
            GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, gender_step)],
            REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, region_step)],
            PHOTO: [MessageHandler(filters.PHOTO | filters.Regex(r"^/skip$"), photo_step)],
            INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, interests_step)],
            CONSENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, consent_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(CommandHandler("vip", vip_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("vip_on", vip_on_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("search_on", search_toggle))
    app.add_handler(CommandHandler("search_off", search_toggle))
    app.add_handler(CommandHandler("gift_vip", admin_gift_vip))
    app.add_handler(CommandHandler("set_channel", set_channel_cmd))
    app.add_handler(CommandHandler("unset_channel", unset_channel_cmd))
    app.add_handler(CommandHandler("set_card", set_card_cmd))
    app.add_handler(CommandHandler("profile", my_profile))
    app.add_handler(MessageHandler(filters.PHOTO, router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    logging.info("TanishuvBot ishga tushmoqda. Polling uchun faqat bitta bot nusxasini ishlating.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
