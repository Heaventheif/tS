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
- `API_KEY` — optional. When set, send it as the `x-api-key` header or `?key=`.

```
GET /api/search?q=nasa                          first 20 videos
GET /api/search?q=nasa&limit=60                 up to 60 (3 pages, server-side)
GET /api/search?q=nasa&sort=likes&published=month&country=us
GET /api/search?q=nasa&cursor=<next_cursor>     continue
GET /api/search?q=nasa&format=urls              bare URLs, one per line
```
Response: `{query, video_count, videos[], urls[], next_cursor, has_more, error}`.
Errors: 400 bad params, 401 bad key, 502 TikTok blocked / failed. Successful answers are cached at the edge for 10 minutes. `maxDuration` is 60 s (`vercel.json`); on the Hobby plan lower it to what your plan allows.

## Tests
```bash
python -m pytest -q     # offline, no network
```

## Layout
```
cli.py                   command line
api/search.py            Vercel endpoint (/api/search)
vercel.json              function timeout + /search rewrite
tiktok_search/search.py  search + automatic paging + parsing
tiktok_search/client.py  session boot, signing, retries
tiktok_search/signer.py  request signer (unchanged)
tests/                   offline tests + one real captured response
```
