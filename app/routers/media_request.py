import sqlite3
import requests
import json
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from typing import Optional, List

from app.core.config import cfg, REPORT_COVER_URL
from app.core.database import DB_PATH
from app.schemas.models import MediaRequestSubmitModel as BaseSubmitModel
from app.services.bot_service import bot

router = APIRouter()

# ==========================================================
# 🔥 核心：【彻底修复】数据库架构强制升级逻辑
# ==========================================================
def ensure_db_schema():
    """
    强制检测并升级数据库。
    解决 UNIQUE constraint failed: media_requests.tmdb_id 报错。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. 检查 media_requests 表的现有结构
    c.execute("PRAGMA table_info(media_requests)")
    columns = c.fetchall()
    
    if not columns:
        # 表不存在，直接创建最强架构
        print("✅ [映迹] 正在初始化 media_requests 数据库表...")
        c.execute("""
            CREATE TABLE IF NOT EXISTS media_requests (
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
    else:
        # 表存在，检查主键是否包含 season
        pk_cols = [col[1] for col in columns if col[5] > 0]
        if pk_cols == ['tmdb_id']:
            print("🚨 [映迹] 检测到旧版单主键架构，正在执行强制迁移...")
            # A. 备份旧数据
            c.execute("ALTER TABLE media_requests RENAME TO media_requests_old")
            # B. 创建复合主键新表
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
            # C. 迁移数据，老数据默认归为 0 季
            c.execute("""
                INSERT OR IGNORE INTO media_requests (tmdb_id, media_type, title, year, poster_path, status, season, reject_reason, created_at)
                SELECT tmdb_id, media_type, title, year, poster_path, status, 0, reject_reason, created_at FROM media_requests_old
            """)
            # D. 删除旧表
            c.execute("DROP TABLE media_requests_old")
            print("✅ [映迹] media_requests 架构强制升级完成。")

    # 2. 检查投票关联表 request_users
    c.execute("PRAGMA table_info(request_users)")
    u_columns = c.fetchall()
    if u_columns:
        u_pk = [col[1] for col in u_columns if col[5] > 0] # 检查是否有 season 参与唯一约束
        # 简单通过字段是否存在来判定
        u_cols_names = [col[1] for col in u_columns]
        if 'season' not in u_cols_names:
            print("🚨 [映迹] 正在升级投票表架构...")
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
            c.execute("INSERT OR IGNORE INTO request_users (tmdb_id, user_id, username, season) SELECT tmdb_id, user_id, username, 0 FROM request_users_old")
            c.execute("DROP TABLE request_users_old")
            print("✅ [映迹] request_users 架构升级完成。")
    else:
        # 初始化投票表
        c.execute("""
            CREATE TABLE IF NOT EXISTS request_users (
                tmdb_id INTEGER, 
                user_id TEXT, 
                username TEXT, 
                season INTEGER DEFAULT 0, 
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                UNIQUE(tmdb_id, user_id, season)
            )
        """)

    conn.commit()
    conn.close()

# 强制初始化
ensure_db_schema()

# ==========================================================
# 🛠️ 模型与工具函数
# ==========================================================
class MediaRequestSubmitModel(BaseSubmitModel):
    season: int = 0
    overview: Optional[str] = ""

class AdminActionModel(BaseModel):
    tmdb_id: int
    season: int = 0
    action: str
    reject_reason: Optional[str] = None

class RequestLoginModel(BaseModel):
    username: str
    password: str

def execute_sql(query, params=()):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute(query, params); conn.commit()
        return True, ""
    except Exception as e:
        conn.rollback()
        print(f"❌ [映迹] SQL异常: {str(e)}")
        return False, str(e)
    finally: conn.close()

def get_emby_admin(host, key):
    try:
        users = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
        return users[0]['Id'] if users else None
    except: return None

# ==========================================================
# 📡 权限认证接口
# ==========================================================
@router.post("/api/requests/auth")
def request_system_login(data: RequestLoginModel, request: Request):
    host = cfg.get("emby_host")
    headers = {"X-Emby-Authorization": 'MediaBrowser Client="EmbyPulse", Device="Web", DeviceId="PulseAuth", Version="2.0"'}
    try:
        res = requests.post(f"{host}/emby/Users/AuthenticateByName", json={"Username": data.username, "Pw": data.password}, headers=headers, timeout=8)
        if res.status_code == 200:
            user_info = res.json().get("User", {})
            request.session["req_user"] = {"Id": user_info.get("Id"), "Name": user_info.get("Name")}
            return {"status": "success"}
        return {"status": "error", "message": "账号或密码错误"}
    except: return {"status": "error", "message": "无法连接到 Emby 服务器"}

@router.get("/api/requests/check")
def check_auth(request: Request):
    user = request.session.get("req_user")
    return {"status": "success", "user": user} if user else {"status": "error"}

@router.post("/api/requests/logout")
def request_system_logout(request: Request):
    request.session.clear()
    return {"status": "success"}

# ==========================================================
# 🧭 影视发现与详情接口
# ==========================================================
@router.get("/api/requests/trending")
def get_trending():
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    proxies = {"https": proxy} if proxy else None
    try:
        m_url = f"https://api.themoviedb.org/3/trending/movie/week?api_key={tmdb_key}&language=zh-CN"
        t_url = f"https://api.themoviedb.org/3/trending/tv/week?api_key={tmdb_key}&language=zh-CN"
        m_res = requests.get(m_url, proxies=proxies, timeout=10).json()
        t_res = requests.get(t_url, proxies=proxies, timeout=10).json()
        
        def format_items(items, m_type):
            return [{
                "tmdb_id": i['id'], "media_type": m_type, "title": i.get('title') or i.get('name'),
                "year": (i.get('release_date') or i.get('first_air_date') or "")[:4],
                "poster_path": f"https://image.tmdb.org/t/p/w500{i['poster_path']}" if i.get('poster_path') else "",
                "backdrop_path": f"https://image.tmdb.org/t/p/w1280{i['backdrop_path']}" if i.get('backdrop_path') else "",
                "overview": i.get('overview', ''), "vote_average": round(i.get('vote_average', 0), 1)
            } for i in items[:20]]

        return {"status": "success", "data": {"movies": format_items(m_res.get('results', []), 'movie'), "tv": format_items(t_res.get('results', []), 'tv')}}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/requests/tv/{tmdb_id}")
def get_tv_details(tmdb_id: int):
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    proxies = {"https": proxy} if proxy else None
    try:
        emby_host = cfg.get("emby_host"); emby_key = cfg.get("emby_api_key")
        admin_id = get_emby_admin(emby_host, emby_key)
        local_seasons = []
        if admin_id:
            s_res = requests.get(f"{emby_host}/emby/Users/{admin_id}/Items?AnyProviderIdEquals=tmdb.{tmdb_id}&IncludeItemTypes=Series&Recursive=true&api_key={emby_key}", timeout=5).json()
            if s_res.get("Items"):
                sid = s_res["Items"][0]["Id"]
                seasons_res = requests.get(f"{emby_host}/emby/Shows/{sid}/Seasons?UserId={admin_id}&api_key={emby_key}", timeout=5).json()
                local_seasons = [s.get("IndexNumber") for s in seasons_res.get("Items", []) if s.get("IndexNumber") is not None]

        tmdb_res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={tmdb_key}&language=zh-CN", proxies=proxies, timeout=10).json()
        seasons = [{
            "season_number": s["season_number"], "name": s["name"], "episode_count": s["episode_count"],
            "exists_locally": s["season_number"] in local_seasons
        } for s in tmdb_res.get("seasons", []) if s["season_number"] > 0]
        
        return {"status": "success", "seasons": seasons}
    except Exception as e: return {"status": "error", "message": str(e)}

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
            if i.get("media_type") not in ["movie", "tv"]: continue
            results.append({
                "tmdb_id": i['id'], "media_type": i['media_type'], "title": i.get('title') or i.get('name'),
                "year": (i.get('release_date') or i.get('first_air_date') or "")[:4],
                "poster_path": f"https://image.tmdb.org/t/p/w500{i['poster_path']}" if i.get('poster_path') else "",
                "overview": i.get('overview', ''), "vote_average": round(i.get('vote_average', 0), 1),
                "local_status": -1 
            })
        return {"status": "success", "data": results}
    except Exception as e: return {"status": "error", "message": str(e)}

# ==========================================================
# ✍️ 求片提交与用户队列
# ==========================================================
@router.post("/api/requests/submit")
def submit_media_request(data: MediaRequestSubmitModel, request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "登录已过期"}

    # 查重逻辑
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT status FROM media_requests WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))
    existing = c.fetchone()
    
    if not existing:
        # 🔥 这里必须要检查 execute_sql 的结果，不能假装成功
        success, err = execute_sql("INSERT INTO media_requests (tmdb_id, media_type, title, year, poster_path, status, season) VALUES (?, ?, ?, ?, ?, 0, ?)",
                                   (data.tmdb_id, data.media_type, data.title, data.year, data.poster_path, data.season))
        if not success:
            return {"status": "error", "message": f"提交失败: {err}"}
    elif existing[0] == 2:
        return {"status": "error", "message": f"第 {data.season} 季已入库"}
    elif existing[0] == 3: 
        execute_sql("UPDATE media_requests SET status = 0, reject_reason = NULL WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))

    execute_sql("INSERT OR IGNORE INTO request_users (tmdb_id, user_id, username, season) VALUES (?, ?, ?, ?)", 
                (data.tmdb_id, user.get("Id"), user.get("Name"), data.season))
    
    type_tag = "电影" if data.media_type == "movie" else f"剧集 (第 {data.season} 季)"
    bot_msg = f"🔔 <b>新求片</b>\n👤 <b>用户</b>：{user.get('Name')}\n📌 <b>片名</b>：{data.title}\n🏷️ <b>类型</b>：{type_tag}"
    bot.send_photo("sys_notify", data.poster_path or REPORT_COVER_URL, bot_msg, platform="all")
    
    return {"status": "success", "message": f"已提交第 {data.season} 季求片心愿"}

@router.get("/api/requests/my")
def get_my_requests(request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    query = """
        SELECT m.tmdb_id, m.title, m.year, m.poster_path, m.status, m.season, m.media_type, r.requested_at, m.reject_reason 
        FROM request_users r 
        JOIN media_requests m ON r.tmdb_id = m.tmdb_id AND r.season = m.season
        WHERE r.user_id = ? ORDER BY r.requested_at DESC
    """
    c.execute(query, (user.get("Id"),)); rows = c.fetchall(); conn.close()
    return {"status": "success", "data": [{
        "tmdb_id": r[0], "title": r[1] + (f" (第 {r[5]} 季)" if r[6]=='tv' else ""),
        "year": r[2], "poster_path": r[3], "status": r[4], "season": r[5], "requested_at": r[7], "reject_reason": r[8]
    } for r in rows]}

# ==========================================================
# 👮 管理中心接口 (审批、拒绝、删除)
# ==========================================================
@router.get("/api/manage/requests")
def get_all_requests(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "无权访问"}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    query = """
        SELECT m.tmdb_id, m.media_type, m.title, m.year, m.poster_path, m.status, m.season, m.created_at, 
               COUNT(r.user_id) as cnt, GROUP_CONCAT(r.username, ', ') as users, m.reject_reason
        FROM media_requests m 
        LEFT JOIN request_users r ON m.tmdb_id = r.tmdb_id AND m.season = r.season
        GROUP BY m.tmdb_id, m.season
        ORDER BY m.status ASC, cnt DESC, m.created_at DESC
    """
    c.execute(query); rows = c.fetchall(); conn.close()
    return {"status": "success", "data": [{
        "tmdb_id": r[0], "media_type": r[1], "title": r[2] + (f" 第 {r[6]} 季" if r[1]=='tv' else ""),
        "year": r[3], "poster_path": r[4], "status": r[5], "season": r[6], "created_at": r[7], 
        "request_count": r[8], "requested_by": r[9] or "系统用户", "reject_reason": r[10]
    } for r in rows]}

@router.post("/api/manage/requests/action")
def manage_request_action(data: AdminActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}
    
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
    c.execute("SELECT * FROM media_requests WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))
    row = c.fetchone(); conn.close()
    if not row: return {"status": "error", "message": "记录已失效"}

    if data.action == "approve":
        mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
        if mp_url and mp_token:
            try:
                mp_api = f"{mp_url.rstrip('/')}/api/v1/subscribe/"
                payload = {"name": row["title"], "tmdbid": int(row["tmdb_id"]), "year": str(row["year"]), "type": "电影" if row["media_type"]=="movie" else "电视剧"}
                if row["media_type"] == "tv": payload["season"] = row["season"] or 1
                headers = {"X-API-KEY": mp_token.strip().strip("'\""), "Content-Type": "application/json"}
                res = requests.post(mp_api, json=payload, headers=headers, timeout=15)
                if res.status_code != 200: return {"status": "error", "message": f"MP 返回错误: {res.text}"}
                execute_sql("UPDATE media_requests SET status = 1 WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))
                return {"status": "success", "message": "已推送至下载流水线"}
            except Exception as e: return {"status": "error", "message": f"连接 MP 失败: {str(e)}"}

    elif data.action == "reject":
        execute_sql("UPDATE media_requests SET status = 3, reject_reason = ? WHERE tmdb_id = ? AND season = ?", (data.reject_reason, data.tmdb_id, data.season))
        return {"status": "success", "message": "已拒绝并反馈给用户"}

    elif data.action == "finish":
        execute_sql("UPDATE media_requests SET status = 2 WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))
        return {"status": "success", "message": "已标记入库"}

    elif data.action == "delete":
        execute_sql("DELETE FROM media_requests WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))
        execute_sql("DELETE FROM request_users WHERE tmdb_id = ? AND season = ?", (data.tmdb_id, data.season))
        return {"status": "success", "message": "记录已彻底删除"}

    return {"status": "error", "message": "未知指令"}