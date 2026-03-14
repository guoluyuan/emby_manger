import os
import asyncio
import threading
import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from app.routers import dedupe
from app.routers import notify_rules
from app.routers import system_tools

# 🔥 修复在这里：完整的引入语句
from app.services.risk_service import start_risk_monitor

from app.routers import insight
from app.core.config import PORT, SECRET_KEY, CONFIG_DIR, FONT_DIR, cfg
from app.core.database import init_db
from app.services.bot_service import bot
from app.routers import media_request
# 🔥 引入所有路由
from app.routers import views, auth, users, stats, bot as bot_router, system, proxy, report, webhook, insight, tasks, history, calendar, search, clients, gaps, risk,notifications

# 初始化目录和数据库
if not os.path.exists("static"): os.makedirs("static")
if not os.path.exists("templates"): os.makedirs("templates")
if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)
if not os.path.exists(FONT_DIR): os.makedirs(FONT_DIR)
init_db()

# ==============================================================================
# 🔥 真·物理隔离：10308 专属 ASGI 独立引擎 (无视任何反代环境)
# ==============================================================================
async def user_portal_app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    elif scope["type"] == "http":
        scope["user_portal"] = True
        path = scope.get("path", "")
        
        # 强制送去求片中心
        if path == "/":
            scope["path"] = "/request"
            scope["raw_path"] = b"/request"
            path = "/request"
            
        # 铁血隔离白名单：仅放行用户端必需路径，避免后台接口泄露
        allowed_prefixes = (
            "/request",
            "/request_login",
            "/invite",
            "/static",
            "/api/register",
            "/api/requests",
            "/api/stats",
            "/api/proxy",
            "/api/captcha",
            "/api/wallpaper"
        )
        allowed_exact = ("/favicon.ico", "/manifest.json", "/request_manifest.json", "/sw.js")
        if not (path in allowed_exact or path.startswith(allowed_prefixes)):
            async def send_404():
                await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/html; charset=utf-8")]})
                await send({"type": "http.response.body", "body": "<h1>404 Not Found</h1><p>非法越界，后台管理界面已被物理阻断。</p>".encode("utf-8")})
            return await send_404()
            
        await app(scope, receive, send)
    else:
        await app(scope, receive, send)

def start_10308_server():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try: sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError: pass
        sock.bind(('0.0.0.0', 10308))
        sock.listen(100)
    except OSError:
        return

    import uvicorn
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # 错误日志才会打印，保证前台安静
    config = uvicorn.Config(app=user_portal_app, log_level="error")
    
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    try:
        loop.run_until_complete(server.serve(sockets=[sock]))
    except BaseException:
        pass

# ==============================================================================
# 🔥 定制化纯中文启动面板 (一口气输出完毕防插队)
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot.start()
    # 唤醒 10308 独立守护引擎
    threading.Thread(target=start_10308_server, daemon=True).start()
    # 🔥 唤醒风控天眼
    start_risk_monitor()
    
    # 🔥 拿掉 sleep，把面板一口气打印完，绝对整齐！
    print("\n" + "="*55)
    print("🚀 [系统启动] EmbyPulse 双引擎初始化成功！")
    print("🤖 [消息通知] 机器人模块已就绪")
    print("👁️ [风险管控] 并发天眼已开启，时刻监控越界行为！")
    print(f"🌍 [核心后台] 管理员仪表盘运行在端口: {PORT}")
    print("🎈 [用户中心] 独立求片门户运行在端口: 10308")
    print("✅ [系统状态] 物理隔离架构已启动，安全防护中！")
    print("="*55 + "\n")
    
    yield
    
    print("\n" + "="*55)
    print("🛑 [系统关闭] 正在停止 EmbyPulse 服务...")
    bot.stop()
    print("💤 [系统关闭] 所有服务已安全退出。")
    print("="*55 + "\n")
# ==============================================================================

app = FastAPI(lifespan=lifespan)

# ------------------------------------------------------------------------------
# 通用安全头 + 压缩
# ------------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

# CSRF protection for session-authenticated requests
class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            session = request.session if hasattr(request, "session") else {}
            if session and (session.get("user") or session.get("req_user")):
                token = request.headers.get("x-csrf-token")
                if not token or token != session.get("csrf_token"):
                    return JSONResponse(status_code=403, content={"status": "error", "message": "CSRF token invalid"})
        return await call_next(request)

# Split admin/user portals by port: block user pages on admin port (10307).
class AdminPortalSplitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, user_port: int = 10308):
        super().__init__(app)
        self.user_port = user_port
        self.block_paths = ("/request", "/request_login", "/request_manifest.json")

    async def dispatch(self, request: Request, call_next):
        if not request.scope.get("user_portal"):
            path = request.url.path
            if path in self.block_paths or path.startswith("/request/") or path.startswith("/invite/") or path == "/invite":
                host = request.url.hostname
                if not host:
                    raw_host = (request.headers.get("host") or "").split(":")[0]
                    host = raw_host if raw_host else None
                if host:
                    target = f"{request.url.scheme}://{host}:{self.user_port}{path}"
                    if request.url.query:
                        target += f"?{request.url.query}"
                    return RedirectResponse(url=target, status_code=307)
        return await call_next(request)

# 中间件
app.add_middleware(AdminPortalSplitMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400*7)
cors_env = os.getenv("CORS_ORIGINS", "")
cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
if not cors_origins:
    cors_origins = cfg.get("cors_origins") or []
if not cors_origins:
    cors_origins = ["http://localhost", "http://127.0.0.1"]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# 压缩 + 安全头放在外层
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)

try:
    from brotli_asgi import BrotliMiddleware
    app.add_middleware(BrotliMiddleware, quality=4)
except Exception:
    pass

# 静态文件
class CacheControlStaticFiles(StaticFiles):
    def __init__(self, *args, cache_control: str = "public, max-age=604800", **kwargs):
        super().__init__(*args, **kwargs)
        self.cache_control = cache_control

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = self.cache_control
        return response

app.mount("/static", CacheControlStaticFiles(directory="static"), name="static")

# 注册路由
app.include_router(views.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(stats.router)
app.include_router(bot_router.router)
app.include_router(system.router)
app.include_router(proxy.router)
app.include_router(report.router)
app.include_router(insight.router)
app.include_router(webhook.router)
app.include_router(tasks.router)
app.include_router(history.router)
app.include_router(calendar.router)
app.include_router(media_request.router)
app.include_router(search.router)
app.include_router(clients.router)
app.include_router(gaps.router)
app.include_router(risk.router)  # 🔥 挂载风控 API
app.include_router(notifications.router)  # 🔥 挂载全局通知 API
app.include_router(dedupe.router)
app.include_router(notify_rules.router)
app.include_router(system_tools.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
