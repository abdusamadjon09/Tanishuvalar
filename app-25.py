import asyncio
import json
import logging
import os
import random
import re
import shutil
import socket
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta
from html import escape

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, Defaults, filters
)

# ===================== SOZLAMALAR =====================
# Maxfiy ma'lumotlar kod ichiga yozilmaydi: Render/Pydroid Environment orqali kiritiladi.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x) for x in (os.getenv("ADMIN_IDS", "") + "," + os.getenv("ADMIN_ID", "")).replace(" ", "").split(",")
    if x.isdigit() and int(x) != 0
}
DB_PATH = os.getenv("BOT_DB_PATH", "tanishuvbot.db")

CARD_NUMBER = os.getenv("CARD_NUMBER", "KARTA_RAQAMINI_SHU_YERGA_KIRITING")
CARD_HOLDER = os.getenv("CARD_HOLDER", "KARTA_EGASI_ISMI")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@ADMIN_USERNAME")

# Hisob to'ldirish chegaralari (so'm)
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "5000") or 5000)
MAX_TOPUP = int(os.getenv("MAX_TOPUP", "5000000") or 5000000)

# VIP tariflari: kun -> (nomi, standart narx). Narxlarni admin panelidan o'zgartirish mumkin.
PLANS = {
    1: ("1 kunlik", 3000),
    7: ("Haftalik (7 kun)", 10000),
    15: ("15 kunlik", 18000),
    30: ("Oylik (30 kun)", 30000),
}
VIP_DAYS = 30  # eski chek-tasdiqlash tizimi uchun

# Voyaga yetmaganlar faqat 13-17 yoshdagilarni, kattalar esa 18+ guruhini ko'radi.
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


# ===================== YORDAMCHI FUNKSIYALAR =====================
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.utcnow()


def is_admin(uid):
    return uid in ADMIN_IDS


def fmt_money(n):
    return f"{int(n):,}".replace(",", " ") + " so'm"


def get_setting(key, default=""):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))


def plan_price(days):
    try:
        return int(get_setting(f"price_{days}", PLANS[days][1]))
    except (ValueError, KeyError):
        return PLANS[days][1]


def card_info():
    return get_setting("card_number", CARD_NUMBER), get_setting("card_holder", CARD_HOLDER)


def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            name TEXT, age INTEGER, gender TEXT, region TEXT,
            photo_id TEXT DEFAULT '', interests TEXT DEFAULT '',
            active INTEGER DEFAULT 1, banned INTEGER DEFAULT 0,
            created_at TEXT, vip_until TEXT DEFAULT '',
            share_username INTEGER DEFAULT 0, balance INTEGER DEFAULT 0
        )""")
        cols = {row["name"] for row in c.execute("PRAGMA table_info(users)")}
        additions = {
            "username": "TEXT DEFAULT ''", "photo_id": "TEXT DEFAULT ''",
            "interests": "TEXT DEFAULT ''", "active": "INTEGER DEFAULT 1",
            "banned": "INTEGER DEFAULT 0", "created_at": "TEXT DEFAULT ''",
            "vip_until": "TEXT DEFAULT ''", "share_username": "INTEGER DEFAULT 0",
            "balance": "INTEGER DEFAULT 0",
        }
        for col, ddl in additions.items():
            if col not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        c.execute("""CREATE TABLE IF NOT EXISTS blocks(
            user_id INTEGER, blocked_id INTEGER, PRIMARY KEY(user_id, blocked_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER,
            reported_id INTEGER, reason TEXT, created_at TEXT, status TEXT DEFAULT 'new')""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            created_at TEXT, status TEXT DEFAULT 'pending',
            receipt_file_id TEXT DEFAULT '')""")
        pcols = {row["name"] for row in c.execute("PRAGMA table_info(payments)")}
        if "amount" not in pcols:
            c.execute("ALTER TABLE payments ADD COLUMN amount INTEGER DEFAULT 0")
        if "kind" not in pcols:
            c.execute("ALTER TABLE payments ADD COLUMN kind TEXT DEFAULT 'vip'")
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS tx(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
            kind TEXT, note TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT UNIQUE,
            title TEXT, link TEXT DEFAULT '')""")
        # Anonim suhbat: juftliklar va qidiruv navbati
        c.execute("""CREATE TABLE IF NOT EXISTS chats(
            user_id INTEGER PRIMARY KEY, partner_id INTEGER, started_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS queue(
            user_id INTEGER PRIMARY KEY, want_gender TEXT, want_region TEXT, since TEXT)""")
        c.execute("DELETE FROM queue")  # bot qayta ishga tushganda eski navbat tozalanadi


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


def vip_status_text(r):
    if not r:
        return "faol emas"
    if is_admin(r["user_id"]):
        return "admin (cheksiz)"
    if is_vip(r["user_id"]):
        return f"faol, {r['vip_until'][:10]} gacha"
    return "faol emas"


def extend_vip(uid, days):
    """VIP muddatini uzaytiradi (amaldagi muddat ustiga qo'shadi). Yangi tugash sanasini qaytaradi."""
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
    return until


def log_tx(c, uid, amount, kind, note=""):
    c.execute("INSERT INTO tx(user_id,amount,kind,note,created_at) VALUES(?,?,?,?,?)",
              (uid, int(amount), kind, note, now().isoformat()))


def add_balance(uid, amount, kind="topup", note=""):
    """Balansga qo'shadi (manfiy bo'lsa ayiradi, lekin 0 dan pastga tushirmaydi). Haqiqiy o'zgarishni qaytaradi."""
    with db() as c:
        row = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return None
        old = int(row["balance"] or 0)
        new = max(0, old + int(amount))
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (new, uid))
        log_tx(c, uid, new - old, kind, note)
    return new - old


def spend_balance(uid, amount, kind, note=""):
    with db() as c:
        cur = c.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, uid, amount))
        if cur.rowcount != 1:
            return False
        log_tx(c, uid, -amount, kind, note)
    return True


# ===================== TUGMALAR VA DIZAYN =====================
def kb(text, style=None):
    """Pastki klaviatura tugmasi. style: primary (ko'k), success (yashil), danger (qizil)."""
    if not style:
        return KeyboardButton(text)
    try:
        return KeyboardButton(text, style=style)
    except TypeError:  # eski python-telegram-bot
        return KeyboardButton(text, api_kwargs={"style": style})


def ib(text, data=None, url=None, style=None):
    kw = {}
    if data is not None:
        kw["callback_data"] = data
    if url:
        kw["url"] = url
    if not style:
        return InlineKeyboardButton(text, **kw)
    try:
        return InlineKeyboardButton(text, style=style, **kw)
    except TypeError:
        return InlineKeyboardButton(text, api_kwargs={"style": style}, **kw)


def ikb(*rows):
    return InlineKeyboardMarkup([list(r) for r in rows])


B_SEARCH = "🚀 Qidiruv"
B_BOY = "👦 O'g'il bola izlash"
B_GIRL = "👧 Qiz bola izlash"
B_REGION = "📍 Viloyat bo'yicha izlash"
B_REGION_VIP = "🔒 Viloyat bo'yicha izlash 💎"  # VIP bo'lmaganlarga ko'rinadi
B_VIP = "💎 VIP obuna"
B_PROFILE = "👤 Profilim"
B_EDIT = "✏️ Profilni tahrirlash"
B_ACCOUNT = "💰 Hisobim"
B_TOPUP = "➕ Hisob to'ldirish"
B_REPORT = "🆘 Shikoyat"
B_HELP = "ℹ️ Yordam"
B_ADMIN = "👑 Admin panel"
B_NEXT = "➡️ Keyingi profil"
B_STOP = "⏹ To'xtatish"
B_HOME = "🏠 Menyu"
MENU_TEXTS = {B_SEARCH, B_BOY, B_GIRL, B_REGION, B_REGION_VIP, B_VIP, B_PROFILE, B_EDIT, B_ACCOUNT,
              B_TOPUP, B_REPORT, B_HELP, B_ADMIN, B_NEXT, B_STOP, B_HOME}


