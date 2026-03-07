from fastapi import APIRouter, BackgroundTasks
import requests
import threading
import concurrent.futures
from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import urllib.parse

from app.core.config import cfg
from app.core.database import query_db
from app.routers.search import get_emby_sys_info, is_new_emby_router

router = APIRouter(prefix="/api/gaps", tags=["gaps"])

# ----------------- 异步状态机 -----------------
scan_state = {"is_scanning": False, "progress": 0, "total": 0, "current_item": "系统准备中...", "results": [], "error": None}
state_lock = threading.Lock()

def update_progress(item_name=None):
    with state_lock:
        scan_state["progress"] += 1
        if item_name: scan_state["current_item"] = f"分析剧集: {item_name[:20]}"

def _get_proxies():
    proxy = cfg.get("proxy_url")
    return {"http": proxy, "https": proxy} if proxy else None

def get_admin_user_id():
    host = cfg.get("emby_host"); key = cfg.get("emby_api_key")
    if not host or not key: return None
    try:
        users = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
        return users[0]['Id'] if users else None
    except: return None

def process_single_series(series, lock_map, host, key, tmdb_key, proxies, today, global_inventory, server_id, use_new_route):
    series_id = series.get("Id"); series_name = series.get("Name", "未知剧集")
    tmdb_id = series.get("ProviderIds", {}).get("Tmdb")
    
    if not tmdb_id or lock_map.get(f"{series_id}_-1_-1", 0) == 1:
        update_progress(series_name)
        return None

    local_inventory = global_inventory.get(series_id, {})
    try:
        tmdb_series_data = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=zh-CN&api_key={tmdb_key}", proxies=proxies, timeout=10).json()
        tmdb_seasons = tmdb_series_data.get("seasons", []); tmdb_status = tmdb_series_data.get("status", "") 
    except: 
        update_progress(series_name)
        return None

    series_gaps = []
    for season in tmdb_seasons:
        s_num = season.get("season_number")
        if not s_num or season.get("episode_count", 0) == 0: continue
        local_season_inventory = local_inventory.get(s_num, set())
        if len(local_season_inventory) >= season.get("episode_count", 0): continue
        
        try:
            tmdb_episodes = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{s_num}?language=zh-CN&api_key={tmdb_key}", proxies=proxies, timeout=10).json().get("episodes", [])
        except: continue
        
        for tmdb_ep in tmdb_episodes:
            e_num = tmdb_ep.get("episode_number"); air_date = tmdb_ep.get("air_date")
            if not air_date or air_date > today: continue
            if e_num not in local_season_inventory and lock_map.get(f"{series_id}_{s_num}_{e_num}", 0) != 1:
                series_gaps.append({"season": s_num, "episode": e_num, "title": tmdb_ep.get("name", f"第 {e_num} 集"), "status": lock_map.get(f"{series_id}_{s_num}_{e_num}", 0)})
    
    update_progress(series_name) 
    
    if series_gaps:
        public_host = (cfg.get("emby_public_url") or cfg.get("emby_external_url") or cfg.get("emby_public_host") or host).rstrip('/')
        emby_url = f"{public_host}/web/index.html#!/item?id={series_id}&serverId={server_id}" if use_new_route else f"{public_host}/web/index.html#!/item/details.html?id={series_id}&serverId={server_id}"
        return {"series_id": series_id, "series_name": series_name, "tmdb_id": tmdb_id, "poster": f"/api/library/image/{series_id}?type=Primary&width=300", "emby_url": emby_url, "gaps": series_gaps}
    else:
        if tmdb_status in ["Ended", "Canceled"]:
            try: query_db("INSERT OR IGNORE INTO gap_perfect_series (series_id, tmdb_id, series_name) VALUES (?, ?, ?)", (series_id, tmdb_id, series_name))
            except: pass
        return None

