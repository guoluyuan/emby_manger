from fastapi import APIRouter
from typing import Optional
from app.core.config import cfg
from app.core.database import query_db, get_base_filter
import requests

router = APIRouter()

# --- 内部工具函数：获取第一个有效用户的ID (优先管理员) ---
def get_admin_user_id():
    key = cfg.get("emby_api_key")
    host = cfg.get("emby_host")
    if key and host:
        try:
            # 获取用户列表
            res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5)
            if res.status_code == 200:
                users = res.json()
                # 优先找管理员
                for u in users:
                    if u.get("Policy", {}).get("IsAdministrator"):
                        return u['Id']
                # 没有管理员则返回第一个用户
                if users:
                    return users[0]['Id']
        except: 
            pass
    return None

# --- 内部工具：获取用户映射 ---
def get_user_map_local():
    user_map = {}
    key = cfg.get("emby_api_key")
    host = cfg.get("emby_host")
    if key and host:
        try:
            res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=2)
            if res.status_code == 200:
                for u in res.json(): 
                    user_map[u['Id']] = u['Name']
        except: 
            pass
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
        
        key = cfg.get("emby_api_key")
        host = cfg.get("emby_host")
        if key and host:
            try:
                res = requests.get(f"{host}/emby/Items/Counts?api_key={key}", timeout=5)
                if res.status_code == 200:
                    d = res.json()
                    lib = {
                        "movie": d.get("MovieCount", 0), 
                        "series": d.get("SeriesCount", 0), 
                        "episode": d.get("EpisodeCount", 0)
                    }
            except Exception as e: 
                print(f"⚠️ Dashboard Emby API Error: {e}")
                
        return {"status": "success", "data": {**base, "library": lib}}
    except Exception as e: 
        print(f"⚠️ Dashboard DB Error: {e}")
        return {"status": "error", "data": {"total_plays":0, "library": {}}}

# 🔥 新增接口：获取媒体库列表 (Views)
@router.get("/api/stats/libraries")
def api_get_libraries():
    key = cfg.get("emby_api_key")
    host = cfg.get("emby_host")
    if not key or not host: return {"status": "error", "data": []}
    
    try:
        user_id = get_admin_user_id()
        if not user_id: return {"status": "error", "data": []}
        
        url = f"{host}/emby/Users/{user_id}/Views?api_key={key}"
        res = requests.get(url, timeout=10)
        
        if res.status_code == 200:
            items = res.json().get("Items", [])
            data = []
            for item in items:
                data.append({
                    "Id": item.get("Id"),
                    "Name": item.get("Name"),
                    "CollectionType": item.get("CollectionType", "unknown"),
                    "Type": item.get("Type")
                })
            return {"status": "success", "data": data}
    except Exception as e:
        print(f"Libraries API Error: {e}")
        
    return {"status": "error", "data": []}

