import os
import re
import json
import time
import httpx
import yt_dlp
import asyncio
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="ViralBit Multi-APIs Platform",
    description="Universal Social Media & MovieBox Multi-API Platform created by DevDrey",
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


# ─── UNIVERSAL MULTI-PLATFORM DOWNLOADER ENGINE (DevDrey) ───────────────────

def extract_social_media(url: str, is_audio_only: bool = False, platform_hint: str = "auto") -> dict:
    if not url or not url.strip():
        return {
            "status": "error",
            "creator": "DevDrey",
            "message": "Query parameter 'url' is required.",
            "url": url
        }

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return {
                    "status": "error",
                    "creator": "DevDrey",
                    "message": "Could not extract media info from provided URL.",
                    "url": url
                }

            title = info.get('title') or 'Media Download'
            thumbnail = info.get('thumbnail')
            duration = info.get('duration')
            uploader = info.get('uploader') or info.get('uploader_id') or info.get('channel') or 'DevDrey Platform'

            downloads = []
            formats = info.get('formats') or []

            seen_urls = set()
            for f in formats:
                f_url = f.get('url')
                if not f_url or f_url in seen_urls:
                    continue
                seen_urls.add(f_url)

                ext = (f.get('ext') or 'mp4').upper()
                height = f.get('height')
                vcodec = f.get('vcodec')
                acodec = f.get('acodec')

                if is_audio_only and acodec == 'none':
                    continue

                quality = f"{height}p" if height else (f.get('format_note') or ('HD' if vcodec != 'none' else 'Audio'))
                filesize = f.get('filesize') or f.get('filesize_approx')
                size_str = f"{round(filesize / (1024 * 1024), 2)} MB" if filesize else "Direct Stream"

                downloads.append({
                    'quality': quality,
                    'format': ext,
                    'url': f_url,
                    'size': size_str,
                    'has_video': vcodec != 'none' and vcodec is not None,
                    'has_audio': acodec != 'none' and acodec is not None,
                })

            if not downloads and info.get('url'):
                downloads.append({
                    'quality': 'Audio' if is_audio_only else 'HD Direct',
                    'format': 'MP3' if is_audio_only else (info.get('ext') or 'MP4').upper(),
                    'url': info.get('url'),
                    'size': 'Direct Stream',
                    'has_video': not is_audio_only,
                    'has_audio': True
                })

            detected_platform = platform_hint if platform_hint != 'auto' else (info.get('extractor_key') or 'Social Media')

            return {
                'status': 'success',
                'creator': 'DevDrey',
                'platform': detected_platform,
                'title': title,
                'author': uploader,
                'thumbnail': thumbnail,
                'duration': duration,
                'url': url,
                'total_downloads': len(downloads),
                'downloads': downloads[:12]
            }
    except Exception as e:
        return {
            'status': 'error',
            'creator': 'DevDrey',
            'platform': platform_hint,
            'message': f"Extraction failed: {str(e)}",
            'url': url
        }

@app.get("/api/downloader/all")
async def download_all(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "Universal All-in-One")

@app.get("/api/downloader/tiktok")
async def download_tiktok(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "TikTok")

@app.get("/api/downloader/youtube")
async def download_youtube(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "YouTube")

@app.get("/api/downloader/instagram")
async def download_instagram(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "Instagram")

@app.get("/api/downloader/facebook")
async def download_facebook(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "Facebook")

@app.get("/api/downloader/x")
async def download_x(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "X (Twitter)")

@app.get("/api/downloader/ytmp3")
async def download_ytmp3(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, True, "YTMP3 Converter")

@app.get("/api/downloader/pinterest")
async def download_pinterest(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "Pinterest")

@app.get("/api/downloader/threads")
async def download_threads(url: str = Query(...)):
    return await asyncio.to_thread(extract_social_media, url, False, "Threads")


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
<title>ViralBit Multi-APIs Platform &mdash; DevDrey</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-dark: #050811;
  --bg-card: rgba(11, 18, 33, 0.85);
  --bg-input: #0a1122;
  --border-color: rgba(0, 136, 255, 0.22);
  --border-glow: rgba(0, 210, 255, 0.5);
  --primary-blue: #0088ff;
  --accent-cyan: #00d2ff;
  --neon-glow: 0 0 25px rgba(0, 210, 255, 0.35);
  --text-main: #f0f4fc;
  --text-sub: #8ca2c0;
  --code-bg: #03060d;
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
    radial-gradient(circle at 15% 15%, rgba(0, 136, 255, 0.18) 0%, transparent 40%),
    radial-gradient(circle at 85% 85%, rgba(0, 210, 255, 0.15) 0%, transparent 45%),
    linear-gradient(to bottom, #03050a, #080d1a);
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
  background: rgba(0, 136, 255, 0.12);
  border: 1px solid rgba(0, 210, 255, 0.4);
  padding: 6px 20px;
  border-radius: 30px;
  color: var(--accent-cyan);
  font-size: 0.82rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  box-shadow: var(--neon-glow);
  margin-bottom: 14px;
}

