from fastapi import APIRouter, BackgroundTasks, Request
import requests
import threading
import concurrent.futures
from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import urllib.parse
import re
import time
import ipaddress
import socket

from app.core.config import cfg
from app.core.database import query_db
from app.routers.search import get_emby_sys_info, is_new_emby_router
# 🔥 引入核心适配器
from app.core.media_adapter import media_api
from app.core.security import get_client_ip

router = APIRouter(prefix="/api/gaps", tags=["gaps"])

scan_state = {"is_scanning": False, "progress": 0, "total": 0, "current_item": "系统准备中...", "results": [], "error": None}
state_lock = threading.Lock()

def update_progress(item_name=None):
    with state_lock:
        scan_state["progress"] += 1
        if item_name: scan_state["current_item"] = f"分析剧集: {item_name[:20]}"

def _get_proxies():
    proxy = cfg.get("proxy_url")
    return {"http": proxy, "https": proxy} if proxy else None

def _extract_host_ip(url: str):
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return None
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            try:
                return socket.gethostbyname(host)
            except:
                return host
    except:
        return None

def _same_lan(ip_a: str, ip_b: str):
    try:
        a = ipaddress.ip_address(ip_a)
        b = ipaddress.ip_address(ip_b)
        # Allow localhost (IPv4/IPv6) to treat private hosts as LAN
        if a.is_loopback:
            return b.is_loopback or b.is_private
        if b.is_loopback:
            return a.is_loopback or a.is_private
        if a.version != b.version:
            return False
        if not (a.is_private and b.is_private):
            return False
        net = ipaddress.ip_network(f"{a}/24", strict=False)
        return b in net
    except:
        return False

def _extract_server_id_from_url(url: str):
    try:
        if not url:
            return ""
        parsed = urllib.parse.urlparse(url)
        def _from_qs(qs: str):
            q = urllib.parse.parse_qs(qs)
            vals = q.get("serverId") or q.get("serverid") or []
            return vals[0] if vals else ""
        sid = _from_qs(parsed.query)
        if sid:
            return sid
        frag = parsed.fragment or ""
        if frag:
            frag_qs = frag.split("?", 1)[1] if "?" in frag else frag
            sid = _from_qs(frag_qs)
            if sid:
                return sid
            m = re.search(r"(?:serverId|serverid)=([^&]+)", frag)
            if m:
                return m.group(1)
        m = re.search(r"(?:serverId|serverid)=([^&]+)", url)
        if m:
            return m.group(1)
        return ""
    except:
        return ""

def _infer_use_new_route(url: str):
    try:
        return False if "details.html" in (url or "") else True
    except:
        return True

def _build_emby_item_url(base_url: str, series_id: str, server_id: str, use_new_route: bool):
    base = (base_url or "").rstrip("/")
    if use_new_route:
        return f"{base}/web/index.html#!/item?id={series_id}&serverId={server_id}"
    return f"{base}/web/index.html#!/item/details.html?id={series_id}&serverId={server_id}"

def _is_lan_access_host(hostname: str):
    try:
        if not hostname:
            return False
        if hostname.lower() in ("localhost",):
            return True
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except:
        return False

