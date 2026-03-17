from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import Optional
import sqlite3
import requests
import json
from app.core.config import cfg, DB_PATH, save_config
from app.services.risk_service import ban_user, log_risk_action, get_user_concurrent_limit

def admin_required(request: Request):
    user = request.session.get("user")
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

router = APIRouter(prefix="/api/risk", tags=["RiskControl"], dependencies=[Depends(admin_required)])

class ActionRequest(BaseModel):
    user_id: str
    username: str
    session_id: str = None
    device_id: str = None  # 🔥 新增设备ID参数，用于物理拔网线
    reason: str = "风控系统强制执行"

class ConfigRequest(BaseModel):
    enable_risk_control: bool
    default_max_concurrent: int
    risk_scan_interval_sec: Optional[int] = None
    enable_risk_auto_ban: Optional[bool] = None

@router.get("/online")
def get_online_status():
    """获取所有在线用户的风控大盘数据"""
    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key", "")
    if not host or not api_key: return {"error": "未配置 Emby 服务器信息"}

    try:
        res = requests.get(f"{host}/emby/Sessions", headers={"X-Emby-Token": api_key}, timeout=10)
        if res.status_code != 200: return {"error": "无法连接到 Emby"}
        
        sessions = res.json()
        active_users = {}
        
        for s in sessions:
            if s.get("NowPlayingItem") and s["NowPlayingItem"].get("MediaType") == "Video":
                uid = s.get("UserId")
                if not uid: continue
                
                if uid not in active_users:
                    limit = get_user_concurrent_limit(uid)
                    active_users[uid] = {
                        "user_id": uid, "username": s.get("UserName", "未知"),
                        "limit": limit, "current_count": 0, "is_warning": False, "devices": []
                    }
                
                active_users[uid]["current_count"] += 1
                active_users[uid]["devices"].append({
                    "session_id": s.get("Id"),
                    "device_id": s.get("DeviceId"), # 🔥 抓取 DeviceId
                    "device_name": s.get("DeviceName", "未知设备"),
                    "client": s.get("Client", "未知客户端"),
                    "ip": s.get("RemoteEndPoint", "未知IP"),
                    "item_name": s["NowPlayingItem"].get("Name", "未知影片")
                })
        
        result_list = []
        for uid, data in active_users.items():
            if data["current_count"] > data["limit"]: data["is_warning"] = True
            result_list.append(data)
            
        result_list.sort(key=lambda x: x["is_warning"], reverse=True)
        return {"data": result_list}
    except Exception as e:
        return {"error": str(e)}

@router.post("/kick")
def api_kick_session(req: ActionRequest):
    """🔥 真·物理拔网线：直接注销第三方播放器的设备 Token"""
    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key", "")
    
    # 1. 发送常规 Stop 指令 (给官方客户端面子)
    if req.session_id:
        requests.post(f"{host}/emby/Sessions/{req.session_id}/Playing/Stop", headers={"X-Emby-Token": api_key}, timeout=5)
        
    # 2. 降维打击：直接删除设备登录凭证 (专门对付 Infuse 等第三方流氓客户端)
    if req.device_id:
        delete_url = f"{host}/emby/Devices?Id={req.device_id}"
        requests.delete(delete_url, headers={"X-Emby-Token": api_key}, timeout=5)

    log_risk_action(req.user_id, req.username, "kick", "强制注销设备Token并断开")
    return {"message": "已成功拔掉该设备的网线！"}

@router.post("/ban")
def api_ban_user(req: ActionRequest):
    if ban_user(req.user_id):
        log_risk_action(req.user_id, req.username, "ban", req.reason)
        return {"message": f"用户 {req.username} 已被关入小黑屋并冻结"}
    raise HTTPException(status_code=500, detail="封禁失败，请检查 API 权限")

@router.get("/logs")
def get_risk_logs():
    """获取历史审计日志"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM risk_logs ORDER BY created_at DESC LIMIT 200")
        rows = cur.fetchall()
        conn.close()
        return {"data": [dict(r) for r in rows]}
    except: return {"data": []}

@router.get("/config")
def get_risk_config():
    """获取风控设置"""
    return {
        "enable_risk_control": cfg.get("enable_risk_control", False),
        "default_max_concurrent": cfg.get("default_max_concurrent", 2),
        "enable_risk_auto_ban": cfg.get("enable_risk_auto_ban", False),
        "risk_scan_interval_sec": cfg.get("risk_scan_interval_sec", 60)
    }

@router.post("/config")
def update_risk_config(req: ConfigRequest):
    """保存风控设置"""
    cfg["enable_risk_control"] = req.enable_risk_control
    cfg["default_max_concurrent"] = req.default_max_concurrent
    if req.risk_scan_interval_sec is not None:
        try:
            interval = int(req.risk_scan_interval_sec)
        except Exception:
            interval = 60
        if interval < 5: interval = 5
        if interval > 3600: interval = 3600
        cfg["risk_scan_interval_sec"] = interval
    if req.enable_risk_auto_ban is not None:
        cfg["enable_risk_auto_ban"] = req.enable_risk_auto_ban
    save_config()
    return {"message": "配置已生效"}

@router.get("/summary")
def get_risk_summary():
    """空闲状态下的风控战报简报"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 1. 统计近 24 小时的拦截数据
        cur.execute("SELECT action, COUNT(*) as cnt FROM risk_logs WHERE datetime(created_at) >= datetime('now', '-1 day') GROUP BY action")
        today_stats = {"warn": 0, "kick": 0, "ban": 0}
        for row in cur.fetchall():
            today_stats[row['action']] = row['cnt']

        # 2. 统计历史高危账号排行榜 (违规次数最多的前 5 名)
        cur.execute("SELECT username, COUNT(*) as total_violations FROM risk_logs GROUP BY username ORDER BY total_violations DESC LIMIT 5")
        top_offenders = [dict(r) for r in cur.fetchall()]

        # 3. 统计有多少人拥有“专属并发特权”
        cur.execute("SELECT COUNT(*) as vip_count FROM users_meta WHERE is_vip = 1")
        vip_row = cur.fetchone()
        vip_count = vip_row['vip_count'] if vip_row else 0

        conn.close()
        return {
            "status": "success",
            "today_stats": today_stats,
            "top_offenders": top_offenders,
            "vip_count": vip_count,
            "global_limit": cfg.get("default_max_concurrent", 2)
        }
    except Exception as e:
        return {"error": str(e)}
