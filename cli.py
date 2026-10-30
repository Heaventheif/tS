"""Search TikTok by keyword and print the video URLs.

    python cli.py "nasa"                         # 20 URLs
    python cli.py "nasa" -n 100                  # 100 URLs (pages automatically)
    python cli.py "nasa" -n 50 --sort likes --published month
    python cli.py "nasa" -n 50 -o urls.txt       # save one URL per line
    python cli.py "nasa" -n 50 --json -o out.json  # full records as JSON
    python cli.py "nasa" -n 20                   # again -> 20 NEW links, none repeated
    python cli.py "nasa" --reset                 # forget what was delivered for "nasa", then search
    python cli.py "nasa" --no-dedupe             # ignore and don't touch the history

Delivered links are remembered per keyword in seen_videos.db (or --db / $TIKTOK_DB,
or Postgres via $DATABASE_URL) and never returned again.
Environment: $TIKTOK_DB (SQLite file) or $DATABASE_URL (Postgres) for the history.
"""
import argparse
import json
import sys

from tiktok_search import search_videos
from tiktok_search.search import PUBLISHED, SORTS
from tiktok_search.store import open_store


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Search TikTok by keyword and get video URLs.")
    p.add_argument("query", help="the search keywords")
    p.add_argument("-n", "--limit", type=int, default=20, help="how many videos to collect (default 20)")
    p.add_argument("--sort", choices=list(SORTS), default="relevance")
    p.add_argument("--published", choices=list(PUBLISHED), help="only videos published within this window")
    p.add_argument("--country", help="2-letter TikTok edition to ask (default us)")
    p.add_argument("--max-pages", type=int, default=50, help="page cap, 20 videos per page (default 50)")
    p.add_argument("--json", action="store_true", help="output full records as JSON instead of bare URLs")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    p.add_argument("--db", help="SQLite file remembering delivered links (default seen_videos.db or $TIKTOK_DB)")
    p.add_argument("--no-dedupe", action="store_true", help="don't skip or record links in the history")
    p.add_argument("--reset", action="store_true", help="clear the history of this keyword before searching")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    store = None
    try:
        if not args.no_dedupe:
            store = open_store(args.db)
            if store is not None and args.reset:
                print(f"history cleared: {store.forget(args.query)} links forgotten", file=sys.stderr)
        result = search_videos(args.query, args.limit, country=args.country, sort=args.sort,
                               published=args.published, max_pages=args.max_pages, store=store)
    except ValueError as e:
        sys.exit(f"error: {e}")
    except Exception as e:
        sys.exit(f"error: {type(e).__name__}: {e}")
    finally:
        if store is not None:
            store.close()

    if args.json:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        text = "\n".join(result["urls"])
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"saved {result['video_count']} videos to {args.output}", file=sys.stderr)
    elif text:
        print(text)
    if store is not None:
        print(f"{result['video_count']} new links, {result['skipped_seen']} already-seen skipped", file=sys.stderr)
        if result["exhausted"]:
            print("note: no more unseen results for this keyword right now (try --reset or other filters)",
                  file=sys.stderr)
    if result["error"]:
        print(f"warning: stopped early ({result['error']})", file=sys.stderr)
    if not result["video_count"]:
        sys.exit("no videos found")


if __name__ == "__main__":
    main()
