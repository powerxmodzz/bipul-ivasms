import time
import logging
import re
import os
import urllib.parse
from datetime import datetime
from collections import deque
from bs4 import BeautifulSoup
from curl_cffi import requests as cf_requests

# ────────── LOGGING ──────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ────────── CONFIG ──────────
USERNAME         = os.getenv("IVASMS_USER", "rehmanaliofficial444@gmail.com")
PASSWORD         = os.getenv("IVASMS_PASS", "456456456")
TELEGRAM_TOKEN   = os.getenv("TG_TOKEN",    "7996277191:AAF8wE9TCrOsiGn_8Il-cCzJi3pEFuhhiCk")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID",  "-1003794052607")

LOGIN_URL    = "https://www.ivasms.com/login"
SMS_LIVE_URL = "https://www.ivasms.com/portal/live/my_sms"
SMS_REC_URL  = "https://www.ivasms.com/portal/sms/received"
SEEN_FILE    = "seen_sms.txt"

# ────────── ANTIBAN ──────────
MIN_DELAY      = 0.5
BURST_LIMIT    = 10
BURST_WINDOW   = 10
MAX_PER_MINUTE = 30
_msg_queue      = deque()
_sent_times     = deque()
_last_sent_time = 0

# ────────── SESSION ──────────
session = cf_requests.Session(impersonate="chrome120")

# ────────── SEEN SMS — memory only, no duplicates ──────────
seen_sms = set()  # memory mein — sirf aaj ka track

def is_seen(number, otp, message=""):
    # number only — ek number ka koi bhi otp sirf ek baar
    key = str(number)
    if key in seen_sms:
        log.info(f"⏭ Skip duplicate: +{number} OTP={otp}")
        return True
    seen_sms.add(key)
    log.info(f"✅ Marked seen: +{number} OTP={otp}")
    return False

# ────────── HELPERS ──────────
def extract_otp(text):
    # Format: 598-909 or 123 456 (xxx-xxx or xxx xxx)
    m = re.search(r'\b(\d{3}[-\s]\d{3})\b', text)
    if m: return m.group(1)
    # Google format: G-123456
    m = re.search(r'\bG-(\d{4,8})\b', text)
    if m: return "G-" + m.group(1)
    # Common OTP keywords in many languages + digits
    m = re.search(
        r'(?:code|otp|pin|kode|codigo|codice|код|رمز|كود|মোট|কোড|'
        r'verif|confirm|one.time|einmal|dogrulama|mot\s*de\s*passe|'
        r'passcode|password|senha|sifre|token|numer|номер)'
        r'[^0-9]{0,20}(\d{4,8})',
        text, re.IGNORECASE
    )
    if m: return m.group(1)
    # Digits after colon or equals: "is: 1234" or "=1234"
    m = re.search(r'[:=]\s*(\d{4,8})\b', text)
    if m: return m.group(1)
    # Hashtag format: #123456
    m = re.search(r'#(\d{4,8})\b', text)
    if m: return m.group(1)
    # Last resort: any standalone 4-8 digit number
    matches = re.findall(r'\b(\d{4,8})\b', text)
    # Filter out phone numbers and years
    for match in matches:
        if not re.match(r'^(19|20)\d{2}$', match):  # skip years
            return match
    return "N/A"

