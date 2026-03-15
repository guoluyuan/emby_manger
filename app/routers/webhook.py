from fastapi import APIRouter, Request, HTTPException
from app.core.config import cfg
from app.core.database import query_db
# 🔥 引入事件总线
from app.core.event_bus import bus
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

@router.post("/api/v1/webhook")
async def emby_webhook(request: Request):
    query_token = request.query_params.get("token")
    header_token = request.headers.get("x-webhook-token")
    token = header_token or query_token
    if token != cfg.get("webhook_token"):
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

        # 1. 违规拦截（最高优先级）
        if intercept_illegal_client(data):
            return {"status": "success", "message": "Blocked"}

        event = data.get("Event", "").lower().strip()

        # 2. 彻底解耦：不再调 bot，而是发布到事件总线
        bus.publish("webhook.received", event, data)

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Webhook 异常: {e}")
        return {"status": "error", "message": str(e)}