def run_scan_task():
    try:
        host = cfg.get("emby_host"); key = cfg.get("emby_api_key"); tmdb_key = cfg.get("tmdb_api_key"); admin_id = get_admin_user_id()
        proxies = _get_proxies(); today = datetime.now().strftime("%Y-%m-%d")
        
        try:
            sys_info = requests.get(f"{host}/emby/System/Info/Public", timeout=5).json()
            server_id = sys_info.get("Id", "")
            use_new_route = is_new_emby_router(sys_info)
        except: server_id = ""; use_new_route = True

        query_db("CREATE TABLE IF NOT EXISTS gap_perfect_series (series_id TEXT PRIMARY KEY, tmdb_id TEXT, series_name TEXT, marked_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        query_db("CREATE TABLE IF NOT EXISTS gap_scan_cache (id INTEGER PRIMARY KEY, result_json TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)")

        records = query_db("SELECT series_id, season_number, episode_number, status FROM gap_records")
        lock_map = {f"{r['series_id']}_{r['season_number']}_{r['episode_number']}": r['status'] for r in records} if records else {}
        perfect_records = query_db("SELECT series_id FROM gap_perfect_series")
        perfect_set = set([r['series_id'] for r in perfect_records]) if perfect_records else set()

        all_series = requests.get(f"{host}/emby/Users/{admin_id}/Items?IncludeItemTypes=Series&Recursive=true&Fields=ProviderIds&api_key={key}", timeout=15).json().get("Items", [])
        pending_series = [s for s in all_series if s.get("Id") not in perfect_set]

        with state_lock:
            scan_state["total"] = len(pending_series)
            scan_state["current_item"] = "正在拉取全库单集缓存..."

        if not pending_series:
            with state_lock: scan_state["results"] = []
            return

        all_eps_data = requests.get(f"{host}/emby/Users/{admin_id}/Items?IncludeItemTypes=Episode&Recursive=true&Fields=IndexNumberEnd&api_key={key}", timeout=45).json().get("Items", [])
        global_inventory = {}
        for ep in all_eps_data:
            ser_id = ep.get("SeriesId"); s_num = ep.get("ParentIndexNumber"); e_num = ep.get("IndexNumber"); e_end = ep.get("IndexNumberEnd")
            if not ser_id or s_num is None or e_num is None: continue
            if ser_id not in global_inventory: global_inventory[ser_id] = {}
            if s_num not in global_inventory[ser_id]: global_inventory[ser_id][s_num] = set()
            for i in range(e_num, (e_end if e_end else e_num) + 1): global_inventory[ser_id][s_num].add(i)

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_single_series, s, lock_map, host, key, tmdb_key, proxies, today, global_inventory, server_id, use_new_route) for s in pending_series]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res: results.append(res)
        
        with state_lock: scan_state["results"] = results
        try: query_db("INSERT OR REPLACE INTO gap_scan_cache (id, result_json, updated_at) VALUES (1, ?, datetime('now', 'localtime'))", (json.dumps(results),))
        except: pass
    except Exception as e:
        with state_lock: scan_state["error"] = str(e)
    finally:
        with state_lock: scan_state["is_scanning"] = False; scan_state["current_item"] = "扫描完成"

def daily_scan_scheduler():
    while True:
        try:
            now = datetime.now()
            if now.hour == 3 and now.minute == 0:
                res = query_db("SELECT status FROM gap_records WHERE series_id='SYSTEM' AND season_number=-99")
                if res and res[0]['status'] == 1 and not scan_state["is_scanning"]: 
                    run_scan_task()
            time.sleep(60)
        except: time.sleep(60)

threading.Thread(target=daily_scan_scheduler, daemon=True).start()

@router.post("/scan/start")
def start_scan(bg_tasks: BackgroundTasks):
    with state_lock:
        if scan_state["is_scanning"]: return {"status": "error"}
        scan_state.update({"is_scanning": True, "progress": 0, "total": 0, "results": [], "error": None, "current_item": "系统准备中..."})
    bg_tasks.add_task(run_scan_task)
    return {"status": "success"}

