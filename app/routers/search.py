from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import requests
import io
from app.core.config import cfg

router = APIRouter()

# ==========================================
# 🌟 智能嗅探：Emby 版本与定制版检测
# ==========================================
_emby_sys_cache = None

def get_emby_sys_info(host, key):
    global _emby_sys_cache
    if _emby_sys_cache:
        return _emby_sys_cache
    try:
        # 使用 Public 接口更稳定，不强制校验高权限 Token
        res = requests.get(f"{host}/emby/System/Info/Public", timeout=3).json()
        _emby_sys_cache = {
            "Version": res.get("Version", "4.10.0.0"),
            "ServerName": res.get("ServerName", "")
        }
        return _emby_sys_cache
    except:
        return {"Version": "4.10.0.0", "ServerName": ""}

def is_new_emby_router(sys_info):
    server_name = sys_info.get("ServerName", "").lower()
    
    # 🔥 特判：小鱼影视等定制版，默认全部使用新路由
    if "xiaoyu" in server_name or "小鱼" in server_name:
        return True
        
    version_str = sys_info.get("Version", "4.10.0.0")
    try:
        parts = version_str.split('.')
        major = int(parts[0])
        minor = int(parts[1])
        
        # 🔥 终极防漏逻辑：只要不是明确的 4.7 及以下老古董版本，全部用新路由
        if major < 4 or (major == 4 and minor <= 7):
            return False
            
        return True
    except:
        return True # 解析失败时，默认信任新版本

def get_emby_admin(host, key):
    try:
        users = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5).json()
        for u in users:
            if u.get("Policy", {}).get("IsAdministrator"):
                return u['Id']
        return users[0]['Id'] if users else None
    except:
        return None

# ==========================================
# 🌟 核心：图片代理器 (绕过内网与HTTPS限制)
# ==========================================
@router.get("/api/library/image/{item_id}")
def proxy_emby_image(item_id: str, type: str = "Primary", width: int = 400):
    host = cfg.get("emby_host")
    key = cfg.get("emby_api_key")
    if not host or not key:
        return {"status": "error"}
    
    emby_img_url = f"{host}/emby/Items/{item_id}/Images/{type}?api_key={key}&MaxWidth={width}"
    try:
        res = requests.get(emby_img_url, stream=True, timeout=5)
        if res.status_code == 200:
            # 🔥 核心修复：注入长达 7 天的强缓存与跨域头，彻底告别重复加载卡顿，并允许前端智能取色
            headers = {
                "Cache-Control": "public, max-age=604800",
                "Access-Control-Allow-Origin": "*",
                "Content-Type": res.headers.get("content-type", "image/jpeg")
            }
            return StreamingResponse(io.BytesIO(res.content), headers=headers)
    except:
        pass
    return {"status": "error"}

