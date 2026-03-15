import sqlite3
import logging
import threading
import time
import requests
from collections import defaultdict
import re
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict

from app.core.config import cfg
from app.core.database import query_db, DB_PATH

logger = logging.getLogger("uvicorn")
router = APIRouter(prefix="/api/dedupe", tags=["去重管理"])

scan_state = {
    "is_scanning": False,
    "progress": 0,
    "total_items": 0,
    "duplicate_groups": 0,
    "message": "空闲中"
}

def init_dedupe_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS dedupe_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_key TEXT,
            tmdb_id TEXT,
            media_type TEXT,
            title TEXT,
            season_num INTEGER,
            episode_num INTEGER,
            item_id TEXT,
            file_name TEXT,
            file_path TEXT,
            resolution TEXT,
            bitrate INTEGER,
            size_bytes REAL,
            video_codec TEXT,
            audio_codec TEXT,
            has_hdr INTEGER,
            has_dovi INTEGER,
            has_chi_sub INTEGER,
            has_ass_sub INTEGER,
            score INTEGER,
            is_recommended_del INTEGER DEFAULT 0,
            is_exempt INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS dedupe_whitelist (
            group_key TEXT PRIMARY KEY,
            title TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        
        c.execute("PRAGMA table_info(dedupe_results)")
        cols = [col[1] for col in c.fetchall()]
        if "file_path" not in cols: c.execute("ALTER TABLE dedupe_results ADD COLUMN file_path TEXT")
            
        c.execute("PRAGMA table_info(dedupe_whitelist)")
        w_cols = [col[1] for col in c.fetchall()]
        if "title" not in w_cols: c.execute("ALTER TABLE dedupe_whitelist ADD COLUMN title TEXT")

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[去重引擎] 自动建表失败: {e}")

init_dedupe_db()

def calculate_score(src: dict, strategy: str = "quality", custom_weights: dict = None):
    score = 0
    video = next((s for s in src.get("MediaStreams", []) if s.get("Type") == "Video"), {})
    audio = next((s for s in src.get("MediaStreams", []) if s.get("Type") == "Audio"), {})
    subs = [s for s in src.get("MediaStreams", []) if s.get("Type") == "Subtitle"]
    
    w = {"res": 40, "bitrate": 20, "codec": 5, "hdr": 15, "chi": 10, "ass": 15}
    if strategy == "subs": w = {"res": 15, "bitrate": 10, "codec": 5, "hdr": 10, "chi": 40, "ass": 30}
    elif strategy == "size": w = {"res": 20, "bitrate": 10, "codec": 30, "hdr": 10, "chi": 10, "ass": 10}
    elif strategy == "custom" and custom_weights: w = custom_weights

    width = video.get("Width") or 0
    res_str = "未知"
    if width >= 3800: score += w.get("res", 40); res_str = "4K"
    elif width >= 1900: score += w.get("res", 40) // 2; res_str = "1080P"
    elif width >= 1200: score += w.get("res", 40) // 4; res_str = "720P"
    elif width > 0: res_str = f"{width}P"
    
    bitrate = src.get("Bitrate") or 0
    if bitrate > 0: score += min(w.get("bitrate", 20), int((bitrate / 1000000) / 2))
        
    codec = video.get("Codec", "").lower()
    if "hevc" in codec or "x265" in codec or "av1" in codec: score += w.get("codec", 5)
        
    v_range = video.get("VideoRange", "")
    v_title = video.get("DisplayTitle", "").upper()
    if "DOVI" in v_title or "DOLBY VISION" in v_title: score += w.get("hdr", 15)
    elif "HDR" in v_range or "HDR" in v_title: score += int(w.get("hdr", 15) * 0.6)
    
    a_codec = audio.get("Codec", "").lower()
    has_chi = has_ass = False
    for sub in subs:
        lang = sub.get("Language", "").lower()
        if lang in ["chi", "zho", "chs", "cht", "zh"]:
            has_chi = True
            sub_codec = sub.get("Codec", "").lower()
            if "ass" in sub_codec or "ssa" in sub_codec: has_ass = True
            
    if has_chi: score += w.get("chi", 10)
    if has_ass: score += w.get("ass", 15)
        
    size = src.get("Size") or 0
    if strategy == "size" and size > 0: score -= int((size / (1024**3)) * 2)
        
    return score, {
        "res": res_str,
        "has_hdr": 1 if ("HDR" in v_range or "HDR" in v_title) else 0,
        "has_dovi": 1 if ("DOVI" in v_title or "DOLBY VISION" in v_title) else 0,
        "has_chi": 1 if has_chi else 0,
        "has_ass": 1 if has_ass else 0,
        "v_codec": codec.upper() if codec else "未知编码",
        "a_codec": a_codec.upper() if a_codec else "未知音轨"
    }

def normalize_title(name: str) -> str:
    if not name: 
        return ""
    t = name.lower()
    # 去掉括号类描述信息
    t = re.sub(r'[\[\(\{【（].*?[\]\)\}】）]', '', t)
    # 去掉所有空白与常见符号，仅保留中英文与数字
    t = re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', t)
    return t

def is_valid_tmdb(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if not s or s.lower() in {"none", "null", "0"}:
        return False
    return s.isdigit()

def run_dedupe_scan(strategy: str = "quality", custom_weights: dict = None):
    global scan_state
    start_time = time.time()
    
    scan_state["is_scanning"] = True
    scan_state["progress"] = 0
    scan_state["message"] = "阶段一：构建剧集映射树..."
    
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    try:
        admin_res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        admin_id = next((u['Id'] for u in admin_res if u.get("Policy", {}).get("IsAdministrator")), admin_res[0]['Id'])
        
        # 🔥 核心修复 1：在拉取单集前，预先拉取所有 Series，建立 [SeriesId -> TMDB ID] 的强映射字典
        series_map = {}
        try:
            s_url = f"{host}/emby/Users/{admin_id}/Items"
            s_params = { "IncludeItemTypes": "Series", "Recursive": "true", "Fields": "ProviderIds", "api_key": key }
            s_res = requests.get(s_url, params=s_params, timeout=15).json().get("Items", [])
            for s in s_res:
                tmdb_id = s.get("ProviderIds", {}).get("Tmdb")
                if tmdb_id: series_map[s["Id"]] = tmdb_id
        except Exception as e:
            logger.error(f"[去重引擎] 构建映射树失败: {e}")

        scan_state["message"] = "阶段二：极速抽取全库索引..."
        items = []; start = 0; limit = 10000
        while True:
            url = f"{host}/emby/Users/{admin_id}/Items"
            params = { "IncludeItemTypes": "Movie,Episode", "Recursive": "true", "Fields": "ProviderIds,ParentIndexNumber,IndexNumber,IndexNumberEnd,SeriesId", "StartIndex": start, "Limit": limit, "api_key": key }
            chunk = requests.get(url, params=params, timeout=30).json().get("Items", [])
            items.extend(chunk)
            if len(chunk) < limit: break
            start += limit
            scan_state["message"] = f"阶段二：已抽取 {len(items)} 条索引..."
            
        scan_state["total_items"] = len(items)
        scan_state["message"] = "阶段三：内存哈希碰撞匹配中..."
        
        whitelist = [r['group_key'] for r in query_db("SELECT group_key FROM dedupe_whitelist")]
        groups = defaultdict(list)
        for i in items:
            mtype = i.get("Type")
            if mtype == "Movie":
                tmdb = i.get("ProviderIds", {}).get("Tmdb")
                if not is_valid_tmdb(tmdb): continue
                norm_title = normalize_title(i.get("Name", ""))
                g_key = f"movie_{tmdb}_{norm_title}" if norm_title else f"movie_{tmdb}"
            elif mtype == "Episode":
                # 🔥 核心修复 2：优先使用「单集级」TMDB ID；缺失时再用 Series + S/E
                ep_tmdb = i.get("ProviderIds", {}).get("Tmdb")
                if is_valid_tmdb(ep_tmdb):
                    g_key = f"ep_{ep_tmdb}"
                else:
                    season = i.get("ParentIndexNumber")
                    episode = i.get("IndexNumber")
                    series_id = i.get("SeriesId") or i.get("ParentId") or "unknown"
                    series_tmdb = series_map.get(series_id)
                    series_tmdb = series_tmdb if is_valid_tmdb(series_tmdb) else series_id
                    # 如果缺少可靠的集号（例如 S0E0），则不参与去重，避免误判
                    if season is not None and episode is not None and episode > 0:
                        g_key = f"tv_{series_tmdb}_s{season}e{episode}"
                    else:
                        g_key = f"ep_{i.get('Id')}"
            else: continue
            
            if g_key not in whitelist: groups[g_key].append(i)
                
        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        scan_state["duplicate_groups"] = len(dup_groups)
        
        conn = sqlite3.connect(DB_PATH)
        conn.cursor().execute("DELETE FROM dedupe_results")
        conn.commit()

        total_dups = len(dup_groups); current = 0
        for g_key, item_list in dup_groups.items():
            current += 1
            scan_state["progress"] = int((current / total_dups) * 100)
            scan_state["message"] = f"阶段四：深层分析视频流 ({current}/{total_dups})"
            
            ids = ",".join([i["Id"] for i in item_list])
            detail_url = f"{host}/emby/Users/{admin_id}/Items?Ids={ids}&Fields=MediaSources,Path&api_key={key}"
            details = requests.get(detail_url, timeout=10).json().get("Items", [])
            
            parsed_items = []
            for d in details:
                is_exempt = 1 if d.get("IndexNumberEnd") and d.get("IndexNumberEnd") > d.get("IndexNumber", 0) else 0
                src = d.get("MediaSources", [{}])[0] if d.get("MediaSources") else {}
                score, tags = calculate_score(src, strategy, custom_weights)
                
                full_path = src.get("Path", "")
                file_name = full_path.split("/")[-1].split("\\")[-1] if full_path else d.get("Name", "未知文件")
                
                # 直接从 g_key 中反向提取出真实的 TMDB 标志
                tmdb_val = g_key.split("_")[1] if "_" in g_key else ""
                
                parsed_items.append({
                    "g_key": g_key, "tmdb": str(tmdb_val),
                    "mtype": d.get("Type"), "title": d.get("SeriesName") or d.get("Name", ""),
                    "season": d.get("ParentIndexNumber", 0), "episode": d.get("IndexNumber", 0),
                    "item_id": d["Id"], "file_name": file_name, "file_path": full_path,
                    "res": tags["res"], "bitrate": src.get("Bitrate") or 0, "size": src.get("Size") or 0, 
                    "v_codec": tags["v_codec"], "a_codec": tags["a_codec"],
                    "hdr": tags["has_hdr"], "dovi": tags["has_dovi"], "chi": tags["has_chi"], "ass": tags["has_ass"],
                    "score": score, "exempt": is_exempt
                })
            
            if parsed_items:
                parsed_items.sort(key=lambda x: x["score"], reverse=True)
                top_score = parsed_items[0]["score"]
                for idx, pi in enumerate(parsed_items):
                    pi["del_mark"] = 1 if idx > 0 and (top_score - pi["score"] >= 10) and pi["exempt"] == 0 else 0
                        
                for pi in parsed_items:
                    conn.cursor().execute('''INSERT INTO dedupe_results 
                        (group_key, tmdb_id, media_type, title, season_num, episode_num, item_id, file_name, file_path,
                         resolution, bitrate, size_bytes, video_codec, audio_codec, has_hdr, has_dovi, 
                         has_chi_sub, has_ass_sub, score, is_recommended_del, is_exempt) 
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (pi['g_key'], pi['tmdb'], pi['mtype'], pi['title'], pi['season'], pi['episode'], 
                         pi['item_id'], pi['file_name'], pi['file_path'], pi['res'], pi['bitrate'], pi['size'], pi['v_codec'], 
                         pi['a_codec'], pi['hdr'], pi['dovi'], pi['chi'], pi['ass'], pi['score'], pi['del_mark'], pi['exempt'])
                    )
            conn.commit()
            time.sleep(0.02)
            
        conn.close()
        elapsed = time.time() - start_time
        scan_state["message"] = f"✅ 扫描完成！遍历 {scan_state['total_items']} 项，发现 {scan_state['duplicate_groups']} 组重复"
        
    except Exception as e:
        logger.error(f"[去重引擎] 扫描异常: {e}")
        scan_state["message"] = f"❌ 扫描失败: {str(e)}"
    finally:
        time.sleep(2) 
        scan_state["is_scanning"] = False

class ScanReq(BaseModel):
    strategy: str = "quality"
    custom_weights: Optional[Dict[str, int]] = None

class DeleteReq(BaseModel):
    item_ids: List[str]
    username: str
    password: str

class IgnoreItem(BaseModel):
    group_key: str
    title: str

class IgnoreReq(BaseModel):
    items: List[IgnoreItem]

class RemoveWhitelistReq(BaseModel):
    group_keys: List[str]

@router.post("/scan")
async def trigger_scan(req: ScanReq, bg_tasks: BackgroundTasks):
    if scan_state["is_scanning"]: return {"success": False, "msg": "系统正在扫描中，请勿重复提交"}
    bg_tasks.add_task(run_dedupe_scan, req.strategy, req.custom_weights)
    return {"success": True, "msg": "🚀 扫描任务已在后台启动！"}

@router.get("/status")
async def get_scan_status():
    return {"success": True, "data": scan_state}

@router.get("/results")
async def get_results():
    rows = query_db("SELECT * FROM dedupe_results ORDER BY group_key, score DESC")
    result_tree = defaultdict(list)
    if rows:
        for r in rows: result_tree[r["group_key"]].append(dict(r))
        
    base_url = cfg.get("emby_public_url") or cfg.get("emby_host") or ""
    if base_url.endswith('/'): base_url = base_url[:-1]
    
    # 🔥 核心修复 3：主动抓取 Emby ServerId 喂给前端进行精确跳转
    server_id = ""
    try:
        host = cfg.get("emby_host"); key = cfg.get("emby_api_key")
        info_res = requests.get(f"{host}/emby/System/Info?api_key={key}", timeout=2).json()
        server_id = info_res.get("Id", "")
        if not server_id:
            item_res = requests.get(f"{host}/emby/Items?Limit=1&api_key={key}", timeout=2).json()
            if item_res.get("Items"): server_id = item_res["Items"][0].get("ServerId", "")
    except: pass
    
    return {"success": True, "data": result_tree, "emby_url": base_url, "server_id": server_id}

@router.post("/ignore")
async def ignore_groups(req: IgnoreReq):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        for item in req.items:
            c.execute("INSERT OR REPLACE INTO dedupe_whitelist (group_key, title) VALUES (?, ?)", (item.group_key, item.title))
            c.execute("DELETE FROM dedupe_results WHERE group_key = ?", (item.group_key,))
        conn.commit(); conn.close()
        return {"success": True, "msg": "已加入永久白名单"}
    except Exception as e: return {"success": False, "msg": str(e)}

@router.get("/whitelist")
async def get_whitelist():
    rows = query_db("SELECT * FROM dedupe_whitelist ORDER BY created_at DESC")
    return {"success": True, "data": [dict(r) for r in rows] if rows else []}

@router.post("/whitelist/remove")
async def remove_whitelist(req: RemoveWhitelistReq):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        for gk in req.group_keys: c.execute("DELETE FROM dedupe_whitelist WHERE group_key = ?", (gk,))
        conn.commit(); conn.close()
        return {"success": True, "msg": "已移出白名单"}
    except Exception as e: return {"success": False, "msg": str(e)}

@router.post("/delete")
async def delete_items(req: DeleteReq):
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    try:
        auth_url = f"{host}/emby/Users/AuthenticateByName?api_key={key}"
        auth_res = requests.post(auth_url, json={"Username": req.username, "Pw": req.password}, timeout=5)
        if auth_res.status_code != 200: return {"success": False, "msg": "🚫 权限被拒绝：Emby 管理员账号或密码错误！"}
        user_info = auth_res.json().get("User", {})
        if not user_info.get("Policy", {}).get("IsAdministrator"): return {"success": False, "msg": "🚫 权限被拒绝：该账号不具备管理员权限！"}
    except Exception as e: return {"success": False, "msg": f"⚠️ 连接 Emby 安全验证服务器失败: {e}"}
    
    success_count = 0; fail_count = 0
    for item_id in req.item_ids:
        try:
            res = requests.delete(f"{host}/emby/Items/{item_id}?api_key={key}", timeout=10)
            if res.status_code in [200, 204]:
                success_count += 1
                query_db("DELETE FROM dedupe_results WHERE item_id = ?", (item_id,))
            else: fail_count += 1
        except: fail_count += 1
            
    return {"success": True, "msg": f"操作完成。成功物理删除 {success_count} 个文件，失败 {fail_count} 个。"}
