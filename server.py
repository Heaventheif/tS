"""خادم HTTP مستقل — نقطة الدخول على Render (Long-running web service).

يغلّف نفس دالة run() الموجودة في api/search.py دون أي تغيير في العقد:
  GET   /  /search  /api/search   -> نفس استجابة Vercel تمامًا (run() يتجاهل المسار، المهم الاستعلام)
  GET   /health                   -> فحص صحة خفيف لمراقبة Render (لا يلمس تيك توك ولا الشبكة)
  HEAD  على أي مسار               -> نفس ترويسات GET دون متن (مع Content-Length الحقيقي)
  OPTIONS                         -> CORS مفتوح كما في نشر Vercel

متغيرات البيئة:
  PORT        منصة Render تضبطه تلقائيًا (افتراضي 10000)
  HOST        افتراضي 0.0.0.0
  LOG_LEVEL   DEBUG | INFO | WARNING | ERROR (افتراضي INFO)

السجل يُكتب إلى stdout وRender يقرؤه تلقائيًا. قيمة ?key=... تُخفى من كل
سطور السجل قبل الطباعة حتى لا يتسرب مفتاح API إلى لوحة Render.

ملاحظة تشغيلية: هذا الخادم لا يستخدم ولا يعتمد على أي proxy. عند حجب
تيك توك لعنوان IP مركز البيانات سترى في السجل رسالة واضحة تفرّق بين
مشكلة الشبكة/الـ IP وخطأ الكود، وتعود الاستجابة 502 مفهومة (انظر DEPLOY_RENDER.md).
"""
import logging
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from api.search import run  # noqa: E402  — نفس العقد المستخدم على Vercel: (status, content_type, body, extra_headers)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,                       # Render يجمع stdout في لوحة السجلات تلقائيًا
)
log = logging.getLogger("server")

HEALTH_PATH = "/health"

# ?key=secret -> ?key=*** : لا يظهر مفتاح API في السجل أبدًا
_KEY_RE = re.compile(r"([?&])key=[^&\s]*", re.IGNORECASE)


def sanitize(text):
    """يخفي قيمة ?key= قبل كتابة أي سطر في السجل."""
    return _KEY_RE.sub(r"\1key=***", text)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"            # نرسل Content-Length دائمًا فتبقى الاتصالات حية
    server_version = "tiktok-video-search/1.1"
    timeout = 60                             # اتصال خامل لا يبقى مفتوحًا للأبد

    # ---- التنفيذ: كل المسارات تمر عبر run() وتُعاد استجابتها كما هي ----
    def _respond(self, send_body=True):
        parsed = urlparse(self.path)
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            if parsed.path == HEALTH_PATH:
                # /health رخيص وسريع دائمًا: لا بحث ولا شبكة — مثالي لفحص Render الدوري
                # (run() يرد على الاستعلام الفارغ بحالة ok دون أي اتصال بتيك توك)
                status, ctype, body, extra = run("", headers)
            else:
                status, ctype, body, extra = run(parsed.query, headers)
        except Exception as e:               # شبكة أمان إضافية: run() يلتقط أخطاءه داخليًا أصلًا
            log.exception("request crashed: %s: %s", type(e).__name__, e)
            status, ctype = 500, "application/json; charset=utf-8"
            body = '{"error": "internal server error"}'
            extra = {}
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))   # HEAD يعطي الحجم الحقيقي دون متن
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass                         # العميل أغلق الاتصال — ليس خطأ منا

    def do_GET(self):
        self._respond(send_body=True)

    def do_HEAD(self):
        self._respond(send_body=False)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "x-api-key, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _method_not_allowed(self):
        body = b'{"error": "method not allowed: use GET (or HEAD/OPTIONS)"}'
        self.send_response(405)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed

    # ---- السجل ----
    def log_message(self, fmt, *args):
        """سطر الوصول إلى stdout بعد إخفاء ?key=..."""
        log.info("%s %s", self.address_string(), sanitize(fmt % args))

    def log_error(self, fmt, *args):
        log.warning("%s %s", self.address_string(), sanitize(fmt % args))


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "10000"))
    except ValueError:
        port = 10000

    server = ThreadingHTTPServer((host, port), Handler)   # خيط لكل طلب — طلب بطيء لا يحجب الآخرين
    server.daemon_threads = True                          # اتصال معلّق لا يمنع الإيقاف

    def graceful(signum, _frame):
        # shutdown() يجب أن يُستدعى من thread آخر غير serve_forever وإلا تعطّل معًا
        log.info("received signal %s — shutting down gracefully ...", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, graceful)   # Render يرسل SIGTERM عند كل نشر أو إيقاف
    signal.signal(signal.SIGINT, graceful)    # Ctrl+C محليًا

    log.info("listening on http://%s:%s (health: %s) — LOG_LEVEL=%s", host, port, HEALTH_PATH, LOG_LEVEL)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        log.info("server closed")


if __name__ == "__main__":
    main()
