import sqlite3
import requests
import json
import time
import secrets
import ipaddress
import urllib.parse
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List

from app.core.config import cfg, REPORT_COVER_URL
# 👇 修复点：引入 add_sys_notification
from app.core.database import DB_PATH, query_db, add_sys_notification
from app.schemas.models import MediaRequestSubmitModel as BaseSubmitModel
from app.services.bot_service import bot
from app.core.security import (
    get_client_ip,
    get_lock_status,
    record_failure,
    reset_failures,
    validate_captcha
)

router = APIRouter()

_server_id_cache = {"value": "", "expires_at": 0}

def _get_server_id_cached(host: str, key: str):
    now = int(time.time())
    if _server_id_cache["value"] and _server_id_cache["expires_at"] > now:
        return _server_id_cache["value"]
    if not host or not key:
        return ""
    try:
        sys_res = requests.get(f"{host}/emby/System/Info?api_key={key}", timeout=5)
        if sys_res.status_code == 200:
            _server_id_cache["value"] = sys_res.json().get("Id", "")
            _server_id_cache["expires_at"] = now + 300
            return _server_id_cache["value"]
    except:
        pass
    return ""

def _extract_host_ip(url: str):
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname
    except:
        return None

def _same_lan(ip_a: str, ip_b: str):
    try:
        a = ipaddress.ip_address(ip_a)
        b = ipaddress.ip_address(ip_b)
        if a.version != b.version:
            return False
        if a.is_loopback and b.is_loopback:
            return True
        if not (a.is_private and b.is_private):
            return False
        net = ipaddress.ip_network(f"{a}/24", strict=False)
        return b in net
    except:
        return False

