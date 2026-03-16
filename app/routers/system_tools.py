import time
import requests
import logging
import sys
import datetime
import os
import json
import re
import shutil
import subprocess
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

def _run_cmd(args, timeout=90):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

def _get_container_id():
    # 允许手动指定（避免 HOSTNAME 非容器 ID 的场景）
    env_cid = (
        os.getenv("DOCKER_UPDATE_CONTAINER")
        or os.getenv("DOCKER_UPDATE_CONTAINER_ID")
        or os.getenv("DOCKER_UPDATE_NAME")
        or ""
    ).strip()
    if env_cid:
        return env_cid

    cid = (os.getenv("HOSTNAME") or "").strip()
    if re.fullmatch(r"[0-9a-f]{12,64}", cid or ""):
        return cid

    # 尝试从 cgroup / mountinfo 中解析容器 ID
    for path in ("/proc/self/cgroup", "/proc/1/cgroup"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    m = re.search(r"docker-([0-9a-f]{64})\.scope", line)
                    if m:
                        return m.group(1)
                    m = re.search(r"([0-9a-f]{64})", line)
                    if m:
                        return m.group(1)
        except Exception:
            pass

    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as f:
            for line in f:
                m = re.search(r"/docker/containers/([0-9a-f]{64})/", line)
                if m:
                    return m.group(1)
    except Exception:
        pass

    # 兜底：用 compose service 标签查找容器
    update_name = (os.getenv("DOCKER_UPDATE_NAME") or "").strip()
    service = (os.getenv("DOCKER_UPDATE_SERVICE") or update_name or "").strip()
    if service and shutil.which("docker"):
        try:
            res = _run_cmd(["docker", "ps", "-q", "-f", f"label=com.docker.compose.service={service}"], timeout=6)
            if res.returncode == 0:
                ids = [i.strip() for i in (res.stdout or "").splitlines() if i.strip()]
                if len(ids) == 1:
                    return ids[0]
        except Exception:
            pass

    return ""

def _docker_ready():
    if not os.path.exists("/var/run/docker.sock"):
        return False, "未检测到 /var/run/docker.sock"
    if not shutil.which("docker"):
        return False, "容器内未安装 docker CLI"
    return True, ""

def _inspect_container(cid: str):
    res = _run_cmd(["docker", "inspect", cid], timeout=10)
    if res.returncode != 0:
        return None, (res.stderr or res.stdout or "").strip()
    try:
        data = json.loads(res.stdout)[0]
        return data, ""
    except Exception as e:
        return None, str(e)

def _get_compose_meta(inspect: dict):
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    files_raw = (os.getenv("DOCKER_UPDATE_COMPOSE_FILES") or "").strip()
    if not files_raw:
        files_raw = (labels.get("com.docker.compose.project.config_files") or "").strip()
    files = [f.strip() for f in files_raw.split(",") if f.strip()]
    update_name = (os.getenv("DOCKER_UPDATE_NAME") or "").strip()
    service = (os.getenv("DOCKER_UPDATE_SERVICE") or update_name or "").strip()
    if not service:
        service = (labels.get("com.docker.compose.service") or "").strip()
    project = (os.getenv("DOCKER_UPDATE_PROJECT_NAME") or update_name or "").strip()
    if not project:
        project = (labels.get("com.docker.compose.project") or "").strip()
    return files, service, project

def _compose_bin():
    res = _run_cmd(["docker", "compose", "version"], timeout=6)
    if res.returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return []

def _get_image_id(image: str):
    res = _run_cmd(["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=10)
    if res.returncode != 0:
        return ""
    return (res.stdout or "").strip()

def _short_id(image_id: str):
    if not image_id:
        return ""
    return image_id.replace("sha256:", "")[:12]

def _extract_env_value(env_list, key: str):
    if not env_list:
        return ""
    prefix = f"{key}="
    for item in env_list:
        if isinstance(item, str) and item.startswith(prefix):
            return item[len(prefix):]
    return ""

def _get_image_env_value(image_ref: str, key: str):
    if not image_ref:
        return ""
    res = _run_cmd(["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image_ref], timeout=10)
    if res.returncode != 0:
        return ""
    try:
        env_list = json.loads(res.stdout or "[]") or []
        return _extract_env_value(env_list, key)
    except Exception:
        return ""

def _format_docker_time(ts: str):
    if not ts:
        return ""
    try:
        import datetime as _dt
        raw = ts.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(raw)
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts

def _get_image_created(image_ref: str):
    if not image_ref:
        return ""
    res = _run_cmd(["docker", "image", "inspect", "--format", "{{.Created}}", image_ref], timeout=10)
    if res.returncode != 0:
        return ""
    return _format_docker_time((res.stdout or "").strip())

def _build_current_info(inspect: dict):
    image = (inspect.get("Config") or {}).get("Image") or ""
    current_image_id = (inspect.get("Image") or "").strip()
    current_env = (inspect.get("Config") or {}).get("Env") or []
    current_version = _extract_env_value(current_env, "APP_VERSION")
    if not current_version:
        current_version = _get_image_env_value(current_image_id, "APP_VERSION")
    current_created = _get_image_created(current_image_id)
    return image, current_image_id, current_version, current_created

def _get_image_digest(image: str):
    res = _run_cmd(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=10)
    if res.returncode != 0:
        return ""
    try:
        digests = json.loads(res.stdout or "[]") or []
        if digests:
            return digests[0]
    except Exception:
        pass
    return ""

def _pull_image(image: str):
    res = _run_cmd(["docker", "pull", image], timeout=300)
    return res.returncode == 0, (res.stderr or res.stdout or "").strip()

def _compose_args(files, project_name: str = ""):
    args = []
    if project_name:
        args.extend(["-p", project_name])
    for f in files:
        args.extend(["-f", f])
    return args

def _all_files_exist(files):
    return all(os.path.exists(f) for f in files)

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

@router.get("/docker_update/status")
async def docker_update_status(request: Request):
    user = request.session.get("user") or {}
    if not user.get("is_admin"):
        return {"status": "error", "message": "权限不足"}

    ok, msg = _docker_ready()
    if not ok:
        return {"status": "error", "message": msg}

    cid = _get_container_id()
    if not cid:
        return {"status": "error", "message": "无法识别当前容器 ID，请设置 DOCKER_UPDATE_NAME"}

    inspect, err = _inspect_container(cid)
    if not inspect:
        return {"status": "error", "message": f"读取容器信息失败: {err or 'unknown'}"}

    image, current_image_id, current_version, current_created = _build_current_info(inspect)

    if not image:
        return {"status": "error", "message": "无法识别当前镜像名称"}

    pull_ok, pull_msg = _pull_image(image)
    if not pull_ok:
        return {"status": "error", "message": f"拉取镜像失败: {pull_msg or 'unknown'}"}

    latest_image_id = _get_image_id(image)
    latest_digest = _get_image_digest(image)
    latest_version = _get_image_env_value(image, "APP_VERSION")
    latest_created = _get_image_created(image)
    available = bool(latest_image_id and current_image_id and latest_image_id != current_image_id)

    files, service, project = _get_compose_meta(inspect)
    compose_ok = bool(files and service and _all_files_exist(files))

    return {
        "status": "success",
        "data": {
            "available": available,
            "image": image,
            "current_image_id": current_image_id,
            "latest_image_id": latest_image_id,
            "current_image_id_short": _short_id(current_image_id),
            "latest_image_id_short": _short_id(latest_image_id),
            "image_digest": latest_digest,
            "current_version": current_version,
            "latest_version": latest_version,
            "current_created": current_created,
            "latest_created": latest_created,
            "compose_files": files,
            "compose_service": service,
            "compose_ready": compose_ok,
            "compose_project": project,
            "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }

@router.get("/docker_update/local")
async def docker_update_local(request: Request):
    user = request.session.get("user") or {}
    if not user.get("is_admin"):
        return {"status": "error", "message": "权限不足"}

    ok, msg = _docker_ready()
    if not ok:
        return {"status": "error", "message": msg}

    cid = _get_container_id()
    if not cid:
        return {"status": "error", "message": "无法识别当前容器 ID，请设置 DOCKER_UPDATE_NAME"}

    inspect, err = _inspect_container(cid)
    if not inspect:
        return {"status": "error", "message": f"读取容器信息失败: {err or 'unknown'}"}

    image, current_image_id, current_version, current_created = _build_current_info(inspect)
    files, service, project = _get_compose_meta(inspect)
    compose_ok = bool(files and service and _all_files_exist(files))

    return {
        "status": "success",
        "data": {
            "image": image,
            "current_image_id": current_image_id,
            "current_image_id_short": _short_id(current_image_id),
            "current_version": current_version,
            "current_created": current_created,
            "compose_files": files,
            "compose_service": service,
            "compose_ready": compose_ok,
            "compose_project": project,
            "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }

@router.post("/docker_update/apply")
async def docker_update_apply(request: Request):
    user = request.session.get("user") or {}
    if not user.get("is_admin"):
        return {"status": "error", "message": "权限不足"}

    ok, msg = _docker_ready()
    if not ok:
        return {"status": "error", "message": msg}

    cid = _get_container_id()
    if not cid:
        return {"status": "error", "message": "无法识别当前容器 ID，请设置 DOCKER_UPDATE_NAME"}

    inspect, err = _inspect_container(cid)
    if not inspect:
        return {"status": "error", "message": f"读取容器信息失败: {err or 'unknown'}"}

    files, service, project = _get_compose_meta(inspect)
    if not files or not service:
        return {"status": "error", "message": "未检测到 compose 信息，请设置 DOCKER_UPDATE_COMPOSE_FILES 与 DOCKER_UPDATE_NAME"}
    if not _all_files_exist(files):
        return {"status": "error", "message": "compose 配置文件在容器内不可见，请挂载并设置正确路径"}

    compose_bin = _compose_bin()
    if not compose_bin:
        return {"status": "error", "message": "未检测到 docker compose 命令"}

    args = compose_bin + _compose_args(files, project)

    pull_res = _run_cmd(args + ["pull", service], timeout=300)
    if pull_res.returncode != 0:
        err_msg = (pull_res.stderr or pull_res.stdout or "").strip()
        return {"status": "error", "message": f"拉取更新失败: {err_msg or 'unknown'}"}

    up_res = _run_cmd(args + ["up", "-d", "--no-deps", "--force-recreate", "--remove-orphans", service], timeout=300)
    if up_res.returncode != 0:
        err_msg = (up_res.stderr or up_res.stdout or "").strip()
        return {"status": "error", "message": f"应用更新失败: {err_msg or 'unknown'}"}

    return {"status": "success", "message": "更新已触发，容器将短暂重启"}

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
