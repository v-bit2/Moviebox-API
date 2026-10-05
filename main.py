import os
import re
import json
import time
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="ViralBit Movie Apis",
    description="Full Pure REST API for moviebox.ph — Zero Scraping",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://moviebox.ph"
API_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"

_bearer_token: str | None = None
_domain_cache: dict = {"value": None, "expires": 0.0}
DOMAIN_TTL = 300  # seconds

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Referer": "https://moviebox.ph/",
    "Origin": "https://moviebox.ph",
    "X-Client-Info": '{"timezone":"Asia/Dhaka"}',
    "X-Request-Lang": "en",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}

PLAYER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "X-Client-Info": '{"timezone":"Asia/Dhaka"}',
    "X-Source": "",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

async def _get_bearer_token() -> str:
    """Auto-acquire a guest JWT from the x-user response header."""
    global _bearer_token
    if _bearer_token:
        return _bearer_token
    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        resp = await client.get(f"{API_BASE}/home?host=moviebox.ph", headers=DEFAULT_HEADERS)
        x_user = resp.headers.get("x-user")
        if x_user:
            try:
                _bearer_token = json.loads(x_user).get("token")
            except json.JSONDecodeError:
                _bearer_token = None
        if not _bearer_token:
            cookie = resp.headers.get("set-cookie", "")
            m = re.search(r"token=([^;]+)", cookie)
            if m:
                _bearer_token = m.group(1)
    return _bearer_token or ""

async def _make_request(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    custom_headers: dict | None = None,
) -> dict:
    global _bearer_token
    token = await _get_bearer_token()
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {token}" if token else "",
        **(custom_headers or {}),
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        try:
            if method == "POST":
                resp = await client.post(url, headers=headers, json=payload)
            else:
                resp = await client.get(url, headers=headers)

            x_user = resp.headers.get("x-user")
            if x_user:
                try:
                    new_token = json.loads(x_user).get("token")
                    if new_token:
                        _bearer_token = new_token
                except json.JSONDecodeError:
                    pass

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Upstream API error: {resp.status_code}",
                )
            return resp.json()
        except HTTPException:
            raise
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Upstream request timed out")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Request failed: {e}")

async def _get_player_domain() -> str:
    now = time.time()
    if _domain_cache["value"] and now < _domain_cache["expires"]:
        return _domain_cache["value"]
    data = await _make_request(f"{API_BASE}/media-player/get-domain")
    domain = (data.get("data") or "https://netfilm.world").rstrip("/")
    _domain_cache["value"] = domain
    _domain_cache["expires"] = now + DOMAIN_TTL
    return domain

def _build_player_referer(domain: str, detail_path: str, subject_id: str, se: int, ep: int) -> str:
    return (
        f"{domain}/spa/videoPlayPage/movies/{detail_path}"
        f"?id={subject_id}&type=/movie/detail&detailSe={se}&detailEp={ep}&lang=en"
    )

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)

# ─── DISCOVERY ────────────────────────────────────────────────────────────────

@app.get("/home")
async def get_home():
    url = f"{API_BASE}/home?host=moviebox.ph"
    data = await _make_request(url)
    sections = []
    for op in data.get("data", {}).get("operatingList", []) or []:
        op_type = op.get("type")
        title = op.get("title", "Featured")
        if op_type == "BANNER":
            items = [
                {
                    "name": item.get("title") or (item.get("subject") or {}).get("title"),
                    "poster_url": (item.get("image") or {}).get("url")
                        or ((item.get("subject") or {}).get("cover") or {}).get("url"),
                    "slug": item.get("detailPath") or (item.get("subject") or {}).get("detailPath"),
                    "subject_id": (item.get("subject") or {}).get("subjectId"),
                    "badge": (item.get("subject") or {}).get("corner"),
                }
                for item in op.get("banner", {}).get("items", [])
                if item.get("title") and "Communities" not in (item.get("title") or "")
            ]
            sections.append({"section": "Banner", "count": len(items), "items": items})
        elif op_type in ("SUBJECTS_MOVIE", "SUBJECTS_TV", "SUBJECTS_ANIMATION"):
            items = [
                {
                    "name": sub.get("title"),
                    "poster_url": (sub.get("cover") or {}).get("url"),
                    "slug": sub.get("detailPath"),
                    "subject_id": sub.get("subjectId"),
                    "badge": sub.get("corner"),
                    "rating": sub.get("imdbRatingValue"),
                }
                for sub in op.get("subjects", [])
            ]
            sections.append({"section": title, "count": len(items), "items": items})
    return {"status": "success", "sections": sections}

