"""Minimal TikTok web-API transport for the search endpoint.

Plain curl_cffi with a browser-impersonated TLS fingerprint — no browser, no
login. A search call needs three things:

  1. a *booted* session: one profile page is loaded first, which sets the
     ttwid / tt_csrf_token cookies and yields the page's own device id
     (without them search answers status 2483 "Please login");
  2. a signed query: signer.py appends X-Dynosaur, msToken, X-Bogus and
     X-Gnarly;
  3. the right exit region: video search answers from US exits and is
     silently empty (0-byte body) from EU ones, so `countries` is tried in
     order, each with a few fresh sessions.

The silent empty body is TikTok's one block signal (-> Blocked). The very
first signed call of a session is often dropped, so a fresh session gets one
free retry.

التشغيل بلا proxy (قرار تصميم نهائي — لا يوجد أي بروكسي في المشروع):
صمود الحجب يتم داخل العملية فقط عبر الأدوات التالية:

  * جلسات متعددة لكل إقليم (SESSIONS_PER_COUNTRY): كل جلسة تُولد بمعرّف
    جهاز عشوائي جديد وكوكيز جديدة بعد boot صفحة حقيقية، فتبدو للطرف الآخر
    كمتصفح جديد قادم من نفس الخادم؛
  * تهدئة تصاعدية بين الجلسات (RETRY_BACKOFF: 0.5s ثم 1s ثم 2s) بعشوائية
    بسيطة تكسر نمط توقيت المحاولات الآلية؛
  * إعادة استخدام الجلسة السليمة حتى SESSION_MAX_AGE: تقليل عدد عمليات
    boot الإضافية يقلل التعرض للفحص ويجعل الإيقاع أقرب لمتصفح حقيقي؛
  * سجل واضح يفرّق بين خطأ الكود وحجب IP مركز البيانات، ورسالة 502 مفهومة.

إذا فشلت كل الجلسات فالسبب المرجّح أن عنوان IP مركز البيانات نفسه مصنّف
كغير مرغوب — هذا لا يُحل داخل الكود؛ انظر DEPLOY_RENDER.md للبدائل
المشروعة (لا يوجد ولا يُقترح أي proxy).
"""
import json
import logging
import os
import random
import re
import time

from . import signer
from .errors import BadRequest, Blocked, UpstreamError

log = logging.getLogger("tiktok_search.client")   # يظهر في stdout عبر إعداد server.py

WWW = "https://www.tiktok.com"
IMPERSONATE = "chrome"

# The signer seals the User-Agent into X-Gnarly, so the header, the
# browser_version param and the signer input must be this same string.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

API_TIMEOUT = 30
PAGE_TIMEOUT = 45
# من 2 إلى 3 جلسات جديدة لكل إقليم: محاولات أكثر ترفع فرص النجاح بلا proxy.
# يمكن ضبطها من البيئة دون تعديل الكود: TIKTOK_SESSIONS_PER_COUNTRY=4
SESSIONS_PER_COUNTRY = max(1, int(os.environ.get("TIKTOK_SESSIONS_PER_COUNTRY") or 3))
SESSION_MAX_AGE = 1800         # seconds a session is reused
BOOT_PATH = "/@tiktok"         # any profile page sets the session cookies
LOGIN_WALL_STATUS = 2483

# تهدئة تصاعدية بين محاولات الجلسات داخل search_get: 0.5s ثم 1s ثم 2s
# (تبقى 2s لما بعدها) وتُضرب بعامل عشوائي بسيط حتى لا يكون التوقيت نمطًا ثابتًا.
RETRY_BACKOFF = (0.5, 1.0, 2.0)

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
}

_UNIVERSAL_RE = re.compile(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)


def universal_data(html):
    """The page's `__DEFAULT_SCOPE__` rehydration dict, or None."""
    match = _UNIVERSAL_RE.search(html or "")
    if not match:
        return None
    try:
        scope = json.loads(match.group(1)).get("__DEFAULT_SCOPE__")
    except (ValueError, AttributeError):
        return None
    return scope if isinstance(scope, dict) else None