SERVICE_KEYWORDS = [
    "whatsapp","telegram","facebook","instagram","google","microsoft",
    "apple","paypal","binance","uber","amazon","netflix","tiktok",
    "twitter","snapchat","bybit","okx","kucoin","discord","linkedin",
    "twitter","x.com","signal","viber","line","wechat","yahoo",
    "outlook","hotmail","gmail","coinbase","kraken","huobi","gate",
    "mexc","bitget","bitmex","crypto","blockchain","trust","metamask",
    "airbnb","booking","grab","lyft","doordash","zomato","swiggy",
    "shopee","lazada","alibaba","ebay","etsy","walmart","target",
    "steam","riot","epic","roblox","xbox","playstation","nintendo",
    "revolut","wise","skrill","neteller","stripe","cashapp","venmo",
    "zoom","slack","teams","dropbox","adobe","spotify","tinder",
    "bumble","badoo","pof","match","hinge","grindr","twitter"
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
    # A
    "afghanistan":"🇦🇫","albania":"🇦🇱","algeria":"🇩🇿","andorra":"🇦🇩",
    "angola":"🇦🇴","argentina":"🇦🇷","armenia":"🇦🇲","australia":"🇦🇺",
    "austria":"🇦🇹","azerbaijan":"🇦🇿",
    # B
    "bahrain":"🇧🇭","bangladesh":"🇧🇩","belarus":"🇧🇾","belgium":"🇧🇪",
    "belize":"🇧🇿","benin":"🇧🇯","bolivia":"🇧🇴","bosnia":"🇧🇦",
    "botswana":"🇧🇼","brazil":"🇧🇷","brunei":"🇧🇳","bulgaria":"🇧🇬",
    "burkina faso":"🇧🇫","burundi":"🇧🇮",
    # C
    "cambodia":"🇰🇭","cameroon":"🇨🇲","canada":"🇨🇦","chad":"🇹🇩",
    "chile":"🇨🇱","china":"🇨🇳","colombia":"🇨🇴","congo":"🇨🇬",
    "costa rica":"🇨🇷","croatia":"🇭🇷","cuba":"🇨🇺","cyprus":"🇨🇾",
    "czech":"🇨🇿","côte d'ivoire":"🇨🇮","ivory coast":"🇨🇮",
    # D
    "denmark":"🇩🇰","djibouti":"🇩🇯","dominican":"🇩🇴",
    # E
    "ecuador":"🇪🇨","egypt":"🇪🇬","el salvador":"🇸🇻","estonia":"🇪🇪",
    "ethiopia":"🇪🇹",
    # F
    "finland":"🇫🇮","france":"🇫🇷",
    # G
    "gabon":"🇬🇦","gambia":"🇬🇲","georgia":"🇬🇪","germany":"🇩🇪",
    "ghana":"🇬🇭","greece":"🇬🇷","guatemala":"🇬🇹","guinea":"🇬🇳",
    # H
    "haiti":"🇭🇹","honduras":"🇭🇳","hong kong":"🇭🇰","hungary":"🇭🇺",
    # I
    "iceland":"🇮🇸","india":"🇮🇳","indonesia":"🇮🇩","iran":"🇮🇷",
    "iraq":"🇮🇶","ireland":"🇮🇪","israel":"🇮🇱","italy":"🇮🇹",
    # J
    "jamaica":"🇯🇲","japan":"🇯🇵","jordan":"🇯🇴",
    # K
    "kazakhstan":"🇰🇿","kenya":"🇰🇪","kuwait":"🇰🇼","kyrgyzstan":"🇰🇬",
    "kosovo":"🇽🇰",
    # L
    "laos":"🇱🇦","latvia":"🇱🇻","lebanon":"🇱🇧","libya":"🇱🇾",
    "lithuania":"🇱🇹","luxembourg":"🇱🇺",
    # M
    "madagascar":"🇲🇬","malawi":"🇲🇼","malaysia":"🇲🇾","maldives":"🇲🇻",
    "mali":"🇲🇱","malta":"🇲🇹","mauritania":"🇲🇷","mauritius":"🇲🇺",
    "mexico":"🇲🇽","moldova":"🇲🇩","mongolia":"🇲🇳","montenegro":"🇲🇪",
    "morocco":"🇲🇦","mozambique":"🇲🇿","myanmar":"🇲🇲",
    # N
    "namibia":"🇳🇦","nepal":"🇳🇵","netherlands":"🇳🇱","new zealand":"🇳🇿",
    "nicaragua":"🇳🇮","niger":"🇳🇪","nigeria":"🇳🇬","north korea":"🇰🇵",
    "norway":"🇳🇴",
    # O
    "oman":"🇴🇲",
    # P
    "pakistan":"🇵🇰","palestine":"🇵🇸","panama":"🇵🇦","paraguay":"🇵🇾",
    "peru":"🇵🇪","philippines":"🇵🇭","poland":"🇵🇱","portugal":"🇵🇹",
    # Q
    "qatar":"🇶🇦",
    # R
    "romania":"🇷🇴","russia":"🇷🇺","rwanda":"🇷🇼",
    # S
    "saudi arabia":"🇸🇦","senegal":"🇸🇳","serbia":"🇷🇸","sierra leone":"🇸🇱",
    "singapore":"🇸🇬","slovakia":"🇸🇰","slovenia":"🇸🇮","somalia":"🇸🇴",
    "south africa":"🇿🇦","south korea":"🇰🇷","spain":"🇪🇸","sri lanka":"🇱🇰",
    "sudan":"🇸🇩","sweden":"🇸🇪","switzerland":"🇨🇭","syria":"🇸🇾",
    # T
    "taiwan":"🇹🇼","tajikistan":"🇹🇯","tanzania":"🇹🇿","thailand":"🇹🇭",
    "togo":"🇹🇬","trinidad":"🇹🇹","tunisia":"🇹🇳","turkey":"🇹🇷",
    "turkmenistan":"🇹🇲",
    # U
    "uganda":"🇺🇬","ukraine":"🇺🇦","uae":"🇦🇪","united arab":"🇦🇪",
    "united kingdom":"🇬🇧","uk":"🇬🇧","united states":"🇺🇸","usa":"🇺🇸",
    "uruguay":"🇺🇾","uzbekistan":"🇺🇿",
    # V
    "venezuela":"🇻🇪","vietnam":"🇻🇳",
    # Y
    "yemen":"🇾🇪",
    # Z
    "zambia":"🇿🇲","zimbabwe":"🇿🇼",
}

