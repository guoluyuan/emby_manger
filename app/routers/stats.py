from fastapi import APIRouter
from typing import Optional
from app.core.config import cfg
from app.core.database import query_db, get_base_filter
import requests
import re

router = APIRouter()

# --- 🧹 智能清洗引擎：强制统一成 "第 X 季"，绝对不分集 ---
def get_clean_name(item_name, item_type):
    if item_type != 'Episode':
        return item_name.split(' - ')[0]

    parts = [p.strip() for p in item_name.split(' - ')]
    series_name = parts[0]
    season_num = None

    cn_map = {'一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}

    for part in parts[1:]:
        m1 = re.search(r'(?:S|Season\s*)0*(\d+)', part, re.I)
        if m1:
            season_num = int(m1.group(1))
            break
        m2 = re.search(r'第\s*(\d+)\s*季', part)
        if m2:
            season_num = int(m2.group(1))
            break
        m3 = re.search(r'第\s*([一二三四五六七八九十]+)\s*季', part)
        if m3:
            season_num = cn_map.get(m3.group(1), 1)
            break

    if season_num is not None:
        return f"{series_name} - 第 {season_num} 季"

    m_f1 = re.search(r'(?:S|Season\s*)0*(\d+)', item_name, re.I)
    if m_f1: return f"{series_name} - 第 {int(m_f1.group(1))} 季"
    m_f2 = re.search(r'第\s*([一二三四五六七八九十]+)\s*季', item_name)
    if m_f2: return f"{series_name} - 第 {cn_map.get(m_f2.group(1), 1)} 季"
    m_f3 = re.search(r'第\s*(\d+)\s*季', item_name)
    if m_f3: return f"{series_name} - 第 {int(m_f3.group(1))} 季"

    return series_name

# --- 🖼️ 海报溯源器 ---
def resolve_poster_ids(items_list):
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    if not host or not key or not items_list: return
    
    ids = ",".join(list(set([str(x['ItemId']) for x in items_list if x.get('ItemId')])))
    if not ids: return
    
    try:
        res = requests.get(f"{host}/emby/Items?Ids={ids}&api_key={key}", timeout=5)
        if res.status_code == 200:
            emby_items = res.json().get("Items", [])
            id_map = {}
            for e in emby_items:
                best_id = e.get("SeasonId") or e.get("SeriesId") or e.get("Id")
                id_map[str(e.get("Id"))] = best_id
            for x in items_list:
                orig_id = str(x.get('ItemId'))
                if orig_id in id_map: x['ItemId'] = id_map[orig_id]
    except Exception: pass

# --- 工具函数 ---
def get_admin_user_id():
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if key and host:
        try:
            res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5)
            if res.status_code == 200:
                users = res.json()
                for u in users:
                    if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
                if users: return users[0]['Id']
        except: pass
    return None

def get_user_map_local():
    user_map = {}
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if key and host:
        try:
            res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=2)
            if res.status_code == 200:
                for u in res.json(): user_map[u['Id']] = u['Name']
        except: pass
    return user_map

@router.get("/api/stats/dashboard")
def api_dashboard(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        plays = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where}", params)[0]['c']
        users = query_db(f"SELECT COUNT(DISTINCT UserId) as c FROM PlaybackActivity {where} AND DateCreated > date('now', '-30 days')", params)[0]['c']
        dur = query_db(f"SELECT SUM(PlayDuration) as c FROM PlaybackActivity {where}", params)[0]['c'] or 0
        base = {"total_plays": plays, "active_users": users, "total_duration": dur}
        lib = {"movie": 0, "series": 0, "episode": 0}
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        if key and host:
            try:
                res = requests.get(f"{host}/emby/Items/Counts?api_key={key}", timeout=5)
                if res.status_code == 200:
                    d = res.json()
                    lib = {"movie": d.get("MovieCount", 0), "series": d.get("SeriesCount", 0), "episode": d.get("EpisodeCount", 0)}
            except: pass
        return {"status": "success", "data": {**base, "library": lib}}
    except: return {"status": "error", "data": {"total_plays":0, "library": {}}}

