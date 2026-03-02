from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
import requests
import sqlite3
from app.core.config import cfg, REPORT_COVER_URL
from app.core.database import DB_PATH
from app.schemas.models import MediaRequestSubmitModel, MediaRequestActionModel
from app.services.bot_service import bot

router = APIRouter()

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

class RequestLoginModel(BaseModel):
    username: str
    password: str

@router.post("/api/requests/auth")
def request_system_login(data: RequestLoginModel, request: Request):
    host = cfg.get("emby_host")
    if not host: return {"status": "error", "message": "系统未配置 Emby 地址"}
    headers = {"X-Emby-Authorization": 'MediaBrowser Client="EmbyPulse", Device="Web", DeviceId="PulseReqSys", Version="1.0"'}
    try:
        res = requests.post(f"{host}/emby/Users/AuthenticateByName", json={"Username": data.username, "Pw": data.password}, headers=headers, timeout=8)
        if res.status_code == 200:
            user_info = res.json().get("User", {})
            request.session["req_user"] = {"Id": user_info.get("Id"), "Name": user_info.get("Name")}
            return {"status": "success", "message": "登录成功"}
        return {"status": "error", "message": "账号或密码错误"}
    except Exception as e: return {"status": "error", "message": f"连接 Emby 失败: {str(e)}"}

# 🔥 优化1：搜索时前置检查数据库状态
@router.get("/api/requests/search")
def search_tmdb(query: str, request: Request):
    if not request.session.get("req_user"): return {"status": "error", "message": "未登录"}
    tmdb_key = cfg.get("tmdb_api_key")
    if not tmdb_key: return {"status": "error", "message": "服主暂未配置 TMDB API Key"}
    proxy = cfg.get("proxy_url"); proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        url = f"https://api.themoviedb.org/3/search/multi?api_key={tmdb_key}&language=zh-CN&query={query}&page=1"
        res = requests.get(url, proxies=proxies, timeout=10)
        if res.status_code == 200:
            data = res.json()
            results = []
            
            # 批量查询本地数据库状态
            tmdb_ids = [str(item['id']) for item in data.get("results", []) if item.get("media_type") in ["movie", "tv"]]
            local_status_map = {}
            if tmdb_ids:
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                placeholders = ','.join('?' * len(tmdb_ids))
                c.execute(f"SELECT tmdb_id, status FROM media_requests WHERE tmdb_id IN ({placeholders})", tuple(tmdb_ids))
                for row in c.fetchall(): local_status_map[row[0]] = row[1]
                conn.close()

            for item in data.get("results", []):
                if item.get("media_type") not in ["movie", "tv"]: continue
                title = item.get("title") or item.get("name")
                year_str = item.get("release_date") or item.get("first_air_date") or ""
                poster = f"https://image.tmdb.org/t/p/w500{item.get('poster_path')}" if item.get("poster_path") else ""
                
                results.append({
                    "tmdb_id": item.get("id"), "media_type": item.get("media_type"),
                    "title": title, "year": year_str[:4] if year_str else "未知",
                    "poster_path": poster, "overview": item.get("overview", ""),
                    "local_status": local_status_map.get(item.get("id"), -1) # -1代表全新未求过
                })
            return {"status": "success", "data": results}
        return {"status": "error", "message": "TMDB API 响应异常"}
    except Exception as e: return {"status": "error", "message": f"网络代理或请求错误: {str(e)}"}

# 🔥 优化2：提交求片时带上后台跳转按钮
@router.post("/api/requests/submit")
def submit_media_request(data: MediaRequestSubmitModel, request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "登录已过期"}

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT status FROM media_requests WHERE tmdb_id = ?", (data.tmdb_id,))
    existing = c.fetchone()
    if not existing:
        execute_sql("INSERT INTO media_requests (tmdb_id, media_type, title, year, poster_path, status) VALUES (?, ?, ?, ?, ?, 0)",
                    (data.tmdb_id, data.media_type, data.title, data.year, data.poster_path))
    else:
        if existing[0] == 2: conn.close(); return {"status": "error", "message": "这部片子已经入库啦！"}

    success, err_msg = execute_sql("INSERT INTO request_users (tmdb_id, user_id, username) VALUES (?, ?, ?)",
                                   (data.tmdb_id, user.get("Id"), user.get("Name")))
    conn.close()

    if not success:
        if "UNIQUE" in err_msg: return {"status": "error", "message": "你已经提交过啦，不用重复点 +1"}
        return {"status": "error", "message": f"写入失败: {err_msg}"}

    # 构造带按钮的机器人通知
    type_cn = "🎬 电影" if data.media_type == "movie" else "📺 剧集"
    bot_msg = f"🔔 <b>新求片订单提醒</b>\n\n👤 <b>求片人</b>：{user.get('Name')}\n📌 <b>片名</b>：{data.title} ({data.year})\n🏷️ <b>类型</b>：{type_cn}"
    
    admin_url = cfg.get("emby_public_url") or cfg.get("emby_host")
    if admin_url.endswith('/'): admin_url = admin_url[:-1]
    keyboard = {"inline_keyboard": [[{"text": "🍿 前往后台审批", "url": f"{admin_url}/requests_admin"}]]}
    
    bot.send_photo("sys_notify", data.poster_path if data.poster_path else REPORT_COVER_URL, bot_msg, reply_markup=keyboard, platform="all")
    return {"status": "success", "message": "心愿提交成功！已通知服主处理。"}

