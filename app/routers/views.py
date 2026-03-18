import os
import requests
import ipaddress
import urllib.parse
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.core.config import cfg
from app.core.database import query_db
from app.core.security import get_client_ip
import logging
import random

logger = logging.getLogger("uvicorn")
templates = Jinja2Templates(directory="templates")
router = APIRouter()

APP_VERSION = os.environ.get("APP_VERSION", "1.2.0.80")
REQUEST_ASSET_VER = os.environ.get("REQUEST_ASSET_VER") or "20260315.6"

def _extract_host_ip(url: str):
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return None
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            import socket
            try:
                return socket.gethostbyname(host)
            except:
                return host
    except:
        return None

def _same_lan(ip_a: str, ip_b: str):
    try:
        a = ipaddress.ip_address(ip_a)
        b = ipaddress.ip_address(ip_b)
        if a.version != b.version:
            return False
        if a.is_loopback:
            # 访问 localhost 时，允许连接到内网 Emby
            return b.is_loopback or b.is_private
        if not (a.is_private and b.is_private):
            return False
        net = ipaddress.ip_network(f"{a}/24", strict=False)
        return b in net
    except:
        return False

def check_login(request: Request):
    user = request.session.get("user")
    if user and user.get("is_admin"): return True
    return False

@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
async def get_apple_touch_icon():
    icon_path = os.path.join("static", "img", "logo-app.png")
    if os.path.exists(icon_path): return FileResponse(icon_path)
    return RedirectResponse("/static/img/logo-light.png")

@router.get("/favicon.ico")
async def get_favicon():
    icon_path = os.path.join("static", "img", "logo-app.png")
    return FileResponse(icon_path)

@router.get("/manifest.json")
async def get_manifest():
    return JSONResponse({
        "name": "EmbyPulse 映迹",
        "short_name": "EmbyPulse",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#4f46e5",
        "icons": [{"src": "/static/img/logo-app.png", "sizes": "180x180", "type": "image/png"}, {"src": "/static/img/logo-app.png", "sizes": "512x512", "type": "image/png"}]
    })

from fastapi.responses import PlainTextResponse

@router.get("/request_manifest.json")
async def get_request_manifest():
    return JSONResponse({
        "name": "用户中心 - EmbyPulse",
        "short_name": "用户中心",
        "start_url": "/request",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": "#4f46e5",
        "icons": [{"src": "/static/img/logo-app.png", "sizes": "192x192", "type": "image/png"}, {"src": "/static/img/logo-app.png", "sizes": "512x512", "type": "image/png"}]
    })

@router.get("/sw.js")
async def get_service_worker():
    sw_content = "const CACHE_NAME='pulse-user-v1'; self.addEventListener('install', (e)=>{self.skipWaiting();}); self.addEventListener('activate', (e)=>{e.waitUntil(clients.claim());}); self.addEventListener('fetch', (e)=>{e.respondWith(fetch(e.request));});"
    return PlainTextResponse(content=sw_content, media_type="application/javascript")

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    host_url = (cfg.get("emby_host") or "").rstrip("/")
    public_url = (cfg.get("emby_public_url") or cfg.get("emby_external_url") or cfg.get("emby_public_host") or "").rstrip("/")
    client_ip = get_client_ip(request)
    host_ip = _extract_host_ip(host_url) if host_url else None
    use_local = bool(client_ip and host_ip and _same_lan(client_ip, host_ip))
    emby_url = host_url if use_local else (public_url or host_url)
    server_id = ""
    try:
        sys_res = requests.get(f"{cfg.get('emby_host')}/emby/System/Info?api_key={cfg.get('emby_api_key')}", timeout=2)
        if sys_res.status_code == 200: server_id = sys_res.json().get("Id", "")
    except: pass
    return templates.TemplateResponse("index.html", {"request": request, "active_page": "dashboard", "version": APP_VERSION, "emby_url": emby_url, "server_id": server_id})

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if check_login(request): return RedirectResponse("/")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "version": APP_VERSION,
        "admin_login_bg_url": cfg.get("admin_login_bg_url", ""),
        "admin_login_bg_pc": cfg.get("admin_login_bg_pc", "") or cfg.get("admin_login_bg_url", ""),
        "admin_login_bg_mobile": cfg.get("admin_login_bg_mobile", ""),
        "admin_login_bg_blur": cfg.get("admin_login_bg_blur", 12)
    })

@router.get("/invite/{code}", response_class=HTMLResponse)
async def invite_page(code: str, request: Request):
    invite = query_db("SELECT * FROM invitations WHERE code = ?", (code,), one=True)
    valid = False; days = 0
    if invite and invite['used_count'] < invite['max_uses']: valid = True; days = invite['days']
    client_url = cfg.get("client_download_url") or "https://emby.media/download.html"
    return templates.TemplateResponse("register.html", {"request": request, "code": code, "valid": valid, "days": days, "client_download_url": client_url, "version": APP_VERSION})

