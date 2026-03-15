import sqlite3
import requests
import logging
import time
import threading
from app.core.config import cfg, DB_PATH
# 👇 引入刚才新增的 add_sys_notification
from app.core.database import add_sys_notification
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
# 👁️ 天眼：零延迟实时扫描
# ==========================================
def get_user_concurrent_limit(user_id: str) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT max_concurrent FROM users_meta WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] is not None: return int(row[0]) 
    except: pass
    return int(cfg.get("default_max_concurrent", 2))

_alerted_sessions = set()

def scan_playbacks_and_alert():
    if not cfg.get("enable_risk_control", True): return

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
                
        global _alerted_sessions
        current_alert_fingerprints = set()
        
        # 🔥 只有在真正有人看视频的时候，才在控制台打印这句战报，0人的时候静默防守
        if len(active_playbacks) > 0:
            pass

        for uid, user_sessions in active_playbacks.items():
            limit = get_user_concurrent_limit(uid)
            current_count = len(user_sessions)
            username = user_sessions[0].get("UserName", "未知用户")
            
            # 仅记录异常，避免常规扫描刷屏
            
            if current_count > limit:
                devices_info = []
                alert_trigger_ids = []
                for s in user_sessions:
                    dev_name = s.get("DeviceName", "未知设备")
                    client = s.get("Client", "未知客户端")
                    sid = s.get("Id", "")
                    devices_info.append(f"{dev_name} ({client})")
                    alert_trigger_ids.append(sid)
                
                fingerprint = f"{uid}-" + "-".join(sorted(alert_trigger_ids))
                current_alert_fingerprints.add(fingerprint)
                
                if fingerprint not in _alerted_sessions:
                    log_risk_action(uid, username, "warn", f"并发超限: 当前 {current_count} / 限额 {limit}")
                    devices_text = "\n".join([f"  🔸 {d}" for d in devices_info])
                    logger.warning(f"🚨 [风控执行] 发现越界！立即通过总线呼叫机器人发送警报！")
                    
                    bus.publish("notify.risk.alert", {
                        "user_id": uid,          # 🔥 必须传给机器人用于封禁按钮
                        "username": username,
                        "current": current_count,
                        "limit": limit,
                        "devices_info": devices_text
                    })
                else:
                    # 防抖命中不再输出日志
                    pass
                    
        _alerted_sessions.clear()
        _alerted_sessions.update(current_alert_fingerprints)
                    
    except Exception as e:
        logger.error(f"[风控天眼] 扫描异常: {e}")

def _on_playback_start(data):
    # 静默触发扫描，避免刷屏
    def delay_scan():
        time.sleep(3)
        scan_playbacks_and_alert()
    threading.Thread(target=delay_scan, daemon=True).start()

def _risk_monitor_loop():
    while True:
        try: scan_playbacks_and_alert()
        except: pass
        time.sleep(60) 

# 👇 新增：为 Web 端全局通知中心订阅风控事件
def _on_risk_alert_for_web(data):
    username = data.get("username", "未知")
    current = data.get("current", 0)
    limit = data.get("limit", 0)
    
    add_sys_notification(
        notify_type="risk",
        title=f"🚨 账号并发越界: {username}",
        message=f"当前并发 {current} / 额度 {limit}，请立即处理！",
        action_url="/risk"
    )

def start_risk_monitor():
    bus.subscribe("notify.playback.start", _on_playback_start)
    # 👇 新增：将这个动作挂载到事件总线上
    bus.subscribe("notify.risk.alert", _on_risk_alert_for_web)
    
    threading.Thread(target=_risk_monitor_loop, daemon=True, name="RiskMonitorThread").start()
    logger.info("👁️ [风险管控] 零延迟天眼系统已启动 (事件驱动 + 60s兜底)")
