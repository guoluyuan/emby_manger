import time
import requests
import logging
from collections import deque
from fastapi import APIRouter, Request
from app.core.config import cfg
from app.core.database import query_db

router = APIRouter(prefix="/api/system", tags=["System Tools"])

# ==========================================
# 🔥 核心黑科技：内存日志总线劫持器
# 不管用户怎么部署(Docker/宝塔/nohup)，直接从内存抓取最新日志，无需生成实体 log 文件
# ==========================================
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=300):
        super().__init__()
        self.logs = deque(maxlen=capacity)
        # 设置极简纯粹的日志格式
        self.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        self.logs.append("[SYSTEM] 内存日志流嗅探器已挂载，等待捕获最新运行动态...")

    def emit(self, record):
        self.logs.append(self.format(record))

# 全局单例挂载 (防止热重载时重复创建)
memory_handler = None
for h in logging.getLogger().handlers:
    if isinstance(h, MemoryLogHandler):
        memory_handler = h
        break

if not memory_handler:
    memory_handler = MemoryLogHandler(capacity=300)
    # 挂载到根节点和 uvicorn 节点
    logging.getLogger().addHandler(memory_handler)
    logging.getLogger("uvicorn").addHandler(memory_handler)
    logging.getLogger("uvicorn.error").addHandler(memory_handler)


def ping_url(url, proxies=None):
    start = time.time()
    try:
        res = requests.get(url, proxies=proxies, timeout=5)
        latency = int((time.time() - start) * 1000)
        return True, latency
    except Exception:
        return False, 0

@router.get("/network_check")
async def network_check():
    proxy_url = cfg.get("proxy_url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    
    tg_ok, tg_ping = ping_url("https://api.telegram.org", proxies)
    
    tmdb_key = cfg.get("tmdb_api_key", "")
    tmdb_url = f"https://api.themoviedb.org/3/configuration?api_key={tmdb_key}" if tmdb_key else "https://api.themoviedb.org/3/"
    tmdb_ok, tmdb_ping = ping_url(tmdb_url, proxies)
    
    last_webhook = "暂无记录"
    try:
        rows = query_db("SELECT DateCreated FROM PlaybackActivity ORDER BY DateCreated DESC LIMIT 1")
        if rows and rows[0]['DateCreated']:
            last_webhook = rows[0]['DateCreated']
            if 'T' in last_webhook:
                last_webhook = last_webhook.replace('T', ' ')[:19]
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "tg": {"ok": tg_ok, "ping": tg_ping},
            "tmdb": {"ok": tmdb_ok, "ping": tmdb_ping},
            "webhook": {"last_active": last_webhook}
        }
    }

@router.get("/logs")
async def get_logs(lines: int = 150):
    """直接从内存环形队列中读取最新日志"""
    try:
        if not memory_handler:
            return {"success": False, "msg": "日志服务未初始化"}
            
        logs_list = list(memory_handler.logs)[-lines:]
        return {"success": True, "data": "\n".join(logs_list)}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@router.post("/debug")
async def toggle_debug(req: Request):
    """动态热切换全局日志等级"""
    data = await req.json()
    enable = data.get("enable", False)
    
    uvicorn_logger = logging.getLogger("uvicorn")
    app_logger = logging.getLogger()
    
    level = logging.DEBUG if enable else logging.INFO
    uvicorn_logger.setLevel(level)
    app_logger.setLevel(level)
    
    if enable:
        app_logger.debug("======== DEBUG MODE ENABLED BY CONTROL CENTER ========")
        
    return {"success": True, "msg": f"Debug 模式已{'开启' if enable else '关闭'}"}