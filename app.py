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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8843356010:AAEpUWyEAEC1sT51FL5SsuaYB9p_lthyROw").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8390092754"))  # Render Environment Variables orqali kiriting
DB_PATH = "tanishuvbot.db"

# To'lov ma'lumotlarini o'zingiznikiga almashtiring.
VIP_PRICE = "30 000 so'm"
VIP_DAYS = 30
CARD_NUMBER = os.getenv("CARD_NUMBER", "6262570820096776")
CARD_HOLDER = os.getenv("CARD_HOLDER", "RAHMONOV ABDUSAMAD")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Abdusamadjon_00")

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
            "vip_until": "TEXT DEFAULT ''", "share_username": "INTEGER DEFAULT 0"
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
            receipt_file_id TEXT DEFAULT '')""")


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
    rows = [
        [KeyboardButton("🚀 Qidiruv")],
        [KeyboardButton("👦 O'g'il bola izlash"), KeyboardButton("👧 Qiz bola izlash")],
        [KeyboardButton("📍 Viloyat bo'yicha izlash"), KeyboardButton("💎 VIP obuna")],
        [KeyboardButton("👤 Profilim"), KeyboardButton("✏️ Profilni tahrirlash")],
        [KeyboardButton("💰 Hisobim"), KeyboardButton("🆘 Shikoyat")],
        [KeyboardButton("ℹ️ Yordam")]
    ]
    if is_admin(uid):
        rows.append([KeyboardButton("👑 Admin panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


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
    uid = update.effective_user.id
    r = user(uid)
    if r:
        await update.effective_message.reply_text(
            f"Assalomu alaykum, {r['name']}! 💚", reply_markup=menu(uid))
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
    status = ""
    r = user(uid)
    if r and is_vip(uid) and not is_admin(uid):
        status = f"✅ VIP {r['vip_until'][:10]} gacha faol.\n\n"
    txt = (
        f"💎 VIP OBUNA\n\n{status}"
        "VIP imkoniyatlari:\n"
        "• O'g'il bola/qiz bola bo'yicha qidiruv\n"
        "• Viloyat bo'yicha qidiruv\n"
        "• Profil kartasida VIP belgisi\n\n"
        f"Narxi: {VIP_PRICE} / {VIP_DAYS} kun\n"
        f"Karta: {CARD_NUMBER}\nKarta egasi: {CARD_HOLDER}\n\n"
        "To'lovdan keyin «To'lov qildim» tugmasini bosing va chek rasmini yuboring. "
        "VIP faqat administrator tasdiqlagandan so'ng yoqiladi."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ To'lov qildim (chek yuborish)", callback_data="pay_start")]])
    await message.reply_text(txt, reply_markup=kb)


async def vip_cmd(update, context):
    await vip_panel(update.effective_message, update.effective_user.id)


async def callback_handler(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "pay_start":
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
        if action == "approve":
            await grant_vip(context, target, VIP_DAYS)
            with db() as c:
                c.execute("UPDATE payments SET status='approved' WHERE user_id=? AND status='pending'", (target,))
            await q.message.reply_text(f"✅ {target} uchun VIP {VIP_DAYS} kunga yoqildi.")
            try:
                await context.bot.send_message(target, f"🎉 To'lov tasdiqlandi! VIP {VIP_DAYS} kunga yoqildi.")
            except Exception:
                pass
        else:
            with db() as c:
                c.execute("UPDATE payments SET status='rejected' WHERE user_id=? AND status='pending'", (target,))
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
    with db() as c:
        c.execute("INSERT INTO payments(user_id,created_at,status,receipt_file_id) VALUES(?,?,?,?)",
                  (uid, now().isoformat(), "pending", file_id))
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
                caption=f"💳 VIP to'lov cheki\nFoydalanuvchi ID: {uid}\n"
                        f"Username: @{update.effective_user.username or 'yo‘q'}",
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
    badge = "💎 VIP\n" if is_vip(r["user_id"]) else ""
    txt = badge + profile_text(r) + "\n\n🔒 Shaxsiy username va ID ko'rsatilmaydi."
    kb = SEARCH_KB
    if r["photo_id"]:
        await update.effective_message.reply_photo(r["photo_id"], caption=txt, reply_markup=kb)
    else:
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
        "/vip_on USER_ID [kun]\n/ban USER_ID\n/unban USER_ID\n/search_on\n/search_off")


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


async def router(update, context):
    if not update.message:
        return
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

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

    if text == "🚀 Qidiruv":
        await find_profile(update, context)
    elif text == "👦 O'g'il bola izlash":
        await find_profile(update, context, gender="O'g'il bola")
    elif text == "👧 Qiz bola izlash":
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
    elif text == "💰 Hisobim":
        r = user(uid)
        if not r:
            await update.message.reply_text("Avval /start yuboring.")
        else:
            await update.message.reply_text(
                f"💰 HISOBIM\nID: {uid}\nVIP: {'faol' if is_vip(uid) else 'faol emas'}\n"
                f"Ro'yxatdan o'tgan: {(r['created_at'] or '')[:10]}", reply_markup=menu(uid))
    elif text == "🆘 Shikoyat":
        await report_start(update, context)
    elif text == "ℹ️ Yordam":
        await update.message.reply_text(
            "🚀 Qidiruv — umumiy profil qidirish\n"
            "👦/👧 va viloyat bo'yicha qidiruv — VIP\n"
            "💎 VIP obuna — narx va to'lov\n"
            "Shaxsiy ma'lumot, parol, uy manzili yoki pul yubormang.",
            reply_markup=menu(uid))
    elif text == "👑 Admin panel":
        await admin_panel(update, context)
    elif text == "➡️ Keyingi profil":
        await next_profile(update, context)
    elif text == "⏹ To'xtatish":
        await stop_search(update, context)
    elif text == "🏠 Menyu":
        await update.message.reply_text("Asosiy menyu:", reply_markup=menu(uid))


async def start_cmd(update, context):
    await start(update, context)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "BOTFATHER_TOKENINI_SHU_YERGA_QOYING":
        raise RuntimeError("BOT_TOKEN qatoriga yangi BotFather tokenini kiriting.")
    init_db()
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
    app.add_handler(CommandHandler("profile", my_profile))
    app.add_handler(MessageHandler(filters.PHOTO, router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    logging.info("TanishuvBot ishga tushmoqda. Majburiy kanal obunasi tekshiruvi mavjud emas.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
