"""Vercel serverless function:  GET /api/search   (also served at /search)

    /api/search?q=nasa                         first 20 videos
    /api/search?q=nasa&limit=60                up to 60 (pages server-side)
    /api/search?q=nasa&sort=likes&published=month
    /api/search?q=nasa&cursor=<next_cursor>    next page(s)
    /api/search?q=nasa&format=urls             bare URLs, one per line

Params: q (required), limit (1-60, default 20), sort, published, country,
cursor, format (json | urls).

Optional protection: set API_KEY in the Vercel project and send it as the
`x-api-key` header or `?key=`.
Optional proxy: set TIKTOK_PROXY (TikTok often refuses datacenter IPs).
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tiktok_search import search_videos  # noqa: E402
from tiktok_search.errors import BadRequest, Blocked, UpstreamError  # noqa: E402

MAX_LIMIT = 60          # 3 pages: keeps a request well inside the function timeout
DEFAULT_LIMIT = 20
PAGE_PAUSE = 0.3


def run(query_string, headers):
    """-> (status, content_type, body_text, extra_headers). Pure function, easy to test."""
    qs = {k: v[0] for k, v in parse_qs(query_string, keep_blank_values=True).items()}

    def reply(status, data):
        return status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False), {}

    if "q" not in qs and not qs.get("cursor"):       # bare / or /api/search: usage + health
        return reply(200, {"status": "ok", "usage": "/api/search?q=nasa&limit=20&sort=likes&published=month"})

    key = os.environ.get("API_KEY")
    if key:
        given = headers.get("x-api-key") or qs.get("key") or ""
        if not hmac.compare_digest(given.encode(), key.encode()):
            return reply(401, {"error": "invalid or missing API key"})

    try:
        limit = int(qs.get("limit") or DEFAULT_LIMIT)
    except ValueError:
        return reply(400, {"error": "limit must be a number"})
    if not 1 <= limit <= MAX_LIMIT:
        return reply(400, {"error": f"limit must be between 1 and {MAX_LIMIT}"})
    fmt = qs.get("format", "json")
    if fmt not in ("json", "urls"):
        return reply(400, {"error": "format must be json or urls"})
    country = (qs.get("country") or "").strip().lower() or None
    if country and not (len(country) == 2 and country.isalpha()):
        return reply(400, {"error": "country must be a 2-letter code"})

    try:
        result = search_videos(qs.get("q", ""), limit, country=country, sort=qs.get("sort") or "relevance",
                               published=qs.get("published") or None, cursor=qs.get("cursor") or None,
                               pause=PAGE_PAUSE, max_pages=3)
    except (ValueError, BadRequest) as e:
        return reply(400, {"error": str(e)})
    except Blocked as e:
        return reply(502, {"error": f"tiktok blocked the request, retry later or set TIKTOK_PROXY: {e}"})
    except UpstreamError as e:
        return reply(502, {"error": f"tiktok search failed: {e}"})
    except Exception as e:
        return reply(500, {"error": f"{type(e).__name__}: {e}"})

    cache = {"Cache-Control": "public, s-maxage=600, stale-while-revalidate=60"} if not result["error"] else {}
    if fmt == "urls":
        return 200, "text/plain; charset=utf-8", "\n".join(result["urls"]), cache
    return 200, "application/json; charset=utf-8", json.dumps(result, ensure_ascii=False), cache


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, ctype, body, extra = run(urlparse(self.path).query, {k.lower(): v for k, v in self.headers.items()})
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "x-api-key")
        self.end_headers()

    def log_message(self, *args):
        pass