def get_admin_user_id():
    try:
        # 🚀 替换为 media_api
        users = media_api.get("/Users", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
        return users[0]['Id'] if users else None
    except: return None

# 🔥 修复：移除了多余的 key 参数
def process_single_series(series, lock_map, host, tmdb_key, proxies, today, global_inventory, server_id, use_new_route):
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
        try: tmdb_episodes = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{s_num}?language=zh-CN&api_key={tmdb_key}", proxies=proxies, timeout=10).json().get("episodes", [])
        except: continue
        for tmdb_ep in tmdb_episodes:
            e_num = tmdb_ep.get("episode_number"); air_date = tmdb_ep.get("air_date")
            
            if not air_date or air_date >= today: continue
                
            if e_num not in local_season_inventory and lock_map.get(f"{series_id}_{s_num}_{e_num}", 0) != 1:
                series_gaps.append({"season": s_num, "episode": e_num, "title": tmdb_ep.get("name", f"第 {e_num} 集"), "status": lock_map.get(f"{series_id}_{s_num}_{e_num}", 0)})
    
    update_progress(series_name) 
    if series_gaps:
        public_host = (cfg.get("emby_public_url") or cfg.get("emby_external_url") or cfg.get("emby_public_host") or host).rstrip('/')
        emby_url = f"{public_host}/web/index.html#!/item?id={series_id}&serverId={server_id}" if use_new_route else f"{public_host}/web/index.html#!/item/details.html?id={series_id}&serverId={server_id}"
        return {
            "series_id": series_id,
            "series_name": series_name,
            "tmdb_id": tmdb_id,
            "tmdb_status": tmdb_status,
            "poster": f"/api/library/image/{series_id}?type=Primary&width=300",
            "emby_url": emby_url,
            "server_id": server_id,
            "use_new_route": use_new_route,
            "gaps": series_gaps
        }
    else:
        if tmdb_status in ["Ended", "Canceled"]:
            try: query_db("INSERT OR IGNORE INTO gap_perfect_series (series_id, tmdb_id, series_name) VALUES (?, ?, ?)", (series_id, tmdb_id, series_name))
            except: pass
        return None

def run_scan_task():
    try:
        host = cfg.get("emby_host"); tmdb_key = cfg.get("tmdb_api_key"); admin_id = get_admin_user_id()
        proxies = _get_proxies(); today = datetime.now().strftime("%Y-%m-%d")
        try:
            # 🚀 替换为 media_api
            sys_info = media_api.get("/System/Info/Public", timeout=5).json()
            server_id = sys_info.get("Id", ""); use_new_route = is_new_emby_router(sys_info)
        except: server_id = ""; use_new_route = True

        query_db("CREATE TABLE IF NOT EXISTS gap_perfect_series (series_id TEXT PRIMARY KEY, tmdb_id TEXT, series_name TEXT, marked_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        query_db("CREATE TABLE IF NOT EXISTS gap_scan_cache (id INTEGER PRIMARY KEY, result_json TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)")

        records = query_db("SELECT series_id, season_number, episode_number, status FROM gap_records")
        lock_map = {f"{r['series_id']}_{r['season_number']}_{r['episode_number']}": r['status'] for r in records} if records else {}
        perfect_records = query_db("SELECT series_id FROM gap_perfect_series")
        perfect_set = set([r['series_id'] for r in perfect_records]) if perfect_records else set()

        # 🚀 替换为 media_api
        all_series = media_api.get(f"/Users/{admin_id}/Items", params={"IncludeItemTypes":"Series","Recursive":"true","Fields":"ProviderIds"}, timeout=15).json().get("Items", [])
        pending_series = [s for s in all_series if s.get("Id") not in perfect_set]

        with state_lock:
            scan_state["total"] = len(pending_series)
            scan_state["current_item"] = "正在拉取全库单集缓存..."

        if not pending_series:
            with state_lock: scan_state["results"] = []
            return

        # 🚀 替换为 media_api
        all_eps_data = media_api.get(f"/Users/{admin_id}/Items", params={"IncludeItemTypes":"Episode","Recursive":"true","Fields":"IndexNumberEnd"}, timeout=45).json().get("Items", [])
        global_inventory = {}
        for ep in all_eps_data:
            ser_id = ep.get("SeriesId"); s_num = ep.get("ParentIndexNumber"); e_num = ep.get("IndexNumber"); e_end = ep.get("IndexNumberEnd")
            if not ser_id or s_num is None or e_num is None: continue
            if ser_id not in global_inventory: global_inventory[ser_id] = {}
            if s_num not in global_inventory[ser_id]: global_inventory[ser_id][s_num] = set()
            for i in range(e_num, (e_end if e_end else e_num) + 1): global_inventory[ser_id][s_num].add(i)

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # 🔥 修复：调用时移除了多余的 key
            futures = [executor.submit(process_single_series, s, lock_map, host, tmdb_key, proxies, today, global_inventory, server_id, use_new_route) for s in pending_series]
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

@router.post("/scan/start")
def start_scan(bg_tasks: BackgroundTasks):
    with state_lock:
        if scan_state["is_scanning"]: return {"status": "error"}
        scan_state.update({"is_scanning": True, "progress": 0, "total": 0, "results": [], "error": None, "current_item": "系统准备中..."})
    bg_tasks.add_task(run_scan_task)
    return {"status": "success"}

@router.get("/scan/progress")
def get_progress(request: Request):
    with state_lock:
        if not scan_state["is_scanning"]:
            if not scan_state["results"]:
                try:
                    row = query_db("SELECT result_json FROM gap_scan_cache WHERE id = 1")
                    if row: scan_state["results"] = json.loads(row[0]['result_json'])
                except: pass
            try:
                ignores = query_db("SELECT series_id FROM gap_records WHERE status=1 AND season_number=-1")
                ignore_ids = set([r['series_id'] for r in ignores]) if ignores else set()
                scan_state["results"] = [s for s in scan_state["results"] if s.get('series_id') not in ignore_ids]
            except: pass
        base_data = {k: v for k, v in scan_state.items() if k != "results"}
        results_copy = [dict(s) for s in (scan_state.get("results") or [])]

    host_url = (cfg.get("emby_host") or "").rstrip("/")
    public_url = (cfg.get("emby_public_url") or cfg.get("emby_external_url") or cfg.get("emby_public_host") or "").rstrip("/")
    access_host = ""
    try:
        access_host = (request.url.hostname or "").strip()
    except:
        access_host = ""
    if not access_host:
        try:
            raw_host = (request.headers.get("host") or "").strip()
            access_host = raw_host.split(":")[0] if raw_host else ""
        except:
            access_host = ""
    use_local = _is_lan_access_host(access_host)
    base_url = host_url if use_local else (public_url or host_url)

    results_out = []
    for s in results_copy:
        series_id = s.get("series_id")
        if series_id:
            server_id = s.get("server_id") or _extract_server_id_from_url(s.get("emby_url", ""))
            use_new_route = s.get("use_new_route")
            if use_new_route is None:
                use_new_route = _infer_use_new_route(s.get("emby_url", ""))
            s["emby_url"] = _build_emby_item_url(base_url, series_id, server_id, use_new_route)
        results_out.append(s)

    return {"status": "success", "data": {**base_data, "results": results_out}}

def run_verify_task():
    try:
        with state_lock:
            if scan_state["is_scanning"] or not scan_state.get("results"): return
            results_copy = json.loads(json.dumps(scan_state["results"]))
            
        admin_id = get_admin_user_id()
        if not admin_id: return
        
        changed = False
        for s in results_copy:
            s_id = s.get("series_id")
            if not s.get("gaps"): continue
            
            try:
                # 🚀 替换为 media_api
                eps_data = media_api.get(f"/Users/{admin_id}/Items", params={"ParentId":s_id,"IncludeItemTypes":"Episode","Recursive":"true","Fields":"IndexNumberEnd"}, timeout=5).json().get("Items", [])
                local_eps = set()
                for ep in eps_data:
                    s_num = ep.get("ParentIndexNumber")
                    e_num = ep.get("IndexNumber")
                    e_end = ep.get("IndexNumberEnd")
                    if s_num is None or e_num is None: continue
                    for i in range(e_num, (e_end if e_end else e_num) + 1):
                        local_eps.add(f"{s_num}_{i}")
                
                original_len = len(s["gaps"])
                new_gaps = []
                for gap in s["gaps"]:
                    if f"{gap['season']}_{gap['episode']}" in local_eps:
                        changed = True
                        try: query_db("DELETE FROM gap_records WHERE series_id=? AND season_number=? AND episode_number=?", (s_id, gap['season'], gap['episode']))
                        except: pass
                    else:
                        new_gaps.append(gap)
                s["gaps"] = new_gaps
                
                if len(new_gaps) == 0 and changed:
                    if s.get("tmdb_status") in ["Ended", "Canceled"]:
                        try: query_db("INSERT OR IGNORE INTO gap_perfect_series (series_id, tmdb_id, series_name) VALUES (?, ?, ?)", (s_id, s.get("tmdb_id"), s.get("series_name")))
                        except: pass
            except: pass
                
        if changed:
            with state_lock:
                if not scan_state["is_scanning"]:
                    scan_state["results"] = [s for s in results_copy if len(s.get("gaps", [])) > 0]
                    try: query_db("INSERT OR REPLACE INTO gap_scan_cache (id, result_json, updated_at) VALUES (1, ?, datetime('now', 'localtime'))", (json.dumps(scan_state["results"]),))
                    except: pass
    except: pass

@router.post("/scan/verify")
def trigger_verify_gaps(bg_tasks: BackgroundTasks):
    with state_lock:
        if scan_state["is_scanning"]: return {"status": "success"}
    bg_tasks.add_task(run_verify_task)
    return {"status": "success"}

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
        s_id = payload.get("series_id"); s_num = int(payload.get("season_number", 0)); e_num = int(payload.get("episode_number", 0))
        query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, ?, ?, 1) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1", (s_id, payload.get("series_name", ""), s_num, e_num))
        with state_lock:
            for s in scan_state["results"]:
                if s.get("series_id") == s_id: s["gaps"] = [ep for ep in s.get("gaps", []) if not (ep["season"] == s_num and ep["episode"] == e_num)]
            scan_state["results"] = [s for s in scan_state["results"] if len(s.get("gaps", [])) > 0]
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

@router.post("/ignore/series")
def ignore_entire_series(payload: dict):
    try:
        s_id = payload.get("series_id")
        query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, -1, -1, 1) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 1", (s_id, payload.get("series_name", "")))
        with state_lock: scan_state["results"] = [s for s in scan_state["results"] if s.get("series_id") != s_id]
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

