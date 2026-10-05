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
RAILWAY_BASE = "https://moviebox-api-production-1d6a.up.railway.app"

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

async def _resolve_detail_path(subject_id: str, detail_path: str | None = None) -> str:
    """Helper to auto-resolve detailPath / slug if not provided by caller."""
    if detail_path:
        return detail_path
    try:
        url = f"{API_BASE}/detail?subjectId={subject_id}"
        data = await _make_request(url)
        sub = (data.get("data") or {}).get("subject") or {}
        found_slug = sub.get("detailPath")
        if found_slug:
            return found_slug
    except Exception:
        pass
    return subject_id

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
    detail_path: str | None = None,
    se: int = 1,
    ep: int = 1,
):
    detail_path = await _resolve_detail_path(subject_id, detail_path)
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
    detail_path: str | None = None,
    se: int = 1,
    ep: int = 1,
):
    detail_path = await _resolve_detail_path(subject_id, detail_path)
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

# ─── DOWNLOAD ─────────────────────────────────────────────────────────────────

@app.get("/api/download/{subject_id}")
async def get_download_links(
    subject_id: str,
    detail_path: str | None = None,
    se: int = 1,
    ep: int = 1,
):
    """Generates direct high-speed download links with file sizes and suggested filenames."""
    resolved_path = await _resolve_detail_path(subject_id, detail_path)

    title = "Media_File"
    try:
        detail_url = f"{API_BASE}/detail?subjectId={subject_id}"
        det_data = await _make_request(detail_url)
        sub = (det_data.get("data") or {}).get("subject") or {}
        if sub.get("title"):
            title = sub.get("title")
    except Exception:
        pass

    clean_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')

    domain = await _get_player_domain()
    referer = _build_player_referer(domain, resolved_path, subject_id, se, ep)
    play_url = (
        f"{domain}/wefeed-h5api-bff/subject/play"
        f"?subjectId={subject_id}&se={se}&ep={ep}&detailPath={resolved_path}"
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        resp = await client.get(play_url, headers={**PLAYER_HEADERS, "Referer": referer})
        try:
            data = resp.json().get("data", {}) or {}
        except Exception:
            raise HTTPException(status_code=502, detail="Player returned non-JSON")

    download_links = []
    for s in data.get("streams", []):
        raw_size = int(s.get("size", 0)) if s.get("size") else 0
        size_str = f"{round(raw_size / (1024 * 1024), 2)} MB" if raw_size else "Unknown"
        res_label = f"{s.get('resolutions')}p" if s.get("resolutions") else "SD"

        filename = f"{clean_title}_S{se:02d}E{ep:02d}_{res_label}.mp4"

        download_links.append({
            "quality": res_label,
            "format": s.get("format", "MP4"),
            "size_bytes": raw_size,
            "size": size_str,
            "duration": s.get("duration"),
            "filename": filename,
            "url": s.get("url"),
        })

    return {
        "status": "success",
        "subject_id": subject_id,
        "title": title,
        "slug": resolved_path,
        "se": se,
        "ep": ep,
        "total_options": len(download_links),
        "download_links": download_links,
        "hls": data.get("hls", []),
        "dash": data.get("dash", []),
    }

# ─── RAILWAY API ALIGNED ENDPOINTS ───────────────────────────────────────────

@app.get("/api/homepage")
async def get_railway_homepage():
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            resp = await client.get(f"{RAILWAY_BASE}/api/homepage")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return await get_home()

@app.get("/api/trending")
async def get_railway_trending():
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            resp = await client.get(f"{RAILWAY_BASE}/api/trending")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    home_data = await get_home()
    sections = home_data.get("sections", [])
    trending_items = []
    for s in sections:
        if any(k in s.get("section", "").lower() for k in ["popular", "trending", "banner", "hot"]):
            trending_items.extend(s.get("items", []))
    return {"status": "success", "trending": trending_items[:30]}

@app.get("/api/search/{query}")
async def railway_search(query: str):
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            resp = await client.get(f"{RAILWAY_BASE}/api/search/{query}")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return await search(q=query, page=1)

@app.get("/api/info/{id}")
async def railway_info(id: str, detailPath: str | None = None):
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            params = {"detailPath": detailPath} if detailPath else {}
            resp = await client.get(f"{RAILWAY_BASE}/api/info/{id}", params=params)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    resolved_slug = await _resolve_detail_path(id, detailPath)
    return await get_movie_detail(resolved_slug)

@app.get("/api/sources/{id}")
async def railway_sources(id: str, season: int = 1, episode: int = 1, detailPath: str | None = None):
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            params = {"season": season, "episode": episode}
            if detailPath:
                params["detailPath"] = detailPath
            resp = await client.get(f"{RAILWAY_BASE}/api/sources/{id}", params=params)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return await get_stream_sources(subject_id=id, detail_path=detailPath, se=season, ep=episode)

@app.get("/api/sports")
async def get_sports(category: str | None = None):
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            params = {"category": category} if category else {}
            resp = await client.get(f"{RAILWAY_BASE}/api/sports", params=params)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return {
        "status": "success",
        "category": category or "all",
        "items": [],
        "note": "Sports schedule feed."
    }

@app.get("/api/imgproxy")
async def imgproxy(url: str = Query(...)):
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        try:
            resp = await client.get(f"{RAILWAY_BASE}/api/imgproxy", params={"url": url})
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return {
        "status": "success",
        "original_url": url,
        "proxy_url": url
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
<title>ViralBit Movie Apis - Professional REST Portal & Documentation</title>
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
  padding: 30px 24px;
}

header {
  text-align: center;
  margin-bottom: 30px;
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
  font-size: clamp(2.2rem, 5vw, 3.4rem);
  font-weight: 800;
  letter-spacing: -1.5px;
  background: linear-gradient(135deg, #ffffff 0%, #a2c4fc 50%, var(--accent-cyan) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: 8px;
}

.subtitle {
  color: var(--text-sub);
  font-size: 1.05rem;
  max-width: 650px;
  margin: 0 auto 24px;
}

/* TOP VIEW SWITCHER TABS */
.main-tabs {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-bottom: 30px;
}

.tab-btn {
  background: rgba(13, 20, 36, 0.6);
  border: 1px solid var(--border-color);
  color: var(--text-sub);
  padding: 10px 24px;
  border-radius: 12px;
  font-weight: 700;
  font-size: 0.95rem;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  transition: all 0.25s ease;
}

.tab-btn:hover {
  background: rgba(0, 136, 255, 0.15);
  color: var(--text-main);
  border-color: rgba(0, 210, 255, 0.4);
}

.tab-btn.active {
  background: linear-gradient(135deg, var(--primary-blue) 0%, #00d2ff 100%);
  color: #030814;
  border-color: transparent;
  box-shadow: var(--neon-glow);
}

/* MAIN LAYOUT GRID */
.app-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
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
  display: flex;
  align-items: center;
  gap: 8px;
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
  padding: 11px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid transparent;
  color: var(--text-main);
  cursor: pointer;
  transition: all 0.25s ease;
  font-weight: 600;
  font-size: 0.9rem;
}

.ep-item .label-group {
  display: flex;
  align-items: center;
  gap: 10px;
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
  padding: 10px 14px;
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
  font-size: 0.95rem;
  padding: 12px 24px;
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
  display: inline-flex;
  align-items: center;
  gap: 6px;
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

/* MEDIA PREVIEW & DOWNLOADS */
.media-preview {
  margin-top: 20px;
  padding: 18px;
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

.download-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.dl-card {
  background: rgba(0, 136, 255, 0.1);
  border: 1px solid rgba(0, 210, 255, 0.3);
  padding: 12px 16px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dl-card .quality { font-weight: 800; color: var(--accent-cyan); font-size: 1rem; }
.dl-card .size { font-size: 0.8rem; color: var(--text-sub); }

.dl-btn {
  background: var(--primary-blue);
  color: #fff;
  padding: 6px 12px;
  border-radius: 6px;
  text-decoration: none;
  font-weight: 700;
  font-size: 0.8rem;
  transition: all 0.2s;
}

.dl-btn:hover { background: var(--accent-cyan); color: #000; }

/* QUICK ITEMS CAROUSEL/GRID */
.quick-items {
  margin-top: 14px;
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

/* DOCUMENTATION TAB STYLING */
.docs-view {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 20px;
  padding: 36px;
  backdrop-filter: blur(16px);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.4);
}

.docs-section {
  margin-bottom: 36px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding-bottom: 28px;
}

.docs-section:last-child { border-bottom: none; }

.docs-section h2 {
  font-size: 1.5rem;
  font-weight: 700;
  color: #fff;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.docs-section p {
  color: var(--text-sub);
  line-height: 1.6;
  font-size: 0.95rem;
  margin-bottom: 16px;
}

.doc-endpoint-card {
  background: rgba(5, 8, 17, 0.7);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  padding: 20px;
  margin-bottom: 20px;
}

.doc-endpoint-card h3 {
  font-size: 1.15rem;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.param-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 14px;
  font-size: 0.88rem;
}

.param-table th, .param-table td {
  text-align: left;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.param-table th {
  color: var(--accent-cyan);
  font-weight: 700;
  text-transform: uppercase;
  font-size: 0.75rem;
  letter-spacing: 1px;
}

.code-snippet {
  background: var(--code-bg);
  border: 1px solid rgba(0, 136, 255, 0.2);
  border-radius: 10px;
  padding: 14px 18px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  color: #aed0ff;
  margin-top: 12px;
  overflow-x: auto;
}

footer {
  text-align: center;
  margin-top: 50px;
  padding-top: 24px;
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
    <div class="brand-badge"><span class="pulse"></span> Official API Platform</div>
    <h1>ViralBit Movie Apis</h1>
    <p class="subtitle">High-performance REST API console and integration guide for MovieBox catalogs, streams, captions, and downloads.</p>
  </header>

  <!-- VIEW SWITCHER TABS -->
  <div class="main-tabs">
    <button id="tab-console-btn" class="tab-btn active" onclick="switchView('console')">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
      API Testing Console
    </button>
    <button id="tab-docs-btn" class="tab-btn" onclick="switchView('docs')">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
      API Documentation & Integration Guide
    </button>
  </div>

  <!-- CONSOLE VIEW -->
  <div id="view-console" class="app-grid">
    <!-- LEFT SIDEBAR -->
    <div class="nav-card">
      <div class="nav-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
        API Endpoints
      </div>
      <div class="endpoint-list">
        <div class="ep-item active" onclick="selectEndpoint('home')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
            <span>Home Feed</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('movies')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect><line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line><line x1="2" y1="12" x2="22" y2="12"></line></svg>
            <span>Movies Catalog</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('tv')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="15" rx="2" ry="2"></rect><polyline points="17 2 12 7 7 2"></polyline></svg>
            <span>TV Series</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('animation')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polygon points="10 8 16 12 10 16 10 8"></polygon></svg>
            <span>Animation</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('search')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <span>Full Search</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('suggest')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4"></path></svg>
            <span>Autocomplete</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('detail')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
            <span>Metadata Detail</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('stream')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
            <span>Stream Sources</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('download')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
            <span>Direct Download</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('captions')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            <span>Captions / Subs</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('trending')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
            <span>Trending Feed</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('sports')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"></path><path d="M2 12h20"></path></svg>
            <span>Sports Feed</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
                <div class="ep-item" onclick="selectEndpoint('health')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"></path></svg>
            <span>System Health</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
      </div>
    </div>

    <!-- MAIN CONSOLE -->
    <div class="console-card">
      <div class="console-header">
        <div class="endpoint-info">
          <h2 id="ep-title">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path></svg>
            Discover Home Feed
          </h2>
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
      <div id="form-container" class="form-grid"></div>

      <!-- QUICK SELECTOR ITEMS -->
      <div id="quick-container" style="display:none;">
        <div class="nav-title" style="padding-left:0; margin-bottom:8px;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
          Quick Select Item (Click to Auto-fill)
        </div>
        <div class="quick-items" id="quick-items-list"></div>
      </div>

      <!-- MEDIA PLAYER PREVIEW FOR STREAMS -->
      <div id="media-preview-container" class="media-preview" style="display:none;">
        <div class="nav-title" style="padding-left:0; margin-bottom:10px; color: var(--accent-cyan);">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
          Direct Video Stream Preview
        </div>
        <video id="stream-player" controls preload="metadata"></video>
      </div>

      <!-- DOWNLOAD OPTIONS PREVIEW -->
      <div id="download-container" class="media-preview" style="display:none;">
        <div class="nav-title" style="padding-left:0; margin-bottom:10px; color: var(--accent-cyan);">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
          High-Speed Direct Download Links
        </div>
        <div class="download-options" id="dl-cards-list"></div>
      </div>

      <!-- RESPONSE CONTAINER -->
      <div class="response-container">
        <div class="response-meta">
          <div style="display:flex; align-items:center; gap:12px;">
            <span class="nav-title" style="padding:0; margin:0;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
              Response Output
            </span>
            <span id="status-tag" class="status-badge" style="display:none;">200 OK</span>
            <span id="time-tag" style="font-size:0.8rem; color:var(--text-sub); display:none;">120ms</span>
          </div>
          <div class="response-actions">
            <button class="btn-sm" onclick="copyCurl()">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
              Copy cURL
            </button>
            <button class="btn-sm" onclick="copyResponse()">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path><rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect></svg>
              Copy JSON
            </button>
          </div>
        </div>

        <div class="code-wrapper">
          <pre id="json-output">// Click "Execute Request" above to test this endpoint live.</pre>
        </div>
      </div>
    </div>
  </div>

  <!-- DOCUMENTATION VIEW -->
  <div id="view-docs" class="docs-view" style="display:none;">
    <div class="docs-section">
      <h2>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
        Overview & Integration
      </h2>
      <p>Welcome to <strong>ViralBit Movie Apis</strong>. This platform provides an ultra-fast REST API interface for movie and TV series discovery, full-text search, direct stream link resolution, subtitles, and high-speed MP4 video downloads.</p>
      <p>No client-side API keys or reverse-engineering needed. All requests run with seamless guest session handling and automated player domain resolution.</p>
    </div>

    <div class="docs-section">
      <h2>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
        Authentication & Session Handling
      </h2>
      <p>The API handles authentication transparently. On the first request, a temporary guest session token is acquired from the upstream server and refreshed automatically. You can call all endpoints without setting any Authorization headers.</p>
    </div>

    <div class="docs-section">
      <h2>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line></svg>
        Endpoint Reference Catalog
      </h2>

      <!-- HOME FEED -->
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/home</code></h3>
        <p>Fetches real-time banners, top trending rows, and curated category lists from the home feed.</p>
        <div class="code-snippet">curl "http://localhost:8000/home"</div>
      </div>

      <!-- CATALOGS -->
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/movies</code>, <code>/tv-series</code>, <code>/animation</code></h3>
        <p>Returns paginated catalog lists with poster artwork, titles, year, ratings, and IDs.</p>
        <table class="param-table">
          <thead>
            <tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr><td><code>page</code></td><td>int</td><td>1</td><td>Page index (1-based)</td></tr>
            <tr><td><code>sort</code></td><td>string</td><td>RECOMMEND</td><td>Sort mode: RECOMMEND, NEWEST, RATING</td></tr>
          </tbody>
        </table>
        <div class="code-snippet">curl "http://localhost:8000/movies?page=1&sort=NEWEST"</div>
      </div>

      <!-- SEARCH -->
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/search</code></h3>
        <p>Full-text movie and series search with robust item normalization.</p>
        <table class="param-table">
          <thead>
            <tr><th>Parameter</th><th>Type</th><th>Required</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr><td><code>q</code></td><td>string</td><td>Yes</td><td>Search query string</td></tr>
            <tr><td><code>page</code></td><td>int</td><td>No (1)</td><td>Page index</td></tr>
          </tbody>
        </table>
        <div class="code-snippet">curl "http://localhost:8000/search?q=avatar"</div>
      </div>

      <!-- STREAM -->
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/api/stream/{subject_id}</code></h3>
        <p>Resolves playable direct MP4 video URLs across 360p, 480p, 720p, and 1080p resolutions.</p>
        <table class="param-table">
          <thead>
            <tr><th>Parameter</th><th>Type</th><th>Required</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr><td><code>subject_id</code></td><td>string</td><td>Yes</td><td>Unique media ID</td></tr>
            <tr><td><code>detail_path</code></td><td>string</td><td>No</td><td>Slug (Auto-resolved if omitted)</td></tr>
            <tr><td><code>se</code></td><td>int</td><td>No (1)</td><td>Season number</td></tr>
            <tr><td><code>ep</code></td><td>int</td><td>No (1)</td><td>Episode number</td></tr>
          </tbody>
        </table>
        <div class="code-snippet">curl "http://localhost:8000/api/stream/3148392746424091800?se=1&ep=1"</div>
      </div>

      <!-- DOWNLOAD -->
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/api/download/{subject_id}</code></h3>
        <p>Generates direct download links with calculated file sizes (MB), clean filenames, and duration info.</p>
        <div class="code-snippet">curl "http://localhost:8000/api/download/3148392746424091800"</div>
      </div>

      <!-- CAPTIONS -->
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/api/stream/{subject_id}/captions</code></h3>
        <p>Returns available SRT/VTT subtitle tracks for the selected movie or episode.</p>
        <div class="code-snippet">curl "http://localhost:8000/api/stream/3148392746424091800/captions"</div>
      </div>
    </div>

    <div class="docs-section">
      <h2>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
        Code Integration Examples
      </h2>
      <p><strong>Python (Async with httpx)</strong></p>
      <div class="code-snippet">import httpx, asyncio

async def fetch_movie_streams(subject_id):
    async with httpx.AsyncClient() as client:
        res = await client.get(f"http://localhost:8000/api/stream/{subject_id}")
        return res.json()

data = asyncio.run(fetch_movie_streams("3148392746424091800"))
print("Sources:", data["sources"])</div>

      <p style="margin-top:16px;"><strong>JavaScript (Node / Browser fetch)</strong></p>
      <div class="code-snippet">async function downloadMovie(subjectId) {
  const response = await fetch(`http://localhost:8000/api/download/${subjectId}`);
  const data = await response.json();
  console.log("Download Links:", data.download_links);
}
downloadMovie("3148392746424091800");</div>
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
    title: 'Discover Home Feed',
    desc: 'Retrieve real-time banners, top trending blocks, and curated categories.',
    path: '/home',
    params: []
  },
  movies: {
    title: 'Movie Catalog',
    desc: 'Browse paginated catalog for movies with sorting support.',
    path: '/movies',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
  },
  tv: {
    title: 'TV Series Catalog',
    desc: 'Browse paginated catalog for TV shows.',
    path: '/tv-series',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
  },
  animation: {
    title: 'Animation Catalog',
    desc: 'Browse paginated catalog for animated series and movies.',
    path: '/animation',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
  },
  search: {
    title: 'Full-Text Search',
    desc: 'High-precision search returning matching titles, slugs, and poster URLs.',
    path: '/search',
    params: [
      { name: 'q', label: 'Search Query', type: 'text', value: 'avatar' },
      { name: 'page', label: 'Page Number', type: 'number', value: '1' }
    ]
  },
  suggest: {
    title: 'Autocomplete Suggestions',
    desc: 'Fast light-weight type-ahead search suggestions.',
    path: '/search/suggest',
    params: [
      { name: 'q', label: 'Keyword', type: 'text', value: 'break' }
    ]
  },
  detail: {
    title: 'Full Metadata Tree',
    desc: 'Deep metadata inspection for seasons, episodes, languages, and artwork.',
    path: '/detail/{slug}',
    params: [
      { name: 'slug', label: 'Subject Slug', type: 'text', value: 'coven-academy-UQietRFFzK3' }
    ]
  },
  stream: {
    title: 'Stream Source Resolver',
    desc: 'Extract direct MP4 video URLs, HLS/DASH links across resolutions.',
    path: '/api/stream/{subject_id}',
    params: [
      { name: 'subject_id', label: 'Subject ID', type: 'text', value: '3148392746424091800' },
      { name: 'detail_path', label: 'Detail Path / Slug (Optional)', type: 'text', value: '' },
      { name: 'se', label: 'Season Number', type: 'number', value: '1' },
      { name: 'ep', label: 'Episode Number', type: 'number', value: '1' }
    ]
  },
  download: {
    title: 'Direct High-Speed Download Links',
    desc: 'Generate direct MP4 download links with size calculation and filenames.',
    path: '/api/download/{subject_id}',
    params: [
      { name: 'subject_id', label: 'Subject ID', type: 'text', value: '3148392746424091800' },
      { name: 'detail_path', label: 'Detail Path / Slug (Optional)', type: 'text', value: '' },
      { name: 'se', label: 'Season Number', type: 'number', value: '1' },
      { name: 'ep', label: 'Episode Number', type: 'number', value: '1' }
    ]
  },
  captions: {
    title: 'Subtitle & Captions',
    desc: 'Fetch full caption track list in SRT/VTT for specific episode.',
    path: '/api/stream/{subject_id}/captions',
    params: [
      { name: 'subject_id', label: 'Subject ID', type: 'text', value: '3148392746424091800' },
      { name: 'detail_path', label: 'Detail Path / Slug (Optional)', type: 'text', value: '' },
      { name: 'se', label: 'Season Number', type: 'number', value: '1' },
      { name: 'ep', label: 'Episode Number', type: 'number', value: '1' }
    ]
  },
  trending: {
    title: 'Trending Feed',
    desc: 'Retrieve top trending subjects and featured content.',
    path: '/api/trending',
    params: []
  },
  sports: {
    title: 'Sports Events Feed',
    desc: 'Retrieve live sports event schedules and stream metadata.',
    path: '/api/sports',
    params: [
      { name: 'category', label: 'Category', type: 'text', value: 'football' }
    ]
  },
    health: {
    title: 'System Health & Cache State',
    desc: 'Check API service liveness, guest token cache status, and player domain TTL.',
    path: '/health',
    params: []
  }
};

function switchView(view) {
  document.getElementById('view-console').style.display = view === 'console' ? 'grid' : 'none';
  document.getElementById('view-docs').style.display = view === 'docs' ? 'block' : 'none';
  document.getElementById('tab-console-btn').className = `tab-btn ${view === 'console' ? 'active' : ''}`;
  document.getElementById('tab-docs-btn').className = `tab-btn ${view === 'docs' ? 'active' : ''}`;
}

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

  // Reset previews
  document.getElementById('media-preview-container').style.display = 'none';
  document.getElementById('download-container').style.display = 'none';
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
  const dlContainer = document.getElementById('download-container');
  const player = document.getElementById('stream-player');

  output.innerText = '// Fetching live response...';
  statusTag.style.display = 'none';
  timeTag.style.display = 'none';
  mediaContainer.style.display = 'none';
  dlContainer.style.display = 'none';
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

    // Populate quick picker
    if (data.items || (data.sections && data.sections[0])) {
      extractQuickItems(data);
    }

    // Video preview for stream
    if (currentEp === 'stream' && data.sources && data.sources.length > 0) {
      const playable = data.sources.find(s => s.url);
      if (playable) {
        player.src = playable.url;
        mediaContainer.style.display = 'block';
      }
    }

    // Download cards for download endpoint
    if (currentEp === 'download' && data.download_links && data.download_links.length > 0) {
      renderDownloadCards(data.download_links);
    }
  } catch (err) {
    statusTag.innerText = 'FETCH ERROR';
    statusTag.className = 'status-badge error';
    statusTag.style.display = 'inline-block';
    output.innerText = `// Request Failed: ${err.message}`;
  }
}

function renderDownloadCards(links) {
  const container = document.getElementById('dl-cards-list');
  const dlContainer = document.getElementById('download-container');
  container.innerHTML = '';

  links.forEach(item => {
    const card = document.createElement('div');
    card.className = 'dl-card';
    card.innerHTML = `
      <div>
        <div class="quality">${item.quality} MP4</div>
        <div class="size">${item.size}</div>
      </div>
      <a href="${item.url}" target="_blank" download="${item.filename}" class="dl-btn">Download</a>
    `;
    container.appendChild(card);
  });

  dlContainer.style.display = 'block';
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
  if (currentEp !== 'detail' && currentEp !== 'stream' && currentEp !== 'download' && currentEp !== 'captions') {
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