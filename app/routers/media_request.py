import sqlite3
import requests
import json
import time
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from typing import Optional, List

from app.core.config import cfg, REPORT_COVER_URL
from app.core.database import DB_PATH
from app.schemas.models import MediaRequestSubmitModel as BaseSubmitModel
from app.services.bot_service import bot

router = APIRouter()

# ==========================================================
# 🔥 核心：数据库架构强制校验与修复
# ==========================================================
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
                    tmdb_id INTEGER, 
                    media_type TEXT, 
                    title TEXT, 
                    year TEXT, 
                    poster_path TEXT,
                    status INTEGER DEFAULT 0, 
                    season INTEGER DEFAULT 0, 
                    reject_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tmdb_id, season)
                )
            """)
            c.execute("""
                INSERT OR IGNORE INTO media_requests (tmdb_id, media_type, title, year, poster_path, status, season, reject_reason, created_at) 
                SELECT tmdb_id, media_type, title, year, poster_path, status, 0, reject_reason, created_at FROM media_requests_old
            """)
            c.execute("DROP TABLE media_requests_old")

    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='request_users'")
    u_sql = c.fetchone()
    if u_sql:
        sql_str = u_sql[0].lower().replace(" ", "")
        if "unique(tmdb_id,user_id,season)" not in sql_str:
            c.execute("ALTER TABLE request_users RENAME TO request_users_old")
            c.execute("""
                CREATE TABLE request_users (
                    tmdb_id INTEGER, 
                    user_id TEXT, 
                    username TEXT, 
                    season INTEGER DEFAULT 0, 
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                    UNIQUE(tmdb_id, user_id, season)
                )
            """)
            c.execute("""
                INSERT OR IGNORE INTO request_users (tmdb_id, user_id, username, season) 
                SELECT tmdb_id, user_id, COALESCE(username, '系统用户'), COALESCE(season, 0) FROM request_users_old
            """)
            c.execute("DROP TABLE request_users_old")

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
    finally: 
        conn.close()

def get_emby_admin(host, key):
    try:
        users = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"): 
                return u['Id']
        return users[0]['Id'] if users else None
    except: 
        return None

def check_emby_exists(tmdb_id, media_type, season=0):
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
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
    except: 
        return False

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

@router.post("/api/requests/auth")
def request_system_login(data: RequestLoginModel, request: Request):
    host = cfg.get("emby_host")
    if not host: return {"status": "error", "message": "未配置 Emby 服务器"}
        
    headers = {"X-Emby-Authorization": 'MediaBrowser Client="EmbyPulse", Device="Web", DeviceId="PulseRequestApp", Version="2.0"'}
    try:
        res = requests.post(f"{host}/emby/Users/AuthenticateByName", json={"Username": data.username, "Pw": data.password}, headers=headers, timeout=8)
        if res.status_code == 200:
            user_info = res.json().get("User", {})
            request.session["req_user"] = {"Id": user_info.get("Id"), "Name": user_info.get("Name")}
            return {"status": "success"}
        return {"status": "error", "message": "账号或密码错误"}
    except Exception as e: 
        return {"status": "error", "message": f"连接失败: {str(e)}"}

@router.get("/api/requests/check")
def check_auth(request: Request):
    user = request.session.get("req_user")
    if user: return {"status": "success", "user": user}
    return {"status": "error"}

@router.post("/api/requests/logout")
def request_system_logout(request: Request):
    request.session.pop("req_user", None)
    return {"status": "success"}

# ==========================================================
# 🧭 TMDB 榜单大厅 (增加内存缓存机制与多元榜单)
# ==========================================================
_trending_cache = {}
_trending_cache_time = 0

@router.get("/api/requests/trending")
def get_trending():
    global _trending_cache, _trending_cache_time
    
    # 🔥 1小时内存缓存：告别 TMDB 网络卡顿，前端秒开！
    if time.time() - _trending_cache_time < 3600 and _trending_cache:
        return {"status": "success", "data": _trending_cache}
        
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    proxies = {"https": proxy} if proxy else None
    
    try:
        m_res = requests.get(f"https://api.themoviedb.org/3/trending/movie/week?api_key={tmdb_key}&language=zh-CN", proxies=proxies, timeout=10).json()
        t_res = requests.get(f"https://api.themoviedb.org/3/trending/tv/week?api_key={tmdb_key}&language=zh-CN", proxies=proxies, timeout=10).json()
        # 新增榜单：高分神作
        top_m_res = requests.get(f"https://api.themoviedb.org/3/movie/top_rated?api_key={tmdb_key}&language=zh-CN&region=CN&page=1", proxies=proxies, timeout=10).json()
        top_t_res = requests.get(f"https://api.themoviedb.org/3/tv/top_rated?api_key={tmdb_key}&language=zh-CN&page=1", proxies=proxies, timeout=10).json()
        
        def fmt(items, t): 
            results = []
            for i in items[:15]:
                results.append({
                    "tmdb_id": i['id'], 
                    "media_type": t, 
                    "title": i.get('title') or i.get('name'), 
                    "year": (i.get('release_date') or i.get('first_air_date') or "")[:4], 
                    "poster_path": f"https://image.tmdb.org/t/p/w500{i['poster_path']}" if i.get('poster_path') else "", 
                    "backdrop_path": f"https://image.tmdb.org/t/p/w1280{i['backdrop_path']}" if i.get('backdrop_path') else "", 
                    "overview": i.get('overview', ''), 
                    "vote_average": round(i.get('vote_average', 0), 1)
                })
            return results
            
        data = {
            "movies": fmt(m_res.get('results', []), 'movie'), 
            "tv": fmt(t_res.get('results', []), 'tv'),
            "top_movies": fmt(top_m_res.get('results', []), 'movie'),
            "top_tv": fmt(top_t_res.get('results', []), 'tv')
        }
        
        # 写入缓存
        _trending_cache = data
        _trending_cache_time = time.time()
        
        return {"status": "success", "data": data}
    except Exception as e: 
        if _trending_cache: 
            return {"status": "success", "data": _trending_cache}
        return {"status": "error", "message": str(e)}

@router.get("/api/requests/search")
def search_tmdb(query: str, request: Request):
    if not request.session.get("req_user"): return {"status": "error", "message": "未登录"}
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    proxies = {"https": proxy} if proxy else None
    
    try:
        res = requests.get(f"https://api.themoviedb.org/3/search/multi?api_key={tmdb_key}&language=zh-CN&query={query}", proxies=proxies, timeout=10).json()
        results = []
        for i in res.get("results", []):
            if i.get("media_type") in ["movie", "tv"]:
                results.append({
                    "tmdb_id": i['id'], 
                    "media_type": i['media_type'], 
                    "title": i.get('title') or i.get('name'), 
                    "year": (i.get('release_date') or i.get('first_air_date') or "")[:4], 
                    "poster_path": f"https://image.tmdb.org/t/p/w500{i['poster_path']}" if i.get('poster_path') else "", 
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
    proxy = cfg.get("proxy_url")
    proxies = {"https": proxy} if proxy else None
    
    try:
        emby_host = cfg.get("emby_host"); emby_key = cfg.get("emby_api_key")
        local_seasons = []
        admin_id = get_emby_admin(emby_host, emby_key)
        
        if admin_id:
            s_res = requests.get(f"{emby_host}/emby/Users/{admin_id}/Items?AnyProviderIdEquals=tmdb.{tmdb_id}&Recursive=true&api_key={emby_key}", timeout=5).json()
            if s_res.get("Items"):
                sid = s_res["Items"][0]["Id"]
                season_res = requests.get(f"{emby_host}/emby/Shows/{sid}/Seasons?UserId={admin_id}&api_key={emby_key}", timeout=5).json()
                local_seasons = [s.get("IndexNumber") for s in season_res.get("Items", [])]
        
        tmdb_res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={tmdb_key}&language=zh-CN", proxies=proxies, timeout=10).json()
        seasons = []
        for s in tmdb_res.get("seasons", []):
            if s["season_number"] > 0:
                seasons.append({
                    "season_number": s["season_number"], 
                    "name": s["name"], 
                    "episode_count": s["episode_count"], 
                    "exists_locally": s["season_number"] in local_seasons
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
    
    uid = str(user.get("Id", ""))
    uname = user.get("Name") or "未知用户"
    results = []

    for sn in data.seasons:
        if check_emby_exists(data.tmdb_id, data.media_type, sn): continue
            
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT status FROM media_requests WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, sn))
        existing = c.fetchone()
        
        if not existing:
            execute_sql("INSERT INTO media_requests (tmdb_id, media_type, title, year, poster_path, status, season) VALUES (?, ?, ?, ?, ?, 0, ?)", 
                       (data.tmdb_id, data.media_type, data.title, data.year, data.poster_path, sn))
        elif existing[0] == 3: 
            execute_sql("UPDATE media_requests SET status = 0, reject_reason = NULL WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, sn))
        elif existing[0] == 2: 
            conn.close(); continue
            
        execute_sql("INSERT OR REPLACE INTO request_users (tmdb_id, user_id, username, season) VALUES (?, ?, ?, ?)", 
                   (data.tmdb_id, uid, uname, sn))
        results.append(sn)
        conn.close()

    if not results: return {"status": "error", "message": "所选资源均已入库或排队中"}

    if data.media_type == 'tv':
        type_name = "剧集"; season_info = f"\n📦 <b>季数</b>：第 {', '.join(map(str, results))} 季"
    else:
        type_name = "电影"; season_info = ""

    overview_text = data.overview[:110] + "..." if data.overview and len(data.overview) > 110 else (data.overview or "无")
    bot_msg = (f"🔔 <b>新求片提醒</b>\n\n"
               f"👤 <b>用户</b>：{uname}\n"
               f"📌 <b>片名</b>：{data.title} ({data.year})\n"
               f"🏷️ <b>类型</b>：{type_name}{season_info}\n\n📝 <b>简介：</b>\n{overview_text}")
    admin_url = cfg.get("pulse_url") or str(request.base_url).rstrip('/')
    bot.send_photo("sys_notify", data.poster_path or REPORT_COVER_URL, bot_msg, reply_markup={"inline_keyboard": [[{"text": "🍿 立即审批", "url": f"{admin_url}/requests_admin"}]]}, platform="all")
    
    return {"status": "success", "message": f"成功提交 {len(results)} 项求片请求"}

@router.get("/api/requests/my")
def get_my_requests(request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
        
    uid = str(user.get("Id", ""))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = """
        SELECT m.tmdb_id, m.title, m.year, m.poster_path, m.status, m.season, m.media_type, r.requested_at, m.reject_reason 
        FROM request_users r 
        JOIN media_requests m ON r.tmdb_id = m.tmdb_id AND r.season = m.season 
        WHERE r.user_id = ? ORDER BY r.requested_at DESC
    """
    c.execute(query, (uid,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            "tmdb_id": r[0], "title": r[1] + (f" (S{r[5]})" if r[6]=='tv' else ""), 
            "year": r[2], "poster_path": r[3], "status": r[4], 
            "season": r[5], "requested_at": r[7], "reject_reason": r[8]
        })
    return {"status": "success", "data": results}

@router.get("/api/manage/requests")
def get_all_requests(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "无权访问"}
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = """
        SELECT m.tmdb_id, m.media_type, m.title, m.year, m.poster_path, m.status, m.season, m.created_at, 
               COUNT(r.user_id) as cnt, GROUP_CONCAT(COALESCE(r.username, '系统用户'), ', ') as users, m.reject_reason 
        FROM media_requests m 
        LEFT JOIN request_users r ON m.tmdb_id = r.tmdb_id AND m.season = r.season 
        GROUP BY m.tmdb_id, m.season 
        ORDER BY m.status ASC, m.created_at DESC
    """
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            "tmdb_id": r[0], "media_type": r[1], "title": r[2] + (f" 第 {r[6]} 季" if r[1]=='tv' else ""), 
            "year": r[3], "poster_path": r[4], "status": r[5], 
            "season": r[6], "created_at": r[7], "request_count": r[8], 
            "requested_by": r[9], "reject_reason": r[10]
        })
    return {"status": "success", "data": results}

@router.post("/api/manage/requests/batch")
def batch_manage_action(data: BulkAdminActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}
        
    for item in data.items:
        tid = item['tmdb_id']; sn = item['season']
        if data.action == "approve":
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM media_requests WHERE tmdb_id = ? AND season = ?", (tid, sn))
            row = c.fetchone()
            conn.close()
            
            mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
            if mp_url and mp_token and row:
                payload = { "name": row["title"], "tmdbid": int(tid), "year": str(row["year"]), "type": "电影" if row["media_type"]=="movie" else "电视剧" }
                if row["media_type"] == "tv": payload["season"] = sn
                requests.post(f"{mp_url.rstrip('/')}/api/v1/subscribe/", json=payload, headers={"X-API-KEY": mp_token.strip().strip("'\"")}, timeout=10)
            execute_sql("UPDATE media_requests SET status = 1 WHERE tmdb_id = ? AND season = ?", (tid, sn))
            
        elif data.action == "reject":
            execute_sql("UPDATE media_requests SET status = 3, reject_reason = ? WHERE tmdb_id = ? AND season = ?", (data.reject_reason, tid, sn))
        elif data.action == "finish":
            execute_sql("UPDATE media_requests SET status = 2 WHERE tmdb_id = ? AND season = ?", (tid, sn))
        elif data.action == "delete":
            execute_sql("DELETE FROM media_requests WHERE tmdb_id = ? AND season = ?; DELETE FROM request_users WHERE tmdb_id = ? AND season = ?", (tid, sn, tid, sn))
            
    return {"status": "success", "message": f"操作已执行"}

@router.post("/api/manage/requests/action")
def manage_request_action(data: AdminActionModel, request: Request):
    return batch_manage_action(BulkAdminActionModel(items=[{"tmdb_id": data.tmdb_id, "season": data.season}], action=data.action, reject_reason=data.reject_reason), request)