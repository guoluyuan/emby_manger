from fastapi import APIRouter, Response
from app.core.config import cfg
from app.core.media_adapter import media_api  # 🔥 引入核心适配器
import requests
import urllib.parse
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import time
from collections import deque

# 初始化日志
logger = logging.getLogger("uvicorn")
router = APIRouter()

# 🔥 保留一个专门用于外部请求 (如 TMDB) 的 Session
ext_session = requests.Session()
retries = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
ext_session.mount('http://', HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100))
ext_session.mount('https://', HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100))

# 图片 ID 映射缓存
smart_image_cache = {}
smart_image_response_cache = {}
smart_image_response_order = deque()
SMART_IMAGE_CACHE_TTL = 3600
SMART_IMAGE_CACHE_MAX = 200

def _get_cached_smart_image(cache_key: str):
    entry = smart_image_response_cache.get(cache_key)
    if not entry:
        return None
    if entry["expires_at"] <= time.time():
        smart_image_response_cache.pop(cache_key, None)
        return None
    return entry

def _set_cached_smart_image(cache_key: str, content: bytes, content_type: str):
    if cache_key in smart_image_response_cache:
        smart_image_response_cache[cache_key] = {"content": content, "content_type": content_type, "expires_at": time.time() + SMART_IMAGE_CACHE_TTL}
        return
    if len(smart_image_response_order) >= SMART_IMAGE_CACHE_MAX:
        oldest = smart_image_response_order.popleft()
        smart_image_response_cache.pop(oldest, None)
    smart_image_response_order.append(cache_key)
    smart_image_response_cache[cache_key] = {"content": content, "content_type": content_type, "expires_at": time.time() + SMART_IMAGE_CACHE_TTL}

def extract_season_number(name: str):
    """从名称中提取季号，例如 '唐朝诡事录 - 第 2 季' -> 2"""
    m = re.search(r'第\s*(\d+)\s*季', name)
    if m: return int(m.group(1))
    m2 = re.search(r'S0*(\d+)', name, re.I)
    if m2: return int(m2.group(1))
    return None

def get_real_image_id_robust(item_id: str):
    """智能 ID 转换（解决剧集封面变单集截图的问题）"""
    try:
        # 🚀 替换为 media_api
        res_a = media_api.get(f"/Items/{item_id}", params={"Fields": "SeriesId,ParentId,SeasonId"}, timeout=3)
        if res_a.status_code == 200:
            data = res_a.json()
            if data.get("Type") == "Episode":
                if data.get("SeasonId"): 
                    season_id = data["SeasonId"]
                    s_res = media_api.get(f"/Items/{season_id}", timeout=2)
                    if s_res.status_code == 200 and s_res.json().get("ImageTags", {}).get("Primary"):
                        return season_id
                if data.get("SeriesId"): return data['SeriesId']
                
            if data.get("SeriesId"): return data['SeriesId']
            if data.get("Type") == "Episode" and data.get("ParentId"): return data['ParentId']
    except: pass

    try:
        res_b = media_api.get(f"/Items/{item_id}/Ancestors", timeout=3)
        if res_b.status_code == 200:
            for ancestor in res_b.json():
                if ancestor.get("Type") == "Series": return ancestor['Id']
                if ancestor.get("Type") == "Season" and not ancestor.get("SeriesId"): return ancestor['Id']
    except: pass

    try:
        res_c = media_api.get("/Items", params={"Ids": item_id, "Fields": "SeriesId", "Recursive": "true"}, timeout=3)
        if res_c.status_code == 200:
            items = res_c.json().get("Items", [])
            if items and items[0].get("SeriesId"): return items[0]['SeriesId']
    except: pass

    return item_id

@router.get("/api/proxy/image/{item_id}/{img_type}")
def proxy_image(item_id: str, img_type: str):
    try:
        target_id = get_real_image_id_robust(item_id) if img_type.lower() == 'primary' else item_id
        
        # 🚀 替换为 media_api，并透传 stream=True
        params = {"maxHeight": 600, "maxWidth": 400, "quality": 90}
        resp = media_api.get(f"/Items/{target_id}/Images/{img_type}", params=params, timeout=10, stream=True)
        
        if resp.status_code == 200:
            return Response(content=resp.content, media_type=resp.headers.get("Content-Type", "image/jpeg"), headers={"Cache-Control": "public, max-age=604800"})
        
        if resp.status_code == 404 and target_id != item_id:
            fallback_resp = media_api.get(f"/Items/{item_id}/Images/{img_type}", params=params, timeout=10, stream=True)
            if fallback_resp.status_code == 200:
                 return Response(content=fallback_resp.content, media_type=fallback_resp.headers.get("Content-Type", "image/jpeg"), headers={"Cache-Control": "public, max-age=604800"})
    except Exception: pass
    return Response(status_code=404)

