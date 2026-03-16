import time
import logging
import re
import os
import urllib.parse
from datetime import datetime
from collections import deque
from bs4 import BeautifulSoup
from curl_cffi import requests as cf_requests
try:
    import pymysql
    import pymysql.cursors
    HAS_DB = True
except ImportError:
    HAS_DB = False

# ────────── LOGGING ──────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ────────── CONFIG ──────────
USERNAME         = os.getenv("IVASMS_USER", "powerxdeveloper@gmail.com")
PASSWORD         = os.getenv("IVASMS_PASS", "Khang1.com")
TELEGRAM_TOKEN   = os.getenv("TG_TOKEN",    "8784790380:AAGX5vI90BLUnSGATdhzVuH9YeBqBGEveWs")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID",  "-1003886766454")
DATABASE_URL     = os.getenv("DATABASE_URL", "")

LOGIN_URL    = "https://www.ivasms.com/login"
SMS_LIVE_URL = "https://www.ivasms.com/portal/live/my_sms"
SMS_REC_URL  = "https://www.ivasms.com/portal/sms/received"

# ────────── ANTIBAN ──────────
MIN_DELAY      = 0.5
BURST_LIMIT    = 10
BURST_WINDOW   = 10
MAX_PER_MINUTE = 30
_msg_queue      = deque()
_sent_times     = deque()
_last_sent_time = 0

# ────────── PROXY CONFIG ──────────
# Free proxy list — Cloudflare bypass ke liye
FREE_PROXIES = [
    "http://103.152.112.162:80",
    "http://103.149.130.38:80", 
    "http://185.162.231.106:80",
    "http://103.155.54.94:80",
    "http://103.152.112.145:80",
]
PROXY_URL = os.getenv("PROXY_URL", "")  # Custom proxy Railway variable mein

def get_proxy():
    if PROXY_URL:
        return {"http": PROXY_URL, "https": PROXY_URL}
    return None

# ────────── SESSION ──────────
session = cf_requests.Session(impersonate="chrome120")

def setup_proxy():
    """Proxy set karo agar available ho"""
    proxy = get_proxy()
    if proxy:
        session.proxies = proxy
        log.info(f"🔀 Proxy set: {list(proxy.values())[0][:30]}...")
    else:
        log.info("⚡ No proxy — direct connection")

# ────────── SEEN SMS ──────────
seen_sms = set()

def is_seen(number, otp, message=""):
    key = str(number)
    if key in seen_sms:
        log.info(f"⏭ Skip duplicate: +{number} OTP={otp}")
        return True
    seen_sms.add(key)
    log.info(f"✅ Marked seen: +{number} OTP={otp}")
    return False

# ────────── DB + FORWARD ──────────
def get_db():
    if not HAS_DB or not DATABASE_URL:
        return None
    try:
        import urllib.parse as up
        p = up.urlparse(DATABASE_URL)
        return pymysql.connect(
            host=p.hostname, port=p.port or 3306,
            user=p.username, password=p.password,
            db=p.path.lstrip('/'),
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5
        )
    except Exception as e:
        log.warning(f"DB connect err: {e}")
        return None

