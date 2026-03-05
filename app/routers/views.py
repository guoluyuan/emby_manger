import os
import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.core.config import cfg
from app.core.database import query_db
import logging

logger = logging.getLogger("uvicorn")
templates = Jinja2Templates(directory="templates")
router = APIRouter()

# 🔥 获取应用版本号
APP_VERSION = os.environ.get("APP_VERSION", "1.2.0.Dev")

def check_login(request: Request):
    user = request.session.get("user")
    if user and user.get("is_admin"):
        return True
    return False

# ==========================================================
# 📱 核心修复：iOS & 安卓 桌面图标与 PWA 路由
# ==========================================================

@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
async def get_apple_touch_icon():
    """ 专门喂给 iOS Safari 的桌面图标 """
    icon_path = os.path.join("static", "img", "logo-app.png")
    if os.path.exists(icon_path):
        return FileResponse(icon_path)
    return RedirectResponse("/static/img/logo-light.png")

@router.get("/favicon.ico")
async def get_favicon():
    """ 兼容旧版浏览器和安卓书签图标 """
    icon_path = os.path.join("static", "img", "logo-app.png")
    return FileResponse(icon_path)

@router.get("/manifest.json")
async def get_manifest():
    """ 为安卓提供的 PWA 配置文件，确保添加到桌面时名称和图标正确 """
    return JSONResponse({
        "name": "EmbyPulse 映迹",
        "short_name": "EmbyPulse",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#4f46e5",
        "icons": [
            {
                "src": "/static/img/logo-app.png",
                "sizes": "180x180",
                "type": "image/png"
            },
            {
                "src": "/static/img/logo-app.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    })

# ==========================================================
# 🏠 基础页面路由
# ==========================================================

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    
    emby_url = cfg.get("emby_public_url") or cfg.get("emby_public_host") or cfg.get("emby_host") or ""
    if emby_url.endswith('/'): emby_url = emby_url[:-1]
    
    server_id = ""
    try:
        sys_res = requests.get(f"{cfg.get('emby_host')}/emby/System/Info?api_key={cfg.get('emby_api_key')}", timeout=2)
        if sys_res.status_code == 200:
            server_id = sys_res.json().get("Id", "")
    except: pass

    return templates.TemplateResponse("index.html", {
        "request": request, 
        "active_page": "dashboard", 
        "version": APP_VERSION,
        "emby_url": emby_url,
        "server_id": server_id
    })

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if check_login(request): return RedirectResponse("/")
    return templates.TemplateResponse("login.html", {"request": request, "version": APP_VERSION})

@router.get("/invite/{code}", response_class=HTMLResponse)
async def invite_page(code: str, request: Request):
    invite = query_db("SELECT * FROM invitations WHERE code = ?", (code,), one=True)
    valid = False; days = 0
    if invite and invite['used_count'] < invite['max_uses']:
        valid = True; days = invite['days']
    
    client_url = cfg.get("client_download_url") or "https://emby.media/download.html"
    
    return templates.TemplateResponse("register.html", {
        "request": request, "code": code, "valid": valid, "days": days, 
        "client_download_url": client_url, "version": APP_VERSION
    })

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

# ================= 独立求片门户 (普通用户前台) =================
@router.get("/request", response_class=HTMLResponse)
async def request_page(request: Request):
    req_user = request.session.get("req_user")
    return templates.TemplateResponse("request.html", {
        "request": request, 
        "req_user": req_user,
        "version": APP_VERSION
    })

@router.get("/request_login", response_class=HTMLResponse)
async def request_login_page(request: Request):
    if request.session.get("req_user"):
        return RedirectResponse("/request")
    return templates.TemplateResponse("request_login.html", {
        "request": request, 
        "version": APP_VERSION
    })

# ================= 独立求片门户 (服主审核后台) =================
@router.get("/requests_admin", response_class=HTMLResponse)
async def requests_admin_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("requests_admin.html", {
        "request": request, 
        "active_page": "requests_admin",
        "version": APP_VERSION
    })

@router.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("clients.html", {"request": request, "active_page": "clients", "version": APP_VERSION})

@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    if not check_login(request): return RedirectResponse("/login")
    return templates.TemplateResponse("about.html", {"request": request, "active_page": "about", "version": APP_VERSION})

import random

# ==========================================================
# 🖼️ 登录页动态壁纸引擎
# ==========================================================
@router.get("/api/wallpaper")
async def get_wallpaper():
    """ 
    获取登录页的动态壁纸
    优先尝试获取 TMDB 今日热门剧集/电影的高清背景图，
    若失败则返回内置的电影质感保底海报。
    """
    # 高清保底海报库 (电影/极客氛围)
    fallback_wallpapers = [
        {"url": "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1925&auto=format&fit=crop", "title": "电影之夜 - Unsplash"},
        {"url": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=2070&auto=format&fit=crop", "title": "家庭影院 - Unsplash"},
        {"url": "https://images.unsplash.com/photo-1505686994434-e3cc5abf1330?q=80&w=2073&auto=format&fit=crop", "title": "放映机 - Unsplash"}
    ]
    
    tmdb_key = cfg.get("tmdb_api_key")
    proxy = cfg.get("proxy_url")
    proxies = {"https": proxy, "http": proxy} if proxy else None
    
    # 1. 尝试从 TMDB 抓取最新热门背景图
    if tmdb_key:
        try:
            res = requests.get(
                f"https://api.themoviedb.org/3/trending/all/day?api_key={tmdb_key}&language=zh-CN", 
                proxies=proxies, 
                timeout=3
            )
            if res.status_code == 200:
                results = res.json().get("results", [])
                # 过滤出有背景大图的数据
                valid_items = [item for item in results if item.get("backdrop_path")]
                
                if valid_items:
                    # 随机抽取一张，增加新鲜感
                    item = random.choice(valid_items)
                    title = item.get("title") or item.get("name") or "TMDB 热门"
                    url = f"https://image.tmdb.org/t/p/original{item['backdrop_path']}"
                    return {"status": "success", "url": url, "title": f"今日热门: {title}"}
        except Exception as e:
            logger.warning(f"获取 TMDB 壁纸失败，使用备用图: {e}")

    # 2. 如果 TMDB 没配、网络不通，返回内置保底海报
    item = random.choice(fallback_wallpapers)
    return {"status": "success", "url": item["url"], "title": item["title"]}