def status_of(payload):
    """The upstream status code of a JSON answer (0 = ok)."""
    for key in ("statusCode", "status_code", "code"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


class _Session:
    """One curl session on one exit, with its own device id and cookie jar."""

    def __init__(self, country):
        from curl_cffi import requests as curl_requests
        self.session = curl_requests.Session(impersonate=IMPERSONATE)
        self.session.headers.update({"user-agent": USER_AGENT, "accept-language": "en-US,en;q=0.9"})
        self.country = country
        # معرّف جهاز عشوائي جديد لكل جلسة: جلسة جديدة = هوية متصفح جديدة
        self.device_id = str(random.randint(7250000000000000000, 7351147085025500000))
        self.born = time.time()
        self.booted = False
        self.answered = False      # has a signed call ever answered on this session

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def boot(self):
        """Load one page: sets the cookies and adopts the page's device id."""
        try:
            resp = self.session.get(WWW + BOOT_PATH, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT)
        except Exception as e:
            raise UpstreamError(f"request failed: {type(e).__name__}: {e}")
        if resp.status_code in (401, 403, 429):
            raise Blocked(f"HTTP {resp.status_code} on {BOOT_PATH}")
        if resp.status_code != 200:
            raise UpstreamError(f"HTTP {resp.status_code} on {BOOT_PATH}")
        scope = universal_data(resp.text)
        if scope is None:      # ~1 page in 25 arrives without its data; a new session fixes it
            raise Blocked(f"{BOOT_PATH} came back without page data")
        wid = (scope.get("webapp.app-context") or {}).get("wid")
        if wid:
            self.device_id = str(wid)
        self.booted = True

    def base_params(self):
        """The web app's fixed query params, in the app's own order."""
        return {
            "WebIdLastTime": str(int(self.born)),
            "aid": "1988",
            "app_language": "en",
            "app_name": "tiktok_web",
            "browser_language": "en-US",
            "browser_name": "Mozilla",
            "browser_online": "true",
            "browser_platform": "MacIntel",
            "browser_version": USER_AGENT[8:],
            "channel": "tiktok_web",
            "cookie_enabled": "true",
            "data_collection_enabled": "false",
            "device_id": self.device_id,
            "device_platform": "web_pc",
            "focus_state": "true",
            "from_page": "user",
            "history_len": "3",
            "is_fullscreen": "false",
            "is_page_visible": "true",
            "language": "en",
            "os": "mac",
            "priority_region": "",
            "referer": "",
            "region": self.country.upper(),
            "screen_height": "1080",
            "screen_width": "1920",
            "tz_name": "America/New_York",
            "user_is_login": "false",
            "webcast_language": "en",
        }

    def get_json(self, path, params, referer):
        """One signed GET -> parsed JSON dict."""
        query = self.base_params()
        query.update({key: str(value) for key, value in params.items() if value is not None})
        ms_token = self.session.cookies.get("msToken") or ""
        query_string, _ = signer.sign(list(query.items()), USER_AGENT, ms_token=ms_token)
        try:
            resp = self.session.get(f"{WWW}{path}?{query_string}",
                                    headers={"accept": "*/*", "referer": referer}, timeout=API_TIMEOUT)
        except Exception as e:
            raise UpstreamError(f"request failed: {type(e).__name__}: {e}")
        status, text = resp.status_code, resp.text or ""
        if status in (401, 403, 429):
            raise Blocked(f"HTTP {status} on {path}")
        if status >= 500:
            raise UpstreamError(f"HTTP {status} on {path}")
        if status == 400:
            raise BadRequest(f"tiktok rejected {path}")
        if not text.strip():
            raise Blocked(f"{path} answered with an empty body")
        try:
            payload = json.loads(text)
        except ValueError:
            raise Blocked(f"{path} returned a non-JSON body")
        if not isinstance(payload, dict):
            raise UpstreamError(f"{path} returned unexpected JSON")
        if status_of(payload) == LOGIN_WALL_STATUS:
            raise Blocked(f"{path} asked for a login")
        return payload


class TikTok:
    """Keeps one booted session alive across calls, so paging a search reuses
    the same cookies, and swaps it for a fresh one when it stops answering."""

    def __init__(self):
        self._session = None

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    def _session_for(self, country):
        current = self._session
        if current and current.country == country and time.time() - current.born < SESSION_MAX_AGE:
            return current
        self.close()
        return _Session(country)

    def search_get(self, path, params, countries, referer=WWW + "/search"):
        """A booted + signed call, trying each country's exits in order.

        بلا proxy: بعد كل جلسة فاشلة تُهدا العملية مدةً متصاعدة (0.5s ثم 1s
        ثم 2s) قبل بدء جلسة جديدة نظيفة — إعادة الضرب فورًا وبسرعة من نفس
        الـ IP تزيد الحجب ولا تحلّه، فالتهدئة جزء من الحل.
        """
        last = None
        tried = 0                                       # عدد الجلسات المُجرّبة في هذا الطلب
        budget = SESSIONS_PER_COUNTRY * len(countries)
        for country in countries:
            for _ in range(SESSIONS_PER_COUNTRY):
                if tried:                               # تهدئة تصاعدية بين الجلسات
                    delay = RETRY_BACKOFF[min(tried, len(RETRY_BACKOFF)) - 1]
                    time.sleep(delay * (0.75 + 0.5 * random.random()))
                tried += 1
                session = self._session_for(country)
                self._session = session
                # a session often drops its FIRST signed call, so give it one retry
                tries = 1 if session.answered else 2
                try:
                    if not session.booted:
                        session.boot()
                    for t in range(tries):
                        try:
                            payload = session.get_json(path, params, referer)
                            break
                        except Blocked as e:
                            last = e
                            log.warning("blocked attempt %d/%d on region %s: %s", tried, budget, country, e)
                            if t + 1 == tries:
                                raise
                except BadRequest:
                    raise
                except UpstreamError as e:
                    last = e
                    log.warning("session %d/%d on region %s failed: %s", tried, budget, country, e)
                    self.close()
                    continue
                session.answered = True
                status = status_of(payload)
                if status != 0:
                    raise UpstreamError(f"search: upstream status {status} {payload.get('status_msg') or ''}".strip())
                return payload
        if isinstance(last, Blocked):
            # السجل الأخير يجب أن يوضح للمشغّل أن السبب عنوان IP الخادم وليس خطأ في الكود
            log.error("all %d session attempts were blocked — احتمال حجب IP مركز البيانات من تيك توك "
                      "(لم يُضبط proxy ولا هو مطلوب هنا): أعد المحاولة لاحقًا أو خفف معدل الاستخدام؛ "
                      "هذه مشكلة شبكة/IP وليست خطأ في الكود", tried)
            raise Blocked(f"{last} — all {tried} session attempts were blocked: "
                          f"احتمال حجب IP مركز البيانات — لم يُضبط proxy؛ أعد المحاولة لاحقًا")
        raise last or UpstreamError("search failed")


def country_order(country, *fallbacks):
    """`country` (default "us") first, then the fallbacks, de-duplicated.

    توسيع اختياري للمخارج الاحتياطية بلا proxy: المتغير TIKTOK_COUNTRIES
    (مثلاً "us,ca,au") يضيف مناطق احتياطية بعد المخارج الصريحة، بشرط أن تكون
    رموز ISO 3166-1 alpha-2 صالحة — رموز مثل "us2" غير مقبولة لأن معامل
    region في تيك توك يقتصر على رمز ISO ثنائي الأحرف. الافتراضي بدون المتغير
    يبقى "us" فقط مع مزيد من الجلسات والتهدئة التصاعدية.
    """
    extra = tuple(c.strip().lower() for c in (os.environ.get("TIKTOK_COUNTRIES") or "").split(",")
                  if c.strip())
    order = []
    for code in (country or "us",) + fallbacks + extra:
        code = (code or "").lower()
        if code and code not in order:
            order.append(code)
    return tuple(order)