async def _get_category_data(
    tab_id: int,
    page: int = 1,
    per_page: int = 24,
    sort: str = "RECOMMEND",
) -> dict:
    url = f"{API_BASE}/subject/filter"
    payload = {
        "tabId": tab_id,
        "filter": {
            "sort": sort,
            "genre": "ALL",
            "country": "ALL",
            "year": "ALL",
            "language": "ALL",
        },
        "page": page,
        "perPage": per_page,
    }
    data = await _make_request(url, method="POST", payload=payload)
    inner = data.get("data", {}) or {}
    raw_items = inner.get("items") or inner.get("subjects") or []
    items = [
        {
            "name": sub.get("title"),
            "poster_url": (sub.get("cover") or {}).get("url"),
            "slug": sub.get("detailPath"),
            "subject_id": sub.get("subjectId"),
            "badge": sub.get("corner"),
            "rating": sub.get("imdbRatingValue"),
            "year": (sub.get("releaseDate") or "")[:4] or None,
        }
        for sub in raw_items
    ]
    pager = inner.get("pager", {}) or {}
    total = pager.get("totalCount") or inner.get("total") or len(items)
    return {"page": page, "per_page": per_page, "total": total, "items": items}

@app.get("/movies")
async def get_movies(page: int = 1, sort: str = "RECOMMEND"):
    return await _get_category_data(tab_id=2, page=page, sort=sort)

@app.get("/tv-series")
async def get_tv_series(page: int = 1, sort: str = "RECOMMEND"):
    return await _get_category_data(tab_id=5, page=page, sort=sort)

@app.get("/animation")
async def get_animation(page: int = 1, sort: str = "RECOMMEND"):
    return await _get_category_data(tab_id=8, page=page, sort=sort)

# ─── SEARCH ───────────────────────────────────────────────────────────────────

@app.get("/search/suggest")
async def get_search_suggestions(q: str = Query(..., min_length=1)):
    url = f"{API_BASE}/subject/search-suggest"
    data = await _make_request(url, method="POST", payload={"keyword": q, "perPage": 10})
    inner = data.get("data", {}) or {}
    raw = inner.get("items") or inner.get("list") or []
    suggestions = []
    for item in raw:
        sub = item.get("subject") if isinstance(item.get("subject"), dict) else {}
        suggestions.append({
            "title": sub.get("title") or item.get("word") or item.get("title"),
            "slug": sub.get("detailPath") or item.get("detailPath"),
            "subject_id": sub.get("subjectId") or item.get("subjectId"),
        })
    return {"suggestions": suggestions}

@app.get("/search")
async def search(q: str = Query(..., min_length=1), page: int = 1):
    url = f"{API_BASE}/subject/search"
    data = await _make_request(url, method="POST", payload={"keyword": q, "page": page, "perPage": 20})
    inner = data.get("data", {}) or {}
    raw = inner.get("items") or inner.get("list") or []
    items = []
    for item in raw:
        sub = item.get("subject") if isinstance(item.get("subject"), dict) else item
        cover = sub.get("cover") or item.get("cover") or {}
        items.append({
            "name": sub.get("title") or item.get("title"),
            "poster_url": cover.get("url") if isinstance(cover, dict) else None,
            "slug": sub.get("detailPath") or item.get("detailPath"),
            "subject_id": sub.get("subjectId") or item.get("subjectId"),
            "badge": sub.get("corner") or item.get("corner"),
            "rating": sub.get("imdbRatingValue") or item.get("imdbRatingValue"),
            "year": (sub.get("releaseDate") or item.get("releaseDate") or "")[:4] or None,
        })
    pager = inner.get("pager", {}) or {}
    total = pager.get("totalCount") or inner.get("total") or len(items)
    return {"query": q, "page": page, "total": total, "items": items}

