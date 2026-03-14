from fastapi import APIRouter, Request, Response, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List
from app.core.config import cfg
from app.core.policy import clone_policy
from app.core.database import query_db
from app.core.media_adapter import media_api
import requests
import datetime
import secrets
import base64
import logging
import ipaddress
import socket
import urllib.parse

router = APIRouter()

def is_safe_image_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        if host.lower() in ("localhost",):
            return False
        if host.endswith(".local"):
            return False

        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False
            return True
        except ValueError:
            pass

        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                ip_str = info[4][0]
                ip = ipaddress.ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                    return False
        except Exception:
            return False
        return True
    except Exception:
        return False

# 🔥 自动无损升级数据库，确保有 remark 字段
try:
    query_db("ALTER TABLE users_meta ADD COLUMN remark TEXT DEFAULT ''")
    logging.getLogger("uvicorn").info("✅ 数据库无损升级：已成功添加用户备注(remark)字段")
except Exception:
    pass

# ==========================================
# 🔥 重新定义全部数据接收模型 (脱离外部模型，防止 422 和 AttributeError 报错)
# ==========================================
class UserUpdateModelEx(BaseModel):
    user_id: str
    is_disabled: bool = False
    expire_date: Optional[str] = None
    password: Optional[str] = None
    enable_all_folders: bool = True
    enabled_folders: List[str] = []
    excluded_sub_folders: List[str] = []
    enable_downloading: bool = True
    enable_video_transcoding: bool = True
    enable_audio_transcoding: bool = True
    max_parental_rating: Optional[int] = None
    max_concurrent: Optional[int] = None
    is_vip: bool = False
    remark: Optional[str] = ""
    apply_template_id: Optional[str] = None
    copy_library: bool = True
    copy_policy: bool = True
    copy_parental: bool = True

class NewUserModelEx(BaseModel):
    name: str
    password: Optional[str] = None
    expire_date: Optional[str] = None
    template_user_id: Optional[str] = None
    copy_library: bool = True
    copy_policy: bool = True
    copy_parental: bool = True
    max_concurrent: Optional[int] = None
    is_vip: bool = False
    remark: Optional[str] = ""

class InviteGenModelLocal(BaseModel):
    days: int
    count: Optional[int] = 1
    template_user_id: Optional[str] = None

class BatchActionModelLocal(BaseModel):
    user_ids: List[str]
    action: str
    value: Optional[str] = None
    copy_library: Optional[bool] = True
    copy_policy: Optional[bool] = True
    copy_parental: Optional[bool] = True

class InviteBatchModelLocal(BaseModel):
    codes: List[str]
    action: str

"""
核心引擎：全量策略快照克隆器
已迁移至 app.core.policy.clone_policy
"""

def check_expired_users():
    try:
        rows = query_db("SELECT user_id, expire_date FROM users_meta WHERE expire_date IS NOT NULL")
        if not rows: return
        now_str = datetime.datetime.now().strftime("%Y-%m-%d")
        for row in rows:
            if row['expire_date'] < now_str: 
                uid = row['user_id']
                try:
                    u_res = media_api.get(f"/Users/{uid}", timeout=5)
                    if u_res.status_code == 200:
                        user = u_res.json()
                        policy = user.get('Policy', {})
                        if not policy.get('IsDisabled', False):
                            policy['IsDisabled'] = True
                            media_api.post(f"/Users/{uid}/Policy", json=policy)
                except Exception as e: pass
    except Exception as e: pass

