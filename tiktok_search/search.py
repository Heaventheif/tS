"""Keyword -> TikTok video URLs.

Endpoint: /api/search/item/full — 20 videos a page. Pages are addressed by
an offset, and every page after the first must carry the first page's search
id; both travel in one opaque cursor "<offset>.<search id>".

Filters are the site's own: sort (relevance | likes) and published
(day | week | month | 3months | 6months).
"""
import re
import time
from datetime import datetime, timezone

from .client import TikTok, country_order
from .errors import BadRequest

SORTS = {"relevance": 0, "likes": 1}
PUBLISHED = {"day": 1, "week": 7, "month": 30, "3months": 90, "6months": 180}
PAGE_SIZE = 20
MAX_QUERY_LENGTH = 200
PAGE_PAUSE = 1.0           # seconds between pages, to stay polite


def video_url(username, video_id, is_photo=False):
    """Canonical link. TikTok redirects /@/video/<id> to the author's own."""
    return f"https://www.tiktok.com/@{username or ''}/{'photo' if is_photo else 'video'}/{video_id}"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_video(item):
    """One itemStruct from the search answer -> a flat record, or None."""
    if not isinstance(item, dict) or not item.get("id"):
        return None
    video_id = str(item["id"])
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    username = author.get("uniqueId") or author.get("unique_id") or None
    exact = item.get("statsV2") if isinstance(item.get("statsV2"), dict) else {}
    rough = item.get("stats") if isinstance(item.get("stats"), dict) else {}

    def count(key):
        value = _int(exact.get(key))
        return value if value is not None else _int(rough.get(key))

    created = _int(item.get("createTime"))
    is_photo = bool(item.get("imagePost"))
    return {
        "url": video_url(username, video_id, is_photo),
        "id": video_id,
        "description": (item.get("desc") or "").strip() or None,
        "author": username,
        "created_at": datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if created else None,
        "play_count": count("playCount"),
        "like_count": count("diggCount"),
        "comment_count": count("commentCount"),
        "share_count": count("shareCount"),
    }


_CURSOR_RE = re.compile(r"^\d{1,5}(?:\.[0-9A-Za-z]{8,64})?$")


def _split_cursor(cursor):
    """"40.20261005ABC" -> (40, "20261005ABC"); None -> (0, "")."""
    if not cursor:
        return 0, ""
    if not _CURSOR_RE.match(str(cursor)):
        raise ValueError("cursor must be the next_cursor value of the previous page, unchanged")
    offset, _, search_id = str(cursor).partition(".")
    return int(offset), search_id


def _search_id(payload):
    for value in ((payload.get("log_pb") or {}).get("impr_id"), (payload.get("extra") or {}).get("logid")):
        if isinstance(value, str) and value:
            return value
    return ""


def _next_cursor(payload, search_id):
    if not payload.get("has_more"):
        return None
    offset = payload.get("cursor")
    if offset in (None, ""):
        return None
    search_id = search_id or _search_id(payload)
    return f"{offset}.{search_id}" if search_id else str(offset)


def check_args(query, sort, published):
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query is longer than {MAX_QUERY_LENGTH} characters")
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    if published is not None and published not in PUBLISHED:
        raise ValueError(f"published must be one of {', '.join(PUBLISHED)}")
    return query


def search_page(query, *, client=None, country=None, sort="relevance", published=None, cursor=None):
    """One page (up to 20 videos) -> {videos, next_cursor, has_more}."""
    query = check_args(query, sort, published)
    own = client is None
    client = client or TikTok()
    offset, search_id = _split_cursor(cursor)
    params = {"keyword": query, "offset": offset, "from_page": "search", "count": PAGE_SIZE}
    if search_id:
        params["search_id"] = search_id
    if sort != "relevance" or published:
        params.update({"is_filter_search": 1, "sort_type": SORTS[sort], "publish_time": PUBLISHED.get(published, 0)})
    try:
        payload = client.search_get("/api/search/item/full/", params, country_order(country, "us"))
    finally:
        if own:
            client.close()
    videos = [v for v in (parse_video(row) for row in payload.get("item_list") or []) if v]
    next_cursor = _next_cursor(payload, search_id)
    return {"videos": videos, "next_cursor": next_cursor, "has_more": next_cursor is not None}


def search_videos(query, limit=20, *, country=None, sort="relevance", published=None, max_pages=50,
                  pause=PAGE_PAUSE, client=None, cursor=None, store=None):
    """Up to `limit` unique videos for the keyword, paging automatically.

    With a `store` (see store.py) only videos never delivered before for this
    keyword are returned, and the delivered ones are recorded, so repeating the
    same search yields fresh links each time. Pages keep being read until `limit`
    new videos are found, TikTok runs out, or `max_pages` is hit.

    Stops at `limit`, when TikTok has no more results, or after `max_pages`.
    If a later page fails, the videos already collected are returned along
    with the error under "error" instead of being thrown away. `next_cursor`
    continues the search later (pass it back as `cursor`); a page may overshoot
    `limit` and the extra videos are dropped, so continue from the cursor only
    with limits that are multiples of 20.
    """
    query = check_args(query, sort, published)
    if limit < 1:
        raise ValueError("limit must be at least 1")
    own = client is None
    client = client or TikTok()
    found, seen, error, pages, skipped = [], set(), None, 0, 0
    try:
        while len(found) < limit and pages < max_pages:
            if pages and pause:
                time.sleep(pause)
            try:
                page = search_page(query, client=client, country=country, sort=sort, published=published, cursor=cursor)
            except BadRequest:
                raise
            except Exception as e:
                if not found:
                    raise
                error = f"{type(e).__name__}: {e}"
                break
            pages += 1
            known = store.known_ids(query, [v["id"] for v in page["videos"]]) if store else set()
            for video in page["videos"]:
                if video["id"] in seen:
                    continue
                seen.add(video["id"])
                if video["id"] in known:
                    skipped += 1
                    continue
                found.append(video)
            cursor = page["next_cursor"]
            if cursor is None:
                break
    finally:
        if own:
            client.close()
    found = found[:limit]
    if store and found:
        found = store.claim(query, found)       # only what is recorded is returned; overshoot stays unclaimed
    return {"query": query, "sort": sort, "published": published, "video_count": len(found), "videos": found,
            "urls": [v["url"] for v in found], "next_cursor": cursor, "has_more": cursor is not None,
            "error": error, "deduplicated": store is not None, "skipped_seen": skipped,
            "exhausted": store is not None and cursor is None and error is None and len(found) < limit}