def forward_otp_to_user(number, otp, service, country, message):
    """Last 3 digits se assigned user dhundho aur OTP forward karo"""
    try:
        last3 = str(number).strip()[-3:]
        db = get_db()
        if not db:
            return
        with db.cursor() as cur:
            cur.execute(
                "SELECT assignedTo FROM phone_numbers WHERE number LIKE %s AND assignedTo IS NOT NULL LIMIT 1",
                (f"%{last3}",)
            )
            row = cur.fetchone()
        db.close()

        if not row or not row.get("assignedTo"):
            log.info(f"📭 No user assigned for ...{last3}")
            return

        user_id = row["assignedTo"]
        flag    = get_flag(country)
        masked  = mask_number(number)

        fwd_msg = (
            f"<b>🔔 YOUR OTP ARRIVED!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<blockquote>{flag} <b>Country:</b> {country}</blockquote>\n"
            f"<blockquote>🟢 <b>Service:</b> {service}</blockquote>\n"
            f"<blockquote>📞 <b>Number:</b> <code>+{masked}</code></blockquote>\n"
            f"<blockquote>🔑 <b>OTP:</b> <code>{otp}</code></blockquote>\n"
            f"<blockquote>📧 <b>Message:</b> {message[:200]}</blockquote>\n"
            f"━━━━━━━━━━━━━━━━"
        )
        markup = {"inline_keyboard": [[
            {"text": f"🔑  {otp}  🔑", "copy_text": {"text": otp}}
        ]]}

        r = cf_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": user_id, "text": fwd_msg,
                  "parse_mode": "HTML", "reply_markup": markup},
            timeout=15
        )
        if r.status_code == 200:
            log.info(f"✅ OTP forwarded to user {user_id} (+...{last3})")
        else:
            log.warning(f"⚠️ Forward failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        log.error(f"forward_otp_to_user err: {e}")

# ────────── HELPERS ──────────
def extract_otp(text):
    m = re.search(r'\b(\d{3}[-\s]\d{3})\b', text)
    if m: return m.group(1)
    m = re.search(r'\bG-(\d{4,8})\b', text)
    if m: return "G-" + m.group(1)
    m = re.search(
        r'(?:code|otp|pin|kode|codigo|codice|код|رمز|كود|মোট|কোড|'
        r'verif|confirm|one.time|einmal|dogrulama|mot\s*de\s*passe|'
        r'passcode|password|senha|sifre|token|numer|номер)'
        r'[^0-9]{0,20}(\d{4,8})',
        text, re.IGNORECASE
    )
    if m: return m.group(1)
    m = re.search(r'[:=]\s*(\d{4,8})\b', text)
    if m: return m.group(1)
    m = re.search(r'#(\d{4,8})\b', text)
    if m: return m.group(1)
    matches = re.findall(r'\b(\d{4,8})\b', text)
    for match in matches:
        if not re.match(r'^(19|20)\d{2}$', match):
            return match
    return "N/A"

SERVICE_KEYWORDS = [
    "whatsapp","telegram","facebook","instagram","google","microsoft",
    "apple","paypal","binance","uber","amazon","netflix","tiktok",
    "twitter","snapchat","bybit","okx","kucoin","discord","linkedin",
    "x.com","signal","viber","line","wechat","yahoo","outlook","hotmail",
    "gmail","coinbase","kraken","huobi","gate","mexc","bitget","bitmex",
    "crypto","blockchain","trust","metamask","airbnb","booking","grab",
    "lyft","doordash","zomato","swiggy","shopee","lazada","alibaba",
    "ebay","etsy","walmart","steam","riot","epic","roblox","xbox",
    "playstation","nintendo","revolut","wise","skrill","neteller",
    "stripe","cashapp","venmo","zoom","slack","teams","dropbox",
    "adobe","spotify","tinder","bumble","badoo","match","hinge"
]

def detect_service(text):
    text_lower = text.lower()
    for k in SERVICE_KEYWORDS:
        if k in text_lower:
            return k.title()
    return "Unknown"

def mask_number(n):
    n = re.sub(r'\D', '', str(n))
    return (n[:3] + "****" + n[-3:]) if len(n) >= 8 else n

COUNTRY_FLAGS = {
    "afghanistan":"🇦🇫","albania":"🇦🇱","algeria":"🇩🇿","andorra":"🇦🇩",
    "angola":"🇦🇴","argentina":"🇦🇷","armenia":"🇦🇲","australia":"🇦🇺",
    "austria":"🇦🇹","azerbaijan":"🇦🇿","bahrain":"🇧🇭","bangladesh":"🇧🇩",
    "belarus":"🇧🇾","belgium":"🇧🇪","belize":"🇧🇿","benin":"🇧🇯",
    "bolivia":"🇧🇴","bosnia":"🇧🇦","botswana":"🇧🇼","brazil":"🇧🇷",
    "brunei":"🇧🇳","bulgaria":"🇧🇬","burkina faso":"🇧🇫","burundi":"🇧🇮",
    "cambodia":"🇰🇭","cameroon":"🇨🇲","canada":"🇨🇦","chad":"🇹🇩",
    "chile":"🇨🇱","china":"🇨🇳","colombia":"🇨🇴","congo":"🇨🇬",
    "costa rica":"🇨🇷","croatia":"🇭🇷","cuba":"🇨🇺","cyprus":"🇨🇾",
    "czech":"🇨🇿","ivory coast":"🇨🇮","côte d'ivoire":"🇨🇮",
    "denmark":"🇩🇰","djibouti":"🇩🇯","dominican":"🇩🇴",
    "ecuador":"🇪🇨","egypt":"🇪🇬","el salvador":"🇸🇻","estonia":"🇪🇪",
    "ethiopia":"🇪🇹","finland":"🇫🇮","france":"🇫🇷",
    "gabon":"🇬🇦","gambia":"🇬🇲","georgia":"🇬🇪","germany":"🇩🇪",
    "ghana":"🇬🇭","greece":"🇬🇷","guatemala":"🇬🇹","guinea":"🇬🇳",
    "haiti":"🇭🇹","honduras":"🇭🇳","hong kong":"🇭🇰","hungary":"🇭🇺",
    "iceland":"🇮🇸","india":"🇮🇳","indonesia":"🇮🇩","iran":"🇮🇷",
    "iraq":"🇮🇶","ireland":"🇮🇪","israel":"🇮🇱","italy":"🇮🇹",
    "jamaica":"🇯🇲","japan":"🇯🇵","jordan":"🇯🇴",
    "kazakhstan":"🇰🇿","kenya":"🇰🇪","kuwait":"🇰🇼","kyrgyzstan":"🇰🇬",
    "kosovo":"🇽🇰","laos":"🇱🇦","latvia":"🇱🇻","lebanon":"🇱🇧",
    "libya":"🇱🇾","lithuania":"🇱🇹","luxembourg":"🇱🇺",
    "madagascar":"🇲🇬","malawi":"🇲🇼","malaysia":"🇲🇾","maldives":"🇲🇻",
    "mali":"🇲🇱","malta":"🇲🇹","mauritania":"🇲🇷","mauritius":"🇲🇺",
    "mexico":"🇲🇽","moldova":"🇲🇩","mongolia":"🇲🇳","montenegro":"🇲🇪",
    "morocco":"🇲🇦","mozambique":"🇲🇿","myanmar":"🇲🇲",
    "namibia":"🇳🇦","nepal":"🇳🇵","netherlands":"🇳🇱","new zealand":"🇳🇿",
    "nicaragua":"🇳🇮","niger":"🇳🇪","nigeria":"🇳🇬","north korea":"🇰🇵",
    "norway":"🇳🇴","oman":"🇴🇲",
    "pakistan":"🇵🇰","palestine":"🇵🇸","panama":"🇵🇦","paraguay":"🇵🇾",
    "peru":"🇵🇪","philippines":"🇵🇭","poland":"🇵🇱","portugal":"🇵🇹",
    "qatar":"🇶🇦","romania":"🇷🇴","russia":"🇷🇺","rwanda":"🇷🇼",
    "saudi arabia":"🇸🇦","senegal":"🇸🇳","serbia":"🇷🇸","sierra leone":"🇸🇱",
    "singapore":"🇸🇬","slovakia":"🇸🇰","slovenia":"🇸🇮","somalia":"🇸🇴",
    "south africa":"🇿🇦","south korea":"🇰🇷","spain":"🇪🇸","sri lanka":"🇱🇰",
    "sudan":"🇸🇩","sweden":"🇸🇪","switzerland":"🇨🇭","syria":"🇸🇾",
    "taiwan":"🇹🇼","tajikistan":"🇹🇯","tanzania":"🇹🇿","thailand":"🇹🇭",
    "togo":"🇹🇬","trinidad":"🇹🇹","tunisia":"🇹🇳","turkey":"🇹🇷",
    "turkmenistan":"🇹🇲","uganda":"🇺🇬","ukraine":"🇺🇦","uae":"🇦🇪",
    "united arab":"🇦🇪","united kingdom":"🇬🇧","uk":"🇬🇧",
    "united states":"🇺🇸","usa":"🇺🇸","uruguay":"🇺🇾","uzbekistan":"🇺🇿",
    "venezuela":"🇻🇪","vietnam":"🇻🇳","yemen":"🇾🇪",
    "zambia":"🇿🇲","zimbabwe":"🇿🇼",
}

def get_flag(country):
    c = country.lower().strip()
    if c in COUNTRY_FLAGS: return COUNTRY_FLAGS[c]
    for k, v in COUNTRY_FLAGS.items():
        if k in c: return v
    return "🌍"

# ────────── RATE LIMIT ──────────
def can_send_now():
    now = time.time()
    if now - _last_sent_time < MIN_DELAY: return False
    if len([t for t in _sent_times if now - t < BURST_WINDOW]) >= BURST_LIMIT: return False
    if len([t for t in _sent_times if now - t < 60]) >= MAX_PER_MINUTE: return False
    return True

# ────────── TELEGRAM ──────────
def _do_send(msg, markup):
    global _last_sent_time
    for _ in range(5):
        try:
            r = cf_requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                      "parse_mode": "HTML", "reply_markup": markup},
                timeout=15
            )
            if r.status_code == 200:
                now = time.time()
                _last_sent_time = now
                _sent_times.append(now)
                while _sent_times and now - _sent_times[0] > 120:
                    _sent_times.popleft()
                return True
            elif r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 10)
                time.sleep(wait + 2)
            else:
                log.error(f"TG error {r.status_code}: {r.text}")
                return False
        except Exception as e:
            log.error(f"TG fail: {e}")
            time.sleep(3)
    return False

