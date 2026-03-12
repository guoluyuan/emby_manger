import sqlite3
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.core.database import query_db, DB_PATH

router = APIRouter(prefix="/api/notifications", tags=["系统通知"])

# 接收请求的模型
class MarkReadReq(BaseModel):
    id: Optional[int] = None  # 如果传了 ID 就标为已读单条，不传就全部已读

@router.get("/")
async def get_notifications(limit: int = 10):
    """拉取最新的通知与未读数量"""
    try:
        # 获取未读总数
        count_res = query_db("SELECT COUNT(*) as c FROM sys_notifications WHERE is_read = 0")
        unread_count = count_res[0]['c'] if count_res else 0

        # 获取最近的通知记录
        rows = query_db("SELECT * FROM sys_notifications ORDER BY created_at DESC LIMIT ?", (limit,))
        
        notifications = []
        if rows:
            for r in rows:
                notifications.append({
                    "id": r["id"],
                    "type": r["type"],
                    "title": r["title"],
                    "message": r["message"],
                    "is_read": r["is_read"],
                    "action_url": r["action_url"],
                    "created_at": r["created_at"]
                })
        return {"success": True, "unread_count": unread_count, "items": notifications}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@router.post("/read")
async def mark_as_read(req: MarkReadReq):
    """标记通知为已读"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if req.id:
            cur.execute("UPDATE sys_notifications SET is_read = 1 WHERE id = ?", (req.id,))
        else:
            cur.execute("UPDATE sys_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        return {"success": False, "msg": str(e)}