.creator-tag {
  color: #fff;
  font-weight: 800;
  background: linear-gradient(90deg, #0088ff, #00d2ff);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
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
  max-width: 700px;
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
  background: rgba(11, 18, 33, 0.7);
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
  margin-bottom: 14px;
  padding-left: 6px;
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
  font-size: 0.88rem;
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
  background: linear-gradient(90deg, rgba(0, 136, 255, 0.22) 0%, rgba(0, 210, 255, 0.05) 100%);
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

/* MEDIA CARD DISPLAY */
.media-preview-card {
  margin-top: 24px;
  background: rgba(5, 8, 17, 0.9);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  padding: 20px;
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 20px;
  align-items: start;
}

@media (max-width: 650px) {
  .media-preview-card { grid-template-columns: 1fr; }
}

.media-preview-card img {
  width: 100%;
  aspect-ratio: 16/9;
  object-fit: cover;
  border-radius: 10px;
  border: 1px solid rgba(0, 210, 255, 0.3);
}

.media-meta h3 {
  font-size: 1.15rem;
  font-weight: 700;
  color: #fff;
  margin-bottom: 6px;
}

.media-meta p {
  color: var(--text-sub);
  font-size: 0.85rem;
  margin-bottom: 12px;
}

.download-options {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.dl-card {
  background: rgba(0, 136, 255, 0.1);
  border: 1px solid rgba(0, 210, 255, 0.3);
  padding: 10px 14px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dl-card .quality { font-weight: 800; color: var(--accent-cyan); font-size: 0.9rem; }
.dl-card .size { font-size: 0.75rem; color: var(--text-sub); }

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
  background: rgba(3, 6, 13, 0.8);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  padding: 20px;
  margin-bottom: 20px;
}

.doc-endpoint-card h3 {
  font-size: 1.1rem;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 12px;
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
    <div class="brand-badge">
      <span class="pulse"></span> Universal Multi-APIs Platform &mdash; Created by <span class="creator-tag">&nbsp;DevDrey</span>
    </div>
    <h1>ViralBit Multi-APIs Platform</h1>
    <p class="subtitle">High-speed media downloader APIs for TikTok, YouTube, Instagram, Facebook, X, YTMP3, Pinterest, Threads, and MovieBox.</p>
  </header>

  <!-- VIEW SWITCHER TABS -->
  <div class="main-tabs">
    <button id="tab-console-btn" class="tab-btn active" onclick="switchView('console')">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
      Interactive Testing Console
    </button>
    <button id="tab-docs-btn" class="tab-btn" onclick="switchView('docs')">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
      API Docs & Integration Guide
    </button>
  </div>

  <!-- CONSOLE VIEW -->
  <div id="view-console" class="app-grid">
    <!-- LEFT SIDEBAR -->
    <div class="nav-card">
      <div class="nav-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
        Media Downloader APIs
      </div>
      <div class="endpoint-list">
        <div class="ep-item active" onclick="selectEndpoint('dl_all')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polygon points="10 8 16 12 10 16 10 8"></polygon></svg>
            <span>All-In-One Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_tiktok')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12a4 4 0 1 0 4 4V4a5 5 0 0 0 5 5"></path></svg>
            <span>TikTok Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_youtube')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2 29 29 0 0 0 .46-5.25 29 29 0 0 0-.46-5.33z"></path><polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02"></polygon></svg>
            <span>YouTube Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_instagram')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"></rect><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"></path><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"></line></svg>
            <span>Instagram Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_facebook')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"></path></svg>
            <span>Facebook Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_x')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 3a10.9 10.9 0 0 1-3.14 1.53 4.48 4.48 0 0 0-7.86 3v1A10.66 10.66 0 0 1 3 4s-4 9 5 13a11.64 11.64 0 0 1-7 2c9 5 20 0 20-11.5a4.5 4.5 0 0 0-.08-.83A7.72 7.72 0 0 0 23 3z"></path></svg>
            <span>X / Twitter Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_ytmp3')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
            <span>YTMP3 Audio Extractor</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_pinterest')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
            <span>Pinterest Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('dl_threads')">
          <div class="label-group">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            <span>Threads Downloader</span>
          </div>
          <span class="method-tag">GET</span>
        </div>

        <div class="nav-title" style="margin-top:16px;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect><line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line><line x1="2" y1="12" x2="22" y2="12"></line></svg>
          MovieBox APIs
        </div>
        <div class="ep-item" onclick="selectEndpoint('home')">
          <div class="label-group">
            <span>Home Feed</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
        <div class="ep-item" onclick="selectEndpoint('movies')">
          <div class="label-group">
            <span>Movies Catalog</span>
          </div>
          <span class="method-tag">GET</span>
        </div>
      </div>
    </div>

    <!-- MAIN CONSOLE -->
    <div class="console-card">
      <div class="console-header">
        <div class="endpoint-info">
          <h2 id="ep-title">Universal All-In-One Media Downloader</h2>
          <p id="ep-desc">Pass any social media video or audio URL (TikTok, YouTube, IG, FB, X, Pinterest, Threads) to extract direct download links.</p>
        </div>
        <button class="btn-submit" onclick="executeApi()">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
          Execute Request
        </button>
      </div>

      <div class="url-bar">
        <span class="method-tag">GET</span>
        <span class="url-path" id="url-display">/api/downloader/all</span>
      </div>

      <!-- DYNAMIC INPUT FORM -->
      <div id="form-container" class="form-grid"></div>

      <!-- MEDIA CARD DISPLAY -->
      <div id="media-card-container" style="display:none;" class="media-preview-card">
        <img id="media-thumb" src="" alt="Thumbnail" />
        <div class="media-meta">
          <h3 id="media-title">Title</h3>
          <p id="media-author">Author</p>
          <div class="download-options" id="media-dl-options"></div>
        </div>
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
          <pre id="json-output">// Click "Execute Request" above to test live.</pre>
        </div>
      </div>
    </div>
  </div>

  <!-- DOCUMENTATION VIEW -->
  <div id="view-docs" class="docs-view" style="display:none;">
    <div class="docs-section">
      <h2>Universal Multi-APIs Platform</h2>
      <p>Created by <strong>DevDrey</strong>. This platform offers multi-platform media downloader APIs for TikTok, YouTube, Instagram, Facebook, X (Twitter), YTMP3, Pinterest, and Threads, as well as full MovieBox feeds and search.</p>
      <p>All endpoints return structured JSON metadata stamped with <code>"creator": "DevDrey"</code>.</p>
    </div>

    <div class="docs-section">
      <h2>Media Downloader Endpoints</h2>
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/api/downloader/all?url={url}</code></h3>
        <p>Auto-detects the platform and returns extracted media download links.</p>
        <div class="code-snippet">curl "http://localhost:8000/api/downloader/all?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"</div>
      </div>
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/api/downloader/tiktok?url={url}</code></h3>
        <p>Extracts TikTok videos and audio tracks without watermarks.</p>
        <div class="code-snippet">curl "http://localhost:8000/api/downloader/tiktok?url={tiktok_url}"</div>
      </div>
      <div class="doc-endpoint-card">
        <h3><span class="method-tag">GET</span> <code>/api/downloader/ytmp3?url={url}</code></h3>
        <p>Converts YouTube video or audio link directly into MP3 stream downloads.</p>
        <div class="code-snippet">curl "http://localhost:8000/api/downloader/ytmp3?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"</div>
      </div>
    </div>
  </div>

  <footer>
    ViralBit Multi-APIs Platform &mdash; Created with precision by <span>DevDrey</span>
  </footer>
</div>

<script>
let currentEp = 'dl_all';
let lastResponseData = null;

const ENDPOINTS = {
  dl_all: {
    title: 'Universal All-In-One Downloader',
    desc: 'Pass any video/audio URL across supported platforms to extract download links.',
    path: '/api/downloader/all',
    params: [{ name: 'url', label: 'Media URL', type: 'text', value: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' }]
  },
  dl_tiktok: {
    title: 'TikTok Video & Audio Downloader',
    desc: 'Extract watermark-free TikTok video streams and audio.',
    path: '/api/downloader/tiktok',
    params: [{ name: 'url', label: 'TikTok Post URL', type: 'text', value: 'https://www.tiktok.com/@tiktok/video/7123456789' }]
  },
  dl_youtube: {
    title: 'YouTube Video Downloader',
    desc: 'Extract YouTube MP4 streams across 360p, 480p, 720p, 1080p, 4K.',
    path: '/api/downloader/youtube',
    params: [{ name: 'url', label: 'YouTube Video URL', type: 'text', value: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' }]
  },
  dl_instagram: {
    title: 'Instagram Reels & Video Downloader',
    desc: 'Download Instagram Reels, IGTV, and posts directly.',
    path: '/api/downloader/instagram',
    params: [{ name: 'url', label: 'Instagram Post/Reel URL', type: 'text', value: 'https://www.instagram.com/reel/C123456789/' }]
  },
  dl_facebook: {
    title: 'Facebook Video Downloader',
    desc: 'Extract HD/SD video downloads from public Facebook posts.',
    path: '/api/downloader/facebook',
    params: [{ name: 'url', label: 'Facebook Video URL', type: 'text', value: 'https://www.facebook.com/watch/?v=123456789' }]
  },
  dl_x: {
    title: 'X / Twitter Video Downloader',
    desc: 'Extract video and GIF media files from X / Twitter posts.',
    path: '/api/downloader/x',
    params: [{ name: 'url', label: 'X Post URL', type: 'text', value: 'https://x.com/username/status/123456789' }]
  },
  dl_ytmp3: {
    title: 'YTMP3 Audio Extractor',
    desc: 'Convert YouTube videos directly to MP3 audio stream links.',
    path: '/api/downloader/ytmp3',
    params: [{ name: 'url', label: 'YouTube URL', type: 'text', value: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' }]
  },
  dl_pinterest: {
    title: 'Pinterest Media Downloader',
    desc: 'Download pins, videos, and images from Pinterest.',
    path: '/api/downloader/pinterest',
    params: [{ name: 'url', label: 'Pinterest Pin URL', type: 'text', value: 'https://www.pinterest.com/pin/123456789/' }]
  },
  dl_threads: {
    title: 'Threads Media Downloader',
    desc: 'Download Threads video posts and media files.',
    path: '/api/downloader/threads',
    params: [{ name: 'url', label: 'Threads Post URL', type: 'text', value: 'https://www.threads.net/@username/post/123456789' }]
  },
  home: {
    title: 'MovieBox Home Feed',
    desc: 'Retrieve real-time movie & TV show banners and curated blocks.',
    path: '/home',
    params: []
  },
  movies: {
    title: 'MovieBox Movie Catalog',
    desc: 'Browse paginated catalog for movies with sorting support.',
    path: '/movies',
    params: [
      { name: 'page', label: 'Page Number', type: 'number', value: '1' },
      { name: 'sort', label: 'Sort By', type: 'select', value: 'RECOMMEND', options: ['RECOMMEND', 'NEWEST', 'RATING'] }
    ]
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
  document.getElementById('media-card-container').style.display = 'none';
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
  const mediaCard = document.getElementById('media-card-container');

  output.innerText = '// Fetching live response...';
  statusTag.style.display = 'none';
  timeTag.style.display = 'none';
  mediaCard.style.display = 'none';

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

    if (data.status === 'success' && data.downloads && data.downloads.length > 0) {
      document.getElementById('media-title').innerText = data.title || 'Extracted Media';
      document.getElementById('media-author').innerText = `Author: ${data.author || 'Platform Media'} | Creator: ${data.creator || 'DevDrey'}`;
      document.getElementById('media-thumb').src = data.thumbnail || 'https://via.placeholder.com/300x180?text=Media+Thumbnail';

      const dlContainer = document.getElementById('media-dl-options');
      dlContainer.innerHTML = '';

      data.downloads.forEach(dl => {
        const item = document.createElement('div');
        item.className = 'dl-card';
        item.innerHTML = `
          <div>
            <div class="quality">${dl.quality} (${dl.format})</div>
            <div class="size">${dl.size}</div>
          </div>
          <a href="${dl.url}" target="_blank" download class="dl-btn">Download</a>
        `;
        dlContainer.appendChild(item);
      });

      mediaCard.style.display = 'grid';
    }
  } catch (err) {
    statusTag.innerText = 'FETCH ERROR';
    statusTag.className = 'status-badge error';
    statusTag.style.display = 'inline-block';
    output.innerText = `// Request Failed: ${err.message}`;
  }
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
selectEndpoint('dl_all');
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    reload_flag = os.environ.get("RELOAD", "0") == "1"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_flag)
