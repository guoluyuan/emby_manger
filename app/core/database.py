import sqlite3
import os
import requests
import json
import logging
from app.core.config import cfg, DB_PATH

logger = logging.getLogger("uvicorn")

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir, exist_ok=True)
        except: pass

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS PlaybackActivity (Id INTEGER PRIMARY KEY AUTOINCREMENT, UserId TEXT, UserName TEXT, ItemId TEXT, ItemName TEXT, PlayDuration INTEGER, DateCreated DATETIME DEFAULT CURRENT_TIMESTAMP, Client TEXT, DeviceName TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS users_meta (user_id TEXT PRIMARY KEY, expire_date TEXT, note TEXT, created_at TEXT)''')
        
        # 🔥 风控模块：为老数据库无损新增“并发控制”和“风控等级”字段
        try: c.execute("ALTER TABLE users_meta ADD COLUMN max_concurrent INTEGER")
        except: pass
        try: c.execute("ALTER TABLE users_meta ADD COLUMN risk_level TEXT DEFAULT 'safe'")
        except: pass
        # 👇 添加这一行：新增 VIP 独立字段
        try: c.execute("ALTER TABLE users_meta ADD COLUMN is_vip INTEGER DEFAULT 0")
        except: pass

        c.execute('''CREATE TABLE IF NOT EXISTS invitations (code TEXT PRIMARY KEY, days INTEGER, used_count INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 1, created_at TEXT, used_at DATETIME, used_by TEXT, status INTEGER DEFAULT 0, template_user_id TEXT)''')
        try: c.execute("ALTER TABLE invitations ADD COLUMN template_user_id TEXT")
        except: pass
        
        c.execute('''CREATE TABLE IF NOT EXISTS tv_calendar_cache (id TEXT PRIMARY KEY, series_id TEXT, season INTEGER, episode INTEGER, air_date TEXT, status TEXT, data_json TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS media_requests (tmdb_id INTEGER, media_type TEXT, title TEXT, year TEXT, poster_path TEXT, status INTEGER DEFAULT 0, season INTEGER DEFAULT 0, reject_reason TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (tmdb_id, season))''')
        c.execute('''CREATE TABLE IF NOT EXISTS request_users (id INTEGER PRIMARY KEY AUTOINCREMENT, tmdb_id INTEGER, user_id TEXT, username TEXT, season INTEGER DEFAULT 0, requested_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(tmdb_id, user_id, season))''')
        c.execute('''CREATE TABLE IF NOT EXISTS insight_ignores (item_id TEXT PRIMARY KEY, item_name TEXT, ignored_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS gap_records (id INTEGER PRIMARY KEY AUTOINCREMENT, series_id TEXT, series_name TEXT, season_number INTEGER, episode_number INTEGER, status INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(series_id, season_number, episode_number))''')

        # 🔥 风控模块：新建独立的小黑屋与执法日志表
        c.execute('''CREATE TABLE IF NOT EXISTS risk_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, username TEXT, action TEXT, reason TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # 👇 新增：系统全局通知表
        c.execute('''CREATE TABLE IF NOT EXISTS sys_notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, title TEXT, message TEXT, is_read INTEGER DEFAULT 0, action_url TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # 👇 新增：登录失败统计与锁定表
        c.execute('''CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, scope TEXT, failed_count INTEGER DEFAULT 0, locked_until INTEGER DEFAULT 0, last_failed INTEGER DEFAULT 0, PRIMARY KEY (ip, scope))''')

        conn.commit()
        conn.close()
    except Exception as e: 
        print(f"❌ DB Init Error: {e}")


class APIRow(dict):
    """
    终极伪装者：让 API 返回的普通字典不仅能支持 FastAPI 的无损 JSON 序列化，
    还能像 sqlite3.Row 一样支持按索引(row[0])和忽略大小写的键名访问。
    """
    def __init__(self, original_dict):
        super().__init__(original_dict)
        self._vals = list(original_dict.values())
        self._lower_keys = {str(k).lower(): k for k in original_dict.keys()}

    def __getitem__(self, key):
        if isinstance(key, int):
            try: return self._vals[key]
            except IndexError: return None
        key_str = str(key)
        if super().__contains__(key_str):
            return super().__getitem__(key_str)
        key_lower = key_str.lower()
        if key_lower in self._lower_keys:
            return super().__getitem__(self._lower_keys[key_lower])
        return None

def _interpolate_sql(query: str, args) -> str:
    if not args: return query
    parts = query.split('?')
    if len(parts) - 1 != len(args): return query 
    res = parts[0]
    for i, arg in enumerate(args):
        if isinstance(arg, bool): val = "1" if arg else "0"
        elif isinstance(arg, (int, float)): val = str(arg)
        elif arg is None: val = "NULL"
        else: val = f"'{str(arg).replace(chr(39), chr(39)+chr(39))}'" 
        res += val + parts[i+1]
    return res

def query_db(query, args=(), one=False):
    mode = cfg.get("playback_data_mode", "sqlite")
    is_playback_query = "PlaybackActivity" in query or "PlaybackReporting" in query
    
    def _query_playback_api():
        host = cfg.get("emby_host")
        token = cfg.get("emby_api_key")
        if not host or not token:
            return None
        full_sql = _interpolate_sql(query, args)
        url = f"{host.rstrip('/')}/emby/user_usage_stats/submit_custom_query"
        headers = {"X-Emby-Token": token, "Content-Type": "application/json"}
        payload = {"CustomQueryString": full_sql}

        try:
            res = requests.post(url, headers=headers, json=payload, timeout=20)
            if res.status_code == 200:
                raw_data = None
                try:
                    res_json = res.json()
                    if isinstance(res_json, str):
                        try: raw_data = json.loads(res_json)
                        except: raw_data = res_json
                    else:
                        raw_data = res_json
                except:
                    try: raw_data = json.loads(res.text)
                    except: raw_data = {}

                final_data = []
                if isinstance(raw_data, dict):
                    columns = raw_data.get("colums") or raw_data.get("columns")
                    results = raw_data.get("results")
                    if columns and isinstance(results, list):
                        for row in results:
                            if isinstance(row, list):
                                row_dict = {}
                                for i, col_name in enumerate(columns):
                                    val = row[i] if i < len(row) else None
                                    if isinstance(val, str) and val.isdigit():
                                        val = int(val)
                                    row_dict[col_name] = val
                                final_data.append(row_dict)
                    else:
                        extracted = raw_data.get("results", raw_data.get("Items", [raw_data]))
                        final_data = extracted if isinstance(extracted, list) else [extracted]
                elif isinstance(raw_data, list):
                    final_data = raw_data
                else:
                    final_data = [raw_data] if raw_data else []

                data = [APIRow(item) if isinstance(item, dict) else item for item in final_data]
                if query.strip().upper().startswith("SELECT"):
                    return (data[0] if data else None) if one else data
                return True
            else:
                print(f"[API 引擎] ❌ 接口拒绝请求! 响应: {res.text[:200]}")
        except Exception as e:
            print(f"[API 引擎] ❌ 网络崩溃异常: {e}")
        return None

    # ==========================================
    # 🔥 双擎路由拦截器 (API 穿透模式)
    # ==========================================
    if mode == "api" and is_playback_query:
        api_result = _query_playback_api()
        if api_result is not None:
            return api_result
        else:
            pass
            
    # ==========================================
    # 🚂 原版 SQLite 执行器 (处理非播放表及降级情况)
    # ==========================================
    if not os.path.exists(DB_PATH): 
        if is_playback_query:
            print(f"[SQLite 引擎] ❌ 找不到文件: {DB_PATH}")
            # 如果是播放数据且 SQLite 不可用，尝试 API 兜底
            api_result = _query_playback_api()
            if api_result is not None:
                return api_result
        return None
        
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, args)
        if query.strip().upper().startswith("SELECT"):
            rv = cur.fetchall()
            conn.close()
            return (rv[0] if rv else None) if one else rv
        else:
            conn.commit()
            conn.close()
            return True
    except Exception as e: 
        if is_playback_query:
            print(f"[SQLite 引擎] 💥 执行失败: {e}")
            api_result = _query_playback_api()
            if api_result is not None:
                return api_result
        return None

def get_base_filter(user_id_filter):
    where = "WHERE 1=1"
    params = []
    
    if user_id_filter and user_id_filter != 'all':
        where += " AND UserId = ?"
        params.append(user_id_filter)
    
    hidden = cfg.get("hidden_users")
    if (not user_id_filter or user_id_filter == 'all') and hidden and len(hidden) > 0:
        placeholders = ','.join(['?'] * len(hidden))
        where += f" AND UserId NOT IN ({placeholders})"
        params.extend(hidden)
        
    return where, params

# 👇 新增：向数据库写入系统通知的基础函数
def add_sys_notification(notify_type: str, title: str, message: str, action_url: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sys_notifications (type, title, message, action_url) VALUES (?, ?, ?, ?)",
            (notify_type, title, message, action_url)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[系统通知] 写入数据库失败: {e}")
