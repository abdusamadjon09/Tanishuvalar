import asyncio, logging, random, sqlite3, html, time
from urllib.parse import quote
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler,
                          TypeHandler, ApplicationHandlerStop, ContextTypes, filters)

# ================= SOZLAMALAR =================
BOT_TOKEN = "8843356010:AAEOJNYNrp-OChksucwsqYpf0wgVqWvZYHc"
ADMIN_ID = 8390092754          # o'zingizning raqamli Telegram ID'ingiz
DB_PATH = "tanishuvbot.db"
MIN_AGE, MAX_AGE, ADULT = 13, 50, 18
ONLINE_MINUTES = 10
SEARCH_MAX_SECONDS = 120   # 'Qidirilmoqda' shuncha soniya foydalanuvchi kutadi

# Admin /set orqali o'zgartira oladigan sozlamalar (birinchi ishga tushishdagi qiymatlar)
DEFAULTS = {
    "search_enabled": "1",
    "vip_price": "30 000 so'm",
    "vip_days": "30",
    "card": "6262570820096776",
    "card_holder": "Rahmonov Abdusamad",
    "admin_username": "@Abdusamadjon_00",
    "ref_need": "2",     # nechta taklif uchun mukofot
    "ref_days": "1",     # mukofot: necha kun VIP
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
NAME, AGE, GENDER, REGION, PHOTO, INTERESTS, CONSENT = range(7)
REGIONS = ["Toshkent shahri","Toshkent viloyati","Andijon viloyati","Buxoro viloyati","Farg'ona viloyati","Jizzax viloyati","Xorazm viloyati","Namangan viloyati","Navoiy viloyati","Qashqadaryo viloyati","Qoraqalpog'iston Respublikasi","Samarqand viloyati","Sirdaryo viloyati","Surxondaryo viloyati"]

def menu_kb(uid):
    rows = [[KeyboardButton("🚀 Qidiruv")],
            [KeyboardButton("👦 O'g'il bola izlash"), KeyboardButton("👧 Qiz bola izlash")],
            [KeyboardButton("📍 Viloyat bo'yicha izlash"), KeyboardButton("💎 VIP obuna")],
            [KeyboardButton("👤 Profilim"), KeyboardButton("💰 Hisobim")],
            [KeyboardButton("✏️ Profilni tahrirlash"), KeyboardButton("🆘 Shikoyat")],
            [KeyboardButton("👥 Referal"), KeyboardButton("ℹ️ Yordam")],
            [KeyboardButton("📋 Boshqa bo'limlar")]]
    if is_admin(uid): rows.append([KeyboardButton("👑 Admin panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

_ages = [str(i) for i in range(MIN_AGE, MAX_AGE + 1)]
AGES = ReplyKeyboardMarkup([_ages[i:i+6] for i in range(0, len(_ages), 6)], resize_keyboard=True, one_time_keyboard=True)
GENDERS = ReplyKeyboardMarkup([["👦 O'g'il bola", "👧 Qiz bola"]], resize_keyboard=True)
REGION_MENU = ReplyKeyboardMarkup([[r] for r in REGIONS], resize_keyboard=True, one_time_keyboard=True)
CONSENT_KB = ReplyKeyboardMarkup([["✅ Ha, ulashaman", "❌ Yo'q, ulashmayman"]], resize_keyboard=True, one_time_keyboard=True)
SEARCH_KB = ReplyKeyboardMarkup([[KeyboardButton("➡️ Keyingi profil"), KeyboardButton("⏹ To'xtatish")],
                                 [KeyboardButton("🎫 /lic"), KeyboardButton("💎 /vip")],
                                 [KeyboardButton("🚫 Bloklash")],
                                 [KeyboardButton("🏠 Menyu")]], resize_keyboard=True)
CHAT_KB = ReplyKeyboardMarkup([["⏹ Suhbatni to'xtatish", "🚫 Bloklash"]], resize_keyboard=True)
MENU_BTNS = {"📋 Boshqa bo'limlar","💰 Hisobim","👥 Referal","📍 Viloyat bo'yicha izlash","🚫 Bloklash","🚀 Qidiruv","👦 O'g'il bola izlash","👧 Qiz bola izlash","💎 VIP obuna","👤 Profilim","✏️ Profilni tahrirlash",
             "🆘 Shikoyat","ℹ️ Yordam","👑 Admin panel","➡️ Keyingi profil","⏹ To'xtatish","🎫 /lic","💎 /vip","🏠 Menyu"}

# ================= BAZA =================
def db():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c
def now(): return datetime.utcnow()
def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT DEFAULT '',name TEXT,age INTEGER,gender TEXT,region TEXT,photo_id TEXT DEFAULT '',interests TEXT DEFAULT '',active INTEGER DEFAULT 1,banned INTEGER DEFAULT 0,created_at TEXT)""")
        cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        for col, ddl in {"username":"TEXT DEFAULT ''","interests":"TEXT DEFAULT ''","photo_id":"TEXT DEFAULT ''","active":"INTEGER DEFAULT 1",
                         "banned":"INTEGER DEFAULT 0","created_at":"TEXT DEFAULT ''","vip_until":"TEXT DEFAULT ''",
                         "share_username":"INTEGER DEFAULT 0","last_seen":"TEXT DEFAULT ''","referred_by":"INTEGER DEFAULT 0","ref_rewarded":"INTEGER DEFAULT 0"}.items():
            if col not in cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        c.execute("CREATE TABLE IF NOT EXISTS blocks(user_id INTEGER,blocked_id INTEGER,PRIMARY KEY(user_id,blocked_id))")
        c.execute("CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT,reporter_id INTEGER,reported_id INTEGER,reason TEXT,created_at TEXT,status TEXT DEFAULT 'new')")
        c.execute("CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,created_at TEXT,status TEXT DEFAULT 'pending')")
        c.execute("CREATE TABLE IF NOT EXISTS referrals(invited_id INTEGER PRIMARY KEY,referrer_id INTEGER,created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS chats(user_id INTEGER PRIMARY KEY,partner_id INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
        for k, v in DEFAULTS.items(): c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
def get_set(k):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r['value'] if r else DEFAULTS.get(k, "")
def set_set(k, v):
    with db() as c: c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
def user(uid):
    with db() as c: return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
def is_admin(uid): return ADMIN_ID != 0 and uid == ADMIN_ID
def is_vip(uid):
    if is_admin(uid): return True
    r = user(uid)
    try: return bool(r and r['vip_until'] and datetime.fromisoformat(r['vip_until']) > now())
    except ValueError: return False
def is_online(r):
    try: return bool(r['last_seen']) and datetime.fromisoformat(r['last_seen']) >= now() - timedelta(minutes=ONLINE_MINUTES)
    except ValueError: return False
def esc(s): return html.escape(str(s or ""))

def card(r, unlocked, online=False):
    badge = "💎 VIP\n" if is_vip(r['user_id']) else ""
    if unlocked:
        t = f"👤 Ism: {esc(r['name'])}\n🎂 Yosh: {r['age']}\n⚧ Jins: {esc(r['gender'])}\n📍 Viloyat: {esc(r['region'])}\n"
    else:
        t = "👤 Ism: 🔒\n🎂 Yosh: 🔒\n⚧ Jins: 🔒\n📍 Viloyat: 🔒\n"
    t += f"💡 Qiziqishlari: {esc(r['interests']) or 'Kiritilmagan'}"
    if not unlocked: t += "\n\n💎 Barcha ma'lumotni ko'rish uchun VIP obunasini sotib oling"
    if online: t = "🟢 Onlayn\n" + t
    return badge + t

def detail_lines(own, r, vip):
    if not vip:
        return "👤 Ismi: /vip\n🚻 Jinsi: /vip\n🎂 Yoshi: /vip\n📍 Viloyati: /vip\n🌐 Username: /vip\n🆔 ID: /vip"
    if own['age'] >= ADULT and r['age'] >= ADULT:
        if r['share_username']:
            un = f"@{esc(r['username'])}" if r['username'] else "yo'q"; idt = str(r['user_id'])
        else: un = idt = "🔒 rozilik bermagan"
    else: un = idt = "🔒 maxfiy"
    return (f"👤 Ismi: {esc(r['name'])}\n🚻 Jinsi: {esc(r['gender'])}\n🎂 Yoshi: {r['age']}\n📍 Viloyati: {esc(r['region'])}\n"
            f"🌐 Username: {un}\n🆔 ID: {idt}")

def contact_info(viewer, target):
    """Aloqa ma'lumoti faqat: VIP + ikkala tomon 18+ + target roziligi bo'lsa ko'rinadi."""
    if viewer['age'] < ADULT or target['age'] < ADULT: return ""
    if not is_vip(viewer['user_id']): return "🔒 Lichka va username faqat VIP obunachilarga ko'rinadi 💎"
    if not target['share_username']: return "🔒 Bu foydalanuvchi aloqa ma'lumotini ulashishga rozilik bermagan."
    who = f"@{esc(target['username'])}" if target['username'] else f'<a href="tg://user?id={target["user_id"]}">Lichkaga yozish</a>'
    return f"🔗 Aloqa: {who}"

# ================= HAR BIR UPDATE: onlayn belgisi + ban =================
async def touch(u: Update, ctx):
    tg = u.effective_user
    if not tg: return
    r = user(tg.id)
    if not r: return
    if r['banned'] and not is_admin(tg.id):
        if u.effective_message:
            try: await u.effective_message.reply_text("🚫 Hisobingiz bloklangan.")
            except Exception: pass
        raise ApplicationHandlerStop
    with db() as c: c.execute("UPDATE users SET last_seen=?,username=? WHERE user_id=?", (now().isoformat(), tg.username or "", tg.id))

# ================= RO'YXATDAN O'TISH =================
async def start(u, ctx):
    uid = u.effective_user.id; r = user(uid)
    if r:
        await u.message.reply_text(f"Assalomu alaykum, {r['name']}! 💚", reply_markup=menu_kb(uid)); return ConversationHandler.END
    ctx.user_data.clear()
    if ctx.args and ctx.args[0].startswith("ref_") and ctx.args[0][4:].isdigit(): ctx.user_data['ref'] = int(ctx.args[0][4:])
    await u.message.reply_text("Assalomu alaykum! 💚 Tanishuv botiga xush kelibsiz.\n\nIsmingizni yozing:")
    return NAME
async def name_step(u, ctx):
    s = u.message.text.strip()
    if not 2 <= len(s) <= 40: await u.message.reply_text("Ism 2–40 belgi bo'lsin."); return NAME
    ctx.user_data['name'] = s
    await u.message.reply_text("Yoshingizni tanlang:", reply_markup=AGES); return AGE
async def age_step(u, ctx):
    s = u.message.text.strip()
    if not s.isdigit() or not MIN_AGE <= int(s) <= MAX_AGE:
        await u.message.reply_text(f"{MIN_AGE}–{MAX_AGE} oralig'idagi yoshni tanlang.", reply_markup=AGES); return AGE
    ctx.user_data['age'] = int(s)
    await u.message.reply_text("Jinsingizni tanlang:", reply_markup=GENDERS); return GENDER
async def gender_step(u, ctx):
    s = u.message.text.strip()
    if s not in ("👦 O'g'il bola", "👧 Qiz bola"):
        await u.message.reply_text("Tugmalardan tanlang.", reply_markup=GENDERS); return GENDER
    ctx.user_data['gender'] = "O'g'il bola" if s.startswith("👦") else "Qiz bola"
    await u.message.reply_text("Qayerdansiz? Viloyatingizni tanlang:", reply_markup=REGION_MENU); return REGION
async def region_step(u, ctx):
    s = u.message.text.strip()
    if s not in REGIONS: await u.message.reply_text("Viloyatni ro'yxatdan tanlang.", reply_markup=REGION_MENU); return REGION
    ctx.user_data['region'] = s
    await u.message.reply_text("Profil uchun rasm yuboring 📷", reply_markup=ReplyKeyboardRemove()); return PHOTO
async def photo_step(u, ctx):
    if not u.message.photo: await u.message.reply_text("Iltimos, rasm (foto) yuboring 📷"); return PHOTO
    ctx.user_data['photo_id'] = u.message.photo[-1].file_id
    await u.message.reply_text("Nimalarga qiziqasiz? Qisqacha yozing (500 belgigacha)."); return INTERESTS
async def interests_step(u, ctx):
    s = u.message.text.strip()
    if not 1 <= len(s) <= 500: await u.message.reply_text("1–500 belgi yozing."); return INTERESTS
    ctx.user_data['interests'] = s
    if ctx.user_data['age'] >= ADULT:
        await u.message.reply_text("🔗 VIP obunachilar sizning username/lichkangizni ko'ra olishiga rozimisiz?\n(Keyinroq profil sozlamalaridan o'zgartirishingiz mumkin.)", reply_markup=CONSENT_KB)
        return CONSENT
    return await finish_registration(u, ctx, 0)
async def consent_step(u, ctx):
    s = u.message.text.strip()
    if s not in ("✅ Ha, ulashaman", "❌ Yo'q, ulashmayman"):
        await u.message.reply_text("Tugmalardan tanlang.", reply_markup=CONSENT_KB); return CONSENT
    return await finish_registration(u, ctx, 1 if s.startswith("✅") else 0)
async def finish_registration(u, ctx, share):
    d = ctx.user_data; tg = u.effective_user; ref = d.get('ref', 0)
    with db() as c:
        c.execute("INSERT OR REPLACE INTO users(user_id,username,name,age,gender,region,photo_id,interests,active,banned,created_at,share_username,last_seen,vip_until) VALUES(?,?,?,?,?,?,?,?,1,0,?,?,?,'')",
                  (tg.id, tg.username or "", d['name'], d['age'], d['gender'], d['region'], d.get('photo_id', ''), d['interests'], now().isoformat(), share, now().isoformat()))
    ctx.user_data.clear()
    await u.message.reply_text("✅ Siz muvaffaqiyatli ro'yxatdan o'tdingiz va botdan foydalanishingiz mumkin!", reply_markup=menu_kb(tg.id))
    await process_ref(ctx, ref, tg.id)
    if ADMIN_ID:
        try: await ctx.bot.send_message(ADMIN_ID, f"🆕 Yangi foydalanuvchi: {d_name(tg.id)}")
        except Exception: pass
    return ConversationHandler.END
def d_name(uid):
    r = user(uid)
    return f"{r['name']}, {r['age']} yosh, {r['region']} (ID {uid})" if r else str(uid)

# ================= REFERAL =================
async def process_ref(ctx, ref, new_id):
    if not ref or ref == new_id or not user(ref): return
    need = max(1, int(get_set('ref_need') or 2)); days = max(1, int(get_set('ref_days') or 1))
    with db() as c:
        cur = c.execute("INSERT OR IGNORE INTO referrals VALUES(?,?,?)", (new_id, ref, now().isoformat()))
        if cur.rowcount == 0: return
        c.execute("UPDATE users SET referred_by=? WHERE user_id=?", (ref, new_id))
        n = c.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=?", (ref,)).fetchone()['n']
        given = c.execute("SELECT ref_rewarded FROM users WHERE user_id=?", (ref,)).fetchone()['ref_rewarded'] or 0
    due = n // need - given
    msg = f"👥 Havolangiz orqali yangi foydalanuvchi qo'shildi! (jami {n} ta taklif)"
    if due > 0:
        until = grant_vip(ref, days * due)
        with db() as c: c.execute("UPDATE users SET ref_rewarded=ref_rewarded+? WHERE user_id=?", (due, ref))
        msg += f"\n🎉 Mukofot: {days * due} kunlik VIP 💎 VIP {until[:10]} gacha faol."
    else: msg += f"\nYana {need - n % need} ta taklif — VIP mukofot."
    try: await ctx.bot.send_message(ref, msg)
    except Exception: pass
async def send_ref(m, ctx, uid):
    bu = (await ctx.bot.get_me()).username
    link = f"https://t.me/{bu}?start=ref_{uid}"
    need, days = get_set('ref_need'), get_set('ref_days')
    await m.reply_text(f"👥 REFERAL TIZIMI\n\n{need} ta do'stingiz sizning havolangiz orqali botda ro'yxatdan o'tsa — {days} kunlik VIP bepul 💎\n(Har {need} ta taklif uchun mukofot beriladi.)\n\n🔗 Sizning havolangiz:\n{link}",
                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Do'stlarga ulashish", url="https://t.me/share/url?url=" + quote(link) + "&text=" + quote("Tanishuv botiga qo'shiling!"))]]))
async def referral_cmd(u, ctx):
    if not user(u.effective_user.id): await u.message.reply_text("Avval /start yuboring."); return
    await send_ref(u.message, ctx, u.effective_user.id)
async def account(u, ctx):
    uid = u.effective_user.id; r = user(uid)
    if not r: await u.message.reply_text("Avval /start yuboring."); return
    need = max(1, int(get_set('ref_need') or 2)); days = max(1, int(get_set('ref_days') or 1))
    with db() as c:
        n = c.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=?", (uid,)).fetchone()['n']
        higher = c.execute("SELECT COUNT(*) n FROM (SELECT COUNT(*) k FROM referrals GROUP BY referrer_id HAVING k>?)", (n,)).fetchone()['n']
        bl = c.execute("SELECT COUNT(*) n FROM blocks WHERE user_id=?", (uid,)).fetchone()['n']
    if is_admin(uid): vip = "admin (doimiy)"
    elif is_vip(uid): vip = f"✅ faol, {r['vip_until'][:10]} gacha"
    else: vip = "❌ yo'q"
    t = (f"💰 HISOBIM\n\n👤 {esc(r['name'])}\n🆔 ID: {uid}\n💎 VIP: {vip}\n📅 Ro'yxatdan o'tgan: {(r['created_at'] or '')[:10]}\n\n"
         f"👥 Takliflar: {n} ta\n🎁 Olingan mukofot: {(r['ref_rewarded'] or 0) * days} kunlik VIP\n⏳ Keyingi mukofotgacha: {need - n % need} ta taklif\n")
    if n > 0: t += f"🏆 Referal reytingi: {higher + 1}-o'rin\n"
    t += f"🚫 Bloklanganlar: {bl} ta"
    await u.message.reply_text(t, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 VIP sotib olish", callback_data="vip_open"), InlineKeyboardButton("👥 Do'st taklif qilish", callback_data="ref_open")]]))

async def cancel(u, ctx):
    ctx.user_data.clear(); await u.message.reply_text("Bekor qilindi. Qayta boshlash uchun /start.", reply_markup=menu_kb(u.effective_user.id)); return ConversationHandler.END

# ================= SUHBAT (anonim yozishish) =================
SAFE_NOTE = "⚠️ Xavfsizlik uchun suhbatda faqat matn, ovozli xabar va stikerlar yuboriladi."
def chat_partner(uid):
    with db() as c: r = c.execute("SELECT partner_id FROM chats WHERE user_id=?", (uid,)).fetchone()
    return r['partner_id'] if r else None
async def end_chat(ctx, uid, notify=True):
    p = chat_partner(uid)
    if not p: return None
    with db() as c: c.execute("DELETE FROM chats WHERE user_id IN(?,?)", (uid, p))
    if notify:
        try: await ctx.bot.send_message(p, "⛔️ Suhbatdosh suhbatni tugatdi.", reply_markup=menu_kb(p))
        except Exception: pass
    return p
def can_chat(a, b):
    """Voyaga yetmaganlar faqat voyaga yetmaganlar bilan, kattalar faqat kattalar bilan; bloklanganlar yozisha olmaydi."""
    if (a['age'] >= ADULT) != (b['age'] >= ADULT): return False
    with db() as c:
        return not c.execute("SELECT 1 FROM blocks WHERE (user_id=? AND blocked_id=?) OR (user_id=? AND blocked_id=?)", (a['user_id'], b['user_id'], b['user_id'], a['user_id'])).fetchone()
async def relay_media(u, ctx):
    uid = u.effective_user.id; p = chat_partner(uid)
    if not p: return
    try: await ctx.bot.copy_message(chat_id=p, from_chat_id=uid, message_id=u.message.message_id)
    except Exception:
        await end_chat(ctx, uid, False); await u.message.reply_text("Suhbatdosh topilmadi. Suhbat tugadi.", reply_markup=menu_kb(uid))
async def block_media(u, ctx):
    if chat_partner(u.effective_user.id): await u.message.reply_text(SAFE_NOTE)

# ================= QIDIRUV =================
async def vip_panel(m, uid):
    r = user(uid)
    status = ""
    if r and is_vip(uid) and not is_admin(uid): status = f"✅ Sizning VIP obunangiz {r['vip_until'][:10]} gacha faol.\n\n"
    text = (f"💎 VIP OBUNA\n\n{status}VIP imkoniyatlari:\n"
            "• 🔗 Foydalanuvchi nomi (username) ko'rinadi\n"
            "• 👤 Ism ko'rinadi\n• 🎂 Yoshi ko'rinadi\n• ⚧ Jinsi ko'rinadi\n• 📍 Qayerdanligi ko'rinadi\n"
            "• 👦/👧 O'g'il yoki qiz bola izlash\n• 📍 Viloyat bo'yicha izlash\n• 💎 Profilda VIP belgisi\n"
            "(Username faqat 18+ va o'zi rozilik bergan foydalanuvchilarniki ko'rinadi.)\n\n"
            f"💰 Narxi: {get_set('vip_price')} / {get_set('vip_days')} kun\n\n"
            f"💳 Karta raqam: {get_set('card')}\n👤 {get_set('card_holder')}\n\n"
            f"To'lov qilib adminga yozing\nAdmin: {get_set('admin_username')}\n\n"
            "To'lovdan so'ng «To'lov qildim» tugmasini bosib chekni yuboring — admin tasdiqlagach VIP yoqiladi.")
    await m.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ To'lov qildim (chek yuborish)", callback_data="pay_start")]]))

async def find_profile(u, ctx, gender=None, region=None):
    uid = u.effective_user.id; own = user(uid); m = u.effective_message
    if not own: await m.reply_text("Avval /start orqali ro'yxatdan o'ting."); return
    if chat_partner(uid): await m.reply_text("💬 Siz suhbatdasiz. Avval /stop bosing."); return
    if (gender or region) and not is_vip(uid):
        what = "O'g'il bola izlash" if gender == "O'g'il bola" else ("Qiz bola izlash" if gender else "Viloyat bo'yicha izlash")
        await m.reply_text(f"🔒 {what} uchun VIP obunasini sotib oling 💎")
        await vip_panel(m, uid); return
    if get_set('search_enabled') != '1' and not is_admin(uid): await m.reply_text("Qidiruv vaqtincha o'chirilgan."); return
    token = random.random(); ctx.user_data['search_id'] = token
    wait = await m.reply_text("🔎 Qidirilmoqda.", reply_markup=SEARCH_KB)
    frames = ["🔎 Qidirilmoqda.", "🔎 Qidirilmoqda..", "🔎 Qidirilmoqda..."]
    rows, waited, n = [], 0, 0
    while True:
        cutoff = (now() - timedelta(minutes=ONLINE_MINUTES)).isoformat()
        # Voyaga yetmaganlar faqat voyaga yetmaganlar bilan, kattalar faqat kattalar bilan
        q = ("SELECT * FROM users WHERE user_id!=? AND active=1 AND banned=0 AND last_seen>=? AND age" + (">=18" if own['age'] >= ADULT else "<18") +
             " AND user_id NOT IN(SELECT blocked_id FROM blocks WHERE user_id=?) AND user_id NOT IN(SELECT user_id FROM blocks WHERE blocked_id=?)")
        args = [uid, cutoff, uid, uid]
        q += " AND user_id NOT IN(SELECT user_id FROM chats)"
        if gender: q += " AND gender=?"; args.append(gender)
        if region: q += " AND region=?"; args.append(region)
        with db() as c: rows = c.execute(q + " ORDER BY RANDOM() LIMIT 30", args).fetchall()
        if rows or ctx.user_data.get('search_id') != token or waited >= SEARCH_MAX_SECONDS: break
        await asyncio.sleep(3); waited += 3; n += 1
        if ctx.user_data.get('search_id') != token: break
        try: await wait.edit_text(frames[n % 3])
        except Exception: pass
    try: await wait.delete()
    except Exception: pass
    if ctx.user_data.get('search_id') != token: return   # to'xtatilgan yoki yangi qidiruv boshlangan
    ctx.user_data.pop('search_id', None)
    if not rows: await m.reply_text("😔 Hozircha onlayn foydalanuvchi topilmadi. Birozdan so'ng qayta urinib ko'ring.", reply_markup=menu_kb(uid)); return
    r = random.choice(rows); ctx.user_data.update(candidate=r['user_id'], gender_filter=gender, region_filter=region, searching=True)
    vip = is_vip(uid)
    msg = ("💎 VIP\n" if vip and is_vip(r['user_id']) else "") + "✅ Suhbatdosh topildi!\n\n" + detail_lines(own, r, vip)
    msg += "\n\n🎫 /lic — suhbatdosh profilini ko'rish\n▶️ /next — keyingi\n⛔️ /stop — to'xtatish"
    # Topilgan zahoti suhbat avtomatik boshlanadi (qabul qilish shart emas)
    pvip = is_vip(r['user_id'])
    pmsg = (("💎 VIP\n" if is_vip(uid) else "") if pvip else "") + "✅ Suhbatdosh topildi!\n\n" + detail_lines(r, own, pvip)
    pmsg += "\n\n🎫 /lic — suhbatdosh profilini ko'rish\n▶️ /next — keyingi\n⛔️ /stop — to'xtatish\n\n💬 Xabar yozing — suhbatdosh o'qiydi."
    with db() as c:
        c.execute("INSERT OR REPLACE INTO chats VALUES(?,?)", (uid, r['user_id'])); c.execute("INSERT OR REPLACE INTO chats VALUES(?,?)", (r['user_id'], uid))
    try:
        if pvip and own['photo_id']: await ctx.bot.send_photo(r['user_id'], own['photo_id'], caption=pmsg, reply_markup=CHAT_KB, parse_mode="HTML")
        else: await ctx.bot.send_message(r['user_id'], pmsg, reply_markup=CHAT_KB, parse_mode="HTML")
        pd = ctx.application.user_data[r['user_id']]; pd.update(candidate=uid, searching=True, gender_filter=None, region_filter=None)
    except Exception:
        with db() as c: c.execute("DELETE FROM chats WHERE user_id IN(?,?)", (uid, r['user_id']))
        await m.reply_text("Suhbatdosh javob bermadi. /next orqali boshqasini toping.", reply_markup=menu_kb(uid)); return
    msg += "\n\n💬 Xabar yozing — suhbatdosh o'qiydi."
    if vip and r['photo_id']: await m.reply_photo(r['photo_id'], caption=msg, reply_markup=CHAT_KB, parse_mode="HTML")
    else: await m.reply_text(msg, reply_markup=CHAT_KB, parse_mode="HTML")
async def region_search_menu(u, ctx):
    uid = u.effective_user.id
    if not user(uid): await u.message.reply_text("Avval /start yuboring."); return
    if not is_vip(uid):
        await u.message.reply_text("🔒 Viloyat bo'yicha izlash uchun VIP obunasini sotib oling 💎"); await vip_panel(u.message, uid); return
    await u.message.reply_text("Qaysi viloyatdan izlaymiz?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(x, callback_data=f"sr:{i}")] for i, x in enumerate(REGIONS)]))
async def block_cmd(u, ctx):
    uid = u.effective_user.id; p = chat_partner(uid); t = p or ctx.user_data.get('candidate')
    if not t: await u.message.reply_text("Avval qidiruv orqali profil toping."); return
    with db() as c: c.execute("INSERT OR IGNORE INTO blocks VALUES(?,?)", (uid, t))
    if p:
        await end_chat(ctx, uid); await u.message.reply_text("🚫 Bloklandi va suhbat tugadi.", reply_markup=menu_kb(uid)); return
    await u.message.reply_text("🚫 Bu foydalanuvchi bloklandi, endi sizga chiqmaydi.")
    await find_profile(u, ctx, ctx.user_data.get('gender_filter'), ctx.user_data.get('region_filter'))
async def search_all(u, ctx): await find_profile(u, ctx)
async def search_boys(u, ctx): await find_profile(u, ctx, "O'g'il bola")
async def search_girls(u, ctx): await find_profile(u, ctx, "Qiz bola")
async def next_cmd(u, ctx):
    if chat_partner(u.effective_user.id): await end_chat(ctx, u.effective_user.id)
    if not ctx.user_data.get('searching'): await u.message.reply_text("Avval qidiruvni boshlang.", reply_markup=menu_kb(u.effective_user.id)); return
    await find_profile(u, ctx, ctx.user_data.get('gender_filter'), ctx.user_data.get('region_filter'))
async def stop_cmd(u, ctx):
    ctx.user_data['searching'] = False; ctx.user_data['search_id'] = None; ctx.user_data.pop('candidate', None)
    if chat_partner(u.effective_user.id):
        await end_chat(ctx, u.effective_user.id); await u.message.reply_text("⏹ Suhbat to'xtatildi.", reply_markup=menu_kb(u.effective_user.id)); return
    await u.message.reply_text("⏹ Muloqot to'xtatildi.", reply_markup=menu_kb(u.effective_user.id))
async def lic_cmd(u, ctx):
    uid = u.effective_user.id; t = user(ctx.user_data.get('candidate', 0))
    if not t: await u.message.reply_text("Avval qidiruv orqali profil toping."); return
    if not is_vip(uid):
        await u.message.reply_text("🔒 Suhbatdosh profilini ko'rish uchun VIP obunasini sotib oling 💎"); await vip_panel(u.message, uid); return
    msg = "🎫 Suhbatdosh profili\n\n" + card(t, True)
    if t['photo_id']: await u.message.reply_photo(t['photo_id'], caption=msg, parse_mode="HTML")
    else: await u.message.reply_text(msg, parse_mode="HTML")
async def vip_cmd(u, ctx):
    uid = u.effective_user.id; t = user(ctx.user_data.get('candidate', 0))
    if not t: await u.message.reply_text("Avval qidiruv orqali profil toping."); return
    if not is_vip(uid):
        await u.message.reply_text("🔒 Ma'lumotni ko'rish uchun VIP obunasini sotib oling 💎"); await vip_panel(u.message, uid); return
    await u.message.reply_text("💎 VIP ma'lumot\n\n" + detail_lines(user(uid), t, True), parse_mode="HTML")

# ================= PROFIL =================
async def my_profile(u, ctx):
    uid = u.effective_user.id; r = user(uid)
    if not r: await u.message.reply_text("Avval /start yuboring."); return
    msg = card(r, True)
    if r['age'] >= ADULT: msg += f"\n🔗 Aloqa ulashish: {'yoqilgan' if r['share_username'] else 'o‘chirilgan'}"
    msg += "\n💎 VIP: " + (f"{r['vip_until'][:10]} gacha" if is_vip(uid) and not is_admin(uid) else ("admin" if is_admin(uid) else "yo'q"))
    if r['photo_id']: await u.message.reply_photo(r['photo_id'], caption=msg, parse_mode="HTML", reply_markup=menu_kb(uid))
    else: await u.message.reply_text(msg, parse_mode="HTML", reply_markup=menu_kb(uid))
async def edit_profile(u, ctx):
    r = user(u.effective_user.id)
    if not r: await u.message.reply_text("Avval /start yuboring."); return
    rows = [[InlineKeyboardButton("✏️ Ism", callback_data="ed:name"), InlineKeyboardButton("🎂 Yosh", callback_data="ed:age")],
            [InlineKeyboardButton("📍 Viloyat", callback_data="ed:region"), InlineKeyboardButton("🖼 Rasm", callback_data="ed:photo")],
            [InlineKeyboardButton("💡 Qiziqish", callback_data="ed:interests")]]
    if r['age'] >= ADULT:
        rows.append([InlineKeyboardButton(f"🔗 Aloqa ulashish: {'✅ yoqilgan' if r['share_username'] else '❌ o‘chirilgan'}", callback_data="ed:share")])
    rows.append([InlineKeyboardButton("🗑 Profilni o'chirish", callback_data="ed:delete")])
    await u.message.reply_text("Nimani o'zgartirmoqchisiz?", reply_markup=InlineKeyboardMarkup(rows))
async def help_cmd(u, ctx):
    await u.message.reply_text("🚀 Qidiruv — onlayn foydalanuvchi topish\n/next — keyingi\n/stop — to'xtatish\n/lic — suhbatdosh profili\n/vip — to'liq ma'lumot (VIP)\n💎 VIP obuna — imkoniyatlar\n💰 Hisobim — VIP va takliflar holati\n👥 Referal — do'st taklif qiling, VIP yutib oling\n🆘 Shikoyat — xabar berish\n\nNotanishlarga parol, pul yoki manzilingizni yubormang.", reply_markup=menu_kb(u.effective_user.id))
async def report_start(u, ctx):
    ctx.user_data['await_report'] = True; ctx.user_data['report_target'] = ctx.user_data.get('candidate')
    await u.message.reply_text("Shikoyat sababini yozing. Maxfiy ma'lumot yubormang.")
async def more_menu(u, ctx):
    r = user(u.effective_user.id)
    if not r: await u.message.reply_text("Avval /start yuboring."); return
    await u.message.reply_text("📋 BOSHQA BO'LIMLAR", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Qoidalar", callback_data="mo:rules"), InlineKeyboardButton("🏆 Reyting", callback_data="mo:top")],
        [InlineKeyboardButton("📞 Adminga yozish", callback_data="mo:contact")],
        [InlineKeyboardButton("👁 Qidiruvda ko'rinish: " + ("✅ ko'rinadi" if r['active'] else "❌ yashirin"), callback_data="mo:vis")]]))
async def vip_menu(u, ctx):
    if not user(u.effective_user.id): await u.message.reply_text("Avval /start yuboring."); return
    await vip_panel(u.message, u.effective_user.id)

# ================= TO'LOV (chek -> admin tasdiqlaydi) =================
async def receipt_or_photo(u, ctx):
    uid = u.effective_user.id
    if ctx.user_data.get('await_receipt'):
        ctx.user_data.pop('await_receipt')
        with db() as c: pid = c.execute("INSERT INTO payments(user_id,created_at) VALUES(?,?)", (uid, now().isoformat())).lastrowid
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"pay_ok:{pid}"), InlineKeyboardButton("❌ Rad etish", callback_data=f"pay_no:{pid}")]])
        r = user(uid)
        cap = f"💳 To'lov cheki #{pid}\nFoydalanuvchi: {uid} (@{u.effective_user.username or '-'})\nIsm: {r['name'] if r else '-'}"
        if ADMIN_ID:
            try: await ctx.bot.send_photo(ADMIN_ID, u.message.photo[-1].file_id, caption=cap, reply_markup=kb)
            except Exception: logging.exception("admin ga chek yuborilmadi")
        await u.message.reply_text("✅ Chek adminga yuborildi. Tasdiqlangach VIP avtomatik yoqiladi.", reply_markup=menu_kb(uid)); return
    if ctx.user_data.get('edit') == 'photo':
        ctx.user_data.pop('edit')
        with db() as c: c.execute("UPDATE users SET photo_id=? WHERE user_id=?", (u.message.photo[-1].file_id, uid))
        await u.message.reply_text("✅ Rasm yangilandi.", reply_markup=menu_kb(uid)); return
    if chat_partner(uid): await u.message.reply_text(SAFE_NOTE)

