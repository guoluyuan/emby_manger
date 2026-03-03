from fastapi import APIRouter, Request
import requests
from app.core.config import cfg

router = APIRouter()

def get_emby_admin(host, key):
    try:
        users = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"):
                return u['Id']
        return users[0]['Id'] if users else None
    except:
        return None

# 🔥 新增：通用的媒体规格提取器 (电影和单集都能用)
def extract_media_badges(item):
    badges = []
    if "MediaSources" in item and item["MediaSources"]:
        source = item["MediaSources"][0]
        media_streams = source.get("MediaStreams", [])
        
        video_stream = next((s for s in media_streams if s["Type"] == "Video"), None)
        audio_stream = next((s for s in media_streams if s["Type"] == "Audio"), None)

        if video_stream:
            width = video_stream.get("Width", 0)
            if width >= 3800:
                badges.append({"type": "res", "text": "4K", "color": "bg-yellow-500 text-yellow-900 border-yellow-400"})
            elif width >= 1900:
                badges.append({"type": "res", "text": "1080P", "color": "bg-blue-500 text-blue-100 border-blue-400"})
            
            video_range = video_stream.get("VideoRange", "")
            if video_range == "HDR":
                badges.append({"type": "fx", "text": "HDR", "color": "bg-purple-600 text-white border-purple-500"})
            elif video_range == "DOVI":
                badges.append({"type": "fx", "text": "Dolby Vision", "color": "bg-gradient-to-r from-indigo-600 to-purple-600 text-white border-indigo-400"})
                
        if audio_stream:
            codec = audio_stream.get("Codec", "").upper()
            channels = audio_stream.get("Channels", 2)
            channel_str = "5.1" if channels == 6 else ("7.1" if channels == 8 else f"{channels}.0")
            badges.append({"type": "audio", "text": f"{codec} {channel_str}", "color": "bg-slate-700 text-slate-200 border-slate-600"})
    return badges

@router.get("/api/library/search")
def global_library_search(query: str, request: Request):
    if not request.session.get("user"):
        return {"status": "error", "message": "未登录"}

    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    if not host or not key:
        return {"status": "error", "message": "未配置 Emby 服务器"}

    admin_id = get_emby_admin(host, key)
    if not admin_id:
        return {"status": "error", "message": "找不到管理员账号"}

    try:
        search_url = f"{host}/emby/Users/{admin_id}/Items"
        params = {
            "api_key": key,
            "SearchTerm": query,
            "IncludeItemTypes": "Movie,Series",
            "Recursive": "true",
            # 🔥 修复1与2：追加了 ImageTags (图片) 和 ProductionYear (年份)
            "Fields": "Overview,MediaSources,ProviderIds,ImageTags,ProductionYear", 
            "Limit": 8 
        }
        res = requests.get(search_url, params=params, timeout=10).json()
        items = res.get("Items", [])

        results = []
        for item in items:
            media_type = "movie" if item["Type"] == "Movie" else "tv"
            
            # ================== 图片获取策略 ==================
            poster_url = ""
            if item.get("ImageTags", {}).get("Primary"):
                poster_url = f"{host}/emby/Items/{item['Id']}/Images/Primary?api_key={key}&MaxWidth=400"
            else:
                if item.get("ImageTags", {}).get("Backdrop"):
                    poster_url = f"{host}/emby/Items/{item['Id']}/Images/Backdrop?api_key={key}&MaxWidth=400"
                else:
                    tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
                    if tmdb_id:
                        poster_url = f"https://image.tmdb.org/t/p/w500/{tmdb_id}.jpg"
                    else:
                        poster_url = "/static/img/logo-dark.png" 

            backdrop_url = ""
            if item.get("ImageTags", {}).get("Backdrop"):
                backdrop_url = f"{host}/emby/Items/{item['Id']}/Images/Backdrop?api_key={key}&MaxWidth=1280"
            elif item.get("ImageTags", {}).get("Primary"):
                backdrop_url = f"{host}/emby/Items/{item['Id']}/Images/Primary?api_key={key}&MaxWidth=1280"
            # ========================================================
            
            info = {
                "id": item["Id"],
                "name": item["Name"],
                "year": item.get("ProductionYear", "未知"),
                "overview": item.get("Overview", "暂无简介"),
                "type": media_type,
                "poster": poster_url,
                "backdrop": backdrop_url,
                "badges": [] 
            }

            # 电影：直接提取本身自带的 MediaSources
            if media_type == "movie":
                info["badges"].extend(extract_media_badges(item))

            # 🔥 修复3：剧集穿透查询 (获取精确集数 + 第一集的画质特效)
            elif media_type == "tv":
                try:
                    # 查这棵树下的 Episodes，只要1条用来读画质，顺便拿总数 (TotalRecordCount)
                    episodes_res = requests.get(
                        f"{host}/emby/Shows/{item['Id']}/Episodes?UserId={admin_id}&api_key={key}&Limit=1&Fields=MediaSources", 
                        timeout=5
                    ).json()
                    
                    total_episodes = episodes_res.get("TotalRecordCount", 0)
                    if total_episodes > 0:
                        info["badges"].append({
                            "type": "season", 
                            "text": f"已入库 {total_episodes} 集", 
                            "color": "bg-emerald-500 text-white border-emerald-400"
                        })
                    
                    # 借用第一集的 MediaSources 来展示画质
                    episodes = episodes_res.get("Items", [])
                    if episodes:
                        info["badges"].extend(extract_media_badges(episodes[0]))
                except:
                    pass
            
            results.append(info)

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": f"全局搜索请求失败: {str(e)}"}