@router.get("/scan/progress")
def get_progress():
    with state_lock:
        if not scan_state["is_scanning"]:
            if not scan_state["results"]:
                try:
                    row = query_db("SELECT result_json FROM gap_scan_cache WHERE id = 1")
                    if row: scan_state["results"] = json.loads(row[0]['result_json'])
                except: pass
            
            # 🔥 强制动态剥离：过滤数据库里的忽略名单
            try:
                ignores = query_db("SELECT series_id FROM gap_records WHERE status=1 AND season_number=-1")
                ignore_ids = set([r['series_id'] for r in ignores]) if ignores else set()
                scan_state["results"] = [s for s in scan_state["results"] if s.get('series_id') not in ignore_ids]
            except: pass
            
        return {"status": "success", "data": scan_state}

@router.post("/scan/auto_toggle")
def toggle_auto_scan(payload: dict):
    enabled = 1 if payload.get("enabled") else 0
    query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES ('SYSTEM', 'AUTO_SCAN', -99, -99, ?) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = ?", (enabled, enabled))
    return {"status": "success"}

@router.get("/scan/auto_status")
def get_auto_status():
    try: return {"status": "success", "enabled": bool(query_db("SELECT status FROM gap_records WHERE series_id='SYSTEM' AND season_number=-99")[0]['status'])}
    except: return {"status": "success", "enabled": False}

@router.post("/ignore")
def ignore_gap(payload: dict):
    try:
        s_id = payload.get("series_id")
        s_num = int(payload.get("season_number", 0))
        e_num = int(payload.get("episode_number", 0))
        query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, ?, ?, 1) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1", (s_id, payload.get("series_name", ""), s_num, e_num))
        
        # 内存同步：瞬间抹除被忽略的单集
        with state_lock:
            for s in scan_state["results"]:
                if s.get("series_id") == s_id:
                    s["gaps"] = [ep for ep in s.get("gaps", []) if not (ep["season"] == s_num and ep["episode"] == e_num)]
            scan_state["results"] = [s for s in scan_state["results"] if len(s.get("gaps", [])) > 0]
            
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

@router.post("/ignore/series")
def ignore_entire_series(payload: dict):
    try:
        s_id = payload.get("series_id")
        query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, -1, -1, 1) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1", (s_id, payload.get("series_name", "")))
        
        # 内存同步：瞬间抹除整部剧
        with state_lock:
            scan_state["results"] = [s for s in scan_state["results"] if s.get("series_id") != s_id]
            
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

@router.get("/ignores")
def get_ignored_list():
    try:
        records = query_db("SELECT id, series_id, series_name, season_number, episode_number, created_at FROM gap_records WHERE status = 1 AND series_id != 'SYSTEM'")
        perfects = query_db("SELECT series_id, series_name, marked_at FROM gap_perfect_series")
        data = []
        if records:
            for r in records:
                typ = "全剧集" if r['season_number'] == -1 else f"S{str(r['season_number']).zfill(2)}E{str(r['episode_number']).zfill(2)}"
                data.append({"type": "record", "id": r['id'], "series_name": r['series_name'], "target": typ, "time": r['created_at']})
        if perfects:
            for r in perfects: data.append({"type": "perfect", "id": r['series_id'], "series_name": r['series_name'], "target": "完结免检金牌", "time": r['marked_at']})
        data.sort(key=lambda x: x['time'], reverse=True)
        return {"status": "success", "data": data}
    except Exception as e: return {"status": "error"}

@router.post("/unignore")
def unignore_item(payload: dict):
    try:
        if payload.get("type") == "record": query_db("DELETE FROM gap_records WHERE id = ?", (payload.get("id"),))
        elif payload.get("type") == "perfect": query_db("DELETE FROM gap_perfect_series WHERE series_id = ?", (payload.get("id"),))
        return {"status": "success"}
    except Exception as e: return {"status": "error"}


# ==========================================
# 🔥 万能字典接口，规避 422
# ==========================================

