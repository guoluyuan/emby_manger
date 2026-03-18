from fastapi import APIRouter, Request
from app.schemas.models import SettingsModel, SetupModel
from app.core.config import cfg, save_config
import requests
import ipaddress
import socket
import urllib.parse

router = APIRouter()

def normalize_host(host: str) -> str:
    h = (host or "").strip()
    if not h:
        return ""
    if not h.startswith("http://") and not h.startswith("https://"):
        h = "http://" + h
    return h.rstrip("/")

def _normalize_url_with_default(url: str, default_scheme: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urllib.parse.urlparse(u)
    if not parsed.scheme:
        u = f"{default_scheme}://{u}"
    return u

def _resolve_host_ips(host: str):
    try:
        infos = socket.getaddrinfo(host, None)
        ips = []
        for info in infos:
            ip_str = info[4][0]
            if ip_str not in ips:
                ips.append(ip_str)
        return ips
    except Exception:
        return []

def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
    except Exception:
        return False

def _extract_mp_save_path(conf: dict) -> str:
    if not isinstance(conf, dict):
        return ""
    for key in ("save_path", "savepath", "download_path", "download_dir", "download_dir_path", "path", "tv_path", "movie_path"):
        val = conf.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""

def is_configured() -> bool:
    host = (cfg.get("emby_host") or "").strip()
    key = (cfg.get("emby_api_key") or "").strip()
    return bool(host and key)

@router.get("/api/setup/status")
def setup_status():
    return {"status": "success", "configured": is_configured()}

@router.post("/api/setup")
def setup_system(data: SetupModel, request: Request):
    if is_configured() and not request.session.get("user"):
        return {"status": "error", "message": "系统已配置，禁止重复初始化"}

    server_type = getattr(data, "server_type", "emby")
    host = normalize_host(data.emby_host)
    api_key = (data.emby_api_key or "").strip()
    if not host or not api_key:
        return {"status": "error", "message": "请填写完整的 Emby Host 和 API Key"}

    url = f"{host}/System/Info" if server_type == "jellyfin" else f"{host}/emby/System/Info"
    headers = {"Authorization": f'MediaBrowser Token="{api_key}"'} if server_type == "jellyfin" else {"X-Emby-Token": api_key}

    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code != 200:
            return {"status": "error", "message": "无法连接媒体服务器，请检查地址或 API Key"}
    except:
        return {"status": "error", "message": "服务器地址无法访问"}

    cfg.update({
        "server_type": server_type,
        "emby_host": host,
        "emby_api_key": api_key
    }, save=False)
    save_config()
    return {"status": "success", "message": "初始化完成"}

@router.get("/api/settings")
def api_get_settings(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    return {
        "status": "success",
        "data": {
            "server_type": cfg.get("server_type", "emby"), 
            "emby_host": cfg.get("emby_host"),
            "emby_api_key": cfg.get("emby_api_key"),
            "tmdb_api_key": cfg.get("tmdb_api_key"),
            "proxy_url": cfg.get("proxy_url"),
            "webhook_token": cfg.get("webhook_token", ""),
            "hidden_users": cfg.get("hidden_users") or [],
            "cors_origins": cfg.get("cors_origins") or [],
            "emby_public_url": cfg.get("emby_public_url", ""),
            "user_public_url": cfg.get("user_public_url", ""),
            "user_lan_url": cfg.get("user_lan_url", ""),
            "admin_login_bg_url": cfg.get("admin_login_bg_url", ""),
            "request_login_bg_url": cfg.get("request_login_bg_url", ""),
            "admin_login_bg_pc": cfg.get("admin_login_bg_pc", ""),
            "admin_login_bg_mobile": cfg.get("admin_login_bg_mobile", ""),
            "request_login_bg_pc": cfg.get("request_login_bg_pc", ""),
            "request_login_bg_mobile": cfg.get("request_login_bg_mobile", ""),
            "admin_login_bg_blur": cfg.get("admin_login_bg_blur", 12),
            "request_login_bg_blur": cfg.get("request_login_bg_blur", 10),
            "default_invite_template_user_id": cfg.get("default_invite_template_user_id", ""),
            "welcome_message": cfg.get("welcome_message", ""),
            "client_download_url": cfg.get("client_download_url", ""),
            "moviepilot_url": cfg.get("moviepilot_url", ""),
            "moviepilot_token": cfg.get("moviepilot_token", ""),
            "moviepilot_downloader": cfg.get("moviepilot_downloader", ""),
            "moviepilot_save_path": cfg.get("moviepilot_save_path", ""),
            "pulse_url": cfg.get("pulse_url", ""),
            "playback_data_mode": cfg.get("playback_data_mode", "sqlite"), # 🔥 就是这里之前少了个逗号
            "notify_user_login": cfg.get("notify_user_login", False),
            "notify_item_deleted": cfg.get("notify_item_deleted", False),
            "disable_update_check": cfg.get("disable_update_check", False)
        }
    }

@router.post("/api/settings")
def api_update_settings(data: SettingsModel, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    
    server_type = getattr(data, "server_type", "emby")
    url = f"{data.emby_host}/System/Info" if server_type == "jellyfin" else f"{data.emby_host}/emby/System/Info"
    headers = {"Authorization": f'MediaBrowser Token="{data.emby_api_key}"'} if server_type == "jellyfin" else {"X-Emby-Token": data.emby_api_key}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code != 200:
            return {"status": "error", "message": "无法连接媒体服务器，请检查地址或 API Key"}
    except:
        return {"status": "error", "message": "服务器地址无法访问"}

    cfg.update({
        "server_type": server_type,
        "emby_host": data.emby_host,
        "emby_api_key": data.emby_api_key,
        "tmdb_api_key": data.tmdb_api_key,
        "proxy_url": data.proxy_url,
        "webhook_token": data.webhook_token,
        "hidden_users": data.hidden_users,
        "cors_origins": data.cors_origins or [],
        "emby_public_url": data.emby_public_url,
        "user_public_url": getattr(data, "user_public_url", ""),
        "user_lan_url": getattr(data, "user_lan_url", ""),
        "admin_login_bg_url": getattr(data, "admin_login_bg_url", ""),
        "request_login_bg_url": getattr(data, "request_login_bg_url", ""),
        "admin_login_bg_pc": getattr(data, "admin_login_bg_pc", ""),
        "admin_login_bg_mobile": getattr(data, "admin_login_bg_mobile", ""),
        "request_login_bg_pc": getattr(data, "request_login_bg_pc", ""),
        "request_login_bg_mobile": getattr(data, "request_login_bg_mobile", ""),
        "admin_login_bg_blur": getattr(data, "admin_login_bg_blur", 12),
        "request_login_bg_blur": getattr(data, "request_login_bg_blur", 10),
        "default_invite_template_user_id": getattr(data, "default_invite_template_user_id", ""),
        "welcome_message": data.welcome_message,
        "client_download_url": data.client_download_url,
        "moviepilot_url": data.moviepilot_url,
        "moviepilot_token": data.moviepilot_token,
        "moviepilot_downloader": getattr(data, "moviepilot_downloader", ""),
        "moviepilot_save_path": getattr(data, "moviepilot_save_path", ""),
        "pulse_url": data.pulse_url,
        "playback_data_mode": getattr(data, "playback_data_mode", "sqlite"),
        "notify_user_login": getattr(data, "notify_user_login", False),
        "notify_item_deleted": getattr(data, "notify_item_deleted", False),
        "disable_update_check": getattr(data, "disable_update_check", False)
    }, save=False)
    
    save_config()
    
    return {"status": "success", "message": "配置已保存"}

@router.post("/api/settings/test_tmdb")
def api_test_tmdb(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    if not tmdb_key: return {"status": "error", "message": "未配置 TMDB API Key"}
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        url = f"https://api.themoviedb.org/3/authentication/token/new?api_key={tmdb_key}"
        res = requests.get(url, proxies=proxies, timeout=10)
        if res.status_code == 200: return {"status": "success", "message": "TMDB 连接成功"}
        return {"status": "error", "message": f"连接失败: {res.status_code}"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/settings/test_mp")
async def test_moviepilot(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}
    data = await request.json()
    mp_url = data.get("mp_url", "").strip().rstrip('/')
    mp_token = data.get("mp_token", "").strip().strip("'\"")
    if not mp_url or not mp_token: return {"status": "error", "message": "请填写 MoviePilot 信息"}
    try:
        res = requests.get(f"{mp_url}/api/v1/site/", headers={"X-API-KEY": mp_token, "User-Agent": "Mozilla/5.0"}, timeout=8)
        if res.status_code == 200: return {"status": "success", "message": "🎉 MoviePilot 连通测试成功！"}
        elif res.status_code in [401, 403]: return {"status": "error", "message": "❌ Token 认证失败"}
        else: return {"status": "success", "message": f"⚠️ 服务器连通(状态码: {res.status_code})"}
    except: return {"status": "error", "message": f"❌ 无法连接到 MoviePilot"}

@router.post("/api/settings/mp_downloaders")
async def api_mp_downloaders(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}
    data = await request.json()
    mp_url = (data.get("mp_url") or cfg.get("moviepilot_url") or "").strip().rstrip('/')
    mp_token = (data.get("mp_token") or cfg.get("moviepilot_token") or "").strip().strip("'\"")
    if not mp_url or not mp_token:
        return {"status": "error", "message": "请先填写 MoviePilot 信息"}
    try:
        res = requests.get(
            f"{mp_url}/api/v1/system/setting/Downloaders",
            headers={"X-API-KEY": mp_token, "User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if res.status_code != 200:
            return {"status": "error", "message": f"下载器配置拉取失败 (HTTP {res.status_code})"}
        payload = res.json()
        if not payload.get("success", False):
            return {"status": "error", "message": payload.get("message") or "下载器配置拉取失败"}
        value = payload.get("data", {}).get("value", []) or []
        downloaders = []
        for d in value:
            conf = d.get("config") or {}
            downloaders.append({
                "name": d.get("name", ""),
                "type": d.get("type", ""),
                "default": bool(d.get("default", False)),
                "enabled": bool(d.get("enabled", True)),
                "save_path": _extract_mp_save_path(conf)
            })
        return {"status": "success", "data": {"downloaders": downloaders}}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/api/settings/test_invite_url")
async def test_invite_url(request: Request):
    if not request.session.get("user"): return {"status": "error", "message": "权限不足"}
    data = await request.json()
    mode = (data.get("mode") or "").strip().lower()
    raw_url = data.get("url", "")
    if mode not in ("public", "lan"):
        return {"status": "error", "message": "无效的测试类型"}
    default_scheme = "https" if mode == "public" else "http"
    base = _normalize_url_with_default(raw_url, default_scheme).rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if not parsed.scheme or not parsed.hostname:
        return {"status": "error", "message": "请填写正确的访问地址"}
    if mode == "public" and parsed.scheme != "https":
        return {"status": "error", "message": "公网地址必须使用 https"}
    if mode == "lan" and parsed.scheme not in ("http", "https"):
        return {"status": "error", "message": "局域网地址仅支持 http/https"}

    cfg_url = cfg.get("user_public_url") if mode == "public" else cfg.get("user_lan_url")
    if cfg_url:
        cfg_parsed = urllib.parse.urlparse(_normalize_url_with_default(cfg_url, default_scheme))
        if cfg_parsed.hostname and cfg_parsed.hostname.lower() != parsed.hostname.lower():
            return {"status": "error", "message": "地址与系统配置不一致，请先在系统设置中保存"}

    host = parsed.hostname
    ips = []
    any_private = False
    all_private = False
    any_public = False

    # IP 直连
    try:
        ip_literal = ipaddress.ip_address(host)
        ips = [host]
        any_private = _is_private_ip(host)
        all_private = any_private
        any_public = not any_private
    except Exception:
        if host.lower() in ("localhost",):
            ips = ["127.0.0.1"]
            any_private = True
            all_private = True
            any_public = False
        else:
            ips = _resolve_host_ips(host)
            if ips:
                any_private = any(_is_private_ip(ip) for ip in ips)
                all_private = all(_is_private_ip(ip) for ip in ips)
                any_public = any(not _is_private_ip(ip) for ip in ips)
            else:
                # 公网模式允许继续尝试（避免 DNS 误判）；局域网仍要求可解析
                if mode == "lan":
                    return {"status": "error", "message": "无法解析域名"}

    if mode == "public":
        if not any_public and all_private:
            # 若与系统配置的公网入口一致，则放行（用于内网解析/分流场景）
            if not (cfg_url and urllib.parse.urlparse(_normalize_url_with_default(cfg_url, "https")).hostname and
                    urllib.parse.urlparse(_normalize_url_with_default(cfg_url, "https")).hostname.lower() == host.lower()):
                return {"status": "error", "message": "公网地址解析为内网 IP，已拒绝"}
    else:
        if not all_private:
            return {"status": "error", "message": "局域网地址必须是内网 IP 或 localhost"}

    if not base:
        return {"status": "error", "message": "请填写访问地址"}
    target = f"{base}/request"
    try:
        res = requests.get(target, timeout=5, allow_redirects=True, headers={"User-Agent": "EmbyPulse Invite Test"})
        if res.status_code == 200:
            return {"status": "success", "message": "连接成功，可正常访问"}
        return {"status": "error", "message": f"连接失败: HTTP {res.status_code}"}
    except Exception as e:
        return {"status": "error", "message": f"连接失败: {e}"}

@router.post("/api/settings/fix_db")
def api_fix_db(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    from app.core.database import DB_PATH
    import sqlite3
    import os
    if not os.path.exists(DB_PATH): return {"status": "error", "message": "数据库不存在"}
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        results = []

        try: c.execute("SELECT 1 FROM PlaybackActivity LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS PlaybackActivity (Id INTEGER PRIMARY KEY AUTOINCREMENT, UserId TEXT, UserName TEXT, ItemId TEXT, ItemName TEXT, PlayDuration INTEGER, DateCreated DATETIME DEFAULT CURRENT_TIMESTAMP, Client TEXT, DeviceName TEXT)''')
            results.append("已修复: 播放活动主表")

        try: c.execute("SELECT 1 FROM users_meta LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS users_meta (user_id TEXT PRIMARY KEY, expire_date TEXT, note TEXT, created_at TEXT)''')
            results.append("已修复: 用户元数据表")

        try: 
            c.execute("SELECT 1 FROM invitations LIMIT 1")
            try: c.execute("SELECT template_user_id FROM invitations LIMIT 1")
            except sqlite3.OperationalError:
                c.execute("ALTER TABLE invitations ADD COLUMN template_user_id TEXT")
                results.append("已升级: 邀请码模板字段")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS invitations (code TEXT PRIMARY KEY, days INTEGER, used_count INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 1, created_at TEXT, used_at DATETIME, used_by TEXT, status INTEGER DEFAULT 0, template_user_id TEXT)''')
            results.append("已修复: 邀请码表")

        try: c.execute("SELECT 1 FROM tv_calendar_cache LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS tv_calendar_cache (id TEXT PRIMARY KEY, series_id TEXT, season INTEGER, episode INTEGER, air_date TEXT, status TEXT, data_json TEXT)''')
            results.append("已修复: 追剧日历缓存表")

        try: c.execute("SELECT 1 FROM media_requests LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS media_requests (tmdb_id INTEGER, media_type TEXT, title TEXT, year TEXT, poster_path TEXT, status INTEGER DEFAULT 0, season INTEGER DEFAULT 0, reject_reason TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (tmdb_id, season))''')
            results.append("已修复: 求片主表")

        try: c.execute("SELECT 1 FROM request_users LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS request_users (id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER, user_id TEXT, username TEXT, season INTEGER DEFAULT 0, requested_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(tmdb_id, user_id, season))''')
            results.append("已修复: 求片关联表")

        try: c.execute("SELECT 1 FROM insight_ignores LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS insight_ignores (item_id TEXT PRIMARY KEY, item_name TEXT, ignored_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            results.append("已修复: 盘点忽略表")

        try: c.execute("SELECT 1 FROM gap_records LIMIT 1")
        except sqlite3.OperationalError:
            c.execute('''CREATE TABLE IF NOT EXISTS gap_records (id INTEGER PRIMARY KEY AUTOINCREMENT, series_id TEXT, series_name TEXT, season_number INTEGER, episode_number INTEGER, status INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(series_id, season_number, episode_number))''')
            results.append("已修复: 缺集记录表")

        conn.commit()
        conn.close()
        
        return {"status": "success", "message": f"修复完成: {', '.join(results)}" if results else "数据库8大核心表结构完整健康，无需修复！"}
    except Exception as e: 
        return {"status": "error", "message": f"修复严重错误: {e}"}