# ─── DETAIL ───────────────────────────────────────────────────────────────────

@app.get("/detail/{slug}")
async def get_movie_detail(slug: str):
    url = f"{API_BASE}/detail?detailPath={slug}"
    return await _make_request(url)

# ─── STREAM ───────────────────────────────────────────────────────────────────

@app.get("/api/stream/{subject_id}")
async def get_stream_sources(
    subject_id: str,
    detail_path: str,
    se: int = 1,
    ep: int = 1,
):
    domain = await _get_player_domain()
    referer = _build_player_referer(domain, detail_path, subject_id, se, ep)
    play_url = (
        f"{domain}/wefeed-h5api-bff/subject/play"
        f"?subjectId={subject_id}&se={se}&ep={ep}&detailPath={detail_path}"
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        resp = await client.get(play_url, headers={**PLAYER_HEADERS, "Referer": referer})
        try:
            data = resp.json().get("data", {}) or {}
        except Exception:
            raise HTTPException(status_code=502, detail="Player returned non-JSON")

    has_resource = data.get("hasResource", False)
    streams = [
        {
            "resolution": f"{s.get('resolutions')}p" if s.get("resolutions") else None,
            "format": s.get("format"),
            "url": s.get("url"),
            "size": s.get("size"),
            "duration": s.get("duration"),
            "codec": s.get("codecName"),
        }
        for s in data.get("streams", [])
    ]
    return {
        "subject_id": subject_id,
        "se": se,
        "ep": ep,
        "has_resource": has_resource,
        "sources": streams,
        "hls": data.get("hls", []),
        "dash": data.get("dash", []),
        "free_episodes": data.get("freeNum"),
        "limited": data.get("limited", False),
        "note": None if has_resource else "No stream found for this episode.",
    }

@app.get("/api/stream/{subject_id}/captions")
async def get_captions(
    subject_id: str,
    detail_path: str,
    se: int = 1,
    ep: int = 1,
):
    domain = await _get_player_domain()
    referer = _build_player_referer(domain, detail_path, subject_id, se, ep)
    play_url = (
        f"{domain}/wefeed-h5api-bff/subject/play"
        f"?subjectId={subject_id}&se={se}&ep={ep}&detailPath={detail_path}"
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        play_resp = await client.get(play_url, headers={**PLAYER_HEADERS, "Referer": referer})
        try:
            play_data = play_resp.json().get("data", {}) or {}
        except Exception:
            raise HTTPException(status_code=502, detail="Player returned non-JSON")

    streams = play_data.get("streams") or []
    dash = play_data.get("dash") or []

    stream_id = None
    stream_format = None
    if streams:
        stream_id = streams[0].get("id")
        stream_format = streams[0].get("format", "MP4")
    elif dash:
        stream_id = dash[0].get("id")
        stream_format = dash[0].get("format", "DASH")

    if not stream_id:
        return {"subject_id": subject_id, "se": se, "ep": ep, "count": 0, "captions": []}

    cap_url = (
        f"{API_BASE}/subject/caption"
        f"?format={stream_format}&id={stream_id}"
        f"&subjectId={subject_id}&detailPath={detail_path}"
    )
    data = await _make_request(cap_url)
    inner = data.get("data", {}) or {}
    captions = inner.get("captions", []) if isinstance(inner, dict) else inner
    return {
        "subject_id": subject_id,
        "se": se,
        "ep": ep,
        "count": len(captions),
        "captions": captions,
    }

# ─── HEALTH ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "token_cached": bool(_bearer_token),
        "domain_cached": _domain_cache["value"],
        "domain_expires_in": max(0, int(_domain_cache["expires"] - time.time())),
    }

# ─── DASHBOARD HTML ───────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ViralBit Movie Apis - Interactive Testing Platform</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-dark: #070a12;
  --bg-card: rgba(13, 20, 36, 0.75);
  --bg-input: #0a1122;
  --border-color: rgba(0, 136, 255, 0.2);
  --border-glow: rgba(0, 210, 255, 0.5);
  --primary-blue: #0088ff;
  --accent-cyan: #00d2ff;
  --neon-glow: 0 0 20px rgba(0, 210, 255, 0.35);
  --text-main: #f0f4fc;
  --text-sub: #8ca2c0;
  --code-bg: #050811;
  --success: #00e676;
  --error: #ff3366;
}

* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'Plus Jakarta Sans', sans-serif;
  background-color: var(--bg-dark);
  color: var(--text-main);
  min-height: 100vh;
  background-image:
    radial-gradient(circle at 15% 15%, rgba(0, 136, 255, 0.15) 0%, transparent 40%),
    radial-gradient(circle at 85% 85%, rgba(0, 210, 255, 0.12) 0%, transparent 45%),
    linear-gradient(to bottom, #05070d, #090e1a);
  background-attachment: fixed;
}

.wrapper {
  max-width: 1400px;
  margin: 0 auto;
  padding: 40px 24px;
}

header {
  text-align: center;
  margin-bottom: 40px;
}

.brand-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(0, 136, 255, 0.1);
  border: 1px solid rgba(0, 210, 255, 0.4);
  padding: 6px 18px;
  border-radius: 30px;
  color: var(--accent-cyan);
  font-size: 0.82rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  box-shadow: var(--neon-glow);
  margin-bottom: 16px;
}

.brand-badge .pulse {
  width: 8px;
  height: 8px;
  background: var(--accent-cyan);
  border-radius: 50%;
  box-shadow: 0 0 10px var(--accent-cyan);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { transform: scale(0.95); opacity: 0.7; }
  50% { transform: scale(1.3); opacity: 1; }
  100% { transform: scale(0.95); opacity: 0.7; }
}

h1 {
  font-size: clamp(2.2rem, 5vw, 3.5rem);
  font-weight: 800;
  letter-spacing: -1.5px;
  background: linear-gradient(135deg, #ffffff 0%, #a2c4fc 50%, var(--accent-cyan) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 12px;
}

.subtitle {
  color: var(--text-sub);
  font-size: 1.1rem;
  max-width: 650px;
  margin: 0 auto;
}

/* MAIN LAYOUT GRID */
.app-grid {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 28px;
  align-items: start;
}

@media (max-width: 992px) {
  .app-grid { grid-template-columns: 1fr; }
}

/* SIDEBAR ENDPOINT NAV */
.nav-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 20px;
  padding: 20px;
  backdrop-filter: blur(16px);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.4);
}

.nav-title {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-sub);
  text-transform: uppercase;
  letter-spacing: 1.2px;
  margin-bottom: 16px;
  padding-left: 8px;
}

.endpoint-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ep-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid transparent;
  color: var(--text-main);
  cursor: pointer;
  transition: all 0.25s ease;
  font-weight: 600;
  font-size: 0.92rem;
}

.ep-item:hover {
  background: rgba(0, 136, 255, 0.08);
  border-color: rgba(0, 210, 255, 0.25);
  transform: translateX(3px);
}

.ep-item.active {
  background: linear-gradient(90deg, rgba(0, 136, 255, 0.2) 0%, rgba(0, 210, 255, 0.05) 100%);
  border-color: var(--accent-cyan);
  box-shadow: inset 0 0 15px rgba(0, 210, 255, 0.15);
  color: #fff;
}

.method-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(0, 210, 255, 0.15);
  color: var(--accent-cyan);
  border: 1px solid rgba(0, 210, 255, 0.3);
}

/* TESTING CONSOLE AREA */
.console-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 20px;
  padding: 28px;
  backdrop-filter: blur(16px);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.4);
}

.console-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding-bottom: 20px;
  margin-bottom: 24px;
}

.endpoint-info h2 {
  font-size: 1.4rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
}

.endpoint-info p {
  color: var(--text-sub);
  font-size: 0.9rem;
  margin-top: 4px;
}