@router.post("/search_mp")
def search_mp_for_gap(payload: dict):
    series_id = payload.get("series_id")
    series_name = payload.get("series_name")
    season = payload.get("season")
    episodes = payload.get("episodes", [])

    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    mp_url = cfg.get("moviepilot_url")
    mp_token = cfg.get("moviepilot_token")
    
    if not mp_url or not mp_token: return {"status": "error", "message": "未配置 MP"}
    
    admin_id = get_admin_user_id()
    genes = []
    if admin_id:
        try:
            items = requests.get(f"{host}/emby/Users/{admin_id}/Items?ParentId={series_id}&IncludeItemTypes=Episode&Recursive=true&Limit=1&Fields=MediaSources&api_key={key}", timeout=5).json().get("Items", [])
            if items and items[0].get("MediaSources"):
                video = next((s for s in items[0]["MediaSources"][0].get("MediaStreams", []) if s.get("Type") == "Video"), None)
                if video:
                    if video.get("Width", 0) >= 3800: genes.append("4K")
                    elif video.get("Width", 0) >= 1900: genes.append("1080P")
                    d_title = video.get("DisplayTitle", "").upper()
                    if "HDR" in video.get("VideoRange", "") or "HDR" in d_title: genes.append("HDR")
                    if "DOVI" in d_title or "DOLBY VISION" in d_title: genes.append("DoVi")
        except: pass
    if not genes: genes = ["无明显特效"]
    
    clean_token = mp_token.strip().strip("'\"")
    headers = {"X-API-KEY": clean_token, "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    
    def deep_extract(d, keys):
        for k in keys:
            if d.get(k) is not None and str(d.get(k)).strip() != "": return d.get(k)
        for nested in ["torrent", "torrent_info", "detail", "data", "info"]:
            if isinstance(d.get(nested), dict):
                for k in keys:
                    if d[nested].get(k) is not None and str(d[nested].get(k)).strip() != "": return d[nested].get(k)
        return None

    try:
        results = []
        is_pack = False
        
        if episodes and len(episodes) == 1:
            keyword = f"{series_name} S{str(season).zfill(2)}E{str(episodes[0]).zfill(2)}"
            mp_res = requests.get(f"{mp_url.rstrip('/')}/api/v1/search/title?keyword={urllib.parse.quote(keyword)}", headers=headers, timeout=20)
            res_data = mp_res.json() if mp_res.status_code == 200 else []
            if isinstance(res_data, dict): res_data = res_data.get("data") or res_data.get("results") or []
            if isinstance(res_data, list): results = res_data
        
        if len(results) == 0:
            fallback_kw = f"{series_name} S{str(season).zfill(2)}"
            mp_res2 = requests.get(f"{mp_url.rstrip('/')}/api/v1/search/title?keyword={urllib.parse.quote(fallback_kw)}", headers=headers, timeout=20)
            res_data2 = mp_res2.json() if mp_res2.status_code == 200 else []
            if isinstance(res_data2, dict): res_data2 = res_data2.get("data") or res_data2.get("results") or []
            if isinstance(res_data2, list): 
                results = res_data2
                is_pack = True

        processed_results = []
        for r in results:
            score = 0
            title_str = str(deep_extract(r, ["name", "title", "torrent_name"]) or "未提取到种名")
            desc_str = str(deep_extract(r, ["description", "desc", "detail", "subtitle"]) or "")
            combined_text = title_str.upper() + " " + desc_str.upper()
            size_val = deep_extract(r, ["size", "enclosure_size", "torrent_size"]) or 0
            enclosure_val = deep_extract(r, ["enclosure", "download_url", "url", "link"]) or ""
            site_val = deep_extract(r, ["site", "site_name", "indexer"]) or "未知站点"
            
            if "4K" in genes: score += 50 if ("2160P" in combined_text or "4K" in combined_text) else -20
            if "1080P" in genes and "1080P" in combined_text: score += 50
            if "DoVi" in genes and ("DOVI" in combined_text or "VISION" in combined_text): score += 30
            if "HDR" in genes and "HDR" in combined_text: score += 20
            if "WEB" in combined_text: score += 10
            
            org_payload = {k: v for k, v in r.items() if not k.startswith("ui_") and k not in ["match_score", "is_pack", "extracted_tags", "org_payload"]}
            org_payload["title"] = title_str
            try: org_payload["size"] = int(size_val)
            except: org_payload["size"] = 0
            if enclosure_val: org_payload["enclosure"] = enclosure_val
            if site_val: org_payload["site"] = site_val
            
            r["ui_title"] = title_str  
            try: r["ui_size"] = float(size_val)
            except: r["ui_size"] = 0
            r["match_score"] = score
            r["is_pack"] = is_pack 
            r["org_payload"] = org_payload 
            
            tags = []
            if "2160P" in combined_text or "4K" in combined_text: tags.append("4K")
            elif "1080P" in combined_text: tags.append("1080P")
            if "DOVI" in combined_text or "VISION" in combined_text: tags.append("DoVi")
            elif "HDR" in combined_text: tags.append("HDR")
            if "WEB" in combined_text: tags.append("WEB-DL")
            r["extracted_tags"] = tags
            processed_results.append(r)

        processed_results.sort(key=lambda x: x["match_score"], reverse=True)
        return {"status": "success", "data": {"genes": genes, "results": processed_results[:10]}}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/download")
def download_gap_item(payload: dict):
    series_id = payload.get("series_id")
    series_name = payload.get("series_name")
    tmdbid = payload.get("tmdbid")
    season = payload.get("season")
    episodes = payload.get("episodes", [])
    torrent_info = payload.get("torrent_info", {})

    print(f"\n[缺集推送] 收到前端完好无损的原始请求: {json.dumps(payload, ensure_ascii=False)[:300]}...\n")

    mp_url = cfg.get("moviepilot_url")
    mp_token = cfg.get("moviepilot_token")
    clean_token = mp_token.strip().strip("'\"") if mp_token else ""
    headers = {"X-API-KEY": clean_token, "Content-Type": "application/json"}
    
    # 这一刻 torrent_info 绝对是一个完美的字典，再也不会报 get 错误了
    pure_torrent_in = torrent_info.get("org_payload", torrent_info)
    
    if torrent_info.get("is_pack", False) or len(episodes) > 1:
        pure_torrent_in["season"] = int(season) if season else 1
        pure_torrent_in["episode"] = [int(e) for e in episodes] 

    mp_payload = {"torrent_in": pure_torrent_in}
    if tmdbid:
        try: mp_payload["tmdbid"] = int(tmdbid)
        except: pass

    print(f"[缺集推送] 发往 MP 的提纯字典: {json.dumps(mp_payload, ensure_ascii=False)}\n")

    try:
        add_url = f"{mp_url.rstrip('/')}/api/v1/download/add"
        res = requests.post(add_url, headers=headers, json=mp_payload, timeout=10)
        
        if res.status_code in [200, 201]:
            try:
                res_data = res.json()
                if res_data.get("success") == False:
                    return {"status": "error", "message": res_data.get("message") or "MP 拒绝下载，请检查官方日志"}
            except: pass

            for ep in episodes:
                query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, ?, ?, 2) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 2", (series_id, series_name, int(season), int(ep)))
            
            with state_lock:
                for s in scan_state["results"]:
                    if s.get("series_id") == series_id:
                        for ep_obj in s.get("gaps", []):
                            if ep_obj["season"] == int(season) and ep_obj["episode"] in [int(e) for e in episodes]:
                                ep_obj["status"] = 2

            return {"status": "success", "message": f"已成功向 MP 下发 {len(episodes)} 集提取指令"}
            
        return {"status": "error", "message": f"MP 接口拒绝 (HTTP {res.status_code})"}
    except Exception as e: 
        print(f"[缺集推送] 请求异常: {e}")
        return {"status": "error", "message": str(e)}