def enqueue(country, number, service, otp, message, label="live"):
    flag   = get_flag(country)
    masked = mask_number(number)
    iso    = re.sub(r'[^A-Z]', '', country.upper())[:2]

    header = "┊°°°📂 𝐑𝐄𝐂𝐎𝐑𝐃 𝐒𝐌𝐒 📂°°°┊" if label.startswith("record") else "┊°°°✅ 𝐎𝐓𝐏 𝐑𝐄𝐂𝐄𝐈𝐕𝐄𝐃 ✅°°°┊"

    msg = (
        f"<b>{header}</b>\n"
        f"<blockquote>"
        f"{flag} <b>#{iso}</b>  <code>+{masked}</code>\n"
        f"🟢 <b>#CLI</b>  {service.upper()}"
        f"</blockquote>"
    )
    markup = {
        "inline_keyboard": [
            [{"text": f"🔑  {otp}  🔑", "copy_text": {"text": otp}}],
            [{"text": "📞 NUMBERS", "url": "https://t.me/dp_numbers"},
             {"text": "🔰 BACKUP",  "url": "https://t.me/powerotpbackup"}]
        ]
    }
    _msg_queue.append((msg, markup, masked, otp, service))
    log.info(f"📥 Queued [{label}]: OTP={otp} | +{masked} | {service}")

    # ── Forward OTP to assigned user via bot ──
    forward_otp_to_user(number, otp, service, country, message)