@router.get("/api/proxy/smart_image")
def proxy_smart_image(item_id: str, name: str = "", year: str = "", type: str = "Primary"):
    # 1. 缓存拦截（仅允许 Emby 内部 ID，不再使用外部链接）
    cached_result = smart_image_cache.get(item_id)
    if cached_result and str(cached_result).startswith('http'):
        cached_result = None

    target_id = cached_result if cached_result and not str(cached_result).startswith('http') else item_id
    img_type = type
    params = {"maxWidth": 1920, "quality": 80} if img_type.lower() == 'backdrop' else {"maxHeight": 800, "maxWidth": 600, "quality": 90}
    
    if img_type.lower() == 'primary' and target_id == item_id:
        target_id = get_real_image_id_robust(target_id)
    cache_key = f"{target_id}:{img_type.lower()}:{params.get('maxWidth','')}:{params.get('maxHeight','')}"
    cached = _get_cached_smart_image(cache_key)
    if cached:
        return Response(
            content=cached["content"],
            media_type=cached["content_type"] or "image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"}
        )
        
    # 2. 第 1 级防御：正常请求媒体库 (使用 media_api)
    primary_failed = False
    try:
        resp = media_api.get(f"/Items/{target_id}/Images/{img_type}", params=params, timeout=5, stream=True)
        if resp.status_code == 200:
            _set_cached_smart_image(cache_key, resp.content, resp.headers.get("Content-Type", "image/jpeg"))
            return Response(content=resp.content, media_type=resp.headers.get("Content-Type", "image/jpeg"), headers={"Cache-Control": "public, max-age=604800"})
        if img_type.lower() == 'primary':
            primary_failed = True
    except requests.exceptions.RequestException as e: 
        logger.debug(f"媒体库图片请求超时或断开: {e}")
        if img_type.lower() == 'primary':
            primary_failed = True

    # 🔥 Primary 无图时，自动降级尝试 Backdrop / Thumb
    if img_type.lower() == 'primary' and primary_failed:
        try:
            bd_params = {"maxWidth": 1280, "quality": 80}
            bd_resp = media_api.get(f"/Items/{target_id}/Images/Backdrop", params=bd_params, timeout=5, stream=True)
            if bd_resp.status_code == 200:
                _set_cached_smart_image(cache_key, bd_resp.content, bd_resp.headers.get("Content-Type", "image/jpeg"))
                return Response(content=bd_resp.content, media_type=bd_resp.headers.get("Content-Type", "image/jpeg"), headers={"Cache-Control": "public, max-age=604800"})
        except requests.exceptions.RequestException:
            pass
        try:
            th_params = {"maxWidth": 800, "quality": 80}
            th_resp = media_api.get(f"/Items/{target_id}/Images/Thumb", params=th_params, timeout=5, stream=True)
            if th_resp.status_code == 200:
                _set_cached_smart_image(cache_key, th_resp.content, th_resp.headers.get("Content-Type", "image/jpeg"))
                return Response(content=th_resp.content, media_type=th_resp.headers.get("Content-Type", "image/jpeg"), headers={"Cache-Control": "public, max-age=604800"})
        except requests.exceptions.RequestException:
            pass

    # 3. 不再使用名称检索或 TMDB 外部兜底，确保只展示 Emby 原始封面
    return Response(status_code=404)

@router.get("/api/proxy/user_image/{user_id}")
def proxy_user_image(user_id: str, tag: str = None):
    try:
        params = {"width": 200, "height": 200, "mode": "Crop", "quality": 90}
        if tag: params["tag"] = tag
        # 🚀 替换为 media_api
        resp = media_api.get(f"/Users/{user_id}/Images/Primary", params=params, timeout=3, stream=True)
        if resp.status_code == 200:
            return Response(content=resp.content, media_type=resp.headers.get("Content-Type", "image/jpeg"))
    except: pass
    return Response(status_code=404)