@router.get("/ignores")
def get_ignored_list():
    try:
        records = query_db("SELECT id, series_id, series_name, season_number, episode_number, created_at FROM gap_records WHERE status = 1 AND series_id != 'SYSTEM'")
        perfects = query_db("SELECT series_id, series_name, marked_at FROM gap_perfect_series")
        data = []
        if records:
            for r in records: data.append({"type": "record", "id": r['id'], "series_name": r['series_name'], "target": "全剧集" if r['season_number'] == -1 else f"S{str(r['season_number']).zfill(2)}E{str(r['episode_number']).zfill(2)}", "time": r['created_at']})
        if perfects:
            for r in perfects: data.append({"type": "perfect", "id": r['series_id'], "series_name": r['series_name'], "target": "完结免检金牌", "time": r['marked_at']})
        data.sort(key=lambda x: str(x['time'] or '0000-00-00'), reverse=True)
        return {"status": "success", "data": data}
    except Exception as e: return {"status": "error"}

@router.post("/unignore")
def unignore_item(payload: dict):
    try:
        if payload.get("type") == "record": query_db("DELETE FROM gap_records WHERE id = ?", (payload.get("id"),))
        elif payload.get("type") == "perfect": query_db("DELETE FROM gap_perfect_series WHERE series_id = ?", (payload.get("id"),))
        return {"status": "success"}
    except Exception as e: return {"status": "error"}