def flush_queue():
    while _msg_queue:
        if can_send_now():
            msg, markup, masked, otp, service = _msg_queue.popleft()
            if _do_send(msg, markup):
                log.info(f"🚀 Sent: OTP={otp} | +{masked} | {service}")
            else:
                _msg_queue.appendleft((msg, markup, masked, otp, service))
        time.sleep(1)

def process_queue():
    if not _msg_queue or not can_send_now(): return
    msg, markup, masked, otp, service = _msg_queue.popleft()
    if _do_send(msg, markup):
        log.info(f"🚀 Sent: OTP={otp} | +{masked} | {service}")
    else:
        _msg_queue.appendleft((msg, markup, masked, otp, service))

# ────────── LOGIN ──────────
def try_login_once():
    """Single login attempt"""
    resp = session.get(LOGIN_URL, timeout=30)
    soup = BeautifulSoup(resp.text, "html.parser")
    csrf_input = soup.find("input", {"name": "_token"})
    csrf_token = csrf_input["value"] if csrf_input else ""
    payload = {"_token": csrf_token, "email": USERNAME, "password": PASSWORD}
    headers = {"Referer": LOGIN_URL, "Origin": "https://www.ivasms.com",
               "Content-Type": "application/x-www-form-urlencoded"}
    resp2 = session.post(LOGIN_URL, data=payload, headers=headers, timeout=30)
    if "logout" in resp2.text.lower() or resp2.url != LOGIN_URL:
        return True
    return False

def do_login():
    log.info("🌐 Logging in (Cloudflare bypass)...")
    
    # Pehle direct try karo
    try:
        setup_proxy()
        if try_login_once():
            log.info("✅ Login successful!")
            return True
        log.warning("❌ Direct login failed — trying free proxies...")
    except Exception as e:
        log.warning(f"Direct login err: {e}")

    # Free proxies try karo
    for proxy in FREE_PROXIES:
        try:
            log.info(f"🔀 Trying proxy: {proxy}")
            session.proxies = {"http": proxy, "https": proxy}
            if try_login_once():
                log.info(f"✅ Login via proxy: {proxy}")
                return True
            log.warning(f"❌ Proxy failed: {proxy}")
            time.sleep(2)
        except Exception as e:
            log.warning(f"Proxy {proxy} err: {e}")
            continue

    # Reset proxy
    session.proxies = {}
    log.error("❌ All login attempts failed!")
    return False

# ────────── AJAX CSRF ──────────
GETSMS_URL = "https://www.ivasms.com/portal/sms/received/getsms"

def get_csrf_token():
    resp = session.get(SMS_REC_URL, timeout=30)
    soup = BeautifulSoup(resp.text, "html.parser")
    inp = soup.find("input", {"name": "_token"})
    if inp: return inp["value"]
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta: return meta.get("content", "")
    for s in soup.find_all("script"):
        t = s.string or ""
        m = re.search(r"_token['\"]?\s*[,:]\s*['\"]([^'\"]{20,})['\"]", t)
        if m: return m.group(1)
    return ""

