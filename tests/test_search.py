"""Offline tests — no network.   python -m pytest -q"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tiktok_search import client as C, search as S, signer  # noqa: E402
from tiktok_search.errors import Blocked  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def payload():
    with open(os.path.join(FIX, "search_item.json"), encoding="utf-8") as f:
        return json.load(f)


class FakeClient:
    """Serves canned pages; page i has ids offset by i so they stay unique."""

    def __init__(self, pages, fail_on=None):
        self.pages, self.calls, self.fail_on = pages, [], fail_on

    def search_get(self, path, params, countries, referer=None):
        self.calls.append((params, countries))
        i = len(self.calls) - 1
        if self.fail_on is not None and i == self.fail_on:
            raise Blocked("empty")
        return self.pages[i]

    def close(self):
        pass


def make_page(start, n, has_more, cursor):
    items = [{"id": str(1000 + start + k), "desc": f"v{start + k}", "author": {"uniqueId": "u"},
              "stats": {"playCount": 5}, "createTime": 1775837312} for k in range(n)]
    return {"status_code": 0, "item_list": items, "has_more": 1 if has_more else 0, "cursor": cursor,
            "log_pb": {"impr_id": "IMPR12345"}}


def test_parse_video():
    video = S.parse_video(payload()["item_list"][0])
    assert video["url"] == "https://www.tiktok.com/@creepyexplore/video/7627163149396905238"
    assert video["author"] == "creepyexplore" and video["play_count"] == 218900 and video["like_count"] == 1838
    assert video["created_at"].endswith("Z") and "moon" in video["description"].lower()
    assert S.parse_video({}) is None and S.parse_video({"id": ""}) is None


def test_photo_posts_get_a_photo_link():
    assert "/photo/" in S.parse_video({"id": "1", "author": {"uniqueId": "a"}, "imagePost": {"x": 1}})["url"]


def test_cursor_round_trip():
    p = payload()
    p["extra"] = {"logid": "20261005135356D987AA8C89367BE6C0E1"}
    cursor = S._next_cursor(p, "")
    assert cursor == "20.20261005135356D987AA8C89367BE6C0E1"
    assert S._split_cursor(cursor) == (20, "20261005135356D987AA8C89367BE6C0E1")
    assert S._split_cursor(None) == (0, "") and S._split_cursor("40") == (40, "")
    assert S._next_cursor({"has_more": 0, "cursor": 20}, "x") is None
    assert S._next_cursor({"has_more": 1, "cursor": 12, "log_pb": {"impr_id": "IMPR"}}, "") == "12.IMPR"
    assert S._next_cursor({"has_more": 1, "cursor": 40}, "KEEP") == "40.KEEP"
    assert S._next_cursor({"has_more": 1, "cursor": 40}, "") == "40"


def test_paging_collects_up_to_limit_and_echoes_search_id():
    pages = [make_page(0, 20, True, 20), make_page(20, 20, True, 40), make_page(40, 20, False, 60)]
    c = FakeClient(pages)
    r = S.search_videos("nasa", 45, client=c, pause=0)
    assert r["video_count"] == 45 and len(r["urls"]) == 45 and r["error"] is None
    assert len(c.calls) == 3
    assert c.calls[0][0]["offset"] == 0 and "search_id" not in c.calls[0][0]
    assert c.calls[1][0]["offset"] == 20 and c.calls[1][0]["search_id"] == "IMPR12345"


def test_stops_when_no_more_results_and_dedupes():
    p1 = make_page(0, 20, True, 20)
    p2 = make_page(10, 20, False, 40)      # overlaps p1 on 10 ids
    r = S.search_videos("nasa", 100, client=FakeClient([p1, p2]), pause=0)
    assert r["video_count"] == 30 and len({v["id"] for v in r["videos"]}) == 30


def test_later_page_failure_keeps_what_was_collected():
    c = FakeClient([make_page(0, 20, True, 20), None], fail_on=1)
    r = S.search_videos("nasa", 60, client=c, pause=0)
    assert r["video_count"] == 20 and "Blocked" in r["error"]


def test_first_page_failure_raises():
    with pytest.raises(Blocked):
        S.search_videos("nasa", 10, client=FakeClient([None], fail_on=0), pause=0)


def test_filters_are_sent():
    c = FakeClient([make_page(0, 3, False, 3)])
    S.search_videos("nasa", 3, client=c, sort="likes", published="month", pause=0)
    params = c.calls[0][0]
    assert (params["is_filter_search"], params["sort_type"], params["publish_time"]) == (1, 1, 30)
    c = FakeClient([make_page(0, 3, False, 3)])
    S.search_videos("nasa", 3, client=c, pause=0)
    assert "is_filter_search" not in c.calls[0][0]


@pytest.mark.parametrize("kwargs", [{"query": ""}, {"query": "  "}, {"query": "x" * 201},
                                    {"query": "a", "sort": "bad"}, {"query": "a", "published": "year"}])
def test_bad_args_rejected(kwargs):
    with pytest.raises(ValueError):
        S.search_videos(client=FakeClient([]), **kwargs)
    with pytest.raises(ValueError):
        S.search_videos("a", 0, client=FakeClient([]))


def test_signer_appends_the_four_params_in_order():
    query, params = signer.sign([("aid", "1988"), ("keyword", "a b")], C.USER_AGENT, ms_token="")
    assert query.startswith("aid=1988&keyword=a%20b&X-Dynosaur=")
    assert list(params) == ["X-Dynosaur", "msToken", "X-Bogus", "X-Gnarly"]


def test_status_country_order_and_universal_data():
    assert C.status_of({"status_code": 0}) == 0 and C.status_of({"statusCode": 2483}) == 2483
    assert C.country_order(None, "de") == ("us", "de") and C.country_order("DE", "de", "us") == ("de", "us")
    html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{"__DEFAULT_SCOPE__":{"a":1}}</script>'
    assert C.universal_data(html) == {"a": 1} and C.universal_data("<html></html>") is None


# ---- Vercel API -------------------------------------------------------------------------

def test_api_handler(monkeypatch):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from api import search as A
    monkeypatch.delenv("API_KEY", raising=False)
    calls = []

    def fake(query, limit, **kw):
        calls.append((query, limit, kw))
        return {"query": query, "video_count": 1, "videos": [{"id": "1"}], "urls": ["https://x/1"],
                "next_cursor": None, "has_more": False, "error": None}

    monkeypatch.setattr(A, "search_videos", fake)
    status, ctype, body, extra = A.run("q=nasa&limit=40&sort=likes&published=month&country=DE", {})
    assert status == 200 and json.loads(body)["urls"] == ["https://x/1"] and "s-maxage" in extra["Cache-Control"]
    assert calls[0][:2] == ("nasa", 40) and calls[0][2]["sort"] == "likes" and calls[0][2]["country"] == "de"
    assert A.run("q=nasa&format=urls", {})[2] == "https://x/1"
    assert A.run("", {})[0] == 200 and "usage" in A.run("", {})[2] and len(calls) == 2
    for bad in ("q=a&limit=0", "q=a&limit=61", "q=a&limit=x", "q=a&format=xml", "q=a&country=usa"):
        assert A.run(bad, {})[0] == 400
    monkeypatch.setenv("API_KEY", "s3cret")
    assert A.run("q=a", {})[0] == 401 and A.run("q=a&key=nope", {})[0] == 401
    assert A.run("q=a&key=s3cret", {})[0] == 200 and A.run("q=a", {"x-api-key": "s3cret"})[0] == 200

    def boom(*a, **k):
        raise Blocked("empty")
    monkeypatch.setattr(A, "search_videos", boom)
    assert A.run("q=a&key=s3cret", {})[0] == 502

    def bad(*a, **k):
        raise ValueError("query must not be empty")
    monkeypatch.setattr(A, "search_videos", bad)
    assert A.run("q=&key=s3cret", {})[0] == 400


def test_bad_cursor_rejected():
    with pytest.raises(ValueError):
        S._split_cursor("not-a-cursor")
