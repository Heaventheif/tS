"""Search TikTok by keyword and print the video URLs.

    python main.py "nasa"                         # 20 URLs
    python main.py "nasa" -n 100                  # 100 URLs (pages automatically)
    python main.py "nasa" -n 50 --sort likes --published month
    python main.py "nasa" -n 50 -o urls.txt       # save one URL per line
    python main.py "nasa" -n 50 --json -o out.json  # full records as JSON

Environment: TIKTOK_PROXY=http://user:pass@host:port (optional).
"""
import argparse
import json
import sys

from tiktok_search import search_videos
from tiktok_search.search import PUBLISHED, SORTS


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
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = search_videos(args.query, args.limit, country=args.country, sort=args.sort,
                               published=args.published, max_pages=args.max_pages)
    except ValueError as e:
        sys.exit(f"error: {e}")
    except Exception as e:
        sys.exit(f"error: {type(e).__name__}: {e}")

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
    if result["error"]:
        print(f"warning: stopped early ({result['error']})", file=sys.stderr)
    if not result["video_count"]:
        sys.exit("no videos found")


if __name__ == "__main__":
    main()