@router.get("/api/stats/recent")
def api_recent_activity(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        # 获取最近 50 条，前端只显示前 10 条
        results = query_db(f"SELECT DateCreated, UserId, ItemId, ItemName, ItemType FROM PlaybackActivity {where} ORDER BY DateCreated DESC LIMIT 50", params)
        
        if not results: 
            return {"status": "success", "data": []}
            
        user_map = get_user_map_local()
        data = []
        for row in results:
            item = dict(row)
            item['UserName'] = user_map.get(item['UserId'], "User")
            item['DisplayName'] = item['ItemName']
            data.append(item)
            
        return {"status": "success", "data": data}
    except Exception as e: 
        print(f"⚠️ Recent Activity Error: {e}")
        return {"status": "error", "data": []}

# 🔥 核心接口：获取最近入库 (使用 Users/Latest)
@router.get("/api/stats/latest")
def api_latest_media(limit: int = 10):
    key = cfg.get("emby_api_key")
    host = cfg.get("emby_host")
    if not key or not host: return {"status": "error", "data": []}
    
    try:
        # 1. 获取执行查询的用户身份
        user_id = get_admin_user_id()
        if not user_id:
            return {"status": "error", "data": []}

        # 2. 构造 Emby 官方推荐的 Latest 接口
        url = f"{host}/emby/Users/{user_id}/Items/Latest"
        
        # 3. 参数配置
        params = {
            "Limit": 30,             # 多取一点用于过滤
            "MediaTypes": "Video",   # 只看视频
            "Fields": "ProductionYear,CommunityRating,Path",
            "api_key": key
        }
        
        res = requests.get(url, params=params, timeout=15)
        
        if res.status_code == 200:
            raw_items = res.json()
            data = []
            
            # 4. 数据清洗
            for item in raw_items:
                if len(data) >= limit: break
                
                # 只保留 电影 和 剧集
                if item.get("Type") not in ["Movie", "Series"]:
                    continue
                    
                data.append({
                    "Id": item.get("Id"),
                    "Name": item.get("Name"),
                    "SeriesName": item.get("SeriesName", ""), 
                    "Year": item.get("ProductionYear"),
                    "Rating": item.get("CommunityRating"),
                    "Type": item.get("Type"),
                    "DateCreated": item.get("DateCreated")
                })
            return {"status": "success", "data": data}
            
    except Exception as e:
        print(f"Latest API Error: {e}")
        
    return {"status": "error", "data": []}

# 🔥 核心修复：路径改为 /api/stats/live 以匹配前端请求
@router.get("/api/stats/live")
def api_live_sessions():
    key = cfg.get("emby_api_key")
    host = cfg.get("emby_host")
    if not key: return {"status": "error"}
    try:
        res = requests.get(f"{host}/emby/Sessions?api_key={key}", timeout=5)
        if res.status_code == 200: 
            return {"status": "success", "data": [s for s in res.json() if s.get("NowPlayingItem")]}
    except Exception as e:
        print(f"❌ Live Sessions Error: {e}") # 增加报错打印，方便调试
    return {"status": "success", "data": []}

# 保留旧接口做兼容
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
            clean = row['ItemName'].split(' - ')[0]
            if clean not in aggregated: 
                aggregated[clean] = {'ItemName': clean, 'ItemId': row['ItemId'], 'PlayCount': 0, 'TotalTime': 0}
            aggregated[clean]['PlayCount'] += 1
            aggregated[clean]['TotalTime'] += (row['PlayDuration'] or 0)
            aggregated[clean]['ItemId'] = row['ItemId']
            
        res = list(aggregated.values())
        res.sort(key=lambda x: x['TotalTime'] if sort_by == 'time' else x['PlayCount'], reverse=True)
        return {"status": "success", "data": res[:50]}
    except: return {"status": "error", "data": []}

@router.get("/api/stats/user_details")
def api_user_details(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        
        # 1. 小时分布图
        h_res = query_db(f"SELECT strftime('%H', DateCreated) as Hour, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY Hour", params)
        h_data = {str(i).zfill(2): 0 for i in range(24)}
        if h_res:
            for r in h_res: h_data[r['Hour']] = r['Plays']
            
        # 2. 设备终端
        d_res = query_db(f"SELECT COALESCE(DeviceName, ClientName, 'Unknown') as Device, COUNT(*) as Plays FROM PlaybackActivity {where} GROUP BY Device ORDER BY Plays DESC LIMIT 10", params)
        
        # 3. 播放流水账
        l_res = query_db(f"SELECT DateCreated, ItemName, PlayDuration, COALESCE(DeviceName, ClientName) as Device, UserId FROM PlaybackActivity {where} ORDER BY DateCreated DESC LIMIT 100", params)
        u_map = get_user_map_local()
        logs = []
        if l_res:
            for r in l_res: 
                l = dict(r)
                l['UserName'] = u_map.get(l['UserId'], "User")
                logs.append(l)

        # 🔥 4. 新增：用户画像大盘数据 (概览、偏好、最爱)
        overview = {"total_plays": 0, "total_duration": 0, "avg_duration": 0, "account_age_days": 1}
        pref = {"movie_plays": 0, "episode_plays": 0}
        top_fav = None

        # 统计总览与入坑时间
        ov_res = query_db(f"SELECT COUNT(*) as Plays, SUM(PlayDuration) as Dur, MIN(DateCreated) as FirstDate FROM PlaybackActivity {where}", params)
        if ov_res and ov_res[0]['Plays'] and ov_res[0]['Plays'] > 0:
            overview['total_plays'] = ov_res[0]['Plays']
            overview['total_duration'] = ov_res[0]['Dur'] or 0
            overview['avg_duration'] = round(overview['total_duration'] / overview['total_plays'])
            
            first_date = ov_res[0]['FirstDate']
            if first_date:
                import datetime
                try:
                    # 兼容 ISO 格式的时间解析
                    fd = datetime.datetime.fromisoformat(first_date.split('.')[0].replace('Z',''))
                    days = (datetime.datetime.now() - fd).days
                    overview['account_age_days'] = max(1, days)
                except: pass
        
        # 统计影视偏好
        try:
            m_res = query_db(f"SELECT ItemType, COUNT(*) as c FROM PlaybackActivity {where} GROUP BY ItemType", params)
            if m_res:
                for m in m_res:
                    if m['ItemType'] == 'Movie': pref['movie_plays'] = m['c']
                    elif m['ItemType'] == 'Episode': pref['episode_plays'] = m['c']
        except: pass

        # 提取最爱影片 (单部片播放时长和次数最高)
        try:
            top_res = query_db(f"SELECT ItemName, ItemId, COUNT(*) as c, SUM(PlayDuration) as d FROM PlaybackActivity {where} GROUP BY ItemId ORDER BY d DESC, c DESC LIMIT 1", params)
            if top_res:
                top_fav = dict(top_res[0])
        except: pass
                
        return {"status": "success", "data": {
            "hourly": h_data, "devices": [dict(r) for r in d_res] if d_res else [], "logs": logs,
            "overview": overview, "preference": pref, "top_fav": top_fav
        }}
    except Exception as e: 
        return {"status": "error", "data": {"hourly": {}, "devices": [], "logs": []}}

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
            for r in results: data[r['Label']] = int(r['Duration'])
        return {"status": "success", "data": data}
    except Exception as e: 
        return {"status": "error", "data": {}}

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
                total_plays += 1; dur = row['PlayDuration'] or 0; total_duration += dur; clean = row['ItemName'].split(' - ')[0]
                if clean not in aggregated: aggregated[clean] = {'ItemName': clean, 'ItemId': row['ItemId'], 'Count': 0, 'Duration': 0}
                aggregated[clean]['Count'] += 1; aggregated[clean]['Duration'] += dur; aggregated[clean]['ItemId'] = row['ItemId'] 
        top_list = list(aggregated.values()); top_list.sort(key=lambda x: x['Count'], reverse=True)
        return {"status": "success", "data": {"plays": total_plays, "hours": round(total_duration / 3600), "server_plays": server_plays, "top_list": top_list[:10], "tags": ["观影达人"]}}
    except: return {"status": "error", "data": {"plays": 0, "hours": 0}}

@router.get("/api/stats/top_users_list")
def api_top_users_list(period: str = 'all'):
    """
    🔥 升级版白金观影榜：支持按 日/周/月/年/总榜 动态过滤
    """
    try:
        where_base, params = get_base_filter('all')
        
        # 动态拼装时间沙漏过滤条件
        date_filter = ""
        if period == 'day':
            date_filter = " AND DateCreated >= date('now', 'start of day')"
        elif period == 'week':
            date_filter = " AND DateCreated >= date('now', '-7 days')"
        elif period == 'month':
            date_filter = " AND DateCreated >= date('now', 'start of month')"
        elif period == 'year':
            date_filter = " AND DateCreated >= date('now', 'start of year')"
            
        sql = f"SELECT UserId, COUNT(*) as Plays, SUM(PlayDuration) as TotalTime FROM PlaybackActivity {where_base} {date_filter} GROUP BY UserId ORDER BY TotalTime DESC LIMIT 10"
        
        res = query_db(sql, params)
        if not res: return {"status": "success", "data": []}
        
        user_map = get_user_map_local()
        hidden = cfg.get("hidden_users") or []
        data = []
        for row in res:
            # 过滤隐藏用户
            if row['UserId'] in hidden: continue
            u = dict(row)
            u['UserName'] = user_map.get(u['UserId'], f"User {str(u['UserId'])[:5]}")
            data.append(u)
            if len(data) >= 5: break # 首页榜单只展示前 5 名
            
        return {"status": "success", "data": data}
    except Exception as e: 
        print(f"Top Users API Error: {e}")
        return {"status": "error", "data": []}
@router.get("/api/stats/badges")
def api_badges(user_id: Optional[str] = None):
    try:
        where, params = get_base_filter(user_id)
        badges = []
        
        # 1. 深夜修仙 (02:00 - 05:00)
        night_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%H', DateCreated) BETWEEN '02' AND '05'", params)
        if night_res and night_res[0]['c'] > 5: badges.append({"id": "night", "name": "深夜修仙", "icon": "fa-moon", "color": "text-indigo-500", "bg": "bg-indigo-100", "desc": "深夜是灵魂最自由的时刻"})
        
        # 2. 周末狂欢 (周六周日)
        weekend_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%w', DateCreated) IN ('0', '6')", params)
        if weekend_res and weekend_res[0]['c'] > 10: badges.append({"id": "weekend", "name": "周末狂欢", "icon": "fa-champagne-glasses", "color": "text-pink-500", "bg": "bg-pink-100", "desc": "工作日唯唯诺诺，周末重拳出击"})
        
        # 3. 肝帝 (时长极大)
        dur_res = query_db(f"SELECT SUM(PlayDuration) as d FROM PlaybackActivity {where}", params)
        if dur_res and dur_res[0]['d'] and dur_res[0]['d'] > 360000: badges.append({"id": "liver", "name": "Emby肝帝", "icon": "fa-fire", "color": "text-red-500", "bg": "bg-red-100", "desc": "阅片无数，肝度爆表"})

        # 4. 摸鱼大师 (周一至周五 09:00-17:00)
        fish_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%w', DateCreated) BETWEEN '1' AND '5' AND strftime('%H', DateCreated) BETWEEN '09' AND '16'", params)
        if fish_res and fish_res[0]['c'] > 10: badges.append({"id": "fish", "name": "带薪观影", "icon": "fa-fish", "color": "text-cyan-500", "bg": "bg-cyan-100", "desc": "工作是老板的，快乐是自己的"})

        # 5. 晨练党 (05:00 - 08:00)
        morning_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where} AND strftime('%H', DateCreated) BETWEEN '05' AND '08'", params)
        if morning_res and morning_res[0]['c'] > 5: badges.append({"id": "morning", "name": "晨练追剧", "icon": "fa-sun", "color": "text-amber-500", "bg": "bg-amber-100", "desc": "比你优秀的人，连看片都比你早"})

        # 6. 设备收集控 (终端数 >= 4)
        device_res = query_db(f"SELECT COUNT(DISTINCT COALESCE(DeviceName, ClientName)) as c FROM PlaybackActivity {where}", params)
        if device_res and device_res[0]['c'] >= 4: badges.append({"id": "device", "name": "全平台制霸", "icon": "fa-gamepad", "color": "text-emerald-500", "bg": "bg-emerald-100", "desc": "手机、平板、电视，哪里都能看"})

        # 7. 专一狂魔 (同一部播放 >= 5)
        loyal_res = query_db(f"SELECT ItemName, COUNT(*) as c FROM PlaybackActivity {where} GROUP BY ItemId ORDER BY c DESC LIMIT 1", params)
        if loyal_res and loyal_res[0]['c'] >= 5: 
            badges.append({"id": "loyal", "name": "N刷狂魔", "icon": "fa-repeat", "color": "text-teal-500", "bg": "bg-teal-100", "desc": f"对《{loyal_res[0]['ItemName'].split(' - ')[0][:10]}》爱得深沉"})

        # 8. 影视鉴赏家 vs 追剧狂魔
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
            for r in results: data[r['Month']] = int(r['Duration'])
        return {"status": "success", "data": data}
    except: return {"status": "error", "data": {}}