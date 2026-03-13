from fastapi import APIRouter
from typing import Optional
from app.core.config import cfg
from app.core.database import query_db, get_base_filter
# 🔥 引入核心适配器
from app.core.media_adapter import media_api
import requests
import re
import datetime
from collections import defaultdict

router = APIRouter()

def get_existing_item_ids(ids):
    ids = [str(i) for i in ids if i]
    if not ids:
        return set()
    existing = set()
    try:
        for i in range(0, len(ids), 100):
            chunk = ids[i:i+100]
            res = media_api.get("/Items", params={"Ids": ",".join(chunk)}, timeout=8)
            if res.status_code == 200:
                items = res.json().get("Items", []) or []
                for it in items:
                    it_id = it.get("Id")
                    if it_id: existing.add(str(it_id))
            else:
                return None
        return existing
    except:
        return None

# --- 🧹 智能清洗引擎 ---
def get_clean_name(item_name, item_type):
    if not item_name: return "未知内容"
    item_name = str(item_name)
    if str(item_type) != 'Episode': return item_name.split(' - ')[0]

    parts = [p.strip() for p in item_name.split(' - ')]
    series_name = parts[0]
    season_num = None

    cn_map = {'一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}

    for part in parts[1:]:
        m1 = re.search(r'(?:S|Season\s*)0*(\d+)', part, re.I)
        if m1: season_num = int(m1.group(1)); break
        m2 = re.search(r'第\s*(\d+)\s*季', part)
        if m2: season_num = int(m2.group(1)); break
        m3 = re.search(r'第\s*([一二三四五六七八九十]+)\s*季', part)
        if m3: season_num = cn_map.get(m3.group(1), 1); break

    if season_num is not None: return f"{series_name} - 第 {season_num} 季"
    m_f1 = re.search(r'(?:S|Season\s*)0*(\d+)', item_name, re.I)
    if m_f1: return f"{series_name} - 第 {int(m_f1.group(1))} 季"
    m_f2 = re.search(r'第\s*([一二三四五六七八九十]+)\s*季', item_name)
    if m_f2: return f"{series_name} - 第 {cn_map.get(m_f2.group(1), 1)} 季"
    m_f3 = re.search(r'第\s*(\d+)\s*季', item_name)
    if m_f3: return f"{series_name} - 第 {int(m_f3.group(1))} 季"

    return series_name

def resolve_poster_ids(items_list):
    if not items_list: return
    ids = ",".join(list(set([str(x['ItemId']) for x in items_list if x.get('ItemId')])))
    if not ids: return
    
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Items", params={"Ids": ids}, timeout=5)
        if res.status_code == 200:
            emby_items = res.json().get("Items", [])
            id_map = {}
            for e in emby_items:
                best_id = e.get("SeriesId") or e.get("SeasonId") or e.get("Id")
                id_map[str(e.get("Id"))] = best_id
            for x in items_list:
                orig_id = str(x.get('ItemId'))
                if orig_id in id_map: 
                    x['ItemId'] = id_map[orig_id]
                    x['smart_poster'] = f"/api/proxy/smart_image?item_id={id_map[orig_id]}&type=Primary"
    except Exception: pass

def get_admin_user_id():
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Users", timeout=5)
        if res.status_code == 200:
            users = res.json()
            for u in users:
                if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
            if users: return users[0]['Id']
    except: pass
    return None

def get_user_map_local():
    user_map = {}
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Users", timeout=2)
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
        
        try:
            # 🚀 替换为 media_api
            res = media_api.get("/Items/Counts", timeout=5)
            if res.status_code == 200:
                d = res.json()
                lib = {"movie": d.get("MovieCount", 0), "series": d.get("SeriesCount", 0), "episode": d.get("EpisodeCount", 0)}
        except: pass
        return {"status": "success", "data": {**base, "library": lib}}
    except: return {"status": "error", "data": {"total_plays":0, "library": {}}}

@router.get("/api/stats/libraries")
def api_get_libraries():
    try:
        user_id = get_admin_user_id()
        if not user_id: return {"status": "error", "data": []}
        # 🚀 替换为 media_api
        res = media_api.get(f"/Users/{user_id}/Views", timeout=10)
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
        # 过滤掉已从 Emby 库中删除的条目
        existing_ids = None
        try:
            ids = [str(dict(r).get("ItemId")) for r in results if dict(r).get("ItemId")]
            if ids:
                res = media_api.get("/Items", params={"Ids": ",".join(ids)}, timeout=8)
                if res.status_code == 200:
                    existing_ids = {str(i.get("Id")) for i in res.json().get("Items", []) if i.get("Id")}
        except Exception:
            existing_ids = None
        data = []
        for row in results:
            item = dict(row)
            if existing_ids is not None and str(item.get("ItemId")) not in existing_ids:
                continue
            item['UserName'] = user_map.get(item['UserId'], "User")
            item['DisplayName'] = item.get('ItemName') or '未知记录'
            data.append(item)
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": []}

@router.get("/api/stats/latest")
def api_latest_media(limit: int = 10):
    try:
        user_id = get_admin_user_id()
        if not user_id: return {"status": "error", "data": []}
        # 🚀 替换为 media_api
        params = {"Limit": 30, "MediaTypes": "Video", "Fields": "ProductionYear,CommunityRating,Path"}
        res = media_api.get(f"/Users/{user_id}/Items/Latest", params=params, timeout=15)
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
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/Sessions", timeout=5)
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
        if category == 'Movie':
            where += " AND ItemType = 'Movie'"
        elif category in ('Episode', 'Series', 'Tv', 'TV'):
            where += " AND ItemType IN ('Episode', 'Series')"
            
        sql = f"SELECT ItemName, ItemId, ItemType, PlayDuration FROM PlaybackActivity {where} LIMIT 5000"
        rows = query_db(sql, params)
        aggregated = {}
        if rows:
            for row in rows:
                row_dict = dict(row)
                clean = get_clean_name(row_dict.get('ItemName'), row_dict.get('ItemType', ''))
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
        
        h_data = {str(i).zfill(2): 0 for i in range(24)}
        raw_logs = query_db(f"SELECT DateCreated FROM PlaybackActivity {where}", params)
        if raw_logs:
            for row in raw_logs:
                r = dict(row)
                dc = r.get('DateCreated')
                if dc:
                    m = re.search(r'(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})', str(dc))
                    if m:
                        dt = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)))
                        h_data[str(dt.hour).zfill(2)] += 1
            
        c_res = query_db(f"SELECT COALESCE(ClientName, 'Unknown') as Client, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY ClientName ORDER BY Plays DESC LIMIT 10", params)
        if c_res is None:
            c_res = query_db(f"SELECT COALESCE(Client, 'Unknown') as Client, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY Client ORDER BY Plays DESC LIMIT 10", params)

        d_res = query_db(f"SELECT COALESCE(DeviceName, 'Unknown') as Device, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY DeviceName ORDER BY Plays DESC LIMIT 10", params)
        
        l_res = query_db(f"SELECT DateCreated, ItemName, ItemId, ItemType, PlayDuration, COALESCE(ClientName, DeviceName) as Device, UserId FROM PlaybackActivity {where} ORDER BY DateCreated DESC LIMIT 100", params)
        if l_res is None:
            l_res = query_db(f"SELECT DateCreated, ItemName, ItemId, ItemType, PlayDuration, COALESCE(Client, DeviceName) as Device, UserId FROM PlaybackActivity {where} ORDER BY DateCreated DESC LIMIT 100", params)

        u_map = get_user_map_local()
        logs = []
        existing_ids = None
        try:
            existing_ids = get_existing_item_ids([dict(r).get("ItemId") for r in l_res]) if l_res else None
        except:
            existing_ids = None
        if l_res:
            for r in l_res: 
                l = dict(r)
                if existing_ids is not None and str(l.get("ItemId")) not in existing_ids:
                    continue
                l['UserName'] = u_map.get(l['UserId'], "User")
                l['smart_poster'] = f"/api/proxy/smart_image?item_id={l['ItemId']}&type=Primary"
                logs.append(l)
            resolve_poster_ids(logs)

        overview = {"total_plays": 0, "total_duration": 0, "avg_duration": 0, "account_age_days": 1}
        pref = {"movie_plays": 0, "episode_plays": 0}
        top_fav = None

        ov_res = query_db(f"SELECT COUNT(*) as Plays, SUM(PlayDuration) as Dur FROM PlaybackActivity {where}", params)
        if ov_res and ov_res[0]['Plays'] and ov_res[0]['Plays'] > 0:
            overview['total_plays'] = ov_res[0]['Plays']
            overview['total_duration'] = ov_res[0]['Dur'] or 0
            overview['avg_duration'] = round(overview['total_duration'] / overview['total_plays'])
            
        try:
            if user_id and user_id != 'all':
                u_res = media_api.get(f"/Users/{user_id}", timeout=5)
                if u_res.status_code == 200:
                    dc = u_res.json().get("DateCreated")
                    if dc:
                        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', str(dc))
                        if m:
                            fd = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                            overview['account_age_days'] = max(1, (datetime.datetime.now() - fd).days)
            else:
                u_res = media_api.get("/Users", timeout=5)
                if u_res.status_code == 200:
                    earliest_dt = None
                    for u in u_res.json():
                        dc = u.get("DateCreated")
                        if dc:
                            m = re.search(r'(\d{4})-(\d{2})-(\d{2})', str(dc))
                            if m:
                                dt = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                                if not earliest_dt or dt < earliest_dt: earliest_dt = dt
                    if earliest_dt:
                        overview['account_age_days'] = max(1, (datetime.datetime.now() - earliest_dt).days)
        except Exception as e: pass

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
                clean = get_clean_name(row_dict.get('ItemName'), row_dict.get('ItemType', ''))
                if clean not in agg_fav: agg_fav[clean] = {"ItemName": clean, "ItemId": row_dict["ItemId"], "c": 0, "d": 0}
                agg_fav[clean]["c"] += 1; agg_fav[clean]["d"] += (row_dict["PlayDuration"] or 0)
            
            top_fav = max(agg_fav.values(), key=lambda x: x['d']) if agg_fav else None
            if top_fav:
                existing = get_existing_item_ids([top_fav.get("ItemId")])
                if existing is not None and str(top_fav.get("ItemId")) not in existing:
                    top_fav = None
                else:
                    resolve_poster_ids([top_fav]) 
        except: pass
                
        return {"status": "success", "data": {
            "hourly": h_data, "devices": [dict(r) for r in d_res] if d_res else [], "clients": [dict(r) for r in c_res] if c_res else [], 
            "logs": logs, "overview": overview, "preference": pref, "top_fav": top_fav
        }}
    except: return {"status": "error", "data": {"hourly": {}, "devices": [], "clients": [], "logs": []}}

