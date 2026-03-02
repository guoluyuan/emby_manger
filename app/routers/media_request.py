from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
import requests
import sqlite3
import io
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

# 🔥 V2.0 搜索：TMDB + 本地数据库查重 + Emby穿透查重
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
            
            tmdb_ids = [str(item['id']) for item in data.get("results", []) if item.get("media_type") in ["movie", "tv"]]
            local_status_map = {}
            emby_exists_set = set()

            if tmdb_ids:
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                placeholders = ','.join('?' * len(tmdb_ids))
                c.execute(f"SELECT tmdb_id, status FROM media_requests WHERE tmdb_id IN ({placeholders})", tuple(tmdb_ids))
                for row in c.fetchall(): local_status_map[str(row[0])] = row[1]
                conn.close()

                emby_host = cfg.get("emby_host"); emby_key = cfg.get("emby_api_key")
                if emby_host and emby_key:
                    provider_query = ",".join([f"tmdb.{tid}" for tid in tmdb_ids])
                    emby_search_url = f"{emby_host}/emby/Items?AnyProviderIdEquals={provider_query}&Recursive=true&IncludeItemTypes=Movie,Series&Fields=ProviderIds&api_key={emby_key}"
                    try:
                        emby_res = requests.get(emby_search_url, timeout=5)
                        if emby_res.status_code == 200:
                            for e_item in emby_res.json().get("Items", []):
                                tid = e_item.get("ProviderIds", {}).get("Tmdb")
                                if tid: emby_exists_set.add(str(tid))
                    except: pass

            for item in data.get("results", []):
                if item.get("media_type") not in ["movie", "tv"]: continue
                tid_str = str(item.get("id"))
                title = item.get("title") or item.get("name")
                year_str = item.get("release_date") or item.get("first_air_date") or ""
                poster = f"https://image.tmdb.org/t/p/w500{item.get('poster_path')}" if item.get("poster_path") else ""
                
                final_status = 2 if tid_str in emby_exists_set else local_status_map.get(tid_str, -1)

                results.append({
                    "tmdb_id": item.get("id"), "media_type": item.get("media_type"),
                    "title": title, "year": year_str[:4] if year_str else "未知",
                    "poster_path": poster, "overview": item.get("overview", ""),
                    "vote_average": round(item.get("vote_average", 0), 1),
                    "local_status": final_status 
                })
            return {"status": "success", "data": results}
        return {"status": "error", "message": "TMDB API 响应异常"}
    except Exception as e: return {"status": "error", "message": f"网络代理或请求错误: {str(e)}"}

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
        if existing[0] == 2: conn.close(); return {"status": "error", "message": "这部片子已经入库啦！不用重复求片。"}

    success, err_msg = execute_sql("INSERT INTO request_users (tmdb_id, user_id, username) VALUES (?, ?, ?)",
                                   (data.tmdb_id, user.get("Id"), user.get("Name")))
    conn.close()

    if not success:
        if "UNIQUE" in err_msg: return {"status": "error", "message": "你已经提交过啦，不用重复点 +1"}
        return {"status": "error", "message": f"写入失败: {err_msg}"}

    type_cn = "🎬 电影" if data.media_type == "movie" else "📺 剧集"
    overview_text = data.overview if data.overview else "暂无剧情简介"
    if len(overview_text) > 120: overview_text = overview_text[:115] + "..."

    bot_msg = (f"🔔 <b>新求片订单提醒</b>\n\n"
               f"👤 <b>求片人</b>：{user.get('Name')}\n"
               f"📌 <b>片名</b>：{data.title} ({data.year})\n"
               f"🏷️ <b>类型</b>：{type_cn}\n\n"
               f"📝 <b>剧情简介：</b>\n{overview_text}")
    
    admin_url = cfg.get("pulse_url") or str(request.base_url).rstrip('/')
    keyboard = {"inline_keyboard": [[{"text": "🍿 前往后台一键审批", "url": f"{admin_url}/requests_admin"}]]}
    
    photo_data = REPORT_COVER_URL
    if data.poster_path:
        try:
            proxy = cfg.get("proxy_url"); proxies = {"http": proxy, "https": proxy} if proxy else None
            img_res = requests.get(data.poster_path, proxies=proxies, timeout=15)
            if img_res.status_code == 200: photo_data = io.BytesIO(img_res.content)
        except: pass

    bot.send_photo("sys_notify", photo_data, bot_msg, reply_markup=keyboard, platform="all")
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

# 🔥 V2.0 操作修复：强制补齐斜杠 + 清洗 Token + 类型对齐
@router.post("/api/manage/requests/action")
def manage_request_action(data: MediaRequestActionModel, request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}

    new_status = 0
    if data.action == "approve":
        new_status = 1
        mp_url = cfg.get("moviepilot_url")
        mp_token = cfg.get("moviepilot_token")
        
        if mp_url and mp_token:
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("SELECT title, tmdb_id, media_type, year FROM media_requests WHERE tmdb_id=?", (data.tmdb_id,))
            req_info = c.fetchone(); conn.close()
            
            if req_info:
                try:
                    # 1. 强力清洗 Token：去除空格、首尾单双引号
                    clean_token = mp_token.strip().strip("'").strip('"')
                    
                    # 2. 补齐末尾斜杠，绕过重定向，直达目的地
                    mp_api = f"{mp_url.rstrip('/')}/api/v1/subscribe/" 
                    
                    # 3. Payload 严格对齐 MP V2 规范
                    payload = {
                        "name": req_info[0], 
                        "tmdbid": int(req_info[1]), 
                        "year": str(req_info[3]) if req_info[3] else "",
                        "type": "movie" if req_info[2] == "movie" else "tv"
                    }
                    
                    # 4. 规范认证头
                    headers = {
                        "Authorization": f"Bearer {clean_token}",
                        "Content-Type": "application/json"
                    }
                    
                    res = requests.post(mp_api, json=payload, headers=headers, timeout=10)
                    
                    if res.status_code != 200:
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
    action_name = {"approve": "已送入 MoviePilot 下载队列" if cfg.get("moviepilot_url") else "已批准", "reject": "已残忍拒绝", "finish": "已标记入库"}.get(data.action, "操作成功")
    return {"status": "success", "message": action_name} if success else {"status": "error", "message": err_msg}