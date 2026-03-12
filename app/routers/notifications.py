import sqlite3
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.core.database import query_db, DB_PATH

logger = logging.getLogger("uvicorn")

# 修复路由结尾斜杠的问题，兼容各种 Nginx 反代环境
router = APIRouter(prefix="/api/notifications", tags=["系统通知"])

class MarkReadReq(BaseModel):
    id: Optional[int] = None

# 暴力兜底建表：防止 init_db 没有成功运行或被旧版 Docker 缓存覆盖
def ensure_table_exists():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS sys_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            title TEXT,
            message TEXT,
            is_read INTEGER DEFAULT 0,
            action_url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[通知中心] 自动建表失败: {e}")

# 同时匹配有无斜杠的请求
@router.get("")
@router.get("/")
async def get_notifications(limit: int = 10):
    ensure_table_exists()  # 每次拉取前检查一下表，防止丢失
    try:
        count_res = query_db("SELECT COUNT(*) as c FROM sys_notifications WHERE is_read = 0")
        if count_res is None:
            print("⚠️ [通知中心] 警告：读取数据库失败，可能是底层 SQL 报错！")
            unread_count = 0
        else:
            unread_count = count_res[0]['c']

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
        print(f"❌ [通知中心] 发生异常: {e}")
        return {"success": False, "msg": str(e)}

@router.post("/read")
async def mark_as_read(req: MarkReadReq):
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

# 👇 终极测试接口：直接访问这个地址，测试整条链路是否畅通！
@router.get("/test_push")
async def test_push_notification():
    ensure_table_exists()
    try:
        from app.core.database import add_sys_notification
        add_sys_notification(
            notify_type="system",
            title="✅ 测试通知成功接入",
            message="如果你看到了这条消息，说明从写入到读取的链路已经完全打通！",
            action_url="/"
        )
        return {"success": True, "msg": "测试通知已注入！请返回控制台刷新页面，看右上角铃铛是否亮起！"}
    except Exception as e:
        return {"success": False, "msg": f"注入失败: {str(e)}"}