def ensure_db_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA table_info(media_requests)")
    cols = c.fetchall()
    if cols:
        pk_cols = [col[1] for col in cols if col[5] > 0]
        if 'season' not in pk_cols:
            c.execute("ALTER TABLE media_requests RENAME TO media_requests_old")
            c.execute("""
                CREATE TABLE media_requests (
                    tmdb_id INTEGER, media_type TEXT, title TEXT, year TEXT, poster_path TEXT,
                    status INTEGER DEFAULT 0, season INTEGER DEFAULT 0, reject_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, season)
                )
            """)
            c.execute("INSERT OR IGNORE INTO media_requests (tmdb_id, media_type, title, year, poster_path, status, 0, reject_reason, created_at) SELECT tmdb_id, media_type, title, year, poster_path, status, 0, reject_reason, created_at FROM media_requests_old")
            c.execute("DROP TABLE media_requests_old")

    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='request_users'")
    u_sql = c.fetchone()
    if u_sql:
        sql_str = u_sql[0].lower().replace(" ", "")
        if "unique(tmdb_id,user_id,season)" not in sql_str:
            c.execute("ALTER TABLE request_users RENAME TO request_users_old")
            c.execute("""
                CREATE TABLE request_users (
                    tmdb_id INTEGER, user_id TEXT, username TEXT, season INTEGER DEFAULT 0, 
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(tmdb_id, user_id, season)
                )
            """)
            c.execute("INSERT OR IGNORE INTO request_users (tmdb_id, user_id, username, season) SELECT tmdb_id, user_id, COALESCE(username, '系统用户'), COALESCE(season, 0) FROM request_users_old")
            c.execute("DROP TABLE request_users_old")

    c.execute("""
        CREATE TABLE IF NOT EXISTS media_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT,
            user_id TEXT,
            username TEXT,
            issue_type TEXT,
            description TEXT,
            status INTEGER DEFAULT 0,
            poster_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("PRAGMA table_info(media_feedback)")
    feed_cols = [col[1] for col in c.fetchall()]
    if 'poster_path' not in feed_cols:
        try: c.execute("ALTER TABLE media_feedback ADD COLUMN poster_path TEXT")
        except: pass

    conn.commit()
    conn.close()

ensure_db_schema()

def execute_sql(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(query, params)
        conn.commit()
        return True, ""
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally: conn.close()

def get_emby_admin(host, key):
    try:
        users = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
        return users[0]['Id'] if users else None
    except: return None

def check_emby_exists(tmdb_id, media_type, season=0):
    host = cfg.get("emby_host"); key = cfg.get("emby_api_key")
    if not host or not key: return False
    try:
        admin_id = get_emby_admin(host, key)
        if not admin_id: return False
        type_filter = "Movie" if media_type == "movie" else "Series"
        url = f"{host}/emby/Users/{admin_id}/Items?AnyProviderIdEquals=tmdb.{tmdb_id}&IncludeItemTypes={type_filter}&Recursive=true&api_key={key}"
        res = requests.get(url, timeout=5).json()
        if not res.get("Items"): return False
        if media_type == "movie": return True
        sid = res["Items"][0]["Id"]
        season_url = f"{host}/emby/Shows/{sid}/Seasons?api_key={key}&UserId={admin_id}"
        s_res = requests.get(season_url, timeout=5).json()
        local_seasons = [s.get("IndexNumber") for s in s_res.get("Items", [])]
        return season in local_seasons
    except: return False

class MediaRequestSubmitModel(BaseSubmitModel):
    seasons: List[int] = [0] 
    overview: Optional[str] = ""

class AdminActionModel(BaseModel):
    tmdb_id: int
    season: int = 0
    action: str
    reject_reason: Optional[str] = None

class BulkAdminActionModel(BaseModel):
    items: List[dict]
    action: str
    reject_reason: Optional[str] = None

class RequestLoginModel(BaseModel):
    username: str
    password: str
    captcha: str

class FeedbackSubmitModel(BaseModel):
    item_name: str
    issue_type: str
    description: Optional[str] = ""
    poster_path: Optional[str] = ""

class FeedbackActionModel(BaseModel):
    id: int
    action: str 

class BulkFeedbackActionModel(BaseModel):
    items: List[int]
    action: str

@router.post("/api/requests/auth")
def request_system_login(data: RequestLoginModel, request: Request):
    ip = get_client_ip(request)
    locked, locked_until, _ = get_lock_status(ip, "request")
    if locked:
        remain_min = max(1, int((locked_until - int(time.time())) / 60) + 1)
        return JSONResponse(status_code=429, content={"status": "error", "message": f"失败次数过多，已锁定 {remain_min} 分钟"})

    ok, msg = validate_captcha(request, data.captcha)
    if not ok:
        locked_until, failed = record_failure(ip, "request", max_fail=3, lock_seconds=3600)
        if locked_until and locked_until > int(time.time()):
            remain_min = max(1, int((locked_until - int(time.time())) / 60) + 1)
            return JSONResponse(status_code=429, content={"status": "error", "message": f"失败次数过多，已锁定 {remain_min} 分钟"})
        remaining = max(0, 3 - failed)
        return {"status": "error", "message": f"{msg}，剩余 {remaining} 次"}

    host = cfg.get("emby_host")
    if not host: return {"status": "error", "message": "未配置 Emby 服务器"}
    headers = {"X-Emby-Authorization": 'MediaBrowser Client="EmbyPulse", Device="Web", DeviceId="PulseRequestApp", Version="2.0"'}
    try:
        res = requests.post(f"{host}/emby/Users/AuthenticateByName", json={"Username": data.username, "Pw": data.password}, headers=headers, timeout=8)
        if res.status_code == 200:
            user_info = res.json().get("User", {})
            request.session["req_user"] = {"Id": user_info.get("Id"), "Name": user_info.get("Name")}
            request.session["csrf_token"] = secrets.token_urlsafe(32)
            reset_failures(ip, "request")
            return {"status": "success"}
        locked_until, failed = record_failure(ip, "request", max_fail=3, lock_seconds=3600)
        if locked_until and locked_until > int(time.time()):
            remain_min = max(1, int((locked_until - int(time.time())) / 60) + 1)
            return JSONResponse(status_code=429, content={"status": "error", "message": f"失败次数过多，已锁定 {remain_min} 分钟"})
        remaining = max(0, 3 - failed)
        return {"status": "error", "message": f"账号或密码错误，剩余 {remaining} 次"}
    except Exception as e: return {"status": "error", "message": f"连接失败: {str(e)}"}

@router.get("/api/requests/check")
def check_auth(request: Request):
    user = request.session.get("req_user")
    if user: 
        user_id = user.get("Id")
        expire_date = "永久有效"
        if user_id:
            try:
                row = query_db("SELECT expire_date FROM users_meta WHERE user_id = ?", (user_id,))
                if row and row[0]['expire_date']:
                    expire_date = row[0]['expire_date']
            except: pass
            
        host = (cfg.get("emby_host") or "").rstrip('/')
        public_url = (cfg.get("emby_public_url") or cfg.get("emby_external_url") or "").rstrip('/')
        client_ip = get_client_ip(request)
        host_ip = _extract_host_ip(host) if host else None
        use_local = bool(client_ip and host_ip and _same_lan(client_ip, host_ip))
        emby_url = host if use_local else (public_url or host)
        server_id = _get_server_id_cached(host, cfg.get("emby_api_key"))
        return {
            "status": "success", 
            "user": {**user, "expire_date": expire_date},
            "server_url": emby_url.rstrip('/'),
            "server_id": server_id,
            "server_url_local": host,
            "server_url_public": public_url
        }
    return {"status": "error"}

@router.post("/api/requests/logout")
def request_system_logout(request: Request):
    request.session.pop("req_user", None)
    request.session.pop("csrf_token", None)
    return {"status": "success"}

@router.get("/api/requests/item_info")
def get_item_info(item_id: str, request: Request):
    key = cfg.get("emby_api_key")
    host = (cfg.get("emby_host") or "").rstrip('/') 
    try:
        admin_id = get_emby_admin(host, key)
        if not admin_id: return {"status": "error"}
        
        url = f"{host}/emby/Users/{admin_id}/Items/{item_id}?api_key={key}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            d = res.json()
            return {"status": "success", "data": {
                "Id": d.get("Id"),
                "SeriesId": d.get("SeriesId") or d.get("SeasonId"),
                "ServerId": d.get("ServerId", ""),
                "Name": d.get("Name", "未知"),
                "Type": d.get("Type", ""),
                "ProductionYear": d.get("ProductionYear", ""),
                "CommunityRating": d.get("CommunityRating", "N/A"),
                "Overview": d.get("Overview", ""),
                "Genres": d.get("Genres", [])
            }}
        return {"status": "error"}
    except Exception as e: 
        return {"status": "error"}

@router.get("/api/requests/hub_data")
def get_hub_data(request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error"}
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    uid = user['Id']
    
    top_rated = []; genres_data = []
    try:
        import random 
        tr_url = f"{host}/emby/Users/{uid}/Items?IncludeItemTypes=Movie,Series&Recursive=true&SortBy=CommunityRating&SortOrder=Descending&Limit=100&Fields=CommunityRating&api_key={key}"
        tr_res = requests.get(tr_url, timeout=5).json()
        
        valid_items = []
        for i in tr_res.get("Items", []):
            rating = i.get("CommunityRating", 0)
            if 8.0 <= rating <= 9.8:
                valid_items.append({
                    "Id": i.get("Id"), "Name": i.get("Name"), "Type": i.get("Type"),
                    "CommunityRating": rating
                })
                
        random.shuffle(valid_items)
        top_rated = valid_items[:10]
                
        g_url = f"{host}/emby/Users/{uid}/Items?IncludeItemTypes=Movie,Series&Recursive=true&SortBy=DateCreated&SortOrder=Descending&Limit=200&Fields=Genres&api_key={key}"
        g_res = requests.get(g_url, timeout=5).json()
        genre_counts = {}
        total_items = 0
        for i in g_res.get("Items", []):
            gs = i.get("Genres", [])
            if gs:
                total_items += 1
                for g in gs: genre_counts[g] = genre_counts.get(g, 0) + 1
        
        if total_items > 0:
            sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:6] 
            for k, v in sorted_genres:
                genres_data.append({"name": k, "count": v, "pct": round(v / total_items * 100)})
    except Exception as e: 
        print(f"获取枢纽数据失败: {e}")
        pass
        
    return {"status": "success", "data": {"top_rated": top_rated, "genres": genres_data}}

@router.get("/api/requests/search")
def search_tmdb(query: str, request: Request):
    if not request.session.get("req_user"): return {"status": "error", "message": "未登录"}
    tmdb_key = cfg.get("tmdb_api_key"); proxy = cfg.get("proxy_url"); proxies = {"https": proxy} if proxy else None
    try:
        res = requests.get(f"https://api.themoviedb.org/3/search/multi?api_key={tmdb_key}&language=zh-CN&query={query}", proxies=proxies, timeout=10).json()
        results = []
        for i in res.get("results", []):
            if i.get("media_type") in ["movie", "tv"]:
                results.append({"tmdb_id": i['id'], "media_type": i['media_type'], "title": i.get('title') or i.get('name'), "year": (i.get('release_date') or i.get('first_air_date') or "")[:4], "poster_path": f"https://image.tmdb.org/t/p/w500{i['poster_path']}" if i.get('poster_path') else "", "overview": i.get('overview', ''), "vote_average": round(i.get('vote_average', 0), 1), "local_status": -1})
        return {"status": "success", "data": results}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/requests/trending")
def get_tmdb_trending(request: Request):
    if not request.session.get("req_user"): return {"status": "error", "message": "未登录"}
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url"); proxies = {"https": proxy} if proxy else None
    try:
        results = []
        for page in [1, 2]:
            res = requests.get(f"https://api.themoviedb.org/3/trending/all/week?api_key={tmdb_key}&language=zh-CN&page={page}", proxies=proxies, timeout=10).json()
            for i in res.get("results", []):
                if i.get("media_type") in ["movie", "tv"] and i.get("poster_path"):
                    results.append({
                        "tmdb_id": i['id'], 
                        "media_type": i['media_type'], 
                        "title": i.get('title') or i.get('name'), 
                        "year": (i.get('release_date') or i.get('first_air_date') or "")[:4], 
                        "poster_path": f"https://image.tmdb.org/t/p/w500{i['poster_path']}", 
                        "overview": i.get('overview', ''), 
                        "vote_average": round(i.get('vote_average', 0), 1), 
                        "local_status": -1
                    })
        return {"status": "success", "data": results}
    except Exception as e: 
        return {"status": "error", "message": str(e)}

@router.get("/api/requests/tv/{tmdb_id}")
def get_tv_details(tmdb_id: int):
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url"); proxies = {"https": proxy} if proxy else None
    try:
        emby_host = cfg.get("emby_host"); emby_key = cfg.get("emby_api_key")
        local_seasons_map = {} 
        
        admin_id = get_emby_admin(emby_host, emby_key)
        if admin_id:
            s_res = requests.get(f"{emby_host}/emby/Users/{admin_id}/Items?AnyProviderIdEquals=tmdb.{tmdb_id}&IncludeItemTypes=Series&Recursive=true&api_key={emby_key}", timeout=5).json()
            if s_res.get("Items"):
                sid = s_res["Items"][0]["Id"]
                ep_res = requests.get(f"{emby_host}/emby/Users/{admin_id}/Items?ParentId={sid}&IncludeItemTypes=Episode&Recursive=true&Fields=ParentIndexNumber&api_key={emby_key}", timeout=5).json()
                for ep in ep_res.get("Items", []):
                    sn = ep.get("ParentIndexNumber")
                    if sn is not None:
                        local_seasons_map[sn] = local_seasons_map.get(sn, 0) + 1

        tmdb_res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={tmdb_key}&language=zh-CN", proxies=proxies, timeout=10).json()
        seasons = []
        for s in tmdb_res.get("seasons", []):
            if s["season_number"] > 0: 
                sn = s["season_number"]
                seasons.append({
                    "season_number": sn, 
                    "name": s["name"], 
                    "episode_count": s["episode_count"],
                    "exists_locally": sn in local_seasons_map,
                    "local_ep_count": local_seasons_map.get(sn, 0)
                })
        return {"status": "success", "seasons": seasons}
    except Exception as e: 
        return {"status": "error", "message": str(e)}

@router.get("/api/requests/check/{media_type}/{tmdb_id}")
def check_local_status(media_type: str, tmdb_id: int):
    exists = check_emby_exists(tmdb_id, media_type)
    return {"status": "success", "exists": exists}

@router.post("/api/requests/submit")
def submit_media_request(data: MediaRequestSubmitModel, request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "请重新登录"}
    uid = str(user.get("Id", "")); uname = user.get("Name") or "未知用户"
    results = []

    for sn in data.seasons:
        if check_emby_exists(data.tmdb_id, data.media_type, sn): continue
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT status FROM media_requests WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, sn))
        existing = c.fetchone()
        
        if not existing:
            execute_sql("INSERT INTO media_requests (tmdb_id, media_type, title, year, poster_path, status, season) VALUES (?, ?, ?, ?, ?, 0, ?)", (data.tmdb_id, data.media_type, data.title, data.year, data.poster_path, sn))
        elif existing[0] == 3: 
            execute_sql("UPDATE media_requests SET status = 0, reject_reason = NULL WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, sn))
        elif existing[0] == 2: 
            conn.close(); continue
            
        execute_sql("INSERT OR REPLACE INTO request_users (tmdb_id, user_id, username, season) VALUES (?, ?, ?, ?)", (data.tmdb_id, uid, uname, sn))
        results.append(sn)
        conn.close()

    if not results: return {"status": "error", "message": "所选资源均已入库或排队中"}

    if data.media_type == 'tv': type_name = "剧集"; season_info = f"\n📦 <b>季数</b>：第 {', '.join(map(str, results))} 季"
    else: type_name = "电影"; season_info = ""

    overview_text = data.overview[:110] + "..." if data.overview and len(data.overview) > 110 else (data.overview or "无")
    bot_msg = (f"🔔 <b>新求片提醒</b>\n\n"
               f"👤 <b>用户</b>：{uname}\n"
               f"📌 <b>片名</b>：{data.title} ({data.year})\n"
               f"🏷️ <b>类型</b>：{type_name}{season_info}\n\n📝 <b>简介：</b>\n{overview_text}")
    admin_url = cfg.get("pulse_url") or str(request.base_url).rstrip('/')
    
    keyboard = {"inline_keyboard": [
        [{"text": "🚀 推送 MP", "callback_data": f"req_approve_{data.tmdb_id}"},
         {"text": "✋ 手动接单", "callback_data": f"req_manual_{data.tmdb_id}"}],
        [{"text": "❌ 拒绝求片", "callback_data": f"req_reject_menu_{data.tmdb_id}"},
         {"text": "💻 网页审批", "url": f"{admin_url}/requests_admin"}]
    ]}
    
    bot.send_photo("sys_notify", data.poster_path or REPORT_COVER_URL, bot_msg, reply_markup=keyboard, platform="all")
    
    # 👇 新增：写入全局通知中心
    try:
        add_sys_notification(
            notify_type="request",
            title=f"🎬 新求片: {uname}",
            message=f"{data.title} ({data.year})",
            action_url="/requests_admin"
        )
    except Exception as e:
        print(f"写入求片通知失败: {e}")

    return {"status": "success", "message": f"成功提交 {len(results)} 项求片请求"}

@router.get("/api/requests/my")
def get_my_requests(request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
    uid = str(user.get("Id", ""))
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    query = "SELECT m.tmdb_id, m.title, m.year, m.poster_path, m.status, m.season, m.media_type, r.requested_at, m.reject_reason FROM request_users r JOIN media_requests m ON r.tmdb_id = m.tmdb_id AND r.season = m.season WHERE r.user_id = ? ORDER BY r.requested_at DESC"
    c.execute(query, (uid,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({"tmdb_id": r[0], "title": r[1] + (f" (S{r[5]})" if r[6]=='tv' else ""), "year": r[2], "poster_path": r[3], "status": r[4], "season": r[5], "requested_at": r[7], "reject_reason": r[8]})
    return {"status": "success", "data": results}

@router.get("/api/manage/requests")
def get_all_requests(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "无权访问"}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    query = "SELECT m.tmdb_id, m.media_type, m.title, m.year, m.poster_path, m.status, m.season, m.created_at, COUNT(r.user_id) as cnt, GROUP_CONCAT(COALESCE(r.username, '系统用户'), ', ') as users, m.reject_reason FROM media_requests m LEFT JOIN request_users r ON m.tmdb_id = r.tmdb_id AND m.season = r.season GROUP BY m.tmdb_id, m.season ORDER BY m.status ASC, m.created_at DESC"
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({"tmdb_id": r[0], "media_type": r[1], "title": r[2] + (f" 第 {r[6]} 季" if r[1]=='tv' else ""), "year": r[3], "poster_path": r[4], "status": r[5], "season": r[6], "created_at": r[7], "request_count": r[8], "requested_by": r[9], "reject_reason": r[10]})
    return {"status": "success", "data": results}

@router.post("/api/manage/requests/batch")
def batch_manage_action(data: BulkAdminActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}
        
    for item in data.items:
        tid = item['tmdb_id']; sn = item['season']
        if data.action == "approve":
            conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
            c.execute("SELECT * FROM media_requests WHERE tmdb_id = ? AND season = ?", (tid, sn))
            row = c.fetchone()
            conn.close()
            
            mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
            if mp_url and mp_token and row:
                payload = { "name": row["title"], "tmdbid": int(tid), "year": str(row["year"]), "type": "电影" if row["media_type"]=="movie" else "电视剧" }
                if row["media_type"] == "tv": payload["season"] = sn
                try: requests.post(f"{mp_url.rstrip('/')}/api/v1/subscribe/", json=payload, headers={"X-API-KEY": mp_token.strip().strip("'\"")}, timeout=10)
                except: pass
            execute_sql("UPDATE media_requests SET status = 1 WHERE tmdb_id = ? AND season = ?", (tid, sn))
            
        elif data.action == "manual":
            execute_sql("UPDATE media_requests SET status = 4 WHERE tmdb_id = ? AND season = ?", (tid, sn))
            
        elif data.action == "reject":
            execute_sql("UPDATE media_requests SET status = 3, reject_reason = ? WHERE tmdb_id = ? AND season = ?", (data.reject_reason, tid, sn))
        elif data.action == "finish":
            execute_sql("UPDATE media_requests SET status = 2 WHERE tmdb_id = ? AND season = ?", (tid, sn))
        elif data.action == "delete":
            execute_sql("DELETE FROM media_requests WHERE tmdb_id = ? AND season = ?", (tid, sn))
            execute_sql("DELETE FROM request_users WHERE tmdb_id = ? AND season = ?", (tid, sn))
            
    return {"status": "success", "message": f"操作已执行"}

@router.post("/api/manage/requests/action")
def manage_request_action(data: AdminActionModel, request: Request):
    return batch_manage_action(BulkAdminActionModel(items=[{"tmdb_id": data.tmdb_id, "season": data.season}], action=data.action, reject_reason=data.reject_reason), request)

@router.get("/api/requests/pending_notify")
def get_pending_notify(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
        
        c.execute("SELECT COUNT(*) as cnt FROM media_requests WHERE status = 0")
        req_count = (c.fetchone() or {'cnt': 0})['cnt']
        c.execute("SELECT m.tmdb_id, m.media_type, m.title, m.poster_path, m.season, m.created_at, GROUP_CONCAT(COALESCE(r.username, '未知用户'), ', ') as users FROM media_requests m LEFT JOIN request_users r ON m.tmdb_id = r.tmdb_id AND m.season = r.season WHERE m.status = 0 GROUP BY m.tmdb_id, m.season ORDER BY m.created_at DESC LIMIT 5")
        req_rows = c.fetchall()

        c.execute("SELECT COUNT(*) as cnt FROM media_feedback WHERE status = 0")
        feed_count = (c.fetchone() or {'cnt': 0})['cnt']
        
        c.execute("""
            SELECT f.id, f.item_name, f.username, f.issue_type, f.created_at,
                   COALESCE(
                       NULLIF(f.poster_path, ''), 
                       (SELECT poster_path FROM media_requests m WHERE m.title = f.item_name LIMIT 1),
                       (SELECT poster_path FROM media_requests m WHERE f.item_name LIKE m.title || '%' LIMIT 1)
                   ) as poster
            FROM media_feedback f 
            WHERE f.status = 0 ORDER BY f.created_at DESC LIMIT 5
        """)
        feed_rows = c.fetchall()
        
        conn.close()
        
        items = []
        for r in req_rows:
            items.append({
                "id": f"req_{r['tmdb_id']}_{r['season']}", 
                "title": r['title'] + (f" (第{r['season']}季)" if r['media_type'] == 'tv' else ""), 
                "poster": r['poster_path'], 
                "users": r['users'], 
                "time": r['created_at'],
                "type": "request"
            })
            
        for f in feed_rows:
            items.append({
                "id": f"feed_{f['id']}",
                "title": f"⚠️ 报错: {f['item_name']}",
                "poster": f['poster'] or "", 
                "users": f"{f['username']} - {f['issue_type']}",
                "time": f['created_at'],
                "type": "feedback"
            })
            
        items.sort(key=lambda x: x['time'], reverse=True)
        return {"status": "success", "count": req_count + feed_count, "items": items[:5]}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/requests/feedback/submit")
def submit_feedback(data: FeedbackSubmitModel, request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "请重新登录"}
    uid = str(user.get("Id", "")); uname = user.get("Name") or "未知用户"
    
    actual_poster = data.poster_path
    if actual_poster and actual_poster.startswith("/"):
        base_url = cfg.get("pulse_url") or str(request.base_url).rstrip('/')
        actual_poster = f"{base_url}{actual_poster}"
        
    if not actual_poster or 'undefined' in actual_poster:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT poster_path FROM media_requests WHERE ? LIKE title || '%' LIMIT 1", (data.item_name,))
        r = c.fetchone()
        if r and r[0]: actual_poster = r[0]
        conn.close()
        
    if not actual_poster or 'undefined' in actual_poster: actual_poster = ""

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO media_feedback (item_name, user_id, username, issue_type, description, poster_path) VALUES (?, ?, ?, ?, ?, ?)",
              (data.item_name, uid, uname, data.issue_type, data.description, actual_poster))
    feed_id = c.lastrowid
    conn.commit(); conn.close()
    
    msg = (f"🚨 <b>新资源报错提醒</b>\n\n"
           f"👤 <b>用户</b>：{uname}\n"
           f"🎬 <b>媒体</b>：{data.item_name}\n"
           f"🏷️ <b>问题</b>：{data.issue_type}\n"
           f"📝 <b>描述</b>：{data.description or '无'}")
    
    admin_url = cfg.get("pulse_url") or str(request.base_url).rstrip('/')
    keyboard = {"inline_keyboard": [
        [{"text": "🛠️ 标记修复中", "callback_data": f"feed_fix_{feed_id}"},
         {"text": "✅ 标记已修复", "callback_data": f"feed_done_{feed_id}"}],
        [{"text": "❌ 暂不处理(忽略)", "callback_data": f"feed_reject_{feed_id}"},
         {"text": "💻 网页处理", "url": f"{admin_url}/requests_admin"}]
    ]}
    
    img_url = actual_poster or REPORT_COVER_URL
    bot.send_photo("sys_notify", img_url, msg, reply_markup=keyboard, platform="all")
    
    # 👇 新增：写入全局通知中心
    try:
        add_sys_notification(
            notify_type="system",
            title=f"⚠️ 资源报错: {uname}",
            message=f"{data.item_name} - {data.issue_type}",
            action_url="/requests_admin?tab=feedback"
        )
    except Exception as e:
        print(f"写入报错通知失败: {e}")
    
    return {"status": "success", "message": "反馈已提交，感谢您的协助！"}

@router.get("/api/requests/feedback/my")
def get_my_feedback(request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
    uid = str(user.get("Id", ""))
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id, item_name, issue_type, description, status, created_at FROM media_feedback WHERE user_id = ? ORDER BY created_at DESC", (uid,))
    rows = c.fetchall(); conn.close()
    results = [{"id": r[0], "item_name": r[1], "issue_type": r[2], "description": r[3], "status": r[4], "created_at": r[5]} for r in rows]
    return {"status": "success", "data": results}

@router.get("/api/manage/feedback")
def get_all_feedback(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id, item_name, username, issue_type, description, status, created_at FROM media_feedback ORDER BY status ASC, created_at DESC")
    rows = c.fetchall(); conn.close()
    results = [{"id": r[0], "item_name": r[1], "username": r[2], "issue_type": r[3], "description": r[4], "status": r[5], "created_at": r[6]} for r in rows]
    return {"status": "success", "data": results}

@router.post("/api/manage/feedback/action")
def manage_feedback_action(data: FeedbackActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    status_map = {"fix": 1, "done": 2, "reject": 3, "delete": -1}
    st = status_map.get(data.action, 0)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    if st == -1: c.execute("DELETE FROM media_feedback WHERE id = ?", (data.id,))
    else: c.execute("UPDATE media_feedback SET status = ? WHERE id = ?", (st, data.id))
    conn.commit(); conn.close()
    return {"status": "success", "message": "已更新工单状态"}

@router.post("/api/manage/feedback/batch")
def batch_feedback_action(data: BulkFeedbackActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    status_map = {"fix": 1, "done": 2, "reject": 3, "delete": -1}
    st = status_map.get(data.action, 0)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    for fid in data.items:
        if st == -1: c.execute("DELETE FROM media_feedback WHERE id = ?", (fid,))
        else: c.execute("UPDATE media_feedback SET status = ? WHERE id = ?", (st, fid))
    conn.commit(); conn.close()
    return {"status": "success", "message": "批量操作已完成"}

@router.get("/api/requests/safe_top")
def get_safe_top_media(category: str, request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
    uid = user['Id']
    
    try:
        from app.routers.stats import api_top_movies
        global_res = api_top_movies(user_id="all", category=category, sort_by="count")
        global_items = global_res.get("data", [])
        
        if not global_items:
            return {"status": "success", "data": []}
            
        candidate_items = global_items[:30]
        item_ids = ",".join([str(i["ItemId"]) for i in candidate_items])
        
        host = (cfg.get("emby_host") or "").rstrip('/')
        key = cfg.get("emby_api_key")
        
        emby_url = f"{host}/emby/Users/{uid}/Items?Ids={item_ids}&Recursive=true&api_key={key}"
        emby_res = requests.get(emby_url, timeout=5).json()
        
        allowed_ids = {str(item["Id"]) for item in emby_res.get("Items", [])}
        safe_top_10 = [i for i in candidate_items if str(i["ItemId"]) in allowed_ids][:10]
        if not safe_top_10:
            # 若权限过滤导致空榜，回退到全局榜单，避免前端空白
            return {"status": "success", "data": candidate_items[:10]}
        return {"status": "success", "data": safe_top_10}
    except Exception as e:
        print(f"安全热播榜生成失败: {e}")
        return {"status": "error", "data": []}

@router.get("/api/requests/safe_latest")
def get_safe_latest(limit: int = 15, request: Request = None):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
    uid = user['Id']
    
    try:
        from app.routers.stats import api_latest_media
        global_res = api_latest_media(limit=40)
        global_items = global_res.get("data", [])
        
        if not global_items:
            return {"status": "success", "data": []}
            
        item_ids = ",".join([str(i.get("Id") or i.get("ItemId")) for i in global_items])
        
        host = (cfg.get("emby_host") or "").rstrip('/')
        key = cfg.get("emby_api_key")
        
        emby_url = f"{host}/emby/Users/{uid}/Items?Ids={item_ids}&Recursive=true&api_key={key}"
        emby_res = requests.get(emby_url, timeout=5).json()
        
        allowed_ids = {str(item["Id"]) for item in emby_res.get("Items", [])}
        
        safe_items = []
        for i in global_items:
            i_id = str(i.get("Id") or i.get("ItemId"))
            if i_id in allowed_ids:
                safe_items.append(i)
                
        return {"status": "success", "data": safe_items[:limit]}
    except Exception as e:
        print(f"安全最新入库生成失败: {e}")
        return {"status": "error", "data": []}