# ────────── FAST POLL ──────────
_csrf_cache   = {"token": "", "fetched_at": 0}
_ranges_cache = {"ranges": [], "nums_cache": {}, "fetched_at": 0}
GETSMS_NUM_URL = "https://www.ivasms.com/portal/sms/received/getsms/number"
SMS_URL        = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"

def get_csrf_cached():
    now = time.time()
    if _csrf_cache["token"] and now - _csrf_cache["fetched_at"] < 300:
        return _csrf_cache["token"]
    token = get_csrf_token()
    _csrf_cache["token"] = token
    _csrf_cache["fetched_at"] = now
    log.info(f"🔑 CSRF refreshed: {token[:15]}...")
    return token

def get_hdrs():
    return {
        "Referer": SMS_REC_URL, "Origin": "https://www.ivasms.com",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html, */*",
    }

def refresh_ranges(today, csrf):
    now = time.time()
    if _ranges_cache["ranges"] and now - _ranges_cache["fetched_at"] < 30:
        return True
    r1 = session.post(GETSMS_URL,
        data=urllib.parse.urlencode({"from": today, "to": today, "_token": csrf}),
        headers=get_hdrs(), timeout=15)
    if r1.status_code != 200:
        log.warning(f"Step1 fail: {r1.status_code}")
        return False
    soup1 = BeautifulSoup(r1.text, "html.parser")
    ranges = []
    for div in soup1.find_all("div", class_="rng"):
        m = re.search(r"toggleRange\('([^']+)'", div.get("onclick", ""))
        if m: ranges.append(m.group(1))
    if not ranges:
        log.info("No ranges yet")
        return False
    nums_cache = {}
    for range_id in ranges:
        r2 = session.post(GETSMS_NUM_URL,
            data=urllib.parse.urlencode({"_token": csrf, "start": today, "end": today, "range": range_id}),
            headers=get_hdrs(), timeout=10)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        range_val = range_id
        for s in soup2.find_all("script"):
            rm = re.search(r"Range:'([^']+)'", s.string or "")
            if rm: range_val = rm.group(1)
        numbers = []
        for ndiv in soup2.find_all("div", class_="nrow"):
            nm = re.search(r"toggleNum\w+\('(\d+)'", ndiv.get("onclick", ""))
            if nm: numbers.append(nm.group(1))
        country_name = re.sub(r'\d+', '', range_id).strip()
        nums_cache[range_id] = {"range_val": range_val, "numbers": numbers, "country": country_name}
        log.info(f"📍 {country_name}: {len(numbers)} numbers")
    _ranges_cache["ranges"]     = ranges
    _ranges_cache["nums_cache"] = nums_cache
    _ranges_cache["fetched_at"] = now
    return True

def fast_poll():
    today = datetime.now().strftime("%Y-%m-%d")
    csrf  = get_csrf_cached()
    if not csrf:
        do_login(); _csrf_cache["token"] = ""; return
    if not refresh_ranges(today, csrf):
        return
    for range_id, info in _ranges_cache["nums_cache"].items():
        range_val    = info["range_val"]
        country_name = info["country"]
        for number in info["numbers"]:
            if str(number) in seen_sms:
                continue
            r3 = session.post(SMS_URL,
                data=urllib.parse.urlencode({"_token": csrf, "start": today, "end": today,
                    "Number": number, "Range": range_val}),
                headers=get_hdrs(), timeout=10)
            soup3 = BeautifulSoup(r3.text, "html.parser")
            for tr in soup3.select("table tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 2: continue
                sender  = tds[0].get_text(strip=True)
                msg_div = tds[1].find(class_="msg-text")
                message = msg_div.get_text(strip=True) if msg_div else tds[1].get_text(strip=True)
                if not message or len(message) < 3: continue
                otp = extract_otp(message)
                if otp == "N/A": continue
                if is_seen(number, otp, message): continue
                service = detect_service(sender + " " + message)
                log.info(f"⚡ NEW OTP: +{number} | {otp} | {sender}")
                enqueue(country_name, number, service, otp, message, label="fast")
                flush_queue()

# ────────── MAIN ──────────
if not do_login():
    log.error("Cannot login. Exiting.")
    exit(1)

log.info("🚀 Starting fast poll loop...")
while True:
    try:
        fast_poll()
    except Exception as e:
        log.warning(f"Poll error: {e}")
        _csrf_cache["token"] = ""
        _ranges_cache["fetched_at"] = 0
    process_queue()
    time.sleep(2)