@router.get("/api/stats/chart")
@router.get("/api/stats/trend")
def api_chart_stats(user_id: Optional[str] = None, dimension: str = 'day'):
    try:
        where, params = get_base_filter(user_id)
        if dimension == 'week': 
            sql = f"SELECT strftime('%Y-%W', substr(replace(DateCreated, 'T', ' '), 1, 19)) as Label, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} AND DateCreated > date('now', '-120 days') GROUP BY Label ORDER BY Label"
        elif dimension == 'month': 
            sql = f"SELECT substr(replace(DateCreated, 'T', ' '), 1, 7) as Label, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} AND DateCreated > date('now', '-365 days') GROUP BY Label ORDER BY Label"
        else: 
            sql = f"SELECT substr(replace(DateCreated, 'T', ' '), 1, 10) as Label, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} AND DateCreated > date('now', '-30 days') GROUP BY Label ORDER BY Label"
            
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

        raw_sql = f"SELECT DateCreated, ItemName, ItemId, ItemType, PlayDuration FROM PlaybackActivity {where_base + date_filter}"
        rows = query_db(raw_sql, params)
        
        total_plays = 0; total_duration = 0; aggregated = {}
        daily_duration = defaultdict(int)
        late_night_record = None
        late_night_hour = -1

        if rows:
            for row in rows:
                row_dict = dict(row)
                total_plays += 1; dur = row_dict['PlayDuration'] or 0; total_duration += dur
                
                dc = row_dict.get('DateCreated', '')
                if dc:
                    day_str = dc[:10]
                    daily_duration[day_str] += dur
                    
                    m = re.search(r'T(\d{2}):(\d{2}):(\d{2})', dc)
                    if m:
                        hour = int(m.group(1))
                        if 1 <= hour <= 5:
                            if hour > late_night_hour:
                                late_night_hour = hour
                                late_night_record = {
                                    "time": f"{hour:02d}:{m.group(2)}",
                                    "date": day_str[5:].replace('-', '月') + '日',
                                    "name": get_clean_name(row_dict.get('ItemName'), row_dict.get('ItemType', ''))
                                }

                clean = get_clean_name(row_dict.get('ItemName'), row_dict.get('ItemType', ''))
                if clean not in aggregated: aggregated[clean] = {'ItemName': clean, 'ItemId': row_dict['ItemId'], 'Count': 0, 'Duration': 0}
                aggregated[clean]['Count'] += 1; aggregated[clean]['Duration'] += dur
                
        binge_day = None
        if daily_duration:
            max_day = max(daily_duration, key=daily_duration.get)
            max_dur = daily_duration[max_day]
            if max_dur > 3600:
                binge_day = {"date": max_day[5:].replace('-', '月') + '日', "hours": round(max_dur / 3600, 1)}
                
        genres = []
        try:
            if user_id and user_id != 'all':
                # 🚀 替换为 media_api
                g_res = media_api.get(f"/Users/{user_id}/Items", params={"IncludeItemTypes":"Movie,Series","Recursive":"true","SortBy":"DateCreated","SortOrder":"Descending","Limit":100,"Fields":"Genres"}, timeout=3).json()
                genre_counts = defaultdict(int)
                for i in g_res.get("Items", []):
                    for g in i.get("Genres", []): genre_counts[g] += 1
                if genre_counts:
                    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:3]
                    genres = [k for k, v in sorted_genres]
        except: pass

        top_list = list(aggregated.values()); top_list.sort(key=lambda x: x['Count'], reverse=True)
        existing_ids = get_existing_item_ids([i.get("ItemId") for i in top_list])
        if existing_ids is not None:
            top_list = [i for i in top_list if str(i.get("ItemId")) in existing_ids]
        top_10 = top_list[:10]
        resolve_poster_ids(top_10)
        
        return {
            "status": "success", 
            "data": {
                "plays": total_plays, 
                "hours": round(total_duration / 3600), 
                "server_plays": server_plays, 
                "top_list": top_10,
                "mood_data": {
                    "late_night": late_night_record,
                    "binge_day": binge_day,
                    "genres": genres
                }
            }
        }
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
        
        raw_data = query_db(f"SELECT DateCreated, PlayDuration, COALESCE(ClientName, DeviceName) as Client, ItemId, ItemName, ItemType FROM PlaybackActivity {where}", params)
        if raw_data is None: 
            raw_data = query_db(f"SELECT DateCreated, PlayDuration, COALESCE(Client, DeviceName) as Client, ItemId, ItemName, ItemType FROM PlaybackActivity {where}", params)
        if not raw_data: raw_data = []

        night_c, weekend_c, fish_c, morning_c = 0, 0, 0, 0
        dur_total = 0
        devices = set()
        items = {}
        movies, eps = 0, 0
        
        for row in raw_data:
            r = dict(row)
            dur = r.get('PlayDuration') or 0
            dur_total += dur
            
            client = r.get('Client')
            if client: devices.add(client)
            
            item_id = r.get('ItemId')
            if item_id:
                if item_id not in items: items[item_id] = {'name': r.get('ItemName'), 'c': 0}
                items[item_id]['c'] += 1
                
            it = r.get('ItemType')
            if it == 'Movie': movies += 1
            elif it == 'Episode': eps += 1
            
            dc = r.get('DateCreated')
            if dc:
                m = re.search(r'(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})', str(dc))
                if m:
                    dt = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)))
                        
                    hour = dt.hour
                    weekday = dt.weekday()
                    
                    if 2 <= hour <= 5: night_c += 1
                    if weekday in (5, 6): weekend_c += 1
                    if 0 <= weekday <= 4 and 9 <= hour <= 17: fish_c += 1
                    if 5 <= hour <= 8: morning_c += 1

        badges = []
        if night_c >= 2: badges.append({"id": "night", "name": "深夜修仙", "icon": "fa-moon", "color": "text-indigo-500", "bg": "bg-indigo-100", "desc": "深夜是灵魂最自由的时刻"})
        if weekend_c >= 5: badges.append({"id": "weekend", "name": "周末狂欢", "icon": "fa-champagne-glasses", "color": "text-pink-500", "bg": "bg-pink-100", "desc": "工作日唯唯诺诺，周末重拳出击"})
        if dur_total > 180000: badges.append({"id": "liver", "name": "核心肝帝", "icon": "fa-fire", "color": "text-red-500", "bg": "bg-red-100", "desc": "阅片无数，肝度爆表"})
        if fish_c >= 5: badges.append({"id": "fish", "name": "带薪观影", "icon": "fa-fish", "color": "text-cyan-500", "bg": "bg-cyan-100", "desc": "工作是老板的，快乐是自己的"})
        if morning_c >= 2: badges.append({"id": "morning", "name": "晨练追剧", "icon": "fa-sun", "color": "text-amber-500", "bg": "bg-amber-100", "desc": "比你优秀的人，连看片都比你早"})
        if len(devices) >= 2: badges.append({"id": "device", "name": "全平台制霸", "icon": "fa-gamepad", "color": "text-emerald-500", "bg": "bg-emerald-100", "desc": "手机、平板、电视，哪里都能看"})
        
        if items:
            loyal = max(items.values(), key=lambda x: x['c'])
            if loyal['c'] >= 3:
                safe_name = str(loyal.get('name') or '未知').split(' - ')[0][:10]
                badges.append({"id": "loyal", "name": "N刷狂魔", "icon": "fa-repeat", "color": "text-teal-500", "bg": "bg-teal-100", "desc": f"对《{safe_name}》爱得深沉"})
                
        total = movies + eps
        if total > 10:
            if movies / total > 0.6: badges.append({"id": "movie_lover", "name": "电影鉴赏家", "icon": "fa-film", "color": "text-blue-500", "bg": "bg-blue-100", "desc": "沉浸在两小时的艺术光影世界"})
            elif eps / total > 0.6: badges.append({"id": "tv_lover", "name": "追剧狂魔", "icon": "fa-tv", "color": "text-purple-500", "bg": "bg-purple-100", "desc": "一集接一集，根本停不下来"})
            
        return {"status": "success", "data": badges}
    except Exception as e: 
        return {"status": "success", "data": []}

@router.get("/api/stats/monthly_stats")
def api_monthly_stats(user_id: Optional[str] = None):
    try:
        where_base, params = get_base_filter(user_id)
        where = where_base + " AND DateCreated > date('now', '-12 months')"
        sql = f"SELECT substr(replace(DateCreated, 'T', ' '), 1, 7) as Month, SUM(PlayDuration) as Duration FROM PlaybackActivity {where} GROUP BY Month ORDER BY Month"
        results = query_db(sql, params); data = {}
        if results: 
            for r in results: data[r['Month']] = int(r['Duration'] or 0)
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": {}}