@router.get("/api/stats/libraries")
def api_get_libraries():
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if not key or not host: return {"status": "error", "data": []}
    try:
        user_id = get_admin_user_id()
        if not user_id: return {"status": "error", "data": []}
        url = f"{host}/emby/Users/{user_id}/Views?api_key={key}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return {"status": "success", "data": [{"Id": i.get("Id"), "Name": i.get("Name"), "CollectionType": i.get("CollectionType", "unknown"), "Type": i.get("Type")} for i in res.json().get("Items", [])]}
    except: pass
    return {"status": "error", "data": []}

@router.get("/api/stats/recent")
def api_recent_activity(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        results = query_db(f"SELECT DateCreated, UserId, ItemId, ItemName, ItemType FROM PlaybackActivity {where} ORDER BY DateCreated DESC LIMIT 50", params)
        if not results: return {"status": "success", "data": []}
        user_map = get_user_map_local()
        data = []
        for row in results:
            item = dict(row); item['UserName'] = user_map.get(item['UserId'], "User"); item['DisplayName'] = item['ItemName']; data.append(item)
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": []}

@router.get("/api/stats/latest")
def api_latest_media(limit: int = 10):
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if not key or not host: return {"status": "error", "data": []}
    try:
        user_id = get_admin_user_id()
        if not user_id: return {"status": "error", "data": []}
        url = f"{host}/emby/Users/{user_id}/Items/Latest"
        params = {"Limit": 30, "MediaTypes": "Video", "Fields": "ProductionYear,CommunityRating,Path", "api_key": key}
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            data = []
            for item in res.json():
                if len(data) >= limit: break
                if item.get("Type") not in ["Movie", "Series"]: continue
                data.append({"Id": item.get("Id"), "Name": item.get("Name"), "SeriesName": item.get("SeriesName", ""), "Year": item.get("ProductionYear"), "Rating": item.get("CommunityRating"), "Type": item.get("Type"), "DateCreated": item.get("DateCreated")})
            return {"status": "success", "data": data}
    except: pass
    return {"status": "error", "data": []}

@router.get("/api/stats/live")
def api_live_sessions():
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if not key: return {"status": "error"}
    try:
        res = requests.get(f"{host}/emby/Sessions?api_key={key}", timeout=5)
        if res.status_code == 200: return {"status": "success", "data": [s for s in res.json() if s.get("NowPlayingItem")]}
    except: pass
    return {"status": "success", "data": []}

@router.get("/api/live")
def api_live_sessions_legacy():
    return api_live_sessions()

@router.get("/api/stats/top_movies")
def api_top_movies(user_id: Optional[str] = None, category: str = 'all', sort_by: str = 'count'):
    try:
        where, params = get_base_filter(user_id)
        if category == 'Movie': where += " AND ItemType = 'Movie'"
        elif category == 'Episode': where += " AND ItemType = 'Episode'"
            
        sql = f"SELECT ItemName, ItemId, ItemType, PlayDuration FROM PlaybackActivity {where} LIMIT 5000"
        rows = query_db(sql, params)
        aggregated = {}
        for row in rows:
            row_dict = dict(row)
            clean = get_clean_name(row_dict['ItemName'], row_dict.get('ItemType', ''))
            if clean not in aggregated: aggregated[clean] = {'ItemName': clean, 'ItemId': row_dict['ItemId'], 'PlayCount': 0, 'TotalTime': 0}
            aggregated[clean]['PlayCount'] += 1; aggregated[clean]['TotalTime'] += (row_dict['PlayDuration'] or 0)
            
        res = list(aggregated.values())
        res.sort(key=lambda x: x['TotalTime'] if sort_by == 'time' else x['PlayCount'], reverse=True)
        top_50 = res[:50]
        resolve_poster_ids(top_50) 
        return {"status": "success", "data": top_50}
    except: return {"status": "error", "data": []}

@router.get("/api/stats/user_details")
def api_user_details(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        
        h_res = query_db(f"SELECT strftime('%H', DateCreated) as Hour, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY Hour", params)
        h_data = {str(i).zfill(2): 0 for i in range(24)}
        if h_res:
            for r in h_res: h_data[r['Hour']] = r['Plays']
            
        # 🔥 核心拆分：分离 Device (硬件) 和 Client (播放器)
        d_res = query_db(f"SELECT COALESCE(DeviceName, 'Unknown') as Device, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY DeviceName ORDER BY Plays DESC LIMIT 10", params)
        c_res = query_db(f"SELECT COALESCE(ClientName, 'Unknown') as Client, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY ClientName ORDER BY Plays DESC LIMIT 10", params)
        
        l_res = query_db(f"SELECT DateCreated, ItemName, PlayDuration, COALESCE(ClientName, DeviceName) as Device, UserId FROM PlaybackActivity {where} ORDER BY DateCreated DESC LIMIT 100", params)
        u_map = get_user_map_local()
        logs = []
        if l_res:
            for r in l_res: 
                l = dict(r); l['UserName'] = u_map.get(l['UserId'], "User"); logs.append(l)

        overview = {"total_plays": 0, "total_duration": 0, "avg_duration": 0, "account_age_days": 1}
        pref = {"movie_plays": 0, "episode_plays": 0}
        top_fav = None

        ov_res = query_db(f"SELECT COUNT(*) as Plays, SUM(PlayDuration) as Dur, MIN(DateCreated) as FirstDate FROM PlaybackActivity {where}", params)
        if ov_res and ov_res[0]['Plays'] and ov_res[0]['Plays'] > 0:
            overview['total_plays'] = ov_res[0]['Plays']
            overview['total_duration'] = ov_res[0]['Dur'] or 0
            overview['avg_duration'] = round(overview['total_duration'] / overview['total_plays'])
            if ov_res[0]['FirstDate']:
                import datetime
                try:
                    fd = datetime.datetime.fromisoformat(ov_res[0]['FirstDate'].split('.')[0].replace('Z',''))
                    overview['account_age_days'] = max(1, (datetime.datetime.now() - fd).days)
                except: pass
        try:
            m_res = query_db(f"SELECT ItemType, COUNT(*) as c FROM PlaybackActivity {where} GROUP BY ItemType", params)
            if m_res:
                for m in m_res:
                    if m['ItemType'] == 'Movie': pref['movie_plays'] = m['c']
                    elif m['ItemType'] == 'Episode': pref['episode_plays'] = m['c']
        except: pass

        try:
            raw_fav = query_db(f"SELECT ItemName, ItemId, ItemType, PlayDuration FROM PlaybackActivity {where}", params)
            agg_fav = {}
            for r in raw_fav:
                row_dict = dict(r)
                clean = get_clean_name(row_dict['ItemName'], row_dict.get('ItemType', ''))
                if clean not in agg_fav: agg_fav[clean] = {"ItemName": clean, "ItemId": row_dict["ItemId"], "c": 0, "d": 0}
                agg_fav[clean]["c"] += 1; agg_fav[clean]["d"] += (row_dict["PlayDuration"] or 0)
            
            top_fav = max(agg_fav.values(), key=lambda x: x['d']) if agg_fav else None
            if top_fav: resolve_poster_ids([top_fav]) 
        except: pass
                
        # 将拆分后的 devices 和 clients 返回给前端
        return {"status": "success", "data": {
            "hourly": h_data, 
            "devices": [dict(r) for r in d_res] if d_res else [], 
            "clients": [dict(r) for r in c_res] if c_res else [], 
            "logs": logs, 
            "overview": overview, 
            "preference": pref, 
            "top_fav": top_fav
        }}
    except: return {"status": "error", "data": {"hourly": {}, "devices": [], "clients": [], "logs": []}}

@router.get("/api/stats/chart")
@router.get("/api/stats/trend")
def api_chart_stats(user_id: Optional[str] = None, dimension: str = 'day'):
    try:
        where, params = get_base_filter(user_id)
        if dimension == 'week':
            sql = f"SELECT strftime('%Y-%W', DateCreated) as Label, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} AND DateCreated > date('now', '-120 days') GROUP BY Label ORDER BY Label"
        elif dimension == 'month':
            sql = f"SELECT strftime('%Y-%m', DateCreated) as Label, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} AND DateCreated > date('now', '-365 days') GROUP BY Label ORDER BY Label"
        else:
            sql = f"SELECT date(DateCreated) as Label, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} AND DateCreated > date('now', '-30 days') GROUP BY Label ORDER BY Label"

        results = query_db(sql, params)
        data = {}
        if results:
            for r in results: data[r['Label']] = int(r['Duration'] or 0)
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": {}}

@router.get("/api/stats/poster_data")
def api_poster_data(user_id: Optional[str] = None, period: str = 'all'):
    try:
        where_base, params = get_base_filter(user_id)
        date_filter = ""
        if period == 'week': date_filter = " AND DateCreated > date('now', '-7 days')"
        elif period == 'month': date_filter = " AND DateCreated > date('now', '-30 days')"
            
        server_res = query_db(f"SELECT COUNT(*) as Plays FROM PlaybackActivity {get_base_filter('all')[0]} {date_filter}", get_base_filter('all')[1])
        server_plays = server_res[0]['Plays'] if server_res else 0

        raw_sql = f"SELECT ItemName, ItemId, ItemType, PlayDuration FROM PlaybackActivity {where_base + date_filter}"
        rows = query_db(raw_sql, params)
        total_plays = 0; total_duration = 0; aggregated = {} 
        if rows:
            for row in rows:
                row_dict = dict(row)
                total_plays += 1; dur = row_dict['PlayDuration'] or 0; total_duration += dur
                clean = get_clean_name(row_dict['ItemName'], row_dict.get('ItemType', ''))
                if clean not in aggregated: aggregated[clean] = {'ItemName': clean, 'ItemId': row_dict['ItemId'], 'Count': 0, 'Duration': 0}
                aggregated[clean]['Count'] += 1; aggregated[clean]['Duration'] += dur
                
        top_list = list(aggregated.values()); top_list.sort(key=lambda x: x['Count'], reverse=True)
        top_10 = top_list[:10]
        resolve_poster_ids(top_10)
        return {"status": "success", "data": {"plays": total_plays, "hours": round(total_duration / 3600), "server_plays": server_plays, "top_list": top_10, "tags": ["观影达人"]}}
    except: return {"status": "error", "data": {"plays": 0, "hours": 0}}

@router.get("/api/stats/top_users_list")
def api_top_users_list(period: str = 'all'):
    try:
        where_base, params = get_base_filter('all')
        date_filter = ""
        if period == 'day': date_filter = " AND DateCreated >= date('now', 'start of day')"
        elif period == 'week': date_filter = " AND DateCreated >= date('now', '-7 days')"
        elif period == 'month': date_filter = " AND DateCreated >= date('now', 'start of month')"
        elif period == 'year': date_filter = " AND DateCreated >= date('now', 'start of year')"
            
        sql = f"SELECT UserId, COUNT(*) as Plays, SUM(PlayDuration) as TotalTime FROM PlaybackActivity {where_base} {date_filter} GROUP BY UserId ORDER BY TotalTime DESC LIMIT 10"
        res = query_db(sql, params)
        if not res: return {"status": "success", "data": []}
        user_map = get_user_map_local()
        hidden = cfg.get("hidden_users") or []
        data = []
        for row in res:
            if row['UserId'] in hidden: continue
            u = dict(row); u['UserName'] = user_map.get(u['UserId'], f"User {str(u['UserId'])[:5]}"); data.append(u)
            if len(data) >= 5: break
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": []}

@router.get("/api/stats/badges")
def api_badges(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        badges = []
        night_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%H', DateCreated) BETWEEN '02' AND '05'", params)
        if night_res and night_res[0]['c'] > 5: badges.append({"id": "night", "name": "深夜修仙", "icon": "fa-moon", "color": "text-indigo-500", "bg": "bg-indigo-100", "desc": "深夜是灵魂最自由的时刻"})
        weekend_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%w', DateCreated) IN ('0', '6')", params)
        if weekend_res and weekend_res[0]['c'] > 10: badges.append({"id": "weekend", "name": "周末狂欢", "icon": "fa-champagne-glasses", "color": "text-pink-500", "bg": "bg-pink-100", "desc": "工作日唯唯诺诺，周末重拳出击"})
        dur_res = query_db(f"SELECT SUM(PlayDuration) as d FROM PlaybackActivity {where}", params)
        if dur_res and dur_res[0]['d'] and dur_res[0]['d'] > 360000: badges.append({"id": "liver", "name": "Emby肝帝", "icon": "fa-fire", "color": "text-red-500", "bg": "bg-red-100", "desc": "阅片无数，肝度爆表"})
        fish_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%w', DateCreated) BETWEEN '1' AND '5' AND strftime('%H', DateCreated) BETWEEN '09' AND '16'", params)
        if fish_res and fish_res[0]['c'] > 10: badges.append({"id": "fish", "name": "带薪观影", "icon": "fa-fish", "color": "text-cyan-500", "bg": "bg-cyan-100", "desc": "工作是老板的，快乐是自己的"})
        morning_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%H', DateCreated) BETWEEN '05' AND '08'", params)
        if morning_res and morning_res[0]['c'] > 5: badges.append({"id": "morning", "name": "晨练追剧", "icon": "fa-sun", "color": "text-amber-500", "bg": "bg-amber-100", "desc": "比你优秀的人，连看片都比你早"})
        device_res = query_db(f"SELECT COUNT(DISTINCT COALESCE(DeviceName, ClientName)) as c FROM PlaybackActivity {where}", params)
        if device_res and device_res[0]['c'] >= 4: badges.append({"id": "device", "name": "全平台制霸", "icon": "fa-gamepad", "color": "text-emerald-500", "bg": "bg-emerald-100", "desc": "手机、平板、电视，哪里都能看"})
        loyal_res = query_db(f"SELECT ItemName, COUNT(*) as c FROM PlaybackActivity {where} GROUP BY ItemId ORDER BY c DESC LIMIT 1", params)
        if loyal_res and loyal_res[0]['c'] >= 5: badges.append({"id": "loyal", "name": "N刷狂魔", "icon": "fa-repeat", "color": "text-teal-500", "bg": "bg-teal-100", "desc": f"对《{loyal_res[0]['ItemName'].split(' - ')[0][:10]}》爱得深沉"})
        try:
            m_res = query_db(f"SELECT ItemType, COUNT(*) as c FROM PlaybackActivity {where} GROUP BY ItemType", params)
            movies, eps = 0, 0
            if m_res:
                for m in m_res:
                    if m['ItemType'] == 'Movie': movies = m['c']
                    elif m['ItemType'] == 'Episode': eps = m['c']
            total = movies + eps
            if total > 20:
                if movies / total > 0.7: badges.append({"id": "movie_lover", "name": "电影鉴赏家", "icon": "fa-film", "color": "text-blue-500", "bg": "bg-blue-100", "desc": "沉浸在两小时的艺术光影世界"})
                elif eps / total > 0.7: badges.append({"id": "tv_lover", "name": "追剧狂魔", "icon": "fa-tv", "color": "text-purple-500", "bg": "bg-purple-100", "desc": "一集接一集，根本停不下来"})
        except: pass
        return {"status": "success", "data": badges}
    except: return {"status": "success", "data": []}

@router.get("/api/stats/monthly_stats")
def api_monthly_stats(user_id: Optional[str] = None):
    try:
        where_base, params = get_base_filter(user_id)
        where = where_base + " AND DateCreated > date('now', '-12 months')"
        sql = f"SELECT strftime('%Y-%m', DateCreated) as Month, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} GROUP BY Month ORDER BY Month"
        results = query_db(sql, params); data = {}
        if results: 
            for r in results: data[r['Month']] = int(r['Duration'] or 0)
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": {}}