def get_flag(country):
    c = country.lower().strip()
    # exact match first
    if c in COUNTRY_FLAGS:
        return COUNTRY_FLAGS[c]
    # partial match
    for k, v in COUNTRY_FLAGS.items():
        if k in c:
            return v
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

    if label.startswith("record"):
        header = "┊°°°📂 𝐑𝐄𝐂𝐎𝐑𝐃 𝐒𝐌𝐒 📂°°°┊"
    else:
        header = "┊°°°✅ 𝐎𝐓𝐏 𝐑𝐄𝐂𝐄𝐈𝐕𝐄𝐃 ✅°°°┊"

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
            [{"text": "📞 NUMBERS", "url": "https://t.me/+EY7OsI1Rvck0ZTU1"},
             {"text": "🔰 BACKUP",  "url": "https://t.me/+TttHxvDBGPk0Y2Rk"}]
        ]
    }
    _msg_queue.append((msg, markup, masked, otp, service))
    log.info(f"📥 Queued [{label}]: OTP={otp} | +{masked} | {service}")

def flush_queue():
    """Send all queued messages with delay"""
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

# ────────── PARSE LIVE SMS ──────────
def parse_live_sms(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Debug: log what we find
    all_tables = soup.find_all("table")
    all_trs = soup.select("table tbody tr")
    log.info(f"🔍 Live page: {len(html)} bytes | tables={len(all_tables)} | rows={len(all_trs)}")

    # Try different column positions
    for tr in all_trs:
        tds = tr.find_all("td")
        log.info(f"  Row has {len(tds)} tds: {[td.get_text(strip=True)[:30] for td in tds]}")
        if len(tds) < 2: continue

        # Try to find number in any td
        number = None
        country = ""
        service = ""
        message = ""

        for i, td in enumerate(tds):
            txt = td.get_text(" ", strip=True)
            nm = re.search(r'\b(\d{8,15})\b', txt)
            if nm and not number:
                number = nm.group(1)
                country = re.sub(r'\b\d+\b', '', txt).strip()

        # message is usually longest td
        texts = [td.get_text(strip=True) for td in tds]
        message = max(texts, key=len) if texts else ""
        service = tds[1].get_text(strip=True) if len(tds) > 1 else ""

        if number and message and len(message) > 5:
            results.append((country, number, service, message))

    return results

# ────────── LOGIN ──────────
def do_login():
    log.info("🌐 Logging in (Cloudflare bypass)...")
    try:
        resp = session.get(LOGIN_URL, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_token"})
        csrf_token = csrf_input["value"] if csrf_input else ""

        payload = {
            "_token":   csrf_token,
            "email":    USERNAME,
            "password": PASSWORD,
        }
        headers = {
            "Referer":      LOGIN_URL,
            "Origin":       "https://www.ivasms.com",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp2 = session.post(LOGIN_URL, data=payload, headers=headers, timeout=30)

        if "logout" in resp2.text.lower() or resp2.url != LOGIN_URL:
            log.info("✅ Login successful!")
            return True
        else:
            log.error("❌ Login failed!")
            return False
    except Exception as e:
        log.error(f"Login exception: {e}")
        return False

# ────────── FETCH RECORDS (AJAX) ──────────
GETSMS_URL = "https://www.ivasms.com/portal/sms/received/getsms"

def get_csrf_token():
    resp = session.get(SMS_REC_URL, timeout=30)
    soup = BeautifulSoup(resp.text, "html.parser")
    inp = soup.find("input", {"name": "_token"})
    if inp:
        return inp["value"]
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta:
        return meta.get("content", "")
    for s in soup.find_all("script"):
        t = s.string or ""
        m = re.search(r"_token['\"]?\s*[,:]\s*['\"]([^'\"]{20,})['\"]", t)
        if m:
            return m.group(1)
    return ""

def fetch_records(date_from, date_to, label_name):
    log.info(f"📂 Fetching records {date_from} to {date_to}...")
    count = 0
    try:
        csrf_token = get_csrf_token()
        log.info(f"🔑 CSRF: {csrf_token[:20]}...")

        headers = {
            "Referer":          SMS_REC_URL,
            "Origin":           "https://www.ivasms.com",
            "X-Requested-With": "XMLHttpRequest",
            "Accept":           "text/html, */*",
        }
        import urllib.parse
        form_data = urllib.parse.urlencode({
            "from":   date_from,
            "to":     date_to,
            "_token": csrf_token,
        })
        resp = session.post(GETSMS_URL, data=form_data, headers={
            "Referer":          SMS_REC_URL,
            "Origin":           "https://www.ivasms.com",
            "X-Requested-With": "XMLHttpRequest",
            "Accept":           "text/html, */*",
            "Content-Type":     "application/x-www-form-urlencoded",
        }, timeout=30)
        log.info(f"📡 Status: {resp.status_code} | Size: {len(resp.text)}")

        if resp.status_code != 200:
            log.error(f"❌ AJAX failed: {resp.status_code}")
            return 0

        soup = BeautifulSoup(resp.text, "html.parser")

        # Step 3: Find all country range divs
        # Structure: <div class="rng" onclick="toggleRange('IVORY COAST 3452','IVORY_COAST_3452')">
        GETSMS_NUM_URL = "https://www.ivasms.com/portal/sms/received/getsms/number"
        country_ranges = []
        for div in soup.find_all("div", class_="rng"):
            onclick = div.get("onclick", "")
            # Extract range id like "IVORY COAST 3452"
            m = re.search(r"toggleRange\('([^']+)'", onclick)
            if m:
                range_id = m.group(1)
                country_name = re.sub(r'\d+', '', range_id).strip()
                country_ranges.append((range_id, country_name))

        log.info(f"🌍 Found {len(country_ranges)} countries: {[c[1] for c in country_ranges]}")

        for range_id, country_name in country_ranges:
            try:
                time.sleep(0.1)
                # Step 4: Fetch numbers for this country
                num_form = urllib.parse.urlencode({
                    "_token": csrf_token,
                    "start":  date_from,
                    "end":    date_to,
                    "range":  range_id,
                })
                r = session.post(GETSMS_NUM_URL, data=num_form, headers={
                    "Referer":          SMS_REC_URL,
                    "Origin":           "https://www.ivasms.com",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type":     "application/x-www-form-urlencoded",
                }, timeout=30)
                soup2 = BeautifulSoup(r.text, "html.parser")
                log.info(f"  {country_name}: status={r.status_code} size={len(r.text)}")

                # Get range value from JS in response
                sms_url = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"
                range_val = range_id
                for s in soup2.find_all("script"):
                    t = s.string or ""
                    rm = re.search(r"Range:'([^']+)'", t)
                    if rm:
                        range_val = rm.group(1)

                # Find numbers from onclick divs
                number_divs = soup2.find_all("div", class_="nrow")
                log.info(f"  📞 Found {len(number_divs)} numbers in {country_name}")

                for ndiv in number_divs:
                    onclick = ndiv.get("onclick", "")
                    nm = re.search(r"toggleNum\w+\('(\d+)'", onclick)
                    if not nm: continue
                    number = nm.group(1)

                    try:
                        time.sleep(0.1)
                        sms_form = urllib.parse.urlencode({
                            "_token": csrf_token,
                            "start":  date_from,
                            "end":    date_to,
                            "Number": number,
                            "Range":  range_val,
                        })
                        r2 = session.post(sms_url, data=sms_form, headers={
                            "Referer":          SMS_REC_URL,
                            "Origin":           "https://www.ivasms.com",
                            "X-Requested-With": "XMLHttpRequest",
                            "Content-Type":     "application/x-www-form-urlencoded",
                        }, timeout=30)
                        soup3 = BeautifulSoup(r2.text, "html.parser")
                        log.info(f"    SMS {number}: status={r2.status_code} size={len(r2.text)}")
                        log.info(f"    RAW SMS RESPONSE: {r2.text[:500]}")
                        with open(f"debug_sms_{number}.html", "w", encoding="utf-8") as f:
                            f.write(r2.text)

                        for tr in soup3.select("table tbody tr"):
                            tds = tr.find_all("td")
                            if len(tds) < 2: continue

                            # Sender from first td
                            sender = tds[0].get_text(strip=True)

                            # Message from msg-text div inside second td
                            msg_div = tds[1].find(class_="msg-text")
                            message = msg_div.get_text(strip=True) if msg_div else tds[1].get_text(strip=True)

                            if not message or len(message) < 3: continue

                            otp = extract_otp(message)
                            if otp == "N/A": continue

                            service = detect_service(sender + " " + message)
                            if is_seen(number, otp, message): continue

                            log.info(f"    ✅ OTP={otp} | {service} | {number}")
                            enqueue(country_name, number, service, otp, message, label=f"record-{label_name}")
                            count += 1
                            flush_queue()

                    except Exception as e:
                        log.error(f"SMS fetch error {number}: {e}")

            except Exception as e:
                log.error(f"Country error {country_name}: {e}")

    except Exception as e:
        log.error(f"Records fetch error: {e}")

    log.info(f"📂 {label_name} records done! Total: {count}")
    return count

# ══════════════════════════════════════
#               MAIN
# ══════════════════════════════════════
if not do_login():
    log.error("Cannot login. Exiting.")
    exit(1)

# ── PHASE 1: SKIPPED — go straight to live ──
log.info("=" * 50)
log.info("🟢 LIVE POLLING STARTED — 2 seconds!")
log.info("=" * 50)

# ── PHASE 2: Fast AJAX polling every 3 seconds ──
log.info("=" * 50)
log.info("🟢 FAST AJAX POLLING — 3 seconds!")
log.info("=" * 50)

import urllib.parse

# ── Caches ──
_csrf_cache    = {"token": "", "fetched_at": 0}
_ranges_cache  = {"ranges": [], "nums_cache": {}, "fetched_at": 0}  # Step1+2 cache

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
        "Referer": SMS_REC_URL,
        "Origin": "https://www.ivasms.com",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html, */*",
    }

def refresh_ranges(today, csrf):
    """Step1+2: Fetch countries+numbers — cache 30 sec"""
    now = time.time()
    if _ranges_cache["ranges"] and now - _ranges_cache["fetched_at"] < 30:
        return True  # use cache

    # Step 1: countries
    r1 = session.post(GETSMS_URL,
        data=urllib.parse.urlencode({"from": today, "to": today, "_token": csrf}),
        headers=get_hdrs(), timeout=15)
    if r1.status_code != 200:
        log.warning(f"Step1 fail: {r1.status_code}")
        return False

    soup1 = BeautifulSoup(r1.text, "html.parser")
    ranges = []
    for div in soup1.find_all("div", class_="rng"):
        oc = div.get("onclick", "")
        m = re.search(r"toggleRange\('([^']+)'", oc)
        if m: ranges.append(m.group(1))

    if not ranges:
        log.info("No ranges yet")
        return False

    # Step 2: numbers for each range
    nums_cache = {}
    for range_id in ranges:
        r2 = session.post(GETSMS_NUM_URL,
            data=urllib.parse.urlencode({"_token": csrf, "start": today, "end": today, "range": range_id}),
            headers=get_hdrs(), timeout=10)
        soup2 = BeautifulSoup(r2.text, "html.parser")

        range_val = range_id
        for s in soup2.find_all("script"):
            t = s.string or ""
            rm = re.search(r"Range:'([^']+)'", t)
            if rm: range_val = rm.group(1)

        numbers = []
        for ndiv in soup2.find_all("div", class_="nrow"):
            oc = ndiv.get("onclick", "")
            nm = re.search(r"toggleNum\w+\('(\d+)'", oc)
            if nm: numbers.append(nm.group(1))

        country_name = re.sub(r'\d+', '', range_id).strip()
        nums_cache[range_id] = {"range_val": range_val, "numbers": numbers, "country": country_name}
        log.info(f"📍 {country_name}: {len(numbers)} numbers")

    _ranges_cache["ranges"]     = ranges
    _ranges_cache["nums_cache"] = nums_cache
    _ranges_cache["fetched_at"] = now
    return True

def fast_poll():
    """Step3 only for NEW unseen numbers — super fast"""
    today = datetime.now().strftime("%Y-%m-%d")
    csrf  = get_csrf_cached()
    if not csrf:
        do_login(); _csrf_cache["token"] = ""; return

    # Refresh ranges+numbers every 30s (Step1+2)
    if not refresh_ranges(today, csrf):
        return

    # Step 3: fetch SMS only for NEW numbers
    for range_id, info in _ranges_cache["nums_cache"].items():
        range_val    = info["range_val"]
        country_name = info["country"]
        for number in info["numbers"]:
            # ⚡ Skip already seen
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
                if not otp or otp == "N/A": continue
                if is_seen(number, otp, message): continue

                log.info(f"⚡ NEW OTP: +{number} | {otp} | {sender}")
                enqueue(country_name, number, sender, otp, message, label="fast")
                flush_queue()

try:
    while True:
        try:
            fast_poll()
        except Exception as e:
            log.warning(f"Poll error: {e}")
            _csrf_cache["token"] = ""
            _ranges_cache["fetched_at"] = 0  # force refresh

        process_queue()
        time.sleep(2)  # 2 sec polling

except KeyboardInterrupt:
    log.info("⛔ Stopped by user")