@router.get("/api/requests/my")
def get_my_requests(request: Request):
    user = request.session.get("req_user")
    if not user: return {"status": "error", "message": "未登录"}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    query = "SELECT m.tmdb_id, m.title, m.year, m.poster_path, m.status, r.requested_at FROM request_users r JOIN media_requests m ON r.tmdb_id = m.tmdb_id WHERE r.user_id = ? ORDER BY r.requested_at DESC"
    c.execute(query, (user.get("Id"),)); rows = c.fetchall(); conn.close()
    return {"status": "success", "data": [{"tmdb_id": r[0], "title": r[1], "year": r[2], "poster_path": r[3], "status": r[4], "requested_at": r[5]} for r in rows]}

@router.get("/api/manage/requests")
def get_all_requests(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "未登录管理后台"}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    query = "SELECT m.tmdb_id, m.media_type, m.title, m.year, m.poster_path, m.status, m.created_at, COUNT(r.user_id) as request_count, GROUP_CONCAT(r.username, ', ') as requested_by FROM media_requests m LEFT JOIN request_users r ON m.tmdb_id = r.tmdb_id GROUP BY m.tmdb_id ORDER BY m.status ASC, request_count DESC, m.created_at DESC"
    c.execute(query); rows = c.fetchall(); conn.close()
    return {"status": "success", "data": [{"tmdb_id": r[0], "media_type": r[1], "title": r[2], "year": r[3], "poster_path": r[4], "status": r[5], "created_at": r[6], "request_count": r[7], "requested_by": r[8] or "未知"} for r in rows]}

# 🔥 V2.0 操作：支持完整版 MoviePilot 推送
@router.post("/api/manage/requests/action")
def manage_request_action(data: MediaRequestActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}

    new_status = 0
    if data.action == "approve":
        new_status = 1
        mp_url = cfg.get("moviepilot_url")
        mp_token = cfg.get("moviepilot_token")
        
        # 如果配置了 MP，调用自动订阅
        if mp_url and mp_token:
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("SELECT title, tmdb_id, media_type FROM media_requests WHERE tmdb_id=?", (data.tmdb_id,))
            req_info = c.fetchone(); conn.close()
            
            if req_info:
                try:
                    # 1. 清理 Token 及其两侧可能存在的单引号/空格
                    clean_token = mp_token.strip().strip("'").strip('"')
                    
                    # 2. 🔥 核心修复：强制加上末尾斜杠，避免 307 重定向
                    mp_api = f"{mp_url.rstrip('/')}/api/v1/subscribe/" 
                    
                    # 3. 准备数据负载 (类型对齐 MP 标准)
                    payload = {
                        "name": req_info[0], 
                        "tmdbid": int(req_info[1]), 
                        "type": req_info[2]  # 'movie' 或 'tv'
                    }
                    
                    # 4. 🔥 核心修复：移除 URL 里的 token，只保留标准的 Bearer Header
                    auth_headers = {
                        "Authorization": f"Bearer {clean_token}",
                        "Content-Type": "application/json"
                    }
                    
                    # 发送请求
                    res = requests.post(mp_api, json=payload, headers=auth_headers, timeout=10)
                    
                    if res.status_code != 200:
                        # 如果 MP 返回 401/403，通常是 Token 真的不对
                        return {"status": "error", "message": f"MoviePilot 拒绝请求: {res.text}"}
                except Exception as e:
                    return {"status": "error", "message": f"连接 MoviePilot 失败: {str(e)}"}

    elif data.action == "reject": new_status = 3
    elif data.action == "finish": new_status = 2
    elif data.action == "delete":
        execute_sql("DELETE FROM media_requests WHERE tmdb_id = ?", (data.tmdb_id,))
        execute_sql("DELETE FROM request_users WHERE tmdb_id = ?", (data.tmdb_id,))
        return {"status": "success", "message": "记录已彻底删除"}

    success, err_msg = execute_sql("UPDATE media_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE tmdb_id = ?", (new_status, data.tmdb_id))
    action_name = {"approve": "已送入 MoviePilot 下载队列" if cfg.get("moviepilot_url") else "已批准（手动模式）", "reject": "已残忍拒绝", "finish": "已标记入库"}.get(data.action, "操作成功")
    return {"status": "success", "message": action_name} if success else {"status": "error", "message": err_msg}