# 通用媒体规格提取器
def extract_media_badges(item):
    badges = []
    if "MediaSources" in item and item["MediaSources"]:
        source = item["MediaSources"][0]
        media_streams = source.get("MediaStreams", [])
        
        video_stream = next((s for s in media_streams if s["Type"] == "Video"), None)
        audio_stream = next((s for s in media_streams if s["Type"] == "Audio"), None)

        # 🔥 1. REMUX 识别
        path_or_name = (source.get("Path", "") + " " + source.get("Name", "")).upper()
        if "REMUX" in path_or_name:
            badges.append({"type": "quality", "text": "REMUX", "color": "bg-blue-600 text-white border-blue-500"})

        if video_stream:
            # 🔥 2. 分辨率
            width = video_stream.get("Width", 0)
            if width >= 3800:
                badges.append({"type": "res", "text": "4K", "color": "bg-gray-900 text-white border-gray-700 dark:bg-gray-100 dark:text-gray-900"})
            elif width >= 1900:
                badges.append({"type": "res", "text": "1080P", "color": "bg-blue-500 text-blue-100 border-blue-400"})
            
            # 🔥 3. 动态范围
            video_range = video_stream.get("VideoRange", "").upper()
            video_range_type = video_stream.get("VideoRangeType", "").upper()
            
            if "DOVI" in video_range or "DOVI" in video_range_type:
                badges.append({"type": "fx", "text": "Dolby Vision", "color": "bg-gradient-to-r from-indigo-600 to-purple-600 text-white border-indigo-400"})
            
            if "HDR" in video_range or "HDR10" in video_range_type:
                badges.append({"type": "fx", "text": "HDR", "color": "bg-yellow-500 text-yellow-900 border-yellow-400"})
                
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

    public_host = cfg.get("emby_public_url") or cfg.get("emby_external_url") or cfg.get("emby_public_host") or host
    public_host = public_host.rstrip('/')

    admin_id = get_emby_admin(host, key)
    if not admin_id:
        return {"status": "error", "message": "找不到管理员账号"}

    sys_info = get_emby_sys_info(host, key)
    use_new_route = is_new_emby_router(sys_info)

    try:
        search_url = f"{host}/emby/Users/{admin_id}/Items"
        params = {
            "api_key": key,
            "SearchTerm": query,
            "IncludeItemTypes": "Movie,Series",
            "Recursive": "true",
            "Fields": "Overview,MediaSources,ProviderIds,ImageTags,ProductionYear", 
            "Limit": 8 
        }
        res = requests.get(search_url, params=params, timeout=10).json()
        items = res.get("Items", [])

        results = []
        for item in items:
            media_type = "movie" if item["Type"] == "Movie" else "tv"
            
            poster_url = ""
            if item.get("ImageTags", {}).get("Primary"):
                poster_url = f"/api/library/image/{item['Id']}?type=Primary&width=400"
            elif item.get("ImageTags", {}).get("Backdrop"):
                poster_url = f"/api/library/image/{item['Id']}?type=Backdrop&width=400"
            else:
                tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
                if tmdb_id:
                    poster_url = f"https://image.tmdb.org/t/p/w500/{tmdb_id}.jpg"
                else:
                    poster_url = "/static/img/logo-dark.png" 

            if use_new_route:
                emby_url = f"{public_host}/web/index.html#!/item?id={item['Id']}&serverId={item.get('ServerId', '')}"
            else:
                emby_url = f"{public_host}/web/index.html#!/item/details.html?id={item['Id']}&serverId={item.get('ServerId', '')}"

            info = {
                "id": item["Id"],
                "name": item["Name"],
                "year": item.get("ProductionYear", "未知"),
                "overview": item.get("Overview", "暂无简介"),
                "type": media_type,
                "poster": poster_url,
                "emby_url": emby_url,  
                "badges": [] 
            }

            if media_type == "movie":
                info["badges"].extend(extract_media_badges(item))

            elif media_type == "tv":
                try:
                    eps_res = requests.get(
                        f"{host}/emby/Shows/{item['Id']}/Episodes?UserId={admin_id}&api_key={key}&Fields=ParentIndexNumber", 
                        timeout=5
                    ).json()
                    
                    season_counts = {}
                    for ep in eps_res.get("Items", []):
                        s_idx = ep.get("ParentIndexNumber")
                        if s_idx and s_idx > 0: 
                            season_counts[s_idx] = season_counts.get(s_idx, 0) + 1
                    
                    for s_idx in sorted(season_counts.keys()):
                        info["badges"].append({
                            "type": "season",
                            "text": f"第{s_idx}季: {season_counts[s_idx]}集",
                            "color": "bg-emerald-500 text-white border-emerald-400"
                        })

                    first_ep_res = requests.get(
                        f"{host}/emby/Shows/{item['Id']}/Episodes?UserId={admin_id}&api_key={key}&Limit=1&Fields=MediaSources", 
                        timeout=3
                    ).json()
                    if first_ep_res.get("Items"):
                        info["badges"].extend(extract_media_badges(first_ep_res["Items"][0]))
                except:
                    pass
            
            results.append(info)

        return {"status": "success", "data": results}
    except Exception as e:
        return {"status": "error", "message": f"全局搜索请求失败: {str(e)}"}