def menu(uid):
    rows = [
        [kb(B_SEARCH)],
        [kb(B_BOY, "success"), kb(B_GIRL, "danger")],
        [kb(B_REGION) if is_vip(uid) else kb(B_REGION_VIP, "danger"), kb(B_VIP, "primary")],
        [kb(B_PROFILE), kb(B_EDIT)],
        [kb(B_ACCOUNT, "success"), kb(B_TOPUP, "primary")],
        [kb(B_REPORT, "danger"), kb(B_HELP)],
    ]
    if is_admin(uid):
        rows.append([kb(B_ADMIN, "primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


AGES = ReplyKeyboardMarkup(
    [[str(i) for i in range(13, 19)], [str(i) for i in range(19, 25)],
     [str(i) for i in range(25, 31)], [str(i) for i in range(31, 37)],
     [str(i) for i in range(37, 43)], [str(i) for i in range(43, 51)]],
    resize_keyboard=True, one_time_keyboard=True
)
GENDERS = ReplyKeyboardMarkup([[kb("👦 O'g'il bola", "success"), kb("👧 Qiz bola", "danger")]],
                              resize_keyboard=True, one_time_keyboard=True)
REGION_MENU = ReplyKeyboardMarkup([[x] for x in REGIONS],
                                  resize_keyboard=True, one_time_keyboard=True)
CONSENT_KB = ReplyKeyboardMarkup(
    [[kb("✅ Ha, ulashaman", "success"), kb("❌ Yo'q, ulashmayman", "danger")]],
    resize_keyboard=True, one_time_keyboard=True
)
SEARCH_KB = ReplyKeyboardMarkup(
    [[kb(B_NEXT, "success"), kb(B_STOP, "danger")], [kb(B_VIP, "primary")], [kb(B_HOME)]],
    resize_keyboard=True
)

LINE = "━━━━━━━━━━━━━━"


def profile_text(r):
    return (
        f"👤 <b>Ism:</b> {escape(str(r['name'] or ''))}\n"
        f"🎂 <b>Yosh:</b> {r['age']}\n"
        f"⚧ <b>Jins:</b> {escape(str(r['gender'] or ''))}\n"
        f"📍 <b>Viloyat:</b> {escape(str(r['region'] or ''))}\n"
        f"💡 <b>Qiziqishlari:</b> {escape(str(r['interests'] or 'Kiritilmagan'))}"
    )


# ===================== MAJBURIY OBUNA =====================
def _chat_ref(chat_id):
    s = str(chat_id)
    return int(s) if s.lstrip("-").isdigit() else s


async def not_subscribed(context, uid):
    """Foydalanuvchi obuna bo'lmagan kanallar ro'yxati (majburiy obuna yoqilgan bo'lsa)."""
    if get_setting("force_sub", "0") != "1":
        return []
    with db() as c:
        chans = c.execute("SELECT * FROM channels").fetchall()
    missing = []
    for ch in chans:
        try:
            m = await context.bot.get_chat_member(_chat_ref(ch["chat_id"]), uid)
            if m.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:  # bot kanalda admin emas bo'lsa: foydalanuvchini bloklamaymiz
            logging.warning("Obunani tekshirib bo'lmadi (%s): %s", ch["chat_id"], e)
    return missing


async def send_sub_gate(message, missing):
    rows = []
    for ch in missing:
        title = ch["title"] or ch["chat_id"]
        if ch["link"]:
            rows.append([ib(f"📢 {title}", url=ch["link"], style="primary")])
    rows.append([ib("✅ Obunani tekshirish", "sub_check", style="success")])
    names = "\n".join(f"• {escape(ch['title'] or ch['chat_id'])}" for ch in missing)
    await message.reply_text(
        f"🔒 <b>Botdan foydalanish uchun kanallarga obuna bo'ling:</b>\n{LINE}\n{names}\n\n"
        "Obuna bo'lgach, «Obunani tekshirish» tugmasini bosing.",
        reply_markup=InlineKeyboardMarkup(rows))


# ===================== RO'YXATDAN O'TISH =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        missing = await not_subscribed(context, uid)
        if missing:
            await send_sub_gate(update.effective_message, missing)
            return ConversationHandler.END
    r = user(uid)
    if r:
        if r["banned"] and not is_admin(uid):
            await update.effective_message.reply_text("🚫 Hisobingiz bloklangan.")
            return ConversationHandler.END
        await update.effective_message.reply_text(
            f"Assalomu alaykum, <b>{escape(str(r['name']))}</b>! 💚", reply_markup=menu(uid))
        return ConversationHandler.END
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Assalomu alaykum! 💚 <b>Tanishuv botiga</b> xush kelibsiz.\n\nIsmingizni yozing:")
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
         created_at,vip_until,share_username,balance)
        VALUES(?,?,?,?,?,?,?,?,1,0,?,
               COALESCE((SELECT vip_until FROM users WHERE user_id=?),''),?,
               COALESCE((SELECT balance FROM users WHERE user_id=?),0))
        """, (tg.id, tg.username or "", d["name"], d["age"], d["gender"],
              d["region"], d.get("photo_id", ""), d["interests"],
              now().isoformat(), tg.id, share, tg.id))
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ <b>Ro'yxatdan o'tish yakunlandi!</b>\n🆔 Sizning ID raqamingiz: <code>{tg.id}</code>",
        reply_markup=menu(tg.id))
    await notify_admins(context, f"🆕 Yangi foydalanuvchi: {tg.id} — {escape(registered_name)}")
    return ConversationHandler.END


async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi. /start bilan qayta boshlang.",
                                    reply_markup=menu(update.effective_user.id))
    return ConversationHandler.END


async def notify_admins(context, text, **kw):
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, text, **kw)
        except Exception:
            logging.warning("Adminga xabar yuborib bo'lmadi: %s", aid)


# ===================== HISOB VA VIP =====================
async def account_panel(message, uid):
    r = user(uid)
    if not r:
        await message.reply_text("Avval /start yuboring.")
        return
    txt = (
        f"💰 <b>HISOBIM</b>\n{LINE}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"💵 Balans: <b>{fmt_money(r['balance'] or 0)}</b>\n"
        f"💎 VIP: {vip_status_text(r)}\n"
        f"📅 Ro'yxatdan o'tgan: {(r['created_at'] or '')[:10]}\n\n"
        "ID raqamingizni bosib nusxalash mumkin."
    )
    await message.reply_text(txt, reply_markup=ikb(
        [ib("➕ Hisobni to'ldirish", "topup", style="success")],
        [ib("💎 VIP sotib olish", "vipinfo", style="primary"), ib("📜 Tranzaksiyalar", "tx")]))


async def vip_panel(message, uid):
    r = user(uid)
    bal = int(r["balance"] or 0) if r else 0
    status = ""
    if r and is_vip(uid) and not is_admin(uid):
        status = f"✅ <b>VIP faol:</b> {r['vip_until'][:10]} gacha\n\n"
    plans = "\n".join(f"▫️ {PLANS[d][0]}: <b>{fmt_money(plan_price(d))}</b>" for d in PLANS)
    txt = (
        f"💎 <b>VIP OBUNA</b>\n{LINE}\n{status}"
        "<b>VIP imkoniyatlari:</b>\n"
        "• 👦/👧 O'g'il bola / qiz bola bo'yicha qidiruv\n"
        "• 📍 Viloyat bo'yicha qidiruv\n"
        "• 💬 /link — suhbatdoshning <b>lichkasini (username)</b> ko'rish\n"
        "• ⚧ /vip — suhbatdosh <b>jinsini</b> aniqlash\n"
        "• 🔗 Suhbatda username / havola yuborish\n\n"
        "ℹ️ Lichka faqat 18+ yoshdagi va ulashishga rozi bo'lgan foydalanuvchilarniki ko'rinadi.\n\n"
        f"<b>Tariflar:</b>\n{plans}\n\n"
        f"💰 Balansingiz: <b>{fmt_money(bal)}</b>\n"
        "VIP balansdan sotib olinadi. Balans yetmasa, avval hisobni to'ldiring."
    )
    styles = {1: None, 7: "primary", 15: "primary", 30: "success"}
    rows = [[ib(f"💎 {PLANS[d][0]} — {fmt_money(plan_price(d))}", f"buy:{d}", style=styles[d])] for d in PLANS]
    rows.append([ib("➕ Hisobni to'ldirish", "topup", style="success")])
    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(rows))


async def topup_start(message, context):
    context.user_data["await_topup"] = True
    context.user_data.pop("await_receipt", None)
    await message.reply_text(
        f"➕ <b>HISOBNI TO'LDIRISH</b>\n{LINE}\n"
        f"Qancha so'm to'ldirmoqchisiz?\n"
        f"Eng kam: <b>{fmt_money(MIN_TOPUP)}</b>, eng ko'p: <b>{fmt_money(MAX_TOPUP)}</b>\n\n"
        "Faqat raqam yozing, masalan: <code>30000</code>",
        reply_markup=ikb([ib("❌ Bekor qilish", "topup_cancel", style="danger")]))


async def handle_topup_amount(update, context, text):
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits or not (MIN_TOPUP <= int(digits) <= MAX_TOPUP):
        await update.message.reply_text(
            f"Summa {fmt_money(MIN_TOPUP)} dan {fmt_money(MAX_TOPUP)} gacha bo'lsin. Raqam yozing.")
        return
    amount = int(digits)
    uid = update.effective_user.id
    with db() as c:
        cur = c.execute("INSERT INTO payments(user_id,created_at,status,amount,kind) VALUES(?,?,?,?,?)",
                        (uid, now().isoformat(), "awaiting", amount, "topup"))
        pid = cur.lastrowid
    context.user_data.pop("await_topup", None)
    context.user_data["await_receipt"] = pid
    card, holder = card_info()
    await update.message.reply_text(
        f"💳 <b>TO'LOV MA'LUMOTLARI</b>\n{LINE}\n"
        f"💵 Summa: <b>{fmt_money(amount)}</b>\n"
        f"💳 Karta: <code>{escape(card)}</code>\n"
        f"👤 Karta egasi: {escape(holder)}\n\n"
        "1️⃣ Shu summani kartaga o'tkazing.\n"
        "2️⃣ To'lov chekining <b>rasmini (skrinshotini)</b> shu yerga yuboring.\n"
        "3️⃣ Admin tasdiqlagach, pul hisobingizga tushadi.",
        reply_markup=ikb([ib("❌ Bekor qilish", "topup_cancel", style="danger")]))


async def receipt_handler(update, context):
    pid = context.user_data.get("await_receipt")
    if not pid:
        return False
    if not update.message.photo:
        await update.message.reply_text("Iltimos, to'lov chekining <b>rasmini</b> yuboring.")
        return True
    uid = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    with db() as c:
        p = c.execute("SELECT * FROM payments WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        if not p or p["status"] != "awaiting":
            context.user_data.pop("await_receipt", None)
            await update.message.reply_text("Bu to'lov so'rovi eskirgan. Qaytadan boshlang.", reply_markup=menu(uid))
            return True
        c.execute("UPDATE payments SET status='pending', receipt_file_id=? WHERE id=?", (file_id, pid))
    context.user_data.pop("await_receipt", None)
    await update.message.reply_text("✅ Chek adminga yuborildi. Tasdiq kutilmoqda ⏳", reply_markup=menu(uid))
    caption = (f"💳 <b>Hisob to'ldirish cheki #{pid}</b>\n"
               f"👤 Foydalanuvchi ID: <code>{uid}</code>\n"
               f"Username: @{escape(update.effective_user.username or 'yo‘q')}\n"
               f"💵 Summa: <b>{fmt_money(p['amount'])}</b>")
    kbd = ikb([ib("✅ Tasdiqlash", f"pok:{pid}", style="success"),
               ib("❌ Rad etish", f"prej:{pid}", style="danger")])
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_photo(aid, file_id, caption=caption, reply_markup=kbd)
        except Exception:
            logging.exception("Chekni adminga yuborib bo'lmadi")
    return True


async def approve_payment(context, pid):
    """To'lovni tasdiqlaydi. Muvaffaqiyatli bo'lsa to'lov qatorini qaytaradi."""
    with db() as c:
        p = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if not p or p["status"] != "pending":
            return None
        cur = c.execute("UPDATE payments SET status='approved' WHERE id=? AND status='pending'", (pid,))
        if cur.rowcount != 1:
            return None
    uid = p["user_id"]
    if p["kind"] == "topup" and p["amount"] > 0:
        add_balance(uid, p["amount"], "topup", f"To'lov #{pid}")
        r = user(uid)
        msg = (f"🎉 To'lov tasdiqlandi! Hisobingizga <b>{fmt_money(p['amount'])}</b> qo'shildi.\n"
               f"💵 Balans: <b>{fmt_money(r['balance'] if r else p['amount'])}</b>")
    else:  # eski turdagi VIP cheki
        extend_vip(uid, VIP_DAYS)
        msg = f"🎉 To'lov tasdiqlandi! VIP {VIP_DAYS} kunga yoqildi."
    try:
        await context.bot.send_message(uid, msg)
    except Exception:
        pass
    return p


async def reject_payment(context, pid):
    with db() as c:
        p = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if not p or p["status"] != "pending":
            return None
        c.execute("UPDATE payments SET status='rejected' WHERE id=? AND status='pending'", (pid,))
    try:
        await context.bot.send_message(
            p["user_id"], f"❌ To'lov cheki tasdiqlanmadi. Savol bo'lsa admin bilan bog'laning: {ADMIN_USERNAME}")
    except Exception:
        pass
    return p


async def buy_vip(q, context, days, confirmed):
    uid = q.from_user.id
    if days not in PLANS:
        return
    price = plan_price(days)
    r = user(uid)
    if not r:
        await q.message.reply_text("Avval /start yuboring.")
        return
    bal = int(r["balance"] or 0)
    if not confirmed:
        if bal < price:
            await q.message.reply_text(
                f"❗ Balans yetarli emas.\n💵 Balans: <b>{fmt_money(bal)}</b>\n"
                f"💎 {PLANS[days][0]}: <b>{fmt_money(price)}</b>\n"
                f"Yetishmaydi: <b>{fmt_money(price - bal)}</b>",
                reply_markup=ikb([ib("➕ Hisobni to'ldirish", "topup", style="success")]))
            return
        await q.message.reply_text(
            f"💎 <b>{PLANS[days][0]}</b> VIP\n💰 Narxi: <b>{fmt_money(price)}</b>\n"
            f"💵 Balansingiz: <b>{fmt_money(bal)}</b>\n\nTasdiqlaysizmi?",
            reply_markup=ikb([ib("✅ Ha, sotib olaman", f"buyok:{days}", style="success"),
                              ib("❌ Yo'q", "close", style="danger")]))
        return
    if not spend_balance(uid, price, "vip", f"VIP {PLANS[days][0]}"):
        await q.message.reply_text("❗ Balans yetarli emas. Hisobni to'ldiring.",
                                   reply_markup=ikb([ib("➕ Hisobni to'ldirish", "topup", style="success")]))
        return
    until = extend_vip(uid, days)
    r = user(uid)
    await q.message.reply_text(
        f"🎉 <b>VIP faollashtirildi!</b>\n💎 {PLANS[days][0]}\n📅 Tugash sanasi: <b>{until[:10]}</b>\n"
        f"💵 Qolgan balans: <b>{fmt_money(r['balance'])}</b>", reply_markup=menu(uid))
    await notify_admins(context, f"💎 VIP sotib olindi: {uid}, {PLANS[days][0]}, {fmt_money(price)}")


async def show_transactions(message, uid):
    with db() as c:
        rows = c.execute("SELECT * FROM tx WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    if not rows:
        await message.reply_text("📜 Hozircha tranzaksiyalar yo'q.")
        return
    names = {"topup": "To'ldirish", "vip": "VIP", "admin": "Admin"}
    lines = []
    for t in rows:
        sign = "➕" if t["amount"] >= 0 else "➖"
        lines.append(f"{sign} {fmt_money(abs(t['amount']))} — {names.get(t['kind'], t['kind'])} ({t['created_at'][:10]})")
    await message.reply_text(f"📜 <b>OXIRGI TRANZAKSIYALAR</b>\n{LINE}\n" + "\n".join(lines))


# ===================== PROFILNI TAHRIRLASH =====================
EDIT_FIELDS = {"name": "ism", "region": "viloyat", "photo": "rasm", "interests": "qiziqishlar"}


async def edit_panel(message, uid):
    r = user(uid)
    rows = [
        [ib("✏️ Ism", "edit:name", style="primary"), ib("📍 Viloyat", "edit:region", style="primary")],
        [ib("🖼 Rasm", "edit:photo", style="success"), ib("💡 Qiziqishlar", "edit:interests", style="success")],
    ]
    if r and r["age"] >= ADULT_AGE:
        rows.append([ib("🔗 Lichka ulashish: " + ("HA ✅" if r["share_username"] else "YO'Q ❌"), "edit:share")])
    await message.reply_text(
        f"✏️ <b>PROFILNI TAHRIRLASH</b>\n{LINE}\nNimani o'zgartirmoqchisiz?\n\n"
        f"ℹ️ Yosh va jinsni xavfsizlik uchun faqat admin o'zgartiradi: {escape(ADMIN_USERNAME)}",
        reply_markup=InlineKeyboardMarkup(rows))


async def handle_edit(update, context, field, text):
    uid = update.effective_user.id
    msg = update.message
    if field == "photo":
        if msg.photo:
            val = msg.photo[-1].file_id
        elif text == "olib tashlash":
            val = ""
        else:
            await msg.reply_text("Rasm yuboring yoki <code>olib tashlash</code> deb yozing.")
            return
    elif field == "name":
        if not 2 <= len(text) <= 40:
            await msg.reply_text("Ism 2–40 belgi bo'lsin.")
            return
        val = text
    elif field == "interests":
        if not 1 <= len(text) <= 500:
            await msg.reply_text("1–500 belgi yozing.")
            return
        val = text
    elif field == "region":
        if text not in REGIONS:
            await msg.reply_text("Viloyatni tugmalardan tanlang.", reply_markup=REGION_MENU)
            return
        val = text
    else:
        context.user_data.pop("edit", None)
        return
    column = {"name": "name", "region": "region", "photo": "photo_id", "interests": "interests"}[field]
    with db() as c:
        c.execute(f"UPDATE users SET {column}=? WHERE user_id=?", (val, uid))
    context.user_data.pop("edit", None)
    await msg.reply_text("✅ Profil yangilandi.", reply_markup=menu(uid))


# ===================== QIDIRUV =====================
async def searching_anim(message):
    """«Qidirilmoqda...» animatsiyasi."""
    m = await message.reply_text("🔎 <b>Qidirilmoqda.</b>")
    for dots in ("..", "..."):
        await asyncio.sleep(0.6)
        try:
            await m.edit_text(f"🔎 <b>Qidirilmoqda{dots}</b>")
        except Exception:
            pass
    await asyncio.sleep(0.4)
    return m


SEARCH_MAX_SECONDS = 10  # profil topilmasa, shuncha soniya qidirishda davom etadi


# ---------- Anonim suhbat (juftlash) ----------
CHAT_KB = ReplyKeyboardMarkup([[kb(B_NEXT, "success"), kb(B_STOP, "danger")]], resize_keyboard=True)
WAIT_KB = ReplyKeyboardMarkup([[kb(B_STOP, "danger")]], resize_keyboard=True)

QUEUE_MAX_AGE_MIN = 60          # navbatda shuncha daqiqadan oshgan so'rovlar o'chiriladi
BLOCK_CONTACT_SHARING = True    # True: VIP bo'lmaganlar suhbatda @username / havola yubora olmaydi
CONTACT_RE = re.compile(r"(@\w{3,}|t\.me|telegram\.me|https?://|www\.)", re.I)
CONTACT_ENTITIES = {"url", "text_link", "mention", "text_mention", "email", "phone_number"}

FOUND_TEXT = (
    "✅ <b>Suhbatdosh topildi!</b> Endi yozishingiz mumkin 👇\n\n"
    "/next — Boshqa muloqotchini izlash\n"
    "/stop — Muloqotni to'xtatish\n"
    "/link — Lichkasini ko'rish👀\n"
    "/vip — Jinsini aniqlash💎"
)


def get_partner(uid):
    with db() as c:
        r = c.execute("SELECT partner_id FROM chats WHERE user_id=?", (uid,)).fetchone()
    return r["partner_id"] if r else None


def in_queue(uid):
    with db() as c:
        return c.execute("SELECT 1 FROM queue WHERE user_id=?", (uid,)).fetchone() is not None


def leave_queue(uid):
    with db() as c:
        c.execute("DELETE FROM queue WHERE user_id=?", (uid,))


def drop_chat(uid):
    """Suhbatni bazadan o'chiradi, sherik ID sini qaytaradi."""
    with db() as c:
        r = c.execute("SELECT partner_id FROM chats WHERE user_id=?", (uid,)).fetchone()
        p = r["partner_id"] if r else None
        c.execute("DELETE FROM chats WHERE user_id=?", (uid,))
        if p:
            c.execute("DELETE FROM chats WHERE user_id=?", (p,))
    return p


def link_chat(a, b):
    t = now().isoformat()
    with db() as c:
        c.execute("DELETE FROM queue WHERE user_id IN (?,?)", (a, b))
        c.execute("INSERT OR REPLACE INTO chats(user_id,partner_id,started_at) VALUES(?,?,?)", (a, b, t))
        c.execute("INSERT OR REPLACE INTO chats(user_id,partner_id,started_at) VALUES(?,?,?)", (b, a, t))


def is_blocked_pair(a, b):
    with db() as c:
        return c.execute(
            "SELECT 1 FROM blocks WHERE (user_id=? AND blocked_id=?) OR (user_id=? AND blocked_id=?)",
            (a, b, b, a)).fetchone() is not None


def _wants(want_gender, want_region, r):
    return (not want_gender or r["gender"] == want_gender) and (not want_region or r["region"] == want_region)


def find_match(own, want_gender, want_region):
    """Navbatda kutayotganlar orasidan mosini topadi.
    Voyaga yetmaganlar faqat voyaga yetmaganlar bilan, kattalar faqat kattalar bilan juftlanadi."""
    cutoff = (now() - timedelta(minutes=QUEUE_MAX_AGE_MIN)).isoformat()
    with db() as c:
        c.execute("DELETE FROM queue WHERE since < ?", (cutoff,))
        rows = c.execute(
            """SELECT u.*, q.want_gender AS want_gender, q.want_region AS want_region
               FROM queue q JOIN users u ON u.user_id=q.user_id
               WHERE q.user_id != ? AND u.active=1 AND u.banned=0 ORDER BY q.since""",
            (own["user_id"],)).fetchall()
    for r in rows:
        if (own["age"] < ADULT_AGE) != (r["age"] < ADULT_AGE):
            continue
        if not _wants(want_gender, want_region, r):
            continue
        if not _wants(r["want_gender"], r["want_region"], own):
            continue
        if is_blocked_pair(own["user_id"], r["user_id"]):
            continue
        return r
    return None


async def end_chat(context, uid):
    """Suhbatni tugatadi va sherikka xabar beradi."""
    p = drop_chat(uid)
    if p:
        try:
            await context.bot.send_message(
                p, "❌ <b>Suhbatdosh muloqotni tugatdi.</b>\n\n"
                   "🚀 Yangi suhbatdosh topish uchun /next ni bosing yoki menyudan «Qidiruv»ni tanlang.",
                reply_markup=menu(p))
        except Exception:
            pass
    return p


async def cmd_guard(update, context):
    """Buyruqlar (/next, /stop, /link) uchun ham ro'yxat, ban va majburiy obuna tekshiruvi."""
    uid = update.effective_user.id
    msg = update.effective_message
    r = user(uid)
    if not r and not is_admin(uid):
        await msg.reply_text("Avval /start orqali ro'yxatdan o'ting.")
        return False
    if r and r["banned"] and not is_admin(uid):
        await msg.reply_text("🚫 Hisobingiz bloklangan.")
        return False
    if not is_admin(uid):
        missing = await not_subscribed(context, uid)
        if missing:
            await send_sub_gate(msg, missing)
            return False
    return True


async def find_profile(update, context, gender=None, region=None):
    uid = update.effective_user.id
    own = user(uid)
    msg = update.effective_message
    if not own:
        await msg.reply_text("Avval /start orqali ro'yxatdan o'ting.")
        return
    if own["banned"] or not own["active"]:
        await msg.reply_text("Hisobingiz vaqtincha bloklangan.")
        return
    if get_setting("search_enabled", "1") == "0" and not is_admin(uid):
        await msg.reply_text("Qidiruv vaqtincha o'chirilgan.")
        return
    if (gender or region) and not is_vip(uid):
        what = "O'g'il bola / qiz bola" if gender else "Viloyat"
        await msg.reply_text(f"🔒 <b>{what}</b> bo'yicha qidiruv faqat 💎 <b>VIP</b> obunachilar uchun.\n"
                             "VIP obunani sotib oling:")
        await vip_panel(msg, uid)
        return

    # Avvalgi suhbat yoki navbatni tugatish
    if get_partner(uid):
        await end_chat(context, uid)
    leave_queue(uid)
    context.user_data["gender_filter"] = gender
    context.user_data["region_filter"] = region
    context.user_data.pop("candidate", None)

    # Navbatda kutayotgan mos odam bormi?
    cand = find_match(own, gender, region)
    while cand:
        try:
            await context.bot.send_message(cand["user_id"], FOUND_TEXT, reply_markup=CHAT_KB)
            break
        except Exception:  # botni bloklagan / chiqib ketgan — navbatdan olib tashlaymiz
            leave_queue(cand["user_id"])
            cand = find_match(own, gender, region)
    if cand:
        link_chat(uid, cand["user_id"])
        context.user_data["candidate"] = cand["user_id"]
        context.application.user_data[cand["user_id"]]["candidate"] = uid
        await msg.reply_text(FOUND_TEXT, reply_markup=CHAT_KB)
        return

    # Hech kim yo'q — navbatga turamiz, topilguncha «Qidirilmoqda»
    with db() as c:
        c.execute("INSERT OR REPLACE INTO queue(user_id,want_gender,want_region,since) VALUES(?,?,?,?)",
                  (uid, gender, region, now().isoformat()))
    await msg.reply_text(
        "🔎 <b>Qidirilmoqda...</b>\n\n"
        "Suhbatdosh topilishi bilan sizga xabar beraman.\nBekor qilish: /stop",
        reply_markup=WAIT_KB)


async def next_profile(update, context):
    await find_profile(update, context, context.user_data.get("gender_filter"),
                       context.user_data.get("region_filter"))


async def stop_search(update, context):
    uid = update.effective_user.id
    msg = update.effective_message
    if get_partner(uid):
        await end_chat(context, uid)
        await msg.reply_text("⏹ Muloqot to'xtatildi.", reply_markup=menu(uid))
    elif in_queue(uid):
        leave_queue(uid)
        await msg.reply_text("⏹ Qidiruv to'xtatildi.", reply_markup=menu(uid))
    else:
        await msg.reply_text("Siz hozir muloqotda emassiz.", reply_markup=menu(uid))
    for k in ("gender_filter", "region_filter"):
        context.user_data.pop(k, None)


async def next_cmd(update, context):
    if await cmd_guard(update, context):
        await next_profile(update, context)


async def stop_cmd(update, context):
    if await cmd_guard(update, context):
        await stop_search(update, context)


async def link_cmd(update, context):
    """/link — suhbatdoshning lichkasi (faqat VIP)."""
    if not await cmd_guard(update, context):
        return
    uid = update.effective_user.id
    msg = update.effective_message
    p = get_partner(uid)
    if not p:
        await msg.reply_text("Siz hozir muloqotda emassiz. 🚀 Qidiruvni boshlang.")
        return
    if not is_vip(uid):
        await msg.reply_text("🔒 <b>Lichkani ko'rish</b> faqat 💎 <b>VIP</b> obunachilar uchun.")
        await vip_panel(msg, uid)
        return
    own, r = user(uid), user(p)
    if not own or not r:
        return
    if r["age"] < ADULT_AGE or own["age"] < ADULT_AGE:
        await msg.reply_text("🔒 Bu suhbatda aloqa ma'lumoti ko'rsatilmaydi.")
    elif not r["share_username"] or not r["username"]:
        await msg.reply_text("🔒 Foydalanuvchi lichkasini ulashmagan.")
    else:
        uname = escape(r["username"])
        await msg.reply_text(f'💬 <b>Lichka:</b> <a href="https://t.me/{uname}">@{uname}</a>')


async def vip_cmd(update, context):
    """/vip — suhbatda bo'lsa: sherik jinsini aniqlash (VIP); aks holda VIP panel."""
    uid = update.effective_user.id
    msg = update.effective_message
    p = get_partner(uid)
    if not p:
        await vip_panel(msg, uid)
        return
    if not is_vip(uid):
        await msg.reply_text("🔒 <b>Jinsni aniqlash</b> faqat 💎 <b>VIP</b> obunachilar uchun.")
        await vip_panel(msg, uid)
        return
    r = user(p)
    if r:
        await msg.reply_text(f"⚧ <b>Suhbatdosh jinsi:</b> {escape(str(r['gender'] or ''))}")


async def relay_message(update, context, partner):
    """Suhbatdoshga xabarni anonim uzatadi."""
    msg = update.message
    uid = update.effective_user.id
    pr = user(partner)
    if not pr or pr["banned"]:
        drop_chat(uid)
        await msg.reply_text("Suhbatdosh mavjud emas. 🚀 Qidiruvni qayta boshlang.", reply_markup=menu(uid))
        return
    if not (msg.text or msg.photo or msg.voice or msg.video_note or msg.sticker
            or msg.video or msg.animation or msg.audio):
        await msg.reply_text("Bu turdagi xabar yuborilmaydi.")
        return
    own = user(uid)
    minor = bool(own and own["age"] < ADULT_AGE)
    if BLOCK_CONTACT_SHARING and (not is_vip(uid) or minor):
        txt = msg.text or msg.caption or ""
        ents = list(msg.entities or []) + list(msg.caption_entities or [])
        if CONTACT_RE.search(txt) or any(e.type in CONTACT_ENTITIES for e in ents):
            await msg.reply_text("🔒 Username, havola va telefon yuborish faqat 💎 VIP (18+) uchun.")
            return
    try:
        await context.bot.copy_message(chat_id=partner, from_chat_id=msg.chat_id,
                                       message_id=msg.message_id, parse_mode=None)
    except Exception:
        drop_chat(uid)
        await msg.reply_text("⚠️ Xabar yetmadi — suhbatdosh chiqib ketgan ko'rinadi.\n"
                             "🚀 Yangi suhbatdosh uchun «Qidiruv»ni bosing.", reply_markup=menu(uid))


async def my_profile(update, context):
    uid = update.effective_user.id
    r = user(uid)
    if not r:
        await update.effective_message.reply_text("Avval /start yuboring.")
        return
    txt = profile_text(r) + f"\n🆔 <b>ID:</b> <code>{uid}</code>"
    if r["age"] >= ADULT_AGE:
        txt += "\nAloqa ma'lumotini ulashish roziligi: " + ("Ha" if r["share_username"] else "Yo'q")
    txt += f"\n💎 <b>VIP:</b> {vip_status_text(r)}"
    txt += f"\n💵 <b>Balans:</b> {fmt_money(r['balance'] or 0)}"
    if r["photo_id"]:
        await update.effective_message.reply_photo(r["photo_id"], caption=txt, reply_markup=menu(uid))
    else:
        await update.effective_message.reply_text(txt, reply_markup=menu(uid))


async def report_start(update, context):
    target = context.user_data.get("candidate")
    if not target:
        await update.effective_message.reply_text("Avval qidiruvda profil tanlang.")
        return
    context.user_data["await_report"] = True
    context.user_data["report_target"] = target
    await update.effective_message.reply_text("Shikoyat sababini yozing.")


# ===================== ADMIN PANEL =====================
def admin_stats_text():
    with db() as c:
        total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) n FROM users WHERE active=1 AND banned=0").fetchone()["n"]
        banned = c.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
        vip = c.execute("SELECT COUNT(*) n FROM users WHERE vip_until>?", (now().isoformat(),)).fetchone()["n"]
        today = c.execute("SELECT COUNT(*) n FROM users WHERE created_at>=?",
                          (now().strftime("%Y-%m-%d"),)).fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM payments WHERE status='pending'").fetchone()["n"]
        reports = c.execute("SELECT COUNT(*) n FROM reports WHERE status='new'").fetchone()["n"]
        total_bal = c.execute("SELECT COALESCE(SUM(balance),0) n FROM users").fetchone()["n"]
        chans = c.execute("SELECT COUNT(*) n FROM channels").fetchone()["n"]
    search = "yoqilgan" if get_setting("search_enabled", "1") != "0" else "o'chirilgan"
    fsub = "yoqilgan" if get_setting("force_sub", "0") == "1" else "o'chirilgan"
    return (
        f"👑 <b>ADMIN PANEL</b>\n{LINE}\n"
        f"👥 Jami: <b>{total}</b> (faol: {active}, bloklangan: {banned})\n"
        f"🆕 Bugun qo'shilgan: <b>{today}</b>\n"
        f"💎 Faol VIP: <b>{vip}</b>\n"
        f"💵 Foydalanuvchilar balansi jami: <b>{fmt_money(total_bal)}</b>\n"
        f"💳 Kutilayotgan to'lovlar: <b>{pending}</b>\n"
        f"🆘 Yangi shikoyatlar: <b>{reports}</b>\n"
        f"🔍 Qidiruv: {search}\n"
        f"📌 Majburiy obuna: {fsub} ({chans} ta kanal)"
    ), pending, reports


def admin_kb(pending, reports):
    search_on = get_setting("search_enabled", "1") != "0"
    return ikb(
        [ib(f"💳 To'lovlar ({pending})", "adm:pending", style="success"),
         ib(f"🆘 Shikoyatlar ({reports})", "adm:reports", style="danger")],
        [ib("🎁 VIP berish", "adm:vipgive", style="primary"), ib("💵 Balans qo'shish", "adm:bal", style="primary")],
        [ib("🔎 Foydalanuvchi", "adm:user"), ib("📢 Xabar yuborish", "adm:bc")],
        [ib("💎 Narxlar", "adm:prices"), ib("💳 Karta", "adm:card")],
        [ib("📌 Majburiy obuna", "adm:chan", style="primary"),
         ib("🔍 Qidiruv: " + ("ON" if search_on else "OFF"), "adm:searchtoggle",
            style="success" if search_on else "danger")],
        [ib("💾 Zaxira (backup)", "adm:backup"), ib("🔄 Yangilash", "adm:home")])


async def admin_panel(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Faqat administrator uchun.")
        return
    context.user_data.pop("adm", None)
    txt, p, r = admin_stats_text()
    await update.effective_message.reply_text(txt, reply_markup=admin_kb(p, r))


def user_card(r):
    banned = "🚫 bloklangan" if r["banned"] else "✅ faol"
    return (
        f"👤 <b>{escape(str(r['name'] or ''))}</b> — ID <code>{r['user_id']}</code>\n{LINE}\n"
        f"🎂 Yosh: {r['age']} | ⚧ {escape(str(r['gender'] or ''))}\n"
        f"📍 {escape(str(r['region'] or ''))}\n"
        f"🔗 Username: @{escape(r['username'] or 'yo‘q')}\n"
        f"💎 VIP: {vip_status_text(r)}\n"
        f"💵 Balans: <b>{fmt_money(r['balance'] or 0)}</b>\n"
        f"📅 Qo'shilgan: {(r['created_at'] or '')[:10]}\n"
        f"Holat: {banned}"
    )


def user_card_kb(r):
    uid = r["user_id"]
    return ikb(
        [ib("+1 kun", f"adm:vip:{uid}:1"), ib("+7 kun", f"adm:vip:{uid}:7"),
         ib("+15 kun", f"adm:vip:{uid}:15"), ib("+30 kun", f"adm:vip:{uid}:30")],
        [ib("❌ VIP olib tashlash", f"adm:unvip:{uid}", style="danger"),
         ib("💵 Balans", f"adm:balto:{uid}", style="primary")],
        [ib("✅ Blokdan chiqarish", f"adm:unban:{uid}", style="success") if r["banned"]
         else ib("🚫 Bloklash", f"adm:ban:{uid}", style="danger")])


async def send_user_card(message, uid):
    r = user(uid)
    if not r:
        await message.reply_text("❗ Bunday foydalanuvchi topilmadi.")
        return
    await message.reply_text(user_card(r), reply_markup=user_card_kb(r))


async def grant_vip_admin(context, uid, days, by_admin=True):
    until = extend_vip(uid, days)
    try:
        await context.bot.send_message(uid, f"🎁 Sizga <b>{days} kunlik VIP</b> berildi!\n📅 Tugash sanasi: <b>{until[:10]}</b>")
    except Exception:
        pass
    return until


def channels_text_kb():
    with db() as c:
        chans = c.execute("SELECT * FROM channels ORDER BY id").fetchall()
    on = get_setting("force_sub", "0") == "1"
    lines = [f"{i}. {escape(ch['title'] or ch['chat_id'])} <code>{escape(ch['chat_id'])}</code>"
             for i, ch in enumerate(chans, 1)] or ["Kanal qo'shilmagan."]
    txt = (f"📌 <b>MAJBURIY OBUNA</b>\n{LINE}\nHolat: <b>{'YOQILGAN ✅' if on else 'O‘CHIRILGAN ❌'}</b>\n\n"
           + "\n".join(lines) +
           "\n\n⚠️ Bot kanalda <b>administrator</b> bo'lishi shart, aks holda obuna tekshirilmaydi.")
    rows = [[ib("🔴 O'chirish" if on else "🟢 Yoqish", "adm:fsub", style="danger" if on else "success")],
            [ib("➕ Kanal qo'shish", "adm:chanadd", style="primary"), ib("🔍 Tekshirish", "adm:chancheck")]]
    for ch in chans:
        rows.append([ib(f"🗑 {ch['title'] or ch['chat_id']}", f"adm:chandel:{ch['id']}", style="danger")])
    rows.append([ib("⬅️ Orqaga", "adm:home")])
    return txt, InlineKeyboardMarkup(rows)


async def admin_callback(q, context, parts):
    msg = q.message
    act = parts[1] if len(parts) > 1 else "home"

    async def ask(action, text, **extra):
        context.user_data["adm"] = {"action": action, **extra}
        await msg.reply_text(text, reply_markup=ikb([ib("❌ Bekor qilish", "adm:cancel", style="danger")]))

    if act == "home" or act == "cancel":
        context.user_data.pop("adm", None)
        txt, p, r = admin_stats_text()
        try:
            await msg.edit_text(txt, reply_markup=admin_kb(p, r))
        except Exception:
            await msg.reply_text(txt, reply_markup=admin_kb(p, r))
    elif act == "pending":
        with db() as c:
            rows = c.execute("SELECT * FROM payments WHERE status='pending' ORDER BY id LIMIT 10").fetchall()
        if not rows:
            await msg.reply_text("✅ Kutilayotgan to'lovlar yo'q.")
        for p in rows:
            cap = (f"💳 <b>To'lov #{p['id']}</b>\n👤 ID: <code>{p['user_id']}</code>\n"
                   f"💵 Summa: <b>{fmt_money(p['amount'])}</b>" if p["amount"] else
                   f"💳 <b>VIP to'lov #{p['id']}</b>\n👤 ID: <code>{p['user_id']}</code>")
            kbd = ikb([ib("✅ Tasdiqlash", f"pok:{p['id']}", style="success"),
                       ib("❌ Rad etish", f"prej:{p['id']}", style="danger")])
            try:
                if p["receipt_file_id"]:
                    await msg.reply_photo(p["receipt_file_id"], caption=cap, reply_markup=kbd)
                else:
                    await msg.reply_text(cap, reply_markup=kbd)
            except Exception:
                await msg.reply_text(cap, reply_markup=kbd)
    elif act == "reports":
        with db() as c:
            rows = c.execute("SELECT * FROM reports WHERE status='new' ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            await msg.reply_text("✅ Yangi shikoyatlar yo'q.")
        for rp in rows:
            await msg.reply_text(
                f"🆘 <b>Shikoyat #{rp['id']}</b>\nYuboruvchi: <code>{rp['reporter_id']}</code>\n"
                f"Profil: <code>{rp['reported_id']}</code>\nSabab: {escape(rp['reason'] or '')}",
                reply_markup=ikb([ib("🚫 Bloklash", f"adm:ban:{rp['reported_id']}", style="danger"),
                                  ib("✅ Ko'rildi", f"adm:repdone:{rp['id']}", style="success")],
                                 [ib("🔎 Profil", f"adm:card:{rp['reported_id']}")]))
    elif act == "repdone" and len(parts) > 2 and parts[2].isdigit():
        with db() as c:
            c.execute("UPDATE reports SET status='done' WHERE id=?", (int(parts[2]),))
        await msg.reply_text("✅ Shikoyat ko'rilgan deb belgilandi.")
    elif act == "card" and len(parts) > 2 and parts[2].lstrip("-").isdigit():
        await send_user_card(msg, int(parts[2]))
    elif act == "vipgive":
        await ask("vip_uid", "🎁 VIP beriladigan foydalanuvchining <b>ID</b> raqamini yozing:")
    elif act == "bal":
        await ask("bal", "💵 <b>ID va summani</b> yozing. Masalan: <code>123456789 20000</code>\n"
                         "Ayirish uchun minus: <code>123456789 -5000</code>")
    elif act == "balto" and len(parts) > 2 and parts[2].isdigit():
        await ask("bal_amount", "💵 Summani yozing (ayirish uchun minus bilan):", target=int(parts[2]))
    elif act == "user":
        await ask("user_uid", "🔎 Foydalanuvchi <b>ID</b> raqamini yozing:")
    elif act == "vip" and len(parts) > 3 and parts[2].isdigit() and parts[3].isdigit():
        uid, days = int(parts[2]), int(parts[3])
        if not user(uid):
            await msg.reply_text("❗ Foydalanuvchi topilmadi.")
            return
        until = await grant_vip_admin(context, uid, days)
        await msg.reply_text(f"✅ {uid} uchun VIP +{days} kun. Tugash: <b>{until[:10]}</b>")
    elif act == "unvip" and len(parts) > 2 and parts[2].isdigit():
        with db() as c:
            c.execute("UPDATE users SET vip_until='' WHERE user_id=?", (int(parts[2]),))
        await msg.reply_text(f"✅ {parts[2]} VIP obunasi olib tashlandi.")
    elif act in ("ban", "unban") and len(parts) > 2 and parts[2].isdigit():
        uid = int(parts[2])
        if is_admin(uid):
            await msg.reply_text("Adminni bloklab bo'lmaydi.")
            return
        with db() as c:
            if act == "ban":
                c.execute("UPDATE users SET banned=1,active=0 WHERE user_id=?", (uid,))
            else:
                c.execute("UPDATE users SET banned=0,active=1 WHERE user_id=?", (uid,))
        await msg.reply_text("🚫 Foydalanuvchi bloklandi." if act == "ban" else "✅ Blokdan chiqarildi.")
    elif act == "prices":
        rows = [[ib(f"{PLANS[d][0]}: {fmt_money(plan_price(d))}", f"adm:price:{d}", style="primary")] for d in PLANS]
        rows.append([ib("⬅️ Orqaga", "adm:home")])
        await msg.reply_text(f"💎 <b>VIP NARXLARI</b>\n{LINE}\nO'zgartirmoqchi bo'lgan tarifni tanlang:",
                             reply_markup=InlineKeyboardMarkup(rows))
    elif act == "price" and len(parts) > 2 and parts[2].isdigit() and int(parts[2]) in PLANS:
        d = int(parts[2])
        await ask("price", f"💎 <b>{PLANS[d][0]}</b> uchun yangi narxni yozing (so'mda, faqat raqam):", days=d)
    elif act == "card" or act == "cardset":
        card, holder = card_info()
        await ask("card", f"💳 Hozirgi karta: <code>{escape(card)}</code>\nEgasi: {escape(holder)}\n\n"
                          "Yangisini shunday yozing: <code>8600123412341234 | Ism Familiya</code>")
    elif act == "chan":
        txt, kbd = channels_text_kb()
        await msg.reply_text(txt, reply_markup=kbd)
    elif act == "fsub":
        set_setting("force_sub", "0" if get_setting("force_sub", "0") == "1" else "1")
        txt, kbd = channels_text_kb()
        try:
            await msg.edit_text(txt, reply_markup=kbd)
        except Exception:
            await msg.reply_text(txt, reply_markup=kbd)
    elif act == "chanadd":
        await ask("chan_add", "📌 Kanalni yozing: <code>@kanal_nomi</code> yoki <code>-1001234567890</code>.\n"
                              "Yopiq kanal bo'lsa, oxiriga havolani ham qo'shing:\n"
                              "<code>-1001234567890 https://t.me/+AbCdEf</code>\n\n"
                              "⚠️ Avval botni shu kanalga <b>administrator</b> qiling.")
    elif act == "chandel" and len(parts) > 2 and parts[2].isdigit():
        with db() as c:
            c.execute("DELETE FROM channels WHERE id=?", (int(parts[2]),))
        txt, kbd = channels_text_kb()
        await msg.reply_text("🗑 Kanal o'chirildi.\n\n" + txt, reply_markup=kbd)
    elif act == "chancheck":
        with db() as c:
            chans = c.execute("SELECT * FROM channels").fetchall()
        if not chans:
            await msg.reply_text("Kanal qo'shilmagan.")
            return
        out = []
        for ch in chans:
            try:
                me = await context.bot.get_chat_member(_chat_ref(ch["chat_id"]), context.bot.id)
                ok = me.status in ("administrator", "creator")
                out.append(f"{'✅' if ok else '⚠️ bot admin emas'} {escape(ch['title'] or ch['chat_id'])}")
            except Exception as e:
                out.append(f"❌ {escape(ch['title'] or ch['chat_id'])}: {escape(str(e)[:60])}")
        await msg.reply_text("🔍 <b>Tekshiruv natijasi</b>\n" + "\n".join(out))
    elif act == "searchtoggle":
        on = get_setting("search_enabled", "1") != "0"
        set_setting("search_enabled", "0" if on else "1")
        txt, p, r = admin_stats_text()
        try:
            await msg.edit_text(txt, reply_markup=admin_kb(p, r))
        except Exception:
            await msg.reply_text(txt, reply_markup=admin_kb(p, r))
    elif act == "bc":
        await ask("broadcast", "📢 Barcha foydalanuvchilarga yuboriladigan <b>xabar matnini</b> yozing:")
    elif act == "bcgo":
        text = context.user_data.pop("bc_text", None)
        if not text:
            await msg.reply_text("Yuboriladigan matn topilmadi.")
            return
        await msg.reply_text("📢 Xabar yuborish boshlandi. Tugagach xabar beraman.")
        asyncio.create_task(do_broadcast(context.bot, text, msg.chat_id))
    elif act == "backup":
        await send_backup(context.bot, msg.chat_id, "💾 Bazaning zaxira nusxasi")


async def admin_input(update, context, text):
    """Admin matn kiritishi (ID, summa, narx, karta, kanal, xabar...)."""
    st = context.user_data.get("adm") or {}
    action = st.get("action")
    msg = update.message

    if action == "vip_uid" or action == "user_uid":
        if not text.lstrip("-").isdigit():
            await msg.reply_text("ID faqat raqamlardan iborat bo'lsin.")
            return
        uid = int(text)
        context.user_data.pop("adm", None)
        if action == "user_uid":
            await send_user_card(msg, uid)
        else:
            if not user(uid):
                await msg.reply_text("❗ Bunday foydalanuvchi topilmadi.")
                return
            await msg.reply_text(
                f"🎁 <code>{uid}</code> uchun VIP tarifini tanlang:",
                reply_markup=ikb(*[[ib(f"💎 {PLANS[d][0]}", f"adm:vip:{uid}:{d}", style="primary")] for d in PLANS]))
    elif action in ("bal", "bal_amount"):
        parts = text.split()
        try:
            if action == "bal":
                uid, amount = int(parts[0]), int(parts[1])
            else:
                uid, amount = st["target"], int(parts[0])
        except (IndexError, ValueError):
            await msg.reply_text("Format: <code>ID summa</code> (masalan <code>123456789 20000</code>)")
            return
        context.user_data.pop("adm", None)
        changed = add_balance(uid, amount, "admin", "Admin tomonidan")
        if changed is None:
            await msg.reply_text("❗ Bunday foydalanuvchi topilmadi.")
            return
        r = user(uid)
        await msg.reply_text(f"✅ {uid}: {'+' if changed >= 0 else ''}{fmt_money(changed)}\n"
                             f"💵 Yangi balans: <b>{fmt_money(r['balance'])}</b>")
        try:
            word = "qo'shildi" if changed >= 0 else "ayirildi"
            await context.bot.send_message(
                uid, f"💵 Hisobingizga admin tomonidan <b>{fmt_money(abs(changed))}</b> {word}.\n"
                     f"Balans: <b>{fmt_money(r['balance'])}</b>")
        except Exception:
            pass
    elif action == "price":
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            await msg.reply_text("Faqat raqam yozing.")
            return
        set_setting(f"price_{st['days']}", int(digits))
        context.user_data.pop("adm", None)
        await msg.reply_text(f"✅ {PLANS[st['days']][0]} narxi: <b>{fmt_money(int(digits))}</b>")
    elif action == "card":
        if "|" not in text:
            await msg.reply_text("Format: <code>karta raqami | Ism Familiya</code>")
            return
        number, holder = [x.strip() for x in text.split("|", 1)]
        if not number or not holder:
            await msg.reply_text("Format: <code>karta raqami | Ism Familiya</code>")
            return
        set_setting("card_number", number)
        set_setting("card_holder", holder)
        context.user_data.pop("adm", None)
        await msg.reply_text(f"✅ Karta yangilandi:\n<code>{escape(number)}</code>\n{escape(holder)}")
    elif action == "chan_add":
        parts = text.split()
        ref = parts[0]
        link = parts[1] if len(parts) > 1 else ""
        try:
            chat = await context.bot.get_chat(_chat_ref(ref))
        except Exception as e:
            await msg.reply_text(f"❌ Kanal topilmadi yoki bot unda yo'q: {escape(str(e)[:80])}")
            return
        if not link:
            if getattr(chat, "username", None):
                link = f"https://t.me/{chat.username}"
            else:
                try:
                    link = await context.bot.export_chat_invite_link(chat.id)
                except Exception:
                    link = ""
        with db() as c:
            c.execute("INSERT OR REPLACE INTO channels(chat_id,title,link) VALUES(?,?,?)",
                      (str(chat.id), chat.title or ref, link))
        context.user_data.pop("adm", None)
        txt, kbd = channels_text_kb()
        warn = "" if link else "\n⚠️ Havola topilmadi: foydalanuvchilarga tugma ko'rinmaydi. Havolani qo'shib qaytadan kiriting."
        await msg.reply_text("✅ Kanal qo'shildi." + warn + "\n\n" + txt, reply_markup=kbd)
    elif action == "broadcast":
        with db() as c:
            n = c.execute("SELECT COUNT(*) n FROM users WHERE active=1 AND banned=0").fetchone()["n"]
        context.user_data["bc_text"] = text
        context.user_data.pop("adm", None)
        await msg.reply_text(
            f"📢 <b>Ko'rib chiqing</b>\n{LINE}\n{escape(text[:1500])}\n{LINE}\n"
            f"Qabul qiluvchilar: <b>{n}</b> ta. Yuboraymi?",
            reply_markup=ikb([ib("✅ Yuborish", "adm:bcgo", style="success"),
                              ib("❌ Bekor", "adm:cancel", style="danger")]))
    else:
        context.user_data.pop("adm", None)


async def do_broadcast(bot, text, admin_chat):
    with db() as c:
        ids = [r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE active=1 AND banned=0")]
    ok = fail = 0
    for i in ids:
        try:
            await bot.send_message(i, text, parse_mode=None)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    try:
        await bot.send_message(admin_chat, f"📢 Yuborish tugadi: ✅ {ok} ta, ❌ {fail} ta")
    except Exception:
        pass


# ===================== ZAXIRA (BACKUP) =====================
def make_backup_copy():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp.name)
    src.backup(dst)
    dst.close()
    src.close()
    return tmp.name


async def send_backup(bot, chat_id, caption):
    path = await asyncio.to_thread(make_backup_copy)
    try:
        with open(path, "rb") as f:
            await bot.send_document(chat_id, f, filename=f"tanishuv_{now():%Y%m%d_%H%M}.db",
                                    caption=caption, parse_mode=None)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def maybe_restore(update, context):
    msg = update.message
    if "restore" not in (msg.caption or "").lower():
        await msg.reply_text("Bazani tiklash uchun .db faylni <code>restore</code> izohi bilan yuboring.")
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        f = await msg.document.get_file()
        await f.download_to_drive(tmp.name)
        test = sqlite3.connect(tmp.name)
        tables = {r[0] for r in test.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "users" not in tables:
            raise ValueError("users jadvali yo'q")
        n = test.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        test.close()
    except Exception as e:
        await msg.reply_text(f"❌ Fayl yaroqsiz: {escape(str(e)[:80])}")
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        return
    shutil.copyfile(tmp.name, DB_PATH)
    os.remove(tmp.name)
    init_db()
    await msg.reply_text(f"✅ Baza tiklandi: <b>{n}</b> ta foydalanuvchi.")


async def backup_loop(app):
    """Har 3 soatda adminga bazaning nusxasini yuboradi (Render'da fayllar o'chib ketsa, tiklash uchun)."""
    while True:
        await asyncio.sleep(3 * 3600)
        try:
            with db() as c:
                n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            if n > 0 and ADMIN_IDS:
                await send_backup(app.bot, min(ADMIN_IDS), f"💾 Avto-zaxira: {n} foydalanuvchi")
        except Exception:
            logging.exception("Avto-zaxira xatosi")


async def post_init(app):
    app.bot_data["backup_task"] = asyncio.create_task(backup_loop(app))


# ===================== CALLBACKLAR =====================
async def callback_handler(update, context):
    q = update.callback_query
    data = q.data or ""
    try:
        await q.answer()
    except Exception:
        pass
    uid = q.from_user.id
    msg = q.message

    # --- admin amallari ---
    if data.startswith(("adm:", "pok:", "prej:", "approve:", "reject:")):
        if not is_admin(uid):
            await msg.reply_text("⛔ Bu amal faqat administrator uchun.")
            return
        if data.startswith("adm:"):
            await admin_callback(q, context, data.split(":"))
            return
        action, raw = data.split(":", 1)
        if not raw.isdigit():
            return
        pid = int(raw)
        if action in ("approve", "reject"):  # eski xabarlardagi tugmalar: foydalanuvchi ID bo'yicha
            with db() as c:
                row = c.execute("SELECT id FROM payments WHERE user_id=? AND status='pending' ORDER BY id DESC",
                                (pid,)).fetchone()
            if not row:
                await msg.reply_text("Bu foydalanuvchining kutilayotgan to'lovi yo'q.")
                return
            pid = row["id"]
        if action in ("pok", "approve"):
            p = await approve_payment(context, pid)
            note = f"✅ To'lov #{pid} tasdiqlandi." if p else "ℹ️ Bu to'lov allaqachon ko'rib chiqilgan."
        else:
            p = await reject_payment(context, pid)
            note = f"❌ To'lov #{pid} rad etildi." if p else "ℹ️ Bu to'lov allaqachon ko'rib chiqilgan."
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await msg.reply_text(note)
        return

    # --- majburiy obuna ---
    if data == "sub_check":
        missing = await not_subscribed(context, uid)
        if missing:
            await msg.reply_text("❗ Hali hamma kanalga obuna bo'lmagansiz. Obuna bo'lib, qayta bosing.")
            return
        try:
            await msg.delete()
        except Exception:
            pass
        if user(uid):
            await msg.reply_text("✅ Rahmat! Obuna tasdiqlandi.", reply_markup=menu(uid))
        else:
            await msg.reply_text("✅ Obuna tasdiqlandi! Ro'yxatdan o'tish uchun /start bosing.")
        return

    r = user(uid)
    if data == "close":
        try:
            await msg.delete()
        except Exception:
            pass
        return
    if not r:
        await msg.reply_text("Avval /start orqali ro'yxatdan o'ting.")
        return
    if r["banned"] and not is_admin(uid):
        await msg.reply_text("🚫 Hisobingiz bloklangan.")
        return

    if data == "topup":
        await topup_start(msg, context)
    elif data == "topup_cancel":
        pid = context.user_data.pop("await_receipt", None)
        context.user_data.pop("await_topup", None)
        if pid:
            with db() as c:
                c.execute("UPDATE payments SET status='cancelled' WHERE id=? AND status='awaiting'", (pid,))
        await msg.reply_text("Bekor qilindi.", reply_markup=menu(uid))
    elif data == "vipinfo":
        await vip_panel(msg, uid)
    elif data == "tx":
        await show_transactions(msg, uid)
    elif data.startswith("buy:") and data[4:].isdigit():
        await buy_vip(q, context, int(data[4:]), confirmed=False)
    elif data.startswith("buyok:") and data[6:].isdigit():
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await buy_vip(q, context, int(data[6:]), confirmed=True)
    elif data.startswith("edit:"):
        field = data[5:]
        if field == "share":
            if r["age"] < ADULT_AGE:
                return
            newv = 0 if r["share_username"] else 1
            with db() as c:
                c.execute("UPDATE users SET share_username=? WHERE user_id=?", (newv, uid))
            await msg.reply_text("✅ Lichkangiz VIP foydalanuvchilarga <b>ko'rinadi</b>." if newv
                                 else "✅ Lichkangiz endi hech kimga <b>ko'rinmaydi</b>.")
            return
        if field not in EDIT_FIELDS:
            return
        context.user_data["edit"] = field
        if field == "region":
            await msg.reply_text("Yangi viloyatni tanlang:", reply_markup=REGION_MENU)
        elif field == "photo":
            await msg.reply_text("Yangi rasmni yuboring (olib tashlash uchun <code>olib tashlash</code> deb yozing):")
        else:
            await msg.reply_text(f"Yangi {EDIT_FIELDS[field]}ni yozing:")


# ===================== BUYRUQLAR =====================
async def admin_only(update):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ruxsat yo'q.")
        return False
    return True


async def vip_on_cmd(update, context):
    if not await admin_only(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Foydalanish: /vip_on USER_ID [kun]")
        return
    uid = int(context.args[0])
    days = VIP_DAYS
    if len(context.args) > 1 and context.args[1].isdigit():
        days = max(1, min(365, int(context.args[1])))
    if not user(uid):
        await update.effective_message.reply_text("❗ Foydalanuvchi topilmadi.")
        return
    until = await grant_vip_admin(context, uid, days)
    await update.effective_message.reply_text(f"✅ {uid} uchun VIP {days} kunga yoqildi. Tugash: {until[:10]}")


async def addbal_cmd(update, context):
    if not await admin_only(update):
        return
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Foydalanish: /addbal USER_ID SUMMA (ayirish uchun minus)")
        return
    changed = add_balance(uid, amount, "admin", "Admin tomonidan")
    if changed is None:
        await update.effective_message.reply_text("❗ Foydalanuvchi topilmadi.")
        return
    r = user(uid)
    await update.effective_message.reply_text(f"✅ Balans o'zgardi: {fmt_money(changed)}. Yangi balans: {fmt_money(r['balance'])}")
    try:
        await context.bot.send_message(uid, f"💵 Balansingiz admin tomonidan o'zgartirildi. Yangi balans: <b>{fmt_money(r['balance'])}</b>")
    except Exception:
        pass


async def ban_cmd(update, context):
    if not await admin_only(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Foydalanish: /ban USER_ID")
        return
    if is_admin(int(context.args[0])):
        await update.effective_message.reply_text("Adminni bloklab bo'lmaydi.")
        return
    with db() as c:
        c.execute("UPDATE users SET banned=1,active=0 WHERE user_id=?", (int(context.args[0]),))
    await update.effective_message.reply_text("🚫 Foydalanuvchi bloklandi.")


async def unban_cmd(update, context):
    if not await admin_only(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Foydalanish: /unban USER_ID")
        return
    with db() as c:
        c.execute("UPDATE users SET banned=0,active=1 WHERE user_id=?", (int(context.args[0]),))
    await update.effective_message.reply_text("✅ Foydalanuvchi blokdan chiqarildi.")


async def search_toggle(update, context):
    if not await admin_only(update):
        return
    value = "0" if update.effective_message.text.startswith("/search_off") else "1"
    set_setting("search_enabled", value)
    await update.effective_message.reply_text("Qidiruv " + ("yoqildi." if value == "1" else "o'chirildi."))


HELP_TEXT = (
    f"ℹ️ <b>YORDAM</b>\n{LINE}\n"
    "🚀 Qidiruv — tasodifiy suhbatdosh topib, anonim yozishish\n"
    "/next — boshqa suhbatdosh, /stop — to'xtatish\n"
    "👦/👧 va viloyat bo'yicha qidiruv, /link (lichka), /vip (jins) — VIP\n"
    "💎 VIP obuna — 1 kunlik, haftalik, 15 kunlik va oylik tariflar\n"
    "💰 Hisobim — ID raqamingiz, balans va tranzaksiyalar\n"
    "➕ Hisob to'ldirish — summani yozing, kartaga o'tkazing, chek yuboring\n"
    "✏️ Profilni tahrirlash — ism, viloyat, rasm, qiziqishlar\n\n"
    "⚠️ Shaxsiy ma'lumot, parol, uy manzili yoki pul yubormang."
)


START_TIME = datetime.utcnow()


async def where_cmd(update, context):
    """Admin: bot qaysi serverda ishlayotganini ko'rsatadi (ikkita nusxani topish uchun)."""
    if not await admin_only(update):
        return
    await update.effective_message.reply_text(
        f"🖥 <b>Server:</b> <code>{escape(socket.gethostname())}</code>\n"
        f"🆔 PID: <code>{os.getpid()}</code>\n"
        f"☁️ Render: {'ha' if os.getenv('RENDER') else 'yo‘q (boshqa joy!)'}\n"
        f"⏱ Ishga tushgan: {START_TIME:%d.%m.%Y %H:%M} UTC")


async def error_handler(update, context):
    err = str(context.error)
    if "Conflict" in err:
        logging.error("⚠️ DIQQAT: shu token bilan boshqa joyda ham bot ishlayapti! Eski nusxani to'xtating. (%s)", err[:100])
    else:
        logging.error("Bot xatosi: %s", err, exc_info=context.error)


# ===================== ASOSIY ROUTER =====================
async def router(update, context):
    msg = update.message
    if not msg:
        return
    uid = update.effective_user.id
    text = (msg.text or "").strip()

    # Admin: bazani tiklash
    if msg.document and is_admin(uid):
        await maybe_restore(update, context)
        return

    r = user(uid)
    if r and (r["username"] or "") != (update.effective_user.username or ""):
        with db() as c:
            c.execute("UPDATE users SET username=? WHERE user_id=?", (update.effective_user.username or "", uid))
    if not r and not is_admin(uid):
        if text or msg.photo:
            await msg.reply_text("Avval /start orqali ro'yxatdan o'ting.")
        return
    if r and r["banned"] and not is_admin(uid):
        await msg.reply_text("🚫 Hisobingiz bloklangan.")
        return
    if not is_admin(uid):
        missing = await not_subscribed(context, uid)
        if missing:
            await send_sub_gate(msg, missing)
            return

    # Menyu tugmasi bosilsa, kutilayotgan holatlar bekor qilinadi
    if text in MENU_TEXTS:
        pid = context.user_data.pop("await_receipt", None)
        if pid:
            with db() as c:
                c.execute("UPDATE payments SET status='cancelled' WHERE id=? AND status='awaiting'", (pid,))
        for k in ("await_topup", "await_report", "await_region", "edit", "adm"):
            context.user_data.pop(k, None)

    # Admin kiritishi
    if is_admin(uid) and context.user_data.get("adm") and text:
        await admin_input(update, context, text)
        return
    # Chek rasmi
    if context.user_data.get("await_receipt"):
        if await receipt_handler(update, context):
            return
    # Hisob to'ldirish summasi
    if context.user_data.get("await_topup") and text:
        await handle_topup_amount(update, context, text)
        return
    # Profilni tahrirlash
    field = context.user_data.get("edit")
    if field and (text or msg.photo):
        await handle_edit(update, context, field, text)
        return
    # Shikoyat
    if context.user_data.get("await_report") and text:
        target = context.user_data.pop("report_target", None)
        context.user_data.pop("await_report", None)
        with db() as c:
            c.execute("INSERT INTO reports(reporter_id,reported_id,reason,created_at) VALUES(?,?,?,?)",
                      (uid, target, text[:1000], now().isoformat()))
        await msg.reply_text("✅ Shikoyat adminga saqlandi.", reply_markup=menu(uid))
        await notify_admins(context, f"🆘 Shikoyat\nYuboruvchi: <code>{uid}</code>\nProfil: <code>{target}</code>\n"
                                     f"Sabab: {escape(text[:700])}")
        return

    # Suhbatda bo'lsa — xabar sherikka uzatiladi (menyu tugmalaridan tashqari)
    partner = get_partner(uid)
    if partner and text not in MENU_TEXTS:
        await relay_message(update, context, partner)
        return

    if text == B_SEARCH:
        await find_profile(update, context)
    elif text == B_BOY:
        await find_profile(update, context, gender="O'g'il bola")
    elif text == B_GIRL:
        await find_profile(update, context, gender="Qiz bola")
    elif text in (B_REGION, B_REGION_VIP):
        if not is_vip(uid):
            await msg.reply_text("🔒 Viloyat bo'yicha izlash uchun <b>VIP</b> obunasini sotib oling 💎")
            await vip_panel(msg, uid)
        else:
            context.user_data["await_region"] = True
            await msg.reply_text("Viloyatni tanlang:", reply_markup=REGION_MENU)
    elif context.user_data.get("await_region") and text in REGIONS:
        context.user_data["await_region"] = False
        await find_profile(update, context, region=text)
    elif text == B_VIP:
        await vip_panel(msg, uid)
    elif text == B_PROFILE:
        await my_profile(update, context)
    elif text == B_EDIT:
        await edit_panel(msg, uid)
    elif text == B_ACCOUNT:
        await account_panel(msg, uid)
    elif text == B_TOPUP:
        await topup_start(msg, context)
    elif text == B_REPORT:
        await report_start(update, context)
    elif text == B_HELP:
        await msg.reply_text(HELP_TEXT, reply_markup=menu(uid))
    elif text == B_ADMIN:
        await admin_panel(update, context)
    elif text == B_NEXT:
        await next_profile(update, context)
    elif text == B_STOP:
        await stop_search(update, context)
    elif text == B_HOME:
        await msg.reply_text("Asosiy menyu:", reply_markup=menu(uid))
    elif in_queue(uid):
        await msg.reply_text("🔎 Qidirilmoqda... Bekor qilish: /stop")
    elif text or msg.photo or msg.voice or msg.sticker:
        await msg.reply_text("Hozir suhbatdosh yo'q. 🚀 «Qidiruv» tugmasini bosing.", reply_markup=menu(uid))


# ===================== SERVER (Render uchun) =====================
def start_health_server():
    """Render PORT bergan bo'lsa, kichik 'OK' serveri ishga tushadi (bot uxlab qolmasligi uchun ping qilish mumkin)."""
    port = os.getenv("PORT")
    if not port:
        return
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def _ok(self, body=b""):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            self._ok(b"OK")

        def do_HEAD(self):
            self._ok()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", int(port)), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logging.info("Health server port: %s", port)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "BOTFATHER_TOKENINI_SHU_YERGA_QOYING":
        raise RuntimeError("BOT_TOKEN topilmadi. Environment'ga BotFather tokenini kiriting.")
    init_db()
    start_health_server()
    app = (Application.builder().token(BOT_TOKEN)
           .defaults(Defaults(parse_mode=ParseMode.HTML))
           .connect_timeout(30).read_timeout(60).write_timeout(120)
           .post_init(post_init).build())

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
    app.add_handler(CommandHandler("next", next_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("vip_on", vip_on_cmd))
    app.add_handler(CommandHandler("addbal", addbal_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("search_on", search_toggle))
    app.add_handler(CommandHandler("search_off", search_toggle))
    app.add_handler(CommandHandler("profile", my_profile))
    app.add_handler(CommandHandler("where", where_cmd))
    app.add_error_handler(error_handler)
    app.add_handler(MessageHandler(~filters.COMMAND, router))

    logging.info("TanishuvBot ishga tushmoqda (v2: hisob, VIP tariflar, admin panel, majburiy obuna).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