@router.get("/api/manage/libraries")
def api_get_libraries(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        res = media_api.get("/Library/VirtualFolders", timeout=5)
        if res.status_code == 200:
            libs = [{"Id": item["Guid"], "Name": item["Name"]} for item in res.json() if "Guid" in item]
            return {"status": "success", "data": libs}
        return {"status": "error", "message": "媒体服务器 API 返回异常"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/manage/users")
def api_manage_users(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    check_expired_users()
    public_host = cfg.get("emby_public_host") or cfg.get("emby_host", "")
    if public_host.endswith('/'): public_host = public_host[:-1]
    
    try:
        res = media_api.get("/Users", timeout=5)
        if res.status_code != 200: return {"status": "error", "message": "媒体服务器无法连接"}
        
        emby_users = res.json()
        meta_rows = query_db("SELECT * FROM users_meta")
        meta_map = {r['user_id']: dict(r) for r in meta_rows} if meta_rows else {}
        
        final_list = []
        for u in emby_users:
            uid = u['Id']
            meta = meta_map.get(uid, {})
            policy = u.get('Policy', {})
            
            final_list.append({
                "Id": uid, "Name": u['Name'], "LastLoginDate": u.get('LastLoginDate'),
                "IsDisabled": policy.get('IsDisabled', False), "IsAdmin": policy.get('IsAdministrator', False),
                "ExpireDate": meta.get('expire_date'), "Note": meta.get('note'), "PrimaryImageTag": u.get('PrimaryImageTag'),
                "EnableAllFolders": policy.get('EnableAllFolders', True),
                "EnabledFolders": policy.get('EnabledFolders', []), "ExcludedSubFolders": policy.get('ExcludedSubFolders', []),
                "EnableDownloading": policy.get('EnableContentDownloading', True),
                "EnableVideoTranscoding": policy.get('EnableVideoPlaybackTranscoding', True),
                "EnableAudioTranscoding": policy.get('EnableAudioPlaybackTranscoding', True),
                "MaxParentalRating": policy.get('MaxParentalRating'),
                "MaxConcurrent": meta.get('max_concurrent'),
                "IsVIP": bool(meta.get('is_vip', 0)),
                "Remark": meta.get('remark', '') 
            })
        return {"status": "success", "data": final_list, "emby_url": public_host}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/manage/user/{user_id}")
def api_get_single_user(user_id: str, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        res = media_api.get(f"/Users/{user_id}", timeout=5)
        if res.status_code == 200:
            user_data = res.json()
            policy = user_data.get('Policy', {})
            meta_row = query_db("SELECT * FROM users_meta WHERE user_id = ?", (user_id,), one=True)
            
            return {
                "status": "success", 
                "data": {
                    "Id": user_data['Id'], "Name": user_data['Name'],
                    "EnableAllFolders": policy.get('EnableAllFolders', True), "EnabledFolders": policy.get('EnabledFolders', []),
                    "ExcludedSubFolders": policy.get('ExcludedSubFolders', []), "EnableDownloading": policy.get('EnableContentDownloading', True),
                    "EnableVideoTranscoding": policy.get('EnableVideoPlaybackTranscoding', True), "EnableAudioTranscoding": policy.get('EnableAudioPlaybackTranscoding', True),
                    "MaxParentalRating": policy.get('MaxParentalRating'),
                    "MaxConcurrent": meta_row['max_concurrent'] if meta_row else None,
                    "IsVIP": bool(meta_row['is_vip']) if meta_row and meta_row['is_vip'] else False,
                    "Remark": meta_row['remark'] if meta_row and 'remark' in meta_row.keys() else "" 
                }
            }
        return {"status": "error"}
    except: return {"status": "error"}

@router.get("/api/user/image/{user_id}")
def get_user_avatar(user_id: str):
    try:
        res = media_api.get(f"/Users/{user_id}/Images/Primary", params={"quality": 90}, timeout=5, stream=True)
        if res.status_code == 200: return Response(content=res.content, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
        return Response(status_code=404)
    except: return Response(status_code=404)

@router.post("/api/manage/user/image")
async def api_update_user_image(request: Request, user_id: str = Form(...), url: str = Form(None), file: UploadFile = File(None)):
    if not request.session.get("user"): return {"status": "error"}
    try:
        img_data = None; c_type = "image/png"
        if url:
            if not is_safe_image_url(url):
                return {"status": "error", "message": "图片 URL 不安全或不可解析"}
            d_res = requests.get(url, timeout=10)
            if d_res.status_code == 200: 
                content_len = d_res.headers.get("Content-Length")
                if content_len and int(content_len) > 5 * 1024 * 1024:
                    return {"status": "error", "message": "图片过大，超过 5MB"}
                img_data = d_res.content
                c_type = d_res.headers.get('Content-Type', 'image/png')
        elif file:
            img_data = await file.read()
            c_type = file.content_type or "image/jpeg"
        if not img_data: return {"status": "error", "message": "无图片数据"}
        b64 = base64.b64encode(img_data)
        media_api.delete(f"/Users/{user_id}/Images/Primary")
        media_api.post(f"/Users/{user_id}/Images/Primary", data=b64, headers={"Content-Type": c_type})
        return {"status": "success"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/manage/invite/gen")
def api_gen_invite(data: InviteGenModelLocal, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        count = data.count if data.count and data.count > 0 else 1
        template_id = data.template_user_id
        if not template_id:
            template_id = cfg.get("default_invite_template_user_id") or None
        if not template_id:
            return {"status": "error", "message": "必须选择模板用户（系统默认模板未设置）"}
        if template_id:
            try:
                t_res = media_api.get(f"/Users/{template_id}", timeout=5)
                if t_res.status_code == 200:
                    t_user = t_res.json()
                    if t_user.get("Policy", {}).get("IsAdministrator", False):
                        return {"status": "error", "message": "模板用户不能是管理员，请更换"}
                else:
                    return {"status": "error", "message": "模板用户不存在或无法访问"}
            except Exception:
                return {"status": "error", "message": "模板用户校验失败"}
        codes = []
        created_at = datetime.datetime.now().isoformat()
        for _ in range(count):
            code = secrets.token_hex(8)
            query_db("INSERT INTO invitations (code, days, created_at, template_user_id) VALUES (?, ?, ?, ?)", (code, data.days, created_at, template_id))
            codes.append(code)
        return {"status": "success", "codes": codes}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/manage/invites")
def api_get_invites(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        rows = query_db("SELECT * FROM invitations ORDER BY created_at DESC")
        data = [dict(r) for r in rows] if rows else []
        return {"status": "success", "data": data}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/manage/invites/batch")
def api_manage_invites_batch(data: InviteBatchModelLocal, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        if data.action == "delete":
            for code in data.codes: query_db("DELETE FROM invitations WHERE code = ?", (code,))
        return {"status": "success", "message": "删除成功"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/manage/user/update")
def api_manage_user_update(data: UserUpdateModelEx, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        exist = query_db("SELECT * FROM users_meta WHERE user_id = ?", (data.user_id,), one=True)
        v_exp = data.expire_date if data.expire_date else None
        v_max = data.max_concurrent
        v_vip = 1 if data.is_vip else 0
        v_remark = data.remark if data.remark else ""
        
        if exist: 
            query_db("UPDATE users_meta SET expire_date = ?, max_concurrent = ?, is_vip = ?, remark = ? WHERE user_id = ?", (v_exp, v_max, v_vip, v_remark, data.user_id))
        else: 
            query_db("INSERT INTO users_meta (user_id, expire_date, max_concurrent, is_vip, remark, created_at) VALUES (?, ?, ?, ?, ?, ?)", (data.user_id, v_exp, v_max, v_vip, v_remark, datetime.datetime.now().isoformat()))
        
        if data.password:
            media_api.post(f"/Users/{data.user_id}/Password", json={"Id": data.user_id, "NewPw": data.password})

        p_res = media_api.get(f"/Users/{data.user_id}")
        if p_res.status_code == 200:
            p = p_res.json().get('Policy', {})
            
            # 🔥 如果前端执行了“获取预设”，优先使用全量克隆覆盖一次底层属性
            if data.apply_template_id:
                src_res = media_api.get(f"/Users/{data.apply_template_id}", timeout=5)
                if src_res.status_code == 200:
                    src_policy = src_res.json().get('Policy', {})
                    p = clone_policy(p, src_policy, data.copy_library, data.copy_policy, data.copy_parental)

            # 再用前端手动修改的显性字段进行覆盖
            if data.is_disabled is not None:
                p['IsDisabled'] = data.is_disabled
                if not data.is_disabled: p['LoginAttemptsBeforeLockout'] = -1
            if data.enable_all_folders is not None:
                p['EnableAllFolders'] = bool(data.enable_all_folders)
                p['EnabledFolders'] = [str(x) for x in data.enabled_folders] if not p['EnableAllFolders'] and data.enabled_folders is not None else []
            if data.excluded_sub_folders is not None: p['ExcludedSubFolders'] = data.excluded_sub_folders
            if data.enable_downloading is not None: p['EnableContentDownloading'] = data.enable_downloading; p['EnableSyncTranscoding'] = data.enable_downloading 
            if data.enable_video_transcoding is not None: p['EnableVideoPlaybackTranscoding'] = data.enable_video_transcoding; p['EnablePlaybackRemuxing'] = data.enable_video_transcoding 
            if data.enable_audio_transcoding is not None: p['EnableAudioPlaybackTranscoding'] = data.enable_audio_transcoding
            if data.max_parental_rating is not None:
                if data.max_parental_rating == -1: p.pop('MaxParentalRating', None)
                else: p['MaxParentalRating'] = data.max_parental_rating
            
            # 🔥 修复3: 移除了所有 p.pop() 清理代码，确保已克隆的 BlockedTags 等属性不会被破坏删除！
            media_api.post(f"/Users/{data.user_id}/Policy", json=p)
            
        return {"status": "success", "message": "用户信息已更新"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/manage/user/new")
def api_manage_user_new(data: NewUserModelEx, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        res = media_api.post("/Users/New", json={"Name": data.name})
        if res.status_code != 200: return {"status": "error", "message": f"创建失败: {res.text}"}
        new_id = res.json()['Id']
        
        if data.password: media_api.post(f"/Users/{new_id}/Password", json={"Id": new_id, "NewPw": data.password})
        
        p = media_api.get(f"/Users/{new_id}").json().get('Policy', {})
        
        # 🔥 全量克隆继承
        if data.template_user_id:
            src = media_api.get(f"/Users/{data.template_user_id}").json().get('Policy', {})
            p = clone_policy(p, src, data.copy_library, data.copy_policy, data.copy_parental)
        else:
            # 清理默认脏属性
            for k in ['BlockedMediaFolders','BlockedChannels','EnableAllChannels','EnabledChannels']: p.pop(k, None)
            
        media_api.post(f"/Users/{new_id}/Policy", json=p)
        
        v_exp = data.expire_date if data.expire_date else None
        v_max = data.max_concurrent
        v_vip = 1 if data.is_vip else 0
        v_remark = data.remark if data.remark else ""
        query_db("INSERT INTO users_meta (user_id, expire_date, max_concurrent, is_vip, remark, created_at) VALUES (?, ?, ?, ?, ?, ?)", (new_id, v_exp, v_max, v_vip, v_remark, datetime.datetime.now().isoformat()))
        
        return {"status": "success", "message": "用户创建成功"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.delete("/api/manage/user/{user_id}")
def api_manage_user_delete(user_id: str, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    if media_api.delete(f"/Users/{user_id}").status_code in [200, 204]:
        query_db("DELETE FROM users_meta WHERE user_id = ?", (user_id,))
        return {"status": "success"}
    return {"status": "error"}

@router.post("/api/manage/users/batch")
def api_manage_users_batch(data: BatchActionModelLocal, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        src_policy = {}; src_max_concurrent = None; src_is_vip = 0
        if data.action == "apply_template" and data.value:
            src_res = media_api.get(f"/Users/{data.value}", timeout=5)
            if src_res.status_code == 200:
                src_policy = src_res.json().get('Policy', {})
                t_meta = query_db("SELECT max_concurrent, is_vip FROM users_meta WHERE user_id = ?", (data.value,), one=True)
                src_max_concurrent = t_meta['max_concurrent'] if t_meta else None
                src_is_vip = t_meta['is_vip'] if t_meta and t_meta['is_vip'] else 0
            else:
                return {"status": "error", "message": "无法获取模板配置"}

        for uid in data.user_ids:
            if data.action == "delete":
                media_api.delete(f"/Users/{uid}")
                query_db("DELETE FROM users_meta WHERE user_id = ?", (uid,))
            elif data.action in ["enable", "disable"]:
                p_res = media_api.get(f"/Users/{uid}", timeout=5)
                if p_res.status_code == 200:
                    p = p_res.json().get('Policy', {})
                    p['IsDisabled'] = (data.action == "disable")
                    if data.action == "enable": p['LoginAttemptsBeforeLockout'] = -1
                    media_api.post(f"/Users/{uid}/Policy", json=p)
            elif data.action == "renew":
                new_date = None
                if data.value.startswith('+'):
                    days_to_add = int(data.value[1:])
                    row = query_db("SELECT expire_date FROM users_meta WHERE user_id = ?", (uid,), one=True)
                    current_expire = row['expire_date'] if row and row['expire_date'] else None
                    if current_expire:
                        try:
                            base_date = datetime.datetime.strptime(current_expire, "%Y-%m-%d")
                            if base_date < datetime.datetime.now(): base_date = datetime.datetime.now()
                        except: base_date = datetime.datetime.now()
                    else: base_date = datetime.datetime.now()
                    new_date = (base_date + datetime.timedelta(days=days_to_add)).strftime("%Y-%m-%d")
                else: new_date = data.value if data.value else None
                
                exist = query_db("SELECT 1 FROM users_meta WHERE user_id = ?", (uid,), one=True)
                if exist: query_db("UPDATE users_meta SET expire_date = ? WHERE user_id = ?", (new_date, uid))
                else: query_db("INSERT INTO users_meta (user_id, expire_date, created_at) VALUES (?, ?, ?)", (uid, new_date, datetime.datetime.now().isoformat()))
            elif data.action == "apply_template":
                p_res = media_api.get(f"/Users/{uid}", timeout=5)
                if p_res.status_code == 200:
                    p = p_res.json().get('Policy', {})
                    
                    # 🔥 核心：执行全量克隆覆盖，不会丢失包括 IsHidden 在内的任何权限！
                    p = clone_policy(p, src_policy, data.copy_library, data.copy_policy, data.copy_parental)
                    
                    # 其他本地专属属性(VIP,并发)独立同步
                    if data.copy_policy:
                        exist = query_db("SELECT 1 FROM users_meta WHERE user_id = ?", (uid,), one=True)
                        if exist: query_db("UPDATE users_meta SET max_concurrent = ?, is_vip = ? WHERE user_id = ?", (src_max_concurrent, src_is_vip, uid))
                        else: query_db("INSERT INTO users_meta (user_id, max_concurrent, is_vip, created_at) VALUES (?, ?, ?, ?)", (uid, src_max_concurrent, src_is_vip, datetime.datetime.now().isoformat()))

                    # 🔥 修复3: 这里也去掉了 p.pop() 破坏代码
                    media_api.post(f"/Users/{uid}/Policy", json=p)

        return {"status": "success", "message": f"成功操作了 {len(data.user_ids)} 个用户"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/users")
def api_get_users(request: Request):
    if not request.session.get("user"): 
        return {"status": "error"}
    try:
        res = media_api.get("/Users", timeout=5)
        if res.status_code == 200:
            hidden = cfg.get("hidden_users") or []
            data = [{
                "UserId": u['Id'],
                "UserName": u['Name'],
                "IsHidden": u['Id'] in hidden,
                "IsAdmin": bool(u.get("Policy", {}).get("IsAdministrator", False))
            } for u in res.json()]
            data.sort(key=lambda x: x['UserName'])
            return {"status": "success", "data": data}
        return {"status": "success", "data": []}
    except: 
        return {"status": "error"}