.url-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
  padding: 8px 12px;
  border-radius: 12px;
  margin-bottom: 24px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9rem;
}

.url-path {
  color: var(--accent-cyan);
  flex: 1;
  word-break: break-all;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 18px;
  margin-bottom: 24px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-group label {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--text-sub);
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.form-control {
  background: var(--bg-input);
  border: 1px solid rgba(0, 136, 255, 0.25);
  border-radius: 10px;
  padding: 12px 16px;
  color: var(--text-main);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 0.95rem;
  outline: none;
  transition: all 0.25s;
}

.form-control:focus {
  border-color: var(--accent-cyan);
  box-shadow: var(--neon-glow);
}

select.form-control {
  cursor: pointer;
}

.btn-submit {
  background: linear-gradient(135deg, var(--primary-blue) 0%, #00d2ff 100%);
  color: #030814;
  font-weight: 800;
  font-size: 0.98rem;
  padding: 14px 28px;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-shadow: 0 8px 25px rgba(0, 136, 255, 0.35);
}

.btn-submit:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(0, 210, 255, 0.5);
  color: #000;
}

/* RESPONSE BOX */
.response-container {
  margin-top: 28px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 24px;
}

.response-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.status-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(0, 230, 118, 0.15);
  color: var(--success);
  border: 1px solid rgba(0, 230, 118, 0.3);
}

.status-badge.error {
  background: rgba(255, 51, 102, 0.15);
  color: var(--error);
  border-color: rgba(255, 51, 102, 0.3);
}

.response-actions {
  display: flex;
  gap: 10px;
}

.btn-sm {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: var(--text-main);
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-sm:hover {
  background: rgba(0, 210, 255, 0.2);
  border-color: var(--accent-cyan);
}

.code-wrapper {
  position: relative;
  background: var(--code-bg);
  border: 1px solid rgba(0, 136, 255, 0.2);
  border-radius: 12px;
  padding: 18px;
  max-height: 480px;
  overflow-y: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  line-height: 1.5;
  color: #aed0ff;
}

.code-wrapper pre { white-space: pre-wrap; word-break: break-all; }

/* MEDIA PREVIEW PLAYER */
.media-preview {
  margin-top: 20px;
  padding: 16px;
  background: rgba(0, 0, 0, 0.5);
  border: 1px solid var(--border-color);
  border-radius: 14px;
}

.media-preview video {
  width: 100%;
  max-height: 400px;
  border-radius: 10px;
  outline: none;
  background: #000;
}

/* QUICK ITEMS CAROUSEL/GRID */
.quick-items {
  margin-top: 20px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 12px;
  max-height: 320px;
  overflow-y: auto;
  padding-right: 4px;
}

.quick-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 8px;
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}

.quick-card:hover {
  border-color: var(--accent-cyan);
  transform: translateY(-2px);
  background: rgba(0, 136, 255, 0.1);
}

.quick-card img {
  width: 100%;
  aspect-ratio: 2/3;
  object-fit: cover;
  border-radius: 6px;
  margin-bottom: 6px;
  background: #0d1424;
}

.quick-card .title {
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-main);
}

footer {
  text-align: center;
  margin-top: 60px;
  padding-top: 30px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  color: var(--text-sub);
  font-size: 0.85rem;
}

