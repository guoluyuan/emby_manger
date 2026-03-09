from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from app.services.bot_service import bot
from app.core.config import cfg
from app.core.database import query_db
import requests
import json
import logging

logger = logging.getLogger("uvicorn")
router = APIRouter()

def intercept_illegal_client(data: dict):
    session = data.get("Session", {})
    device_id = session.get("DeviceId") or data.get("DeviceId")
    client = session.get("Client") or data.get("Client") or data.get("AppName")
    session_id = session.get("Id")
    
    if not client or not device_id:
        return False
        
    client_lower = client.lower()
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    
    try:
        blacklist_rows = query_db("SELECT app_name FROM client_blacklist")
        if not blacklist_rows: return False
            
        blacklist = [r['app_name'].lower() for r in blacklist_rows]
        if client_lower in blacklist:
            if session_id:
                msg_cmd = {
                    "Name": "DisplayMessage",
                    "Arguments": {
                        "Header": "🚫 违规客户端拦截",
                        "Text": f"检测到违规客户端 ({client})，该设备已被踢出！",
                        "TimeoutMs": "10000"
                    }
                }
                try: requests.post(f"{host}/emby/Sessions/{session_id}/Command?api_key={key}", json=msg_cmd, timeout=2)
                except: pass
                try: requests.post(f"{host}/emby/Sessions/{session_id}/Playing/Stop?api_key={key}", timeout=2)
                except: pass
            
            try: requests.delete(f"{host}/emby/Devices?Id={device_id}&api_key={key}", timeout=3)
            except: pass
            
            logger.warning(f"💥 [主动防御] 已秒踢违规客户端: {client}")
            return True
    except: pass
    return False

def clear_gap_record_async(item: dict):
    try:
        if item.get("Type") != "Episode": return
        
        series_id = str(item.get("SeriesId"))
        season = int(item.get("ParentIndexNumber", -1))
        episode = int(item.get("IndexNumber", -1))
        
        if season == -1 or episode == -1: return

        query_db("DELETE FROM gap_records WHERE series_id=? AND season_number=? AND episode_number=?", (series_id, season, episode))
        
        try:
            from app.routers.gaps import state_lock, scan_state
            with state_lock:
                if scan_state.get("results"):
                    for s in scan_state["results"]:
                        if str(s.get("series_id")) == series_id:
                            s["gaps"] = [ep for ep in s.get("gaps", []) if not (int(ep.get("season")) == season and int(ep.get("episode")) == episode)]
                            
                            # 🔥 Webhook 触发金牌颁发
                            if len(s["gaps"]) == 0 and s.get("tmdb_status") in ["Ended", "Canceled"]:
                                try: query_db("INSERT OR IGNORE INTO gap_perfect_series (series_id, tmdb_id, series_name) VALUES (?, ?, ?)", (series_id, s.get("tmdb_id"), s.get("series_name")))
                                except: pass
                                
                    scan_state["results"] = [s for s in scan_state["results"] if len(s.get("gaps", [])) > 0]
                    query_db("INSERT OR REPLACE INTO gap_scan_cache (id, result_json, updated_at) VALUES (1, ?, datetime('now', 'localtime'))", (json.dumps(scan_state["results"]),))
            
            logger.info(f"🎉 [缺集联动] 检测到 S{season}E{episode} 入库，已自动完成抹除。")
        except: pass
    except Exception as e:
        logger.error(f"清道夫任务执行失败: {e}")

@router.post("/api/v1/webhook")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    query_token = request.query_params.get("token")
    if query_token != cfg.get("webhook_token"):
        raise HTTPException(status_code=403, detail="Invalid Token")

    try:
        data = None
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
        elif "form" in content_type:
            form = await request.form()
            raw_data = form.get("data")
            if raw_data: data = json.loads(raw_data)

        if not data: return {"status": "error", "message": "Empty"}

        if intercept_illegal_client(data):
            return {"status": "success", "message": "Blocked"}

        event = data.get("Event", "").lower().strip()
        if event: logger.info(f"🔔 Webhook Event: {event}")

        if "item.added" in event or "library.new" in event:
            item = data.get("Item", {})
            if item.get("Id"):
                bot.add_library_task(item)
                if item.get("Type") == "Episode":
                    from app.services.calendar_service import calendar_service
                    calendar_service.mark_episode_ready(item.get("SeriesId"), item.get("ParentIndexNumber"), item.get("IndexNumber"))
                    background_tasks.add_task(clear_gap_record_async, item)

        elif "playback.start" in event:
            background_tasks.add_task(bot.push_playback_event, data, "start")
        elif "playback.stop" in event:
            background_tasks.add_task(bot.push_playback_event, data, "stop")

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Webhook 通道故障: {e}")
        return {"status": "error", "message": str(e)}