@router.get("/content", response_class=HTMLResponse)
async def content_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("content.html", {"request": request, "active_page": "content", "version": APP_VERSION})

@router.get("/details", response_class=HTMLResponse)
async def details_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("details.html", {"request": request, "active_page": "details", "version": APP_VERSION})

@router.get("/report", response_class=HTMLResponse)
async def report_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("report.html", {"request": request, "active_page": "report", "version": APP_VERSION})

@router.get("/bot", response_class=HTMLResponse)
async def bot_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("bot.html", {"request": request, "active_page": "bot", "version": APP_VERSION})

@router.get("/users_manage", response_class=HTMLResponse)
@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("users.html", {"request": request, "active_page": "users", "version": APP_VERSION})

@router.get("/settings", response_class=HTMLResponse)
@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("settings.html", {"request": request, "active_page": "settings", "version": APP_VERSION})

@router.get("/insight", response_class=HTMLResponse)
async def insight_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("insight.html", {"request": request, "active_page": "insight", "version": APP_VERSION})

@router.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("tasks.html", {"request": request, "active_page": "tasks", "version": APP_VERSION})

@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    user = request.session.get("user")
    if not user: return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("history.html", {"request": request, "user": user, "active_page": "history", "version": APP_VERSION})

@router.get("/request", response_class=HTMLResponse)
async def request_page(request: Request):
    req_user = request.session.get("req_user")
    return templates.TemplateResponse("request.html", {
        "request": request,
        "req_user": req_user,
        "version": APP_VERSION,
        "request_asset_ver": REQUEST_ASSET_VER,
        "request_login_bg_pc": cfg.get("request_login_bg_pc", "") or cfg.get("request_login_bg_url", ""),
        "request_login_bg_mobile": cfg.get("request_login_bg_mobile", ""),
        "request_login_bg_blur": cfg.get("request_login_bg_blur", 10)
    })

@router.get("/request_login", response_class=HTMLResponse)
async def request_login_page(request: Request):
    if request.session.get("req_user"): return RedirectResponse("/request")
    return templates.TemplateResponse("request_login.html", {
        "request": request,
        "version": APP_VERSION,
        "request_login_bg_url": cfg.get("request_login_bg_url", ""),
        "request_login_bg_pc": cfg.get("request_login_bg_pc", "") or cfg.get("request_login_bg_url", ""),
        "request_login_bg_mobile": cfg.get("request_login_bg_mobile", ""),
        "request_login_bg_blur": cfg.get("request_login_bg_blur", 10)
    })

@router.get("/requests_admin", response_class=HTMLResponse)
async def requests_admin_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("requests_admin.html", {"request": request, "active_page": "requests_admin", "version": APP_VERSION})

@router.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("clients.html", {"request": request, "active_page": "clients", "version": APP_VERSION})

@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("about.html", {"request": request, "active_page": "about", "version": APP_VERSION})

@router.get("/gaps", response_class=HTMLResponse)
async def gaps_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("gaps.html", {"request": request, "active_page": "gaps", "version": APP_VERSION})

@router.get("/risk", response_class=HTMLResponse)
async def risk_control_page(request: Request):
    """风险管控大盘页面"""
    if not check_login(request): 
        return RedirectResponse("/login")
    # 🔥 必须补上 version 和 active_page，否则前端无法渲染版本和高亮
    return templates.TemplateResponse("risk.html", {
        "request": request, 
        "title": "风险管控中心",
        "active_page": "risk",  # 确保侧边栏亮起
        "version": APP_VERSION  # 确保版本号显示 (假设你定义的变量名是 APP_VERSION)
    })

@router.get("/api/wallpaper")
async def get_wallpaper():
    fallback_wallpapers = [
        {"url": "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1925&auto=format&fit=crop", "title": "电影之夜 - Unsplash"},
        {"url": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=2070&auto=format&fit=crop", "title": "家庭影院 - Unsplash"}
    ]
    tmdb_key = cfg.get("tmdb_api_key"); proxy = cfg.get("proxy_url"); proxies = {"https": proxy, "http": proxy} if proxy else None
    if tmdb_key:
        try:
            res = requests.get(f"https://api.themoviedb.org/3/trending/all/day?api_key={tmdb_key}&language=zh-CN", proxies=proxies, timeout=3)
            if res.status_code == 200:
                valid_items = [item for item in res.json().get("results", []) if item.get("backdrop_path")]
                if valid_items:
                    item = random.choice(valid_items)
                    title = item.get("title") or item.get("name") or "TMDB 热门"
                    url = f"https://image.tmdb.org/t/p/original{item['backdrop_path']}"
                    return {"status": "success", "url": url, "title": f"今日热门: {title}"}
        except: pass
    item = random.choice(fallback_wallpapers)
    return {"status": "success", "url": item["url"], "title": item["title"]}

@router.get("/dedupe", response_class=HTMLResponse)
async def dedupe_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("dedupe.html", {
        "request": request, 
        "active_page": "dedupe", 
        "version": APP_VERSION  # 🔥 保持与项目全局版本号完全一致
    })
