import os
import socket
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.routers import insight
from app.core.config import PORT, SECRET_KEY, CONFIG_DIR, FONT_DIR
from app.core.database import init_db
from app.services.bot_service import bot
from app.routers import media_request
# 🔥 引入所有路由
from app.routers import views, auth, users, stats, bot as bot_router, system, proxy, report, webhook, insight, tasks, history, calendar, search, clients, gaps

# 初始化目录和数据库
if not os.path.exists("static"): os.makedirs("static")
if not os.path.exists("templates"): os.makedirs("templates")
if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)
if not os.path.exists(FONT_DIR): os.makedirs(FONT_DIR)
init_db()

# ==============================================================================
# 🔥 黑客级网络引擎：底层 TCP 流量微型转发器 (无视多进程冲突)
# ==============================================================================
def forward_data(source, destination):
    try:
        while True:
            data = source.recv(8192)
            if not data: break
            destination.sendall(data)
    except Exception: pass
    finally:
        try: source.close()
        except: pass
        try: destination.close()
        except: pass

def start_tcp_proxy():
    try:
        # 创建一个纯底层的 TCP 监听器，不依赖任何 Web 框架
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', 10308))
        server.listen(100)
        print("🎈 [User Portal] 10308 端口已启动 (原生流量无感转发模式就绪)")
        
        while True:
            client_sock, _ = server.accept()
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.connect(('127.0.0.1', int(PORT))) # 内部悄悄连回主程序
            
            threading.Thread(target=forward_data, args=(client_sock, server_sock), daemon=True).start()
            threading.Thread(target=forward_data, args=(server_sock, client_sock), daemon=True).start()
    except OSError:
        # 完美解决 Errno 98：如果是多进程启动，只有一个能抢到端口，剩下的静默退出，绝不崩服
        pass
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting EmbyPulse...")
    bot.start()
    
    # 🌟 启动极轻量级的 TCP 转发线程
    threading.Thread(target=start_tcp_proxy, daemon=True).start()
    
    yield
    print("🛑 Stopping EmbyPulse...")
    bot.stop()

app = FastAPI(lifespan=lifespan)

# ==============================================================================
# 🔥 核心防御：10308 专属隐形分流中间件
# ==============================================================================
@app.middleware("http")
async def port_10308_dispatcher(request: Request, call_next):
    # 获取浏览器发来的原始请求头（虽然走了内部转发，但 Host 头依然是 10308）
    host_header = request.headers.get("host", "")
    
    # 铁律：只要网址后面带的是 10308，统统关进求片中心的小黑屋
    if host_header.endswith(":10308"):
        path = request.url.path
        
        # 隐形重写：访问根目录当做访问求片中心
        if path == "/":
            request.scope["path"] = "/request"
            
        # 物理隔绝：只放行这几个安全路径，后台的统统 404 封死
        allowed_prefixes = (
            "/request", "/request_login", 
            "/api/v1/request", "/api/proxy/smart_image", 
            "/static", "/favicon.ico"
        )
        if not request.scope["path"].startswith(allowed_prefixes):
            return HTMLResponse("<h1>404 Not Found</h1><p>Access Denied.</p>", status_code=404)
            
    return await call_next(request)
# ==============================================================================

# 中间件
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400*7)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)