footer span { color: var(--accent-cyan); font-weight: 700; }
</style>
</head>
<body>
<div class="wrapper">
  <header>
    <div class="brand-badge"><span class="pulse"></span> Live API Portal</div>
    <h1>ViralBit Movie Apis</h1>
    <p class="subtitle">Interactive REST API testing console for MovieBox feeds, catalogs, metadata, streams & captions.</p>
  </header>

  <div class="app-grid">
    <!-- LEFT SIDEBAR -->
    <div class="nav-card">
      <div class="nav-title">API Endpoints</div>
      <div class="endpoint-list">
        <div class="ep-item active" onclick="selectEndpoint('home')">
          <span>🏠 Home Feed</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('movies')">
          <span>🎬 Movies Catalog</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('tv')">
          <span>📺 TV Series</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('animation')">
          <span>🐉 Animation</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('search')">
          <span>🔍 Full Search</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('suggest')">
          <span>💡 Autocomplete</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('detail')">
          <span>📄 Metadata Detail</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('stream')">
          <span>⚡ Stream Sources</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('captions')">
          <span>💬 Captions / Subs</span>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('health')">
          <span>❤️ System Health</span>
          <span class="method-tag">GET</span>
        </div>
      </div>
    </div>

    <!-- MAIN CONSOLE -->
    <div class="console-card">
      <div class="console-header">
        <div class="endpoint-info">
          <h2 id="ep-title">🏠 Discover Home Feed</h2>
          <p id="ep-desc">Retrieve real-time banners, top trending blocks, and curated categories.</p>
        </div>
        <button class="btn-submit" onclick="executeApi()">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
          Execute Request
        </button>
      </div>

      <div class="url-bar">
        <span class="method-tag">GET</span>
        <span class="url-path" id="url-display">/home</span>
      </div>

      <!-- DYNAMIC INPUT FORM -->
      <div id="form-container" class="form-grid">
        <!-- Injected via JavaScript -->
      </div>

      <!-- QUICK SELECTOR ITEMS (AUTO POPULATED FROM HOME/SEARCH) -->
      <div id="quick-container" style="display:none;">
        <div class="nav-title" style="padding-left:0; margin-bottom:8px;">Quick Select Item (Click to Auto-fill)</div>
        <div class="quick-items" id="quick-items-list"></div>
      </div>

      <!-- MEDIA PLAYER PREVIEW FOR STREAMS -->
      <div id="media-preview-container" class="media-preview" style="display:none;">
        <div class="nav-title" style="padding-left:0; margin-bottom:10px; color: var(--accent-cyan);">Direct Video Player Stream Preview</div>
        <video id="stream-player" controls preload="metadata"></video>
      </div>

      <!-- RESPONSE CONTAINER -->
      <div class="response-container">
        <div class="response-meta">
          <div style="display:flex; align-items:center; gap:12px;">
            <span class="nav-title" style="padding:0; margin:0;">Response Output</span>
            <span id="status-tag" class="status-badge" style="display:none;">200 OK</span>
            <span id="time-tag" style="font-size:0.8rem; color:var(--text-sub); display:none;">120ms</span>
          </div>
          <div class="response-actions">
            <button class="btn-sm" onclick="copyCurl()">Copy cURL</button>
            <button class="btn-sm" onclick="copyResponse()">Copy JSON</button>
          </div>
        </div>

        <div class="code-wrapper">
          <pre id="json-output">// Click "Execute Request" above to test this endpoint live.</pre>
        </div>
      </div>
    </div>
  </div>

  <footer>
    Powering Next-Gen Media Discovery &mdash; <span>ViralBit Movie Apis</span>
  </footer>
</div>

<script>
let currentEp = 'home';
let lastResponseData = null;

