import sqlite3
import requests
import logging
import time
import threading
from app.core.config import cfg, DB_PATH
from app.core.event_bus import bus

logger = logging.getLogger("uvicorn")

# ==========================================
# 🗡️ 屠龙刀：强力执法接口
# ==========================================
def kick_session(session_id: str, reason: str = "管理员强制中止播放"):
    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key", "")
    if not host or not api_key: return False
    
    url = f"{host}/emby/Sessions/{session_id}/Playing/Stop"
    try:
        res = requests.post(url, headers={"X-Emby-Token": api_key}, timeout=5)
        return res.status_code in [200, 204]
    except Exception as e:
        logger.error(f"[风控] 踢出设备失败: {e}")
        return False

def ban_user(user_id: str):
    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key", "")
    if not host or not api_key: return False
    
    policy_url = f"{host}/emby/Users/{user_id}"
    try:
        res = requests.get(policy_url, headers={"X-Emby-Token": api_key}, timeout=5)
        if res.status_code == 200:
            user_data = res.json()
            policy = user_data.get("Policy", {})
            policy["IsDisabled"] = True
            
            update_url = f"{host}/emby/Users/{user_id}/Policy"
            update_res = requests.post(update_url, headers={"X-Emby-Token": api_key}, json=policy, timeout=5)
            return update_res.status_code in [200, 204]
    except Exception as e:
        logger.error(f"[风控] 封禁用户失败: {e}")
    return False

def log_risk_action(user_id: str, username: str, action: str, reason: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO risk_logs (user_id, username, action, reason) VALUES (?, ?, ?, ?)", (user_id, username, action, reason))
        if action == "ban":
            cur.execute("UPDATE users_meta SET risk_level = 'banned' WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[风控] 记录日志失败: {e}")

# ==========================================
# 👁️ 天眼：实时并发巡逻警报
# ==========================================
def get_user_concurrent_limit(user_id: str) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT max_concurrent FROM users_meta WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] is not None:
            return int(row[0]) 
    except: pass
    return int(cfg.get("default_max_concurrent", 2))

_alerted_sessions = set()

def scan_playbacks_and_alert():
    if not cfg.get("enable_risk_control", False): return

    host = cfg.get("emby_host", "").rstrip('/')
    api_key = cfg.get("emby_api_key", "")
    if not host or not api_key: return

    try:
        res = requests.get(f"{host}/emby/Sessions", headers={"X-Emby-Token": api_key}, timeout=10)
        if res.status_code != 200: return
        sessions = res.json()
        
        active_playbacks = {}
        for s in sessions:
            if s.get("NowPlayingItem") and s["NowPlayingItem"].get("MediaType") == "Video":
                uid = s.get("UserId")
                if not uid: continue
                if uid not in active_playbacks:
                    active_playbacks[uid] = []
                active_playbacks[uid].append(s)
                
        for uid, user_sessions in active_playbacks.items():
            limit = get_user_concurrent_limit(uid)
            current_count = len(user_sessions)
            
            if current_count > limit:
                username = user_sessions[0].get("UserName", "未知用户")
                devices_info = []
                alert_trigger_ids = []
                
                for s in user_sessions:
                    dev_name = s.get("DeviceName", "未知设备")
                    client = s.get("Client", "未知客户端")
                    sid = s.get("Id", "")
                    devices_info.append(f"{dev_name} ({client})")
                    alert_trigger_ids.append(sid)
                
                fingerprint = f"{uid}-" + "-".join(sorted(alert_trigger_ids))
                if fingerprint in _alerted_sessions: continue  
                
                _alerted_sessions.add(fingerprint)
                log_risk_action(uid, username, "warn", f"并发超限: 当前 {current_count} / 限额 {limit}")
                
                # 🔥 完美解耦：通过事件总线向外发送安全预警，取代直接调用 Bot
                devices_text = "\n".join([f"  🔸 {d}" for d in devices_info])
                bus.publish("notify.risk.alert", {
                    "username": username,
                    "current": current_count,
                    "limit": limit,
                    "devices_info": devices_text
                })
                    
    except Exception as e:
        logger.error(f"[风控天眼] 扫描异常: {e}")

def _risk_monitor_loop():
    while True:
        try: scan_playbacks_and_alert()
        except Exception as e: logger.error(f"[风控天眼] 守护线程异常: {e}")
        time.sleep(15) 

def start_risk_monitor():
    threading.Thread(target=_risk_monitor_loop, daemon=True, name="RiskMonitorThread").start()
    logger.info("👁️ [风险管控] 天眼巡逻系统已启动，并发监控中...")