def grant_vip(uid, days):
    r = user(uid); base = now()
    try:
        if r and r['vip_until'] and datetime.fromisoformat(r['vip_until']) > base: base = datetime.fromisoformat(r['vip_until'])
    except ValueError: pass
    until = (base + timedelta(days=days)).isoformat()
    with db() as c: c.execute("UPDATE users SET vip_until=? WHERE user_id=?", (until, uid))
    return until

# ================= INLINE TUGMALAR =================
def admin_kb():
    on = get_set('search_enabled') == '1'
    return InlineKeyboardMarkup([[InlineKeyboardButton("📊 Statistika", callback_data="adm:stats"), InlineKeyboardButton("💳 To'lovlar", callback_data="adm:pay")],
                                 [InlineKeyboardButton("🆘 Shikoyatlar", callback_data="adm:reports"), InlineKeyboardButton("⚙️ Sozlamalar", callback_data="adm:settings")],
                                 [InlineKeyboardButton("🔎 Qidiruv: " + ("✅ yoqilgan" if on else "❌ o‘chirilgan") + " (almashtirish)", callback_data="adm:toggle")]])
async def cb(u, ctx):
    q = u.callback_query; d = q.data; uid = q.from_user.id; await q.answer()
    m = q.message
    if d.startswith("mo:"):
        k = d[3:]; r = user(uid)
        if not r: return
        if k == "rules":
            await m.reply_text("📜 QOIDALAR\n\n• Haqorat, spam va aldov taqiqlanadi\n• Parol, karta ma'lumoti yoki pul so'ramang va yubormang\n• Boshqa odamning rasmi va ma'lumotidan foydalanmang\n• Qoidabuzarlik haqida 🆘 Shikoyat orqali xabar bering\n• Qoidani buzganlar bloklanadi\n• 18 yoshgacha bo'lganlar faqat tengdoshlari bilan muloqot qiladi")
        elif k == "top":
            with db() as c: rows = c.execute("SELECT referrer_id, COUNT(*) k FROM referrals GROUP BY referrer_id ORDER BY k DESC LIMIT 10").fetchall()
            medals = ["🥇", "🥈", "🥉"]; lines = []
            for i, x in enumerate(rows):
                t = user(x['referrer_id']); nm = (t['name'][:2] + "***") if t else "***"
                lines.append(f"{medals[i] if i < 3 else str(i + 1) + '.'} {esc(nm)} — {x['k']} ta taklif")
            await m.reply_text("🏆 REFERAL REYTINGI (TOP 10)\n\n" + ("\n".join(lines) or "Hali hech kim taklif qilmagan.") + "\n\n👥 Do'st taklif qilib, siz ham reytingga kiring!", parse_mode="HTML")
        elif k == "contact":
            ctx.user_data['await_admin_msg'] = True
            await m.reply_text("📞 Adminga xabaringizni yozing (bitta xabar):")
        elif k == "vis":
            with db() as c: c.execute("UPDATE users SET active=1-active WHERE user_id=? AND banned=0", (uid,))
            r = user(uid); await m.reply_text("👁 Endi siz qidiruvda " + ("ko'rinasiz ✅" if r['active'] else "ko'rinmaysiz ❌ (yashirin)"))
        return
    if d == "vip_open": await vip_panel(m, uid); return
    if d == "ref_open":
        if user(uid): await send_ref(m, ctx, uid)
        return
    if d == "pay_start":
        if not user(uid): await m.reply_text("Avval /start yuboring."); return
        ctx.user_data['await_receipt'] = True
        await m.reply_text("To'lov chekining rasmini (skrinshot) yuboring 📷"); return
    if d.startswith("ed:"):
        r = user(uid)
        if not r: return
        k = d[3:]
        if k == "region":
            await m.reply_text("Viloyatni tanlang:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(x, callback_data=f"reg:{i}")] for i, x in enumerate(REGIONS)]))
        elif k == "share":
            if r['age'] < ADULT: return
            with db() as c: c.execute("UPDATE users SET share_username=? WHERE user_id=?", (0 if r['share_username'] else 1, uid))
            await m.reply_text("✅ Sozlama o'zgartirildi.")
        elif k == "delete":
            await m.reply_text("Profilingiz o'chirilsinmi?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Ha, o'chirish", callback_data="del:yes"), InlineKeyboardButton("Bekor", callback_data="del:no")]]))
        else:
            ctx.user_data['edit'] = k
            await m.reply_text({"name": "Yangi ismingizni yozing:", "age": f"Yangi yoshingizni yozing ({MIN_AGE}–{MAX_AGE}):",
                                "photo": "Yangi rasm yuboring 📷", "interests": "Yangi qiziqishlaringizni yozing:"}[k])
        return
    if d.startswith("reg:"):
        i = int(d[4:])
        if 0 <= i < len(REGIONS):
            with db() as c: c.execute("UPDATE users SET region=? WHERE user_id=?", (REGIONS[i], uid))
            await m.reply_text("✅ Viloyat yangilandi: " + REGIONS[i])
        return
    if d.startswith("sr:"):
        i = int(d[3:])
        if 0 <= i < len(REGIONS): await find_profile(u, ctx, None, REGIONS[i])
        return
    if d == "del:yes":
        with db() as c: c.execute("DELETE FROM users WHERE user_id=?", (uid,))
        await m.reply_text("Profilingiz o'chirildi. Qayta ro'yxatdan o'tish uchun /start."); return
    if d == "del:no": await m.reply_text("Bekor qilindi."); return
    # ---- faqat admin ----
    if not is_admin(uid): await m.reply_text("⛔ Ruxsat yo'q."); return
    if d.startswith("pay_ok:") or d.startswith("pay_no:"):
        pid = int(d.split(":")[1])
        with db() as c: p = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if not p or p['status'] != 'pending': await m.reply_text("Bu to'lov allaqachon ko'rib chiqilgan."); return
        if d.startswith("pay_ok:"):
            until = grant_vip(p['user_id'], int(get_set('vip_days') or 30))
            with db() as c: c.execute("UPDATE payments SET status='ok' WHERE id=?", (pid,))
            await m.reply_text(f"✅ #{pid} tasdiqlandi. VIP {until[:10]} gacha.")
            try: await ctx.bot.send_message(p['user_id'], f"🎉 To'lov tasdiqlandi! VIP obunangiz {until[:10]} gacha faol 💎")
            except Exception: pass
        else:
            with db() as c: c.execute("UPDATE payments SET status='no' WHERE id=?", (pid,))
            await m.reply_text(f"❌ #{pid} rad etildi.")
            try: await ctx.bot.send_message(p['user_id'], f"❌ To'lovingiz tasdiqlanmadi. Admin bilan bog'laning: {get_set('admin_username')}")
            except Exception: pass
        return
    if d == "adm:stats": await m.reply_text(stats_text(), reply_markup=admin_kb())
    elif d == "adm:toggle":
        set_set('search_enabled', '0' if get_set('search_enabled') == '1' else '1'); await m.reply_text("Qidiruv holati o'zgartirildi.", reply_markup=admin_kb())
    elif d == "adm:pay":
        with db() as c: rows = c.execute("SELECT * FROM payments WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
        if not rows: await m.reply_text("Kutilayotgan to'lov yo'q."); return
        for p in rows:
            await m.reply_text(f"💳 To'lov #{p['id']} — foydalanuvchi {p['user_id']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"pay_ok:{p['id']}"), InlineKeyboardButton("❌ Rad etish", callback_data=f"pay_no:{p['id']}")]]))
    elif d == "adm:reports":
        with db() as c: rows = c.execute("SELECT * FROM reports WHERE status='new' ORDER BY id DESC LIMIT 10").fetchall()
        await m.reply_text("\n\n".join(f"#{x['id']} | yuboruvchi {x['reporter_id']} → {x['reported_id']}\n{x['reason'][:200]}" for x in rows) or "Yangi shikoyat yo'q.")
    elif d == "adm:settings":
        await m.reply_text("⚙️ SOZLAMALAR\n" + "\n".join(f"{k} = {get_set(k)}" for k in DEFAULTS if k != 'search_enabled') +
                           "\n\nO'zgartirish: /set KALIT QIYMAT\nMasalan: /set vip_price 50 000 so'm\nKalitlar: vip_price, vip_days, card, card_holder, admin_username, ref_need, ref_days")
def stats_text():
    with db() as c:
        n = c.execute("SELECT COUNT(*) n FROM users").fetchone()['n']
        a = c.execute("SELECT COUNT(*) n FROM users WHERE active=1 AND banned=0").fetchone()['n']
        on = c.execute("SELECT COUNT(*) n FROM users WHERE last_seen>=?", ((now() - timedelta(minutes=ONLINE_MINUTES)).isoformat(),)).fetchone()['n']
        v = c.execute("SELECT COUNT(*) n FROM users WHERE vip_until>?", (now().isoformat(),)).fetchone()['n']
        rp = c.execute("SELECT COUNT(*) n FROM reports WHERE status='new'").fetchone()['n']
        pp = c.execute("SELECT COUNT(*) n FROM payments WHERE status='pending'").fetchone()['n']
        rf = c.execute("SELECT COUNT(*) n FROM referrals").fetchone()['n']
    return f"👑 ADMIN PANEL\n👥 Jami: {n}\n🟢 Onlayn: {on}\n✅ Faol: {a}\n💎 VIP: {v}\n💳 Kutilayotgan to'lov: {pp}\n🆘 Shikoyatlar: {rp}\n👥 Referallar: {rf}"

# ================= ADMIN BUYRUQLARI =================
def admin_only(fn):
    async def w(u, ctx):
        if not is_admin(u.effective_user.id): await u.message.reply_text("⛔ Faqat administrator uchun."); return
        await fn(u, ctx)
    return w
@admin_only
async def admin(u, ctx):
    await u.message.reply_text(stats_text() + "\n\nBuyruqlar:\n/ban ID\n/unban ID\n/grantvip ID [kun]\n/revokevip ID\n/set KALIT QIYMAT\n/broadcast XABAR", reply_markup=admin_kb())
@admin_only
async def ban_cmd(u, ctx):
    if not ctx.args or not ctx.args[0].isdigit(): await u.message.reply_text("Foydalanish: /ban USER_ID"); return
    with db() as c: c.execute("UPDATE users SET banned=1,active=0 WHERE user_id=?", (int(ctx.args[0]),))
    await end_chat(ctx, int(ctx.args[0]))
    await u.message.reply_text("🚫 Bloklandi.")
@admin_only
async def unban_cmd(u, ctx):
    if not ctx.args or not ctx.args[0].isdigit(): await u.message.reply_text("Foydalanish: /unban USER_ID"); return
    with db() as c: c.execute("UPDATE users SET banned=0,active=1 WHERE user_id=?", (int(ctx.args[0]),))
    await u.message.reply_text("✅ Blokdan chiqarildi.")
@admin_only
async def grant_cmd(u, ctx):
    if not ctx.args or not ctx.args[0].isdigit(): await u.message.reply_text("Foydalanish: /grantvip USER_ID [kun]"); return
    days = int(ctx.args[1]) if len(ctx.args) > 1 and ctx.args[1].isdigit() else int(get_set('vip_days') or 30)
    if not user(int(ctx.args[0])): await u.message.reply_text("Bunday foydalanuvchi yo'q."); return
    until = grant_vip(int(ctx.args[0]), days); await u.message.reply_text(f"💎 VIP {until[:10]} gacha berildi.")
@admin_only
async def revoke_cmd(u, ctx):
    if not ctx.args or not ctx.args[0].isdigit(): await u.message.reply_text("Foydalanish: /revokevip USER_ID"); return
    with db() as c: c.execute("UPDATE users SET vip_until='' WHERE user_id=?", (int(ctx.args[0]),))
    await u.message.reply_text("VIP olib tashlandi.")
@admin_only
async def set_cmd(u, ctx):
    if len(ctx.args) < 2 or ctx.args[0] not in DEFAULTS or ctx.args[0] == 'search_enabled':
        await u.message.reply_text("Foydalanish: /set KALIT QIYMAT\nKalitlar: vip_price, vip_days, card, card_holder, admin_username, ref_need, ref_days"); return
    set_set(ctx.args[0], " ".join(ctx.args[1:])); await u.message.reply_text("✅ Saqlandi.")
@admin_only
async def reply_cmd(u, ctx):
    if len(ctx.args) < 2 or not ctx.args[0].isdigit(): await u.message.reply_text("Foydalanish: /reply USER_ID matn"); return
    try: await ctx.bot.send_message(int(ctx.args[0]), "📩 Admin javobi:\n" + " ".join(ctx.args[1:])); await u.message.reply_text("✅ Yuborildi.")
    except Exception: await u.message.reply_text("❌ Yuborib bo'lmadi.")
@admin_only
async def toggle(u, ctx):
    v = '0' if u.message.text.startswith('/search_off') else '1'
    set_set('search_enabled', v); await u.message.reply_text("Qidiruv " + ("yoqildi." if v == '1' else "o'chirildi."))
@admin_only
async def broadcast(u, ctx):
    msg = " ".join(ctx.args).strip()
    if not msg: await u.message.reply_text("Foydalanish: /broadcast Xabar matni"); return
    with db() as c: ids = [x['user_id'] for x in c.execute("SELECT user_id FROM users WHERE banned=0").fetchall()]
    sent = 0
    for i in ids:
        try: await ctx.bot.send_message(i, msg); sent += 1
        except Exception: pass
    await u.message.reply_text(f"📢 {sent}/{len(ids)} foydalanuvchiga yuborildi.")

# ================= MATNLI XABARLAR =================
async def router(u, ctx):
    if not u.message or not u.message.text: return
    s = u.message.text.strip(); uid = u.effective_user.id
    p = chat_partner(uid)
    if p:
        if s == "⏹ Suhbatni to'xtatish": await stop_cmd(u, ctx); return
        if s == "🚫 Bloklash": await block_cmd(u, ctx); return
        if s in MENU_BTNS: await u.message.reply_text("Avval suhbatni to'xtating: /stop"); return
        try: await ctx.bot.copy_message(chat_id=p, from_chat_id=uid, message_id=u.message.message_id)
        except Exception:
            await end_chat(ctx, uid, False); await u.message.reply_text("Suhbatdosh topilmadi. Suhbat tugadi.", reply_markup=menu_kb(uid))
        return
    if s in MENU_BTNS:
        for k in ('edit', 'await_report', 'await_receipt', 'await_admin_msg'): ctx.user_data.pop(k, None)
    if ctx.user_data.pop('await_admin_msg', None):
        if ADMIN_ID:
            try: await ctx.bot.send_message(ADMIN_ID, f"📩 Foydalanuvchi xabari\nID: {uid} (@{u.effective_user.username or '-'})\n\n{s[:1000]}\n\nJavob: /reply {uid} matn")
            except Exception: pass
        await u.message.reply_text("✅ Xabaringiz adminga yuborildi.", reply_markup=menu_kb(uid)); return
    if ctx.user_data.get('await_report'):
        target = ctx.user_data.pop('report_target', None); ctx.user_data.pop('await_report', None)
        with db() as c: c.execute("INSERT INTO reports(reporter_id,reported_id,reason,created_at) VALUES(?,?,?,?)", (uid, target, s[:1000], now().isoformat()))
        await u.message.reply_text("✅ Shikoyat saqlandi.", reply_markup=menu_kb(uid))
        if ADMIN_ID:
            try: await ctx.bot.send_message(ADMIN_ID, f"🆘 Shikoyat\nYuboruvchi: {uid}\nProfil: {target}\nSabab: {s[:700]}")
            except Exception: pass
        return
    edit = ctx.user_data.get('edit')
    if edit in ('name', 'age', 'interests'):
        if edit == 'name' and not 2 <= len(s) <= 40: await u.message.reply_text("Ism 2–40 belgi bo'lsin."); return
        if edit == 'age' and not (s.isdigit() and MIN_AGE <= int(s) <= MAX_AGE): await u.message.reply_text(f"{MIN_AGE}–{MAX_AGE} oralig'ida son yozing."); return
        if edit == 'interests' and not 1 <= len(s) <= 500: await u.message.reply_text("1–500 belgi yozing."); return
        with db() as c:
            if edit == 'age':
                c.execute("UPDATE users SET age=?,share_username=CASE WHEN ?<18 THEN 0 ELSE share_username END WHERE user_id=?", (int(s), int(s), uid))
            else: c.execute(f"UPDATE users SET {edit}=? WHERE user_id=?", (s, uid))
        ctx.user_data.pop('edit'); await u.message.reply_text("✅ Yangilandi.", reply_markup=menu_kb(uid)); return
    routes = {"📋 Boshqa bo'limlar": more_menu, "💰 Hisobim": account, "👥 Referal": referral_cmd, "📍 Viloyat bo'yicha izlash": region_search_menu, "🚫 Bloklash": block_cmd, "🚀 Qidiruv": search_all, "👦 O'g'il bola izlash": search_boys, "👧 Qiz bola izlash": search_girls, "💎 VIP obuna": vip_menu,
              "👤 Profilim": my_profile, "✏️ Profilni tahrirlash": edit_profile, "🆘 Shikoyat": report_start, "ℹ️ Yordam": help_cmd,
              "➡️ Keyingi profil": next_cmd, "⏹ To'xtatish": stop_cmd, "🎫 /lic": lic_cmd, "💎 /vip": vip_cmd}
    if s in routes: await routes[s](u, ctx)
    elif s == "👑 Admin panel": await admin(u, ctx)
    elif s == "🏠 Menyu": await u.message.reply_text("Asosiy menyu:", reply_markup=menu_kb(uid))
    else: await u.message.reply_text("Menyudan foydalaning yoki /help yuboring.", reply_markup=menu_kb(uid))

def main():
    if not BOT_TOKEN or BOT_TOKEN == "BOTFATHER_TOKENINI_SHU_YERGA_QOYING": raise RuntimeError("BOT_TOKEN qatoriga BotFather tokenini kiriting.")
    init_db()
    # Sekin/beqaror internet uchun uzun kutish vaqtlari
    app = (Application.builder().token(BOT_TOKEN)
           .connect_timeout(30).read_timeout(30).write_timeout(30).pool_timeout(30)
           .get_updates_connect_timeout(30).get_updates_read_timeout(40).get_updates_write_timeout(30).get_updates_pool_timeout(30)
           .concurrent_updates(True)
           .build())
    app.add_handler(TypeHandler(Update, touch), group=-1)
    T = filters.TEXT & ~filters.COMMAND
    conv = ConversationHandler(entry_points=[CommandHandler("start", start)], states={
        NAME: [MessageHandler(T, name_step)], AGE: [MessageHandler(T, age_step)], GENDER: [MessageHandler(T, gender_step)],
        REGION: [MessageHandler(T, region_step)], PHOTO: [MessageHandler(filters.PHOTO | T, photo_step)],
        INTERESTS: [MessageHandler(T, interests_step)], CONSENT: [MessageHandler(T, consent_step)]
    }, fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True)
    app.add_handler(conv)
    for cmd, fn in [('admin', admin), ('ban', ban_cmd), ('unban', unban_cmd), ('grantvip', grant_cmd), ('revokevip', revoke_cmd), ('set', set_cmd),
                    ('search_on', toggle), ('search_off', toggle), ('broadcast', broadcast), ('next', next_cmd), ('stop', stop_cmd),
                    ('link', lic_cmd), ('lic', lic_cmd), ('vip', vip_cmd), ('help', help_cmd), ('profile', my_profile), ('hisob', account), ('reply', reply_cmd), ('referal', referral_cmd)]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_or_photo))
    app.add_handler(MessageHandler(filters.Sticker.ALL | filters.VOICE, relay_media))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE | filters.Document.ALL | filters.AUDIO | filters.ANIMATION | filters.LOCATION | filters.CONTACT, block_media))
    app.add_handler(MessageHandler(T, router))
    logging.info("TanishuvBot PRO v3 started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1, timeout=20)
print("Bot ishga tushmoqda... (versiya 3.6)")
main()