const ENDPOINTS = {
  home: {
    title: '🏠 Discover Home Feed',
    desc: 'Retrieve real-time banners, top trending blocks, and curated categories.',
    path: '/home',
    params: []
  },
  movies: {
    title: '🎬 Movie Catalog',
    desc: 'Browse paginated catalog for movies with sorting support.',
    path: '/movies',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
  },
  tv: {
    title: '📺 TV Series Catalog',
    desc: 'Browse paginated catalog for TV shows.',
    path: '/tv-series',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
  },
  animation: {
    title: '🐉 Animation Catalog',
    desc: 'Browse paginated catalog for animated series and movies.',
    path: '/animation',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
  },
  search: {
    title: '🔍 Full-Text Search',
    desc: 'High-precision search returning matching titles, slugs, and poster URLs.',
    path: '/search',
    params: [
      { name: 'q', label: 'Search Query', type: 'text', value: 'matrix' },
      { name: 'page', label: 'Page Number', type: 'number', value: '1' }
    ]
  },
  suggest: {
    title: '💡 Autocomplete Suggestions',
    desc: 'Fast light-weight type-ahead search suggestions.',
    path: '/search/suggest',
    params: [
      { name: 'q', label: 'Keyword', type: 'text', value: 'break' }
    ]
  },
  detail: {
    title: '📄 Full Metadata Tree',
    desc: 'Deep metadata inspection for seasons, episodes, languages, and artwork.',
    path: '/detail/{slug}',
    params: [
      { name: 'slug', label: 'Subject Slug', type: 'text', value: 'coven-academy-UQietRFFzK3' }
    ]
  },
  stream: {
    title: '⚡ Stream Source Resolver',
    desc: 'Extract direct MP4 video URLs, HLS/DASH links across resolutions.',
    path: '/api/stream/{subject_id}',
    params: [
      { name: 'subject_id', label: 'Subject ID', type: 'text', value: '3148392746424091800' },
      { name: 'detail_path', label: 'Detail Path / Slug', type: 'text', value: 'coven-academy-UQietRFFzK3' },
      { name: 'se', label: 'Season Number', type: 'number', value: '1' },
      { name: 'ep', label: 'Episode Number', type: 'number', value: '1' }
    ]
  },
  captions: {
    title: '💬 Subtitle & Captions',
    desc: 'Fetch full caption track list in SRT/VTT for specific episode.',
    path: '/api/stream/{subject_id}/captions',
    params: [
      { name: 'subject_id', label: 'Subject ID', type: 'text', value: '3148392746424091800' },
      { name: 'detail_path', label: 'Detail Path / Slug', type: 'text', value: 'coven-academy-UQietRFFzK3' },
      { name: 'se', label: 'Season Number', type: 'number', value: '1' },
      { name: 'ep', label: 'Episode Number', type: 'number', value: '1' }
    ]
  },
  health: {
    title: '❤️ System Health & Cache State',
    desc: 'Check API service liveness, guest token cache status, and player domain TTL.',
    path: '/health',
    params: []
  }
};

function selectEndpoint(key) {
  currentEp = key;
  document.querySelectorAll('.ep-item').forEach((el, idx) => {
    el.classList.toggle('active', Object.keys(ENDPOINTS)[idx] === key);
  });

  const config = ENDPOINTS[key];
  document.getElementById('ep-title').innerText = config.title;
  document.getElementById('ep-desc').innerText = config.desc;

  renderForm(config.params);
  updateUrlDisplay();

  // Reset media player if switching endpoints
  document.getElementById('media-preview-container').style.display = 'none';
  const player = document.getElementById('stream-player');
  player.pause();
  player.src = '';
}

function renderForm(params) {
  const container = document.getElementById('form-container');
  container.innerHTML = '';

  if (!params || params.length === 0) {
    container.innerHTML = '<div style="color:var(--text-sub); font-size:0.9rem; grid-column:1/-1;">No required parameters for this endpoint.</div>';
    return;
  }

  params.forEach(p => {
    const group = document.createElement('div');
    group.className = 'form-group';

    const label = document.createElement('label');
    label.innerText = p.label;
    group.appendChild(label);

    if (p.type === 'select') {
      const select = document.createElement('select');
      select.className = 'form-control';
      select.id = `input-${p.name}`;
      p.options.forEach(opt => {
        const option = document.createElement('option');
        option.value = opt;
        option.innerText = opt;
        if (opt === p.value) option.selected = true;
        select.appendChild(option);
      });
      select.onchange = updateUrlDisplay;
      group.appendChild(select);
    } else {
      const input = document.createElement('input');
      input.className = 'form-control';
      input.type = p.type;
      input.id = `input-${p.name}`;
      input.value = p.value;
      input.oninput = updateUrlDisplay;
      group.appendChild(input);
    }

    container.appendChild(group);
  });
}

function buildRequestUrl() {
  const config = ENDPOINTS[currentEp];
  let path = config.path;
  const queryParams = new URLSearchParams();

  config.params.forEach(p => {
    const el = document.getElementById(`input-${p.name}`);
    const val = el ? el.value.trim() : p.value;

    if (path.includes(`{${p.name}}`)) {
      path = path.replace(`{${p.name}}`, encodeURIComponent(val));
    } else if (val !== '') {
      queryParams.append(p.name, val);
    }
  });

  const qString = queryParams.toString();
  return qString ? `${path}?${qString}` : path;
}

