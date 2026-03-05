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
    """
    🔥 城门级主动防御：毫秒级拦截并秒踢黑名单客户端
    """
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
        # 极速比对黑名单表
        blacklist_rows = query_db("SELECT app_name FROM client_blacklist")
        if not blacklist_rows: 
            return False
            
        blacklist = [r['app_name'].lower() for r in blacklist_rows]
        
        if client_lower in blacklist:
            # 🎯 命中黑名单！触发 API 截杀连招
            
            # 连招 1：如果检测到有效 Session，瞬间发送系统警告弹窗并强制停播
            if session_id:
                msg_cmd = {
                    "Name": "DisplayMessage",
                    "Arguments": {
                        "Header": "🚫 违规客户端拦截",
                        "Text": f"系统检测到您正在使用被封禁的客户端 ({client})。您的设备已被强制拉黑并断开连接，请更换官方推荐客户端！",
                        "TimeoutMs": "10000"
                    }
                }
                # 发送弹窗命令 (不阻塞)
                try: requests.post(f"{host}/emby/Sessions/{session_id}/Command?api_key={key}", json=msg_cmd, timeout=2)
                except: pass
                
                # 强行掐断播放流 (不阻塞)
                try: requests.post(f"{host}/emby/Sessions/{session_id}/Playing/Stop?api_key={key}", timeout=2)
                except: pass
                
            # 连招 2：物理销毁该设备的 Token，彻底踢出登录态 (抛出 401 Unauthorized)
            try: requests.delete(f"{host}/emby/Devices?Id={device_id}&api_key={key}", timeout=3)
            except: pass
            
            logger.warning(f"💥 [主动防御] 已秒踢违规客户端下线: {client} (DeviceID: {device_id})")
            return True
            
    except Exception as e:
        logger.error(f"主动防御执行异常: {e}")
        
    return False

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
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            raw_data = form.get("data")
            if raw_data: data = json.loads(raw_data)

        if not data: return {"status": "error", "message": "Empty"}

        # ==========================================
        # 🔥 绝对防御：在任何业务发生前拦截违规客户端
        # ==========================================
        if intercept_illegal_client(data):
            # 拦截成功后直接抛弃这个 Webhook，阻断后续所有通知与统计
            return {"status": "success", "message": "Blocked illegal client"}

        event = data.get("Event", "").lower().strip()
        if event: logger.info(f"🔔 Webhook: {event}")

        # 入库通知处理
        if event in ["library.new", "item.added"]:
            item = data.get("Item", {})
            if item.get("Id") and item.get("Type") in ["Movie", "Episode", "Series"]:
                # 加入队列
                bot.add_library_task(item)

                # 日历联动
                if item.get("Type") == "Episode":
                    series_id = item.get("SeriesId")
                    season = item.get("ParentIndexNumber")
                    episode = item.get("IndexNumber")
                    
                    if series_id and season is not None and episode is not None:
                        from app.services.calendar_service import calendar_service
                        calendar_service.mark_episode_ready(series_id, season, episode)

        # 播放状态推送
        elif event == "playback.start":
            background_tasks.add_task(bot.push_playback_event, data, "start")
        elif event == "playback.stop":
            background_tasks.add_task(bot.push_playback_event, data, "stop")

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return {"status": "error", "message": str(e)}