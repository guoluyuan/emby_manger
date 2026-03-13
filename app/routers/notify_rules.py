from fastapi import APIRouter, Request
from app.core.database import query_db
from app.core.config import cfg
import sqlite3
from app.core.database import DB_PATH
import requests

router = APIRouter(prefix="/api/notify_rules", tags=["Notification Rules"])

@router.get("/users")
async def get_emby_users():
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if not key or not host: return {"success": False, "data": []}
    try:
        res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5)
        if res.status_code == 200:
            return {"success": True, "data": [{"id": u["Id"], "name": u["Name"]} for u in res.json()]}
    except: pass
    return {"success": False, "data": []}

@router.get("/mutes")
async def get_mutes():
    try:
        rows = query_db("SELECT user_id, event_type FROM notify_mutes")
        mutes = {"playback": [], "login": []}
        if rows:
            for r in rows:
                if r['event_type'] in mutes:
                    mutes[r['event_type']].append(r['user_id'])
        return {"success": True, "data": mutes}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@router.post("/mutes")
async def save_mutes(req: Request):
    data = await req.json()
    playback_users = data.get("playback", [])
    login_users = data.get("login", [])
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM notify_mutes")
        
        for uid in playback_users:
            c.execute("INSERT INTO notify_mutes (user_id, event_type) VALUES (?, ?)", (uid, "playback"))
        for uid in login_users:
            c.execute("INSERT INTO notify_mutes (user_id, event_type) VALUES (?, ?)", (uid, "login"))
            
        conn.commit()
        conn.close()
        return {"success": True, "msg": "静音规则保存成功！新规即刻生效。"}
    except Exception as e:
        return {"success": False, "msg": str(e)}