function updateUrlDisplay() {
  document.getElementById('url-display').innerText = buildRequestUrl();
}

async function executeApi() {
  const reqUrl = buildRequestUrl();
  const output = document.getElementById('json-output');
  const statusTag = document.getElementById('status-tag');
  const timeTag = document.getElementById('time-tag');
  const mediaContainer = document.getElementById('media-preview-container');
  const player = document.getElementById('stream-player');

  output.innerText = '// Fetching live response...';
  statusTag.style.display = 'none';
  timeTag.style.display = 'none';
  mediaContainer.style.display = 'none';
  player.pause();
  player.src = '';

  const startTime = performance.now();

  try {
    const res = await fetch(reqUrl);
    const duration = Math.round(performance.now() - startTime);
    const data = await res.json();
    lastResponseData = data;

    statusTag.innerText = `${res.status} ${res.statusText || 'OK'}`;
    statusTag.className = `status-badge ${res.ok ? '' : 'error'}`;
    statusTag.style.display = 'inline-block';

    timeTag.innerText = `${duration}ms`;
    timeTag.style.display = 'inline-block';

    output.innerText = JSON.stringify(data, null, 2);

    // Populate quick picker if items returned
    if (data.items || (data.sections && data.sections[0])) {
      extractQuickItems(data);
    }

    // Direct preview player if sources are present
    if (currentEp === 'stream' && data.sources && data.sources.length > 0) {
      const playable = data.sources.find(s => s.url);
      if (playable) {
        player.src = playable.url;
        mediaContainer.style.display = 'block';
      }
    }
  } catch (err) {
    statusTag.innerText = 'FETCH ERROR';
    statusTag.className = 'status-badge error';
    statusTag.style.display = 'inline-block';
    output.innerText = `// Request Failed: ${err.message}`;
  }
}

function extractQuickItems(data) {
  let items = [];
  if (data.items) {
    items = data.items;
  } else if (data.sections) {
    data.sections.forEach(s => {
      if (s.items) items.push(...s.items);
    });
  }

  items = items.filter(i => i.slug && i.subject_id).slice(0, 10);
  if (items.length === 0) return;

  const quickContainer = document.getElementById('quick-container');
  const quickList = document.getElementById('quick-items-list');
  quickList.innerHTML = '';

  items.forEach(item => {
    const card = document.createElement('div');
    card.className = 'quick-card';
    card.onclick = () => fillItemDetails(item.slug, item.subject_id);

    const img = document.createElement('img');
    img.src = item.poster_url || 'https://via.placeholder.com/100x150?text=No+Poster';
    img.alt = item.name || 'Poster';
    card.appendChild(img);

    const title = document.createElement('div');
    title.className = 'title';
    title.innerText = item.name || 'Untitled';
    card.appendChild(title);

    quickList.appendChild(card);
  });

  quickContainer.style.display = 'block';
}

function fillItemDetails(slug, subjectId) {
  if (currentEp !== 'detail' && currentEp !== 'stream' && currentEp !== 'captions') {
    selectEndpoint('detail');
  }

  setTimeout(() => {
    const slugInput = document.getElementById('input-slug') || document.getElementById('input-detail_path');
    const sidInput = document.getElementById('input-subject_id');

    if (slugInput) slugInput.value = slug;
    if (sidInput) sidInput.value = subjectId;

    updateUrlDisplay();
  }, 50);
}

function copyResponse() {
  if (!lastResponseData) return;
  navigator.clipboard.writeText(JSON.stringify(lastResponseData, null, 2));
  alert('JSON Response copied to clipboard!');
}

function copyCurl() {
  const fullUrl = window.location.origin + buildRequestUrl();
  const curlCmd = `curl -X GET "${fullUrl}"`;
  navigator.clipboard.writeText(curlCmd);
  alert('cURL command copied to clipboard!');
}

// Initial setup
selectEndpoint('home');
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    reload_flag = os.environ.get("RELOAD", "0") == "1"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_flag)