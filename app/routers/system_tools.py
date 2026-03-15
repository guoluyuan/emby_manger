import time
import requests
import logging
import sys
import datetime
from collections import deque
from fastapi import APIRouter, Request
from app.core.config import cfg
from app.core.database import query_db

router = APIRouter(prefix="/api/system", tags=["System Tools"])

# ==========================================
# 🔥 核心黑科技：全局底层流劫持器 (Stdout/Stderr Tee)
# 抛弃原生 logging 拦截，直接在最底层劫持所有 print() 和系统输出
# 保证你在网页端看到的日志，和 Docker 控制台 100% 绝对一致！
# ==========================================

# 初始化全局内存环形队列，最多保留 300 行防内存溢出
if not hasattr(sys, '_emby_pulse_log_queue'):
    sys._emby_pulse_log_queue = deque(maxlen=300)
    sys._emby_pulse_log_queue.append(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [SYSTEM] 底层控制台流嗅探器已挂载，同步捕获全局 Print 与 Uvicorn 输出...")

class StreamTee:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        self.buffer = ""

    def write(self, data):
        # 1. 保证原有的控制台/Docker正常输出
        try:
            self.original_stream.write(data)
        except Exception:
            pass
            
        # 2. 同步将输出数据劫持到我们的内存队列中
        try:
            self.buffer += data
            if '\n' in self.buffer:
                lines = self.buffer.split('\n')
                # 只处理完整的行
                for line in lines[:-1]:
                    clean_line = line.strip()
                    if clean_line:
                        # 智能时间戳：如果原本的输出(如 print)没有时间戳，给它自动补上
                        if not clean_line.startswith('['):
                            ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            sys._emby_pulse_log_queue.append(f"[{ts}] {clean_line}")
                        else:
                            sys._emby_pulse_log_queue.append(clean_line)
                
                # 剩余未换行的部分放回 buffer 等待下一次拼接
                self.buffer = lines[-1]
        except Exception:
            pass

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass
            
    # 完美伪装成原生 stream，防止部分第三方库调用底层属性时报错
    def __getattr__(self, name):
        return getattr(self.original_stream, name)

# 动态替换标准输出流 (加上防重复挂载机制，完美适配热重载)
if not getattr(sys.stdout, '_is_tee', False):
    sys.stdout = StreamTee(sys.stdout)
    sys.stdout._is_tee = True

if not getattr(sys.stderr, '_is_tee', False):
    sys.stderr = StreamTee(sys.stderr)
    sys.stderr._is_tee = True


# ==========================================
# 往下是常规的系统诊断与读取逻辑
# ==========================================
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
        if not hasattr(sys, '_emby_pulse_log_queue'):
            return {"success": False, "msg": "日志服务未初始化"}
            
        logs_list = list(sys._emby_pulse_log_queue)[-lines:]
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
    
    return {"success": True, "msg": f"Debug 模式已{'开启' if enable else '关闭'}"}