@router.get("/config")
def get_gap_config():
    query_db("CREATE TABLE IF NOT EXISTS gap_config (key TEXT PRIMARY KEY, value TEXT)")
    rows = query_db("SELECT key, value FROM gap_config")
    conf = {r['key']: r['value'] for r in rows} if rows else {}
    return {"status": "success", "data": conf}

@router.post("/config")
def save_gap_config(payload: dict):
    query_db("CREATE TABLE IF NOT EXISTS gap_config (key TEXT PRIMARY KEY, value TEXT)")
    for k, v in payload.items():
        query_db("INSERT OR REPLACE INTO gap_config (key, value) VALUES (?, ?)", (k, str(v).strip()))
    return {"status": "success"}

@router.post("/search_mp")
def search_mp_for_gap(payload: dict):
    series_id = payload.get("series_id"); series_name = payload.get("series_name")
    season = payload.get("season"); episodes = payload.get("episodes", [])
    mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
    if not mp_url or not mp_token: return {"status": "error", "message": "未配置 MP"}
    
    admin_id = get_admin_user_id(); genes = []
    if admin_id:
        try:
            # 🚀 替换为 media_api
            items = media_api.get(f"/Users/{admin_id}/Items", params={"ParentId":series_id,"IncludeItemTypes":"Episode","Recursive":"true","Limit":1,"Fields":"MediaSources"}, timeout=5).json().get("Items", [])
            if items and items[0].get("MediaSources"):
                v = next((s for s in items[0]["MediaSources"][0].get("MediaStreams", []) if s.get("Type") == "Video"), None)
                if v:
                    if v.get("Width", 0) >= 3800: genes.append("4K")
                    elif v.get("Width", 0) >= 1900: genes.append("1080P")
                    if "HDR" in v.get("VideoRange", "") or "HDR" in v.get("DisplayTitle", "").upper(): genes.append("HDR")
                    if "DOVI" in v.get("DisplayTitle", "").upper() or "DOLBY VISION" in v.get("DisplayTitle", "").upper(): genes.append("DoVi")
        except: pass
    if not genes: genes = ["无明显特效"]
    
    headers = {"X-API-KEY": mp_token.strip().strip("'\""), "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    
    def deep_extract(d, keys):
        for k in keys:
            if d.get(k) is not None and str(d.get(k)).strip() != "": return d.get(k)
        for n in ["torrent", "torrent_info", "detail", "data", "info"]:
            if isinstance(d.get(n), dict):
                for k in keys:
                    if d[n].get(k) is not None and str(d[n].get(k)).strip() != "": return d[n].get(k)
        return None

    try:
        results = []; is_pack = False
        if len(episodes) == 1:
            kw = f"{series_name} S{str(season).zfill(2)}E{str(episodes[0]).zfill(2)}"
            res_data = requests.get(f"{mp_url.rstrip('/')}/api/v1/search/title?keyword={urllib.parse.quote(kw)}", headers=headers, timeout=20).json()
            if isinstance(res_data, dict): res_data = res_data.get("data") or res_data.get("results") or []
            if isinstance(res_data, list): results = res_data
        
        if len(results) == 0:
            kw2 = f"{series_name} S{str(season).zfill(2)}"
            res_data2 = requests.get(f"{mp_url.rstrip('/')}/api/v1/search/title?keyword={urllib.parse.quote(kw2)}", headers=headers, timeout=20).json()
            if isinstance(res_data2, dict): res_data2 = res_data2.get("data") or res_data2.get("results") or []
            if isinstance(res_data2, list): results = res_data2; is_pack = True

        processed = []
        for r in results:
            score = 0
            title = str(deep_extract(r, ["name", "title", "torrent_name"]) or "未提取到种名")
            desc = str(deep_extract(r, ["description", "desc", "detail", "subtitle"]) or "")
            text = (title + " " + desc).upper()
            size_val = deep_extract(r, ["size", "enclosure_size", "torrent_size"]) or 0
            site_val = deep_extract(r, ["site_name", "site", "indexer"]) or "未知站点"
            seeders_val = deep_extract(r, ["seeders", "seeder"]) or 0
            
            if "4K" in text: score += 50 if ("2160P" in text or "4K" in text) else -20
            if "1080P" in text: score += 50
            if "DoVi" in text or "VISION" in text: score += 30
            if "HDR" in text: score += 20
            if "WEB" in text: score += 10
            
            r["ui_title"] = title; r["ui_site"] = str(site_val)
            try: r["ui_size"] = float(size_val)
            except: r["ui_size"] = 0
            try: r["ui_seeders"] = int(seeders_val)
            except: r["ui_seeders"] = 0
            
            r["match_score"] = score
            r["is_pack"] = is_pack 
            r["org_payload"] = r.get("torrent_info", r) 
            
            tags = []
            if "2160P" in text or "4K" in text: tags.append("4K")
            elif "1080P" in text: tags.append("1080P")
            if "DOVI" in text or "VISION" in text: tags.append("DoVi")
            elif "HDR" in text: tags.append("HDR")
            if "WEB" in text: tags.append("WEB-DL")
            r["extracted_tags"] = tags
            processed.append(r)

        processed.sort(key=lambda x: x["match_score"], reverse=True)
        return {"status": "success", "data": {"genes": genes, "results": processed[:10]}}
    except Exception as e: return {"status": "error", "message": str(e)}

def extract_episodes_from_filename(filename: str) -> set:
    eps = set()
    fname = filename.upper()
    s_e = re.findall(r'S\d{1,2}E(\d{1,3})(?:-E?(\d{1,3}))?', fname)
    for e1, e2 in s_e:
        eps.add(int(e1))
        if e2: eps.update(range(int(e1), int(e2)+1))
    ep = re.findall(r'(?:EPISODE|EP|E)[\s\.\-]*(\d{1,3})(?:-E?(\d{1,3}))?', fname)
    for e1, e2 in ep:
        eps.add(int(e1))
        if e2: eps.update(range(int(e1), int(e2)+1))
    zh = re.findall(r'第\s*(\d{1,3})\s*(?:-|至|到)\s*(\d{1,3})\s*集', filename)
    for e1, e2 in zh:
        eps.update(range(int(e1), int(e2)+1))
    zh_single = re.findall(r'第\s*(\d{1,3})\s*集', filename)
    for e in zh_single: eps.add(int(e))
    if not eps:
        naked = re.findall(r'(?:\[|\s-?\s|\.)(\d{2,4})(?:\]|\s|\.)', fname)
        for e in naked:
            num = int(e)
            if num not in (480, 720, 1080, 2160, 264, 265, 2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027):
                eps.add(num)
    return eps

def hook_qbittorrent(host, user, password, expected_size, target_episodes):
    try:
        s = requests.Session()
        login = s.post(f"{host.rstrip('/')}/api/v2/auth/login", data={"username": user, "password": password}, timeout=10)
        if login.status_code != 200 or "Ok" not in login.text: return False, "qBittorrent 登录失败"
        target_hash = None
        for attempt in range(20):
            time.sleep(3)
            res = s.get(f"{host.rstrip('/')}/api/v2/torrents/info?filter=all", timeout=10)
            if res.status_code == 200:
                for t in res.json():
                    if time.time() - t.get("added_on", 0) < 300: 
                        if expected_size > 0 and abs(t.get("total_size", 0) - expected_size) < 10 * 1024 * 1024:
                            target_hash = t.get("hash"); break
                        elif expected_size == 0:
                            target_hash = t.get("hash"); break
            if target_hash:
                f_res = s.get(f"{host.rstrip('/')}/api/v2/torrents/files?hash={target_hash}", timeout=10)
                files = f_res.json() if f_res.status_code == 200 else []
                if files and len(files) > 0 and files[0].get("size", 0) > 0:
                    wanted, unwanted = [], []
                    for i, f in enumerate(files):
                        fname = f.get("name", "")
                        if not fname.lower().endswith(('.mp4', '.mkv', '.avi', '.ts', '.iso')):
                            unwanted.append(str(i)); continue
                        f_eps = extract_episodes_from_filename(fname)
                        is_wanted = any(e in target_episodes for e in f_eps)
                        if is_wanted: wanted.append(str(i))
                        else: unwanted.append(str(i))
                    if not wanted: return False, "⚠️ 正则未能识别出视频集数，为防误杀已放行"
                    if unwanted: s.post(f"{host.rstrip('/')}/api/v2/torrents/filePrio", data={"hash": target_hash, "id": "|".join(unwanted), "priority": 0}, timeout=10)
                    if wanted: s.post(f"{host.rstrip('/')}/api/v2/torrents/filePrio", data={"hash": target_hash, "id": "|".join(wanted), "priority": 1}, timeout=10)
                    return True, f"🔪 截胡成功！保留 {len(wanted)} 集，剔除 {len(unwanted)} 个多余文件"
        return False, "轮询 60 秒超时：未锁定种子或未获取到文件列表"
    except Exception as e: return False, f"qB 交互异常: {str(e)}"

def hook_transmission(host, user, password, expected_size, target_episodes):
    try:
        rpc_url = f"{host.rstrip('/')}/transmission/rpc"
        auth = (user, password) if user else None
        s = requests.Session()
        res = s.post(rpc_url, auth=auth, timeout=10)
        session_id = res.headers.get('X-Transmission-Session-Id')
        if not session_id: return False, "Transmission 认证失败"
        s.headers.update({'X-Transmission-Session-Id': session_id})
        target_id = None
        for attempt in range(20):
            time.sleep(3)
            payload = {"method": "torrent-get", "arguments": {"fields": ["id", "addedDate", "totalSize", "files"]}}
            r = s.post(rpc_url, json=payload, auth=auth, timeout=10)
            if r.status_code == 200:
                torrents = r.json().get("arguments", {}).get("torrents", [])
                for t in torrents:
                    if time.time() - t.get("addedDate", 0) < 300:
                        if expected_size > 0 and abs(t.get("totalSize", 0) - expected_size) < 10 * 1024 * 1024:
                            target_id = t.get("id"); files = t.get("files", []); break
            if target_id and files and len(files) > 0 and files[0].get("length", 0) > 0:
                wanted, unwanted = [], []
                for i, f in enumerate(files):
                    fname = f.get("name", "")
                    if not fname.lower().endswith(('.mp4', '.mkv', '.avi', '.ts', '.iso')):
                        unwanted.append(i); continue
                    f_eps = extract_episodes_from_filename(fname)
                    if any(e in target_episodes for e in f_eps): wanted.append(i)
                    else: unwanted.append(i)
                if not wanted: return False, "⚠️ 正则未匹配到视频集数，为防止误杀，已放行全包下载"
                set_payload = {"method": "torrent-set", "arguments": {"id": target_id}}
                if unwanted: set_payload["arguments"]["files-unwanted"] = unwanted
                if wanted: set_payload["arguments"]["files-wanted"] = wanted
                s.post(rpc_url, json=set_payload, auth=auth, timeout=10)
                return True, f"🔪 TR 截胡成功！保留 {len(wanted)} 集，剔除 {len(unwanted)} 个文件"
        return False, "轮询 60 秒超时：未锁定种子"
    except Exception as e: return False, f"TR 交互异常: {str(e)}"

@router.post("/download")
def download_gap_item(payload: dict):
    series_id = payload.get("series_id"); series_name = payload.get("series_name")
    season = payload.get("season"); episodes = payload.get("episodes", [])
    torrent_info = payload.get("torrent_info", {})

    mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
    clean_token = mp_token.strip().strip("'\"") if mp_token else ""
    headers = {"X-API-KEY": clean_token, "Content-Type": "application/json"}
    
    query_db("CREATE TABLE IF NOT EXISTS gap_config (key TEXT PRIMARY KEY, value TEXT)")
    ui_conf = {r['key']: r['value'] for r in query_db("SELECT key, value FROM gap_config")} if query_db("SELECT key, value FROM gap_config") else {}
    
    client_type = ui_conf.get("client_type", ""); client_url = ui_conf.get("client_url", "")
    client_user = ui_conf.get("client_user", ""); client_pass = ui_conf.get("client_pass", "")
    
    pure_torrent_in = torrent_info.get("org_payload", torrent_info)
    try: pure_torrent_in["size"] = int(float(pure_torrent_in.get("size", 0)))
    except: pure_torrent_in["size"] = 0

    mp_payload = {"torrent_in": pure_torrent_in}

    try:
        res = requests.post(f"{mp_url.rstrip('/')}/api/v1/download/add", headers=headers, json=mp_payload, timeout=90)
        hook_msg = ""
        if res.status_code in [200, 201]:
            if client_type and client_url and len(episodes) > 0 and torrent_info.get("is_pack", False):
                expected_size = pure_torrent_in.get("size", 0)
                if client_type == "qbittorrent": success, hook_msg = hook_qbittorrent(client_url, client_user, client_pass, expected_size, episodes)
                elif client_type == "transmission": success, hook_msg = hook_transmission(client_url, client_user, client_pass, expected_size, episodes)
                hook_msg = f"\n{hook_msg}"

            for ep in episodes:
                query_db("INSERT INTO gap_records (series_id, series_name, season_number, episode_number, status) VALUES (?, ?, ?, ?, 2) ON CONFLICT(series_id, season_number, episode_number) DO UPDATE SET status = 2", (series_id, series_name, int(season), int(ep)))
            
            with state_lock:
                for s in scan_state["results"]:
                    if s.get("series_id") == series_id:
                        for ep_obj in s.get("gaps", []):
                            if ep_obj["season"] == int(season) and ep_obj["episode"] in [int(e) for e in episodes]: ep_obj["status"] = 2

            return {"status": "success", "message": f"种子已推给 MP！{hook_msg}"}
        return {"status": "error", "message": f"MP 接口拒绝 (HTTP {res.status_code})"}
    except requests.exceptions.ReadTimeout: return {"status": "error", "message": "推送超时，但 MP 可能仍在后台处理，请稍后检查 qB。"}
    except Exception as e: return {"status": "error", "message": str(e)}
