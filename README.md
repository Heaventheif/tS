# TikTok Video Search

Give it keywords, get TikTok video URLs. Nothing else.

## Install (local)
```bash
python -m pip install -r requirements-dev.txt
```

## Command line
```bash
python cli.py "nasa"                                   # 20 URLs, one per line
python cli.py "nasa" -n 100                            # pages automatically (20 per page)
python cli.py "nasa" -n 50 --sort likes --published month
python cli.py "nasa" -n 50 -o urls.txt                 # save to a file
python cli.py "nasa" -n 50 --json -o videos.json       # full records
```

| option | meaning |
|---|---|
| `-n, --limit` | how many videos to collect (default 20) |
| `--sort` | `relevance` (default) or `likes` |
| `--published` | `day`, `week`, `month`, `3months`, `6months` |
| `--country` | 2-letter TikTok edition (default `us`) |
| `--max-pages` | page cap (default 50) |
| `--json` | output full records instead of bare URLs |
| `-o, --output` | write to a file |

## From Python
```python
from tiktok_search import search_videos

result = search_videos("nasa", limit=60, sort="likes", published="month")
print(result["urls"])        # ["https://www.tiktok.com/@user/video/123...", ...]
print(result["videos"][0])   # url, id, description, author, created_at, play/like/comment/share counts
```
If a later page fails, the videos already collected are returned and the reason is in `result["error"]`.

## No repeated links (history database)
Every link that is returned is recorded **per keyword** in a database; the next search for the same
keyword skips anything already delivered and keeps paging until it finds `limit` *new* videos.
Keywords are normalised, so `NASA`, ` nasa ` and `Nasa` share one history; filters (`sort`, `published`,
`country`) do not split it.

```bash
python cli.py "nasa" -n 20            # 20 links
python cli.py "nasa" -n 20            # 20 different links
python cli.py "nasa" --reset          # forget the history of "nasa", then search
python cli.py "nasa" --no-dedupe      # one-off: neither skip nor record
python cli.py "nasa" --db my.db       # another SQLite file (default ./seen_videos.db or $TIKTOK_DB)
```
From Python: `search_videos("nasa", 20, store=open_store())` (`from tiktok_search.store import open_store`).
The result gains `skipped_seen` (already-delivered videos skipped) and `exhausted` (true when TikTok has no
more unseen videos for the keyword at the moment — use other filters or `reset`).

Only the videos actually returned are recorded; extra videos read from a page stay available for next time.
Recording is atomic (`INSERT … ON CONFLICT DO NOTHING`), so two simultaneous requests can never get the same link.

**Storage backends**
| where | what to set |
|---|---|
| local / CLI | nothing — SQLite file `seen_videos.db` |
| Vercel | `DATABASE_URL` = a Postgres connection string (e.g. free Neon: Vercel → Storage → Neon). The table is created automatically. |

Vercel's disk is ephemeral, so SQLite cannot be used there. Without `DATABASE_URL` the API still works but
adds a `warning` and cannot deduplicate. If a database is configured but unreachable the API answers 503
instead of silently returning repeats.

## Proxy (optional)
```bash
export TIKTOK_PROXY=http://user:pass@host:port
# if your provider picks the country in the URL, put {country} where the code goes:
export TIKTOK_PROXY=http://user-cr.{country}:pass@host:port
```
Video search answers only from US exits. If TikTok is blocked where you run, use a proxy.

## Deploy as an API on Vercel
```bash
npm i -g vercel
vercel            # first deploy (preview)
vercel --prod
```
Environment variables (Project → Settings → Environment Variables):
- `TIKTOK_PROXY` — recommended. TikTok often refuses datacenter IPs such as Vercel's; a residential US proxy fixes it.
- `DATABASE_URL` — **required for no-repeat links** (Postgres, see above).
- `API_KEY` — optional. When set, send it as the `x-api-key` header or `?key=`.

```
GET /api/search?q=nasa                          first 20 videos
GET /api/search?q=nasa&limit=60                 up to 60 (3 pages, server-side)
GET /api/search?q=nasa&sort=likes&published=month&country=us
GET /api/search?q=nasa&cursor=<next_cursor>     continue
GET /api/search?q=nasa&format=urls              bare URLs, one per line
GET /api/search?q=nasa&dedupe=0                 ignore the history for this call (and keep edge caching)
GET /api/search?q=nasa&reset=1&key=...          forget the keyword's history first (requires API_KEY)
```
Response: `{query, video_count, videos[], urls[], next_cursor, has_more, error, deduplicated, skipped_seen, exhausted}`.
Errors: 400 bad params, 401 bad key, 502 TikTok blocked / failed. With the history enabled responses are `Cache-Control: no-store` (a cached reply would repeat the same links); with `dedupe=0` successful answers are cached at the edge for 10 minutes. `maxDuration` is 60 s (`vercel.json`); on the Hobby plan lower it to what your plan allows.

## Tests
```bash
python -m pytest -q     # offline, no network
```

## Layout
```
cli.py                   command line
api/search.py            Vercel endpoint (/api/search)
vercel.json              function timeout + /search rewrite
tiktok_search/search.py  search + automatic paging + parsing + skipping seen links
tiktok_search/store.py   history database (SQLite / Postgres), per-keyword
tiktok_search/client.py  session boot, signing, retries
tiktok_search/signer.py  request signer (unchanged)
tests/                   offline tests + one real captured response
```
