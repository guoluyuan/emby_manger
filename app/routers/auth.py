import sqlite3
import requests
import datetime
import secrets
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from app.core.config import cfg
from app.core.database import DB_PATH, query_db
from app.schemas.models import LoginModel, UserRegisterModel

router = APIRouter()

def ensure_invitations_schema():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("PRAGMA table_info(invitations)")
        cols = [col[1] for col in c.fetchall()]
        if cols:
            if 'used_count' not in cols: c.execute("ALTER TABLE invitations ADD COLUMN used_count INTEGER DEFAULT 0")
            if 'max_uses' not in cols: c.execute("ALTER TABLE invitations ADD COLUMN max_uses INTEGER DEFAULT 1")
            if 'used_by' not in cols: c.execute("ALTER TABLE invitations ADD COLUMN used_by TEXT")
            if 'used_at' not in cols: c.execute("ALTER TABLE invitations ADD COLUMN used_at DATETIME")
            if 'status' not in cols: c.execute("ALTER TABLE invitations ADD COLUMN status INTEGER DEFAULT 0")
            if 'template_user_id' not in cols: c.execute("ALTER TABLE invitations ADD COLUMN template_user_id TEXT")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Upgrade invitations table error: {e}")

ensure_invitations_schema()


@router.post("/api/register")
async def api_register(data: UserRegisterModel):
    try:
        invite = query_db("SELECT * FROM invitations WHERE code = ?", (data.code,), one=True)
        if not invite:
            return JSONResponse(content={"status": "error", "message": "无效的邀请码"})
        
        used_count = invite['used_count'] if invite['used_count'] is not None else 0
        max_uses = invite['max_uses'] if invite['max_uses'] is not None else 1
        
        if used_count >= max_uses:
            return JSONResponse(content={"status": "error", "message": "邀请码已被使用或超过最大次数"})

        host = cfg.get("emby_host"); key = cfg.get("emby_api_key")
        if not host or not key:
            return JSONResponse(content={"status": "error", "message": "系统未配置 Emby 连接"})

        res = requests.post(f"{host}/emby/Users/New?api_key={key}", json={"Name": data.username})
        if res.status_code != 200:
            return JSONResponse(content={"status": "error", "message": f"用户名可能已存在"})
        
        new_id = res.json()['Id']

        pwd_res = requests.post(f"{host}/emby/Users/{new_id}/Password?api_key={key}", json={"Id": new_id, "NewPw": data.password})
        if pwd_res.status_code not in [200, 204]:
            requests.delete(f"{host}/emby/Users/{new_id}?api_key={key}")
            return JSONResponse(content={"status": "error", "message": "密码设置失败"})

        p_res = requests.get(f"{host}/emby/Users/{new_id}?api_key={key}")
        policy = p_res.json().get('Policy', {}) if p_res.status_code == 200 else {}
        
        policy['IsDisabled'] = False
        policy['LoginAttemptsBeforeLockout'] = -1
        
        template_id = invite['template_user_id'] if 'template_user_id' in invite.keys() else None
        
        if template_id:
            try:
                src_res = requests.get(f"{host}/emby/Users/{template_id}?api_key={key}", timeout=5)
                if src_res.status_code == 200:
                    src_policy = src_res.json().get('Policy', {})
                    
                    policy['EnableAllFolders'] = src_policy.get('EnableAllFolders', True)
                    policy['EnabledFolders'] = src_policy.get('EnabledFolders', [])
                    policy['ExcludedSubFolders'] = src_policy.get('ExcludedSubFolders', [])
                    
                    # 🔥 同步所有连带的子权限
                    policy['EnableContentDownloading'] = src_policy.get('EnableContentDownloading', True)
                    policy['EnableSyncTranscoding'] = src_policy.get('EnableSyncTranscoding', True) 
                    
                    policy['EnableVideoPlaybackTranscoding'] = src_policy.get('EnableVideoPlaybackTranscoding', True)
                    policy['EnablePlaybackRemuxing'] = src_policy.get('EnablePlaybackRemuxing', True)
                    
                    policy['EnableAudioPlaybackTranscoding'] = src_policy.get('EnableAudioPlaybackTranscoding', True)
                    
                    if 'MaxParentalRating' in src_policy:
                        policy['MaxParentalRating'] = src_policy['MaxParentalRating']
            except: pass
            
        requests.post(f"{host}/emby/Users/{new_id}/Policy?api_key={key}", json=policy)

        expire_date = None
        if invite['days'] > 0:
            expire_dt = datetime.datetime.now() + datetime.timedelta(days=invite['days'])
            expire_date = expire_dt.strftime("%Y-%m-%d")
            query_db("INSERT INTO users_meta (user_id, expire_date, created_at) VALUES (?, ?, ?)", 
                     (new_id, expire_date, datetime.datetime.now().isoformat()))

        used_at = datetime.datetime.now().isoformat()
        query_db(
            "UPDATE invitations SET used_count = COALESCE(used_count, 0) + 1, used_by = ?, used_at = ?, status = 1 WHERE code = ?", 
            (data.username, used_at, data.code)
        )

        public_url = cfg.get("emby_public_url") or host 
        welcome_msg = cfg.get("welcome_message") or "请妥善保管您的账号密码。"

        return JSONResponse(content={
            "status": "success",
            "server_url": public_url,
            "welcome_message": welcome_msg
        })

    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)})


@router.post("/api/login")
async def api_login(data: LoginModel, request: Request):
    try:
        host = cfg.get("emby_host")
        if not host: 
            return JSONResponse(content={"status": "error", "message": "请先在网页完成首次配置"})
            
        url = f"{host}/emby/Users/AuthenticateByName"
        payload = {"Username": data.username, "Pw": data.password}
        headers = {"X-Emby-Authorization": 'MediaBrowser Client="EmbyPulse", Device="Web", DeviceId="EmbyPulse", Version="1.0.0"'}
        
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if res.status_code == 200:
            user_info = res.json().get("User", {})
            if not user_info.get("Policy", {}).get("IsAdministrator", False):
                return JSONResponse(content={"status": "error", "message": "权限不足：仅限 Emby 管理员登录"})
            
            request.session["user"] = {
                "id": user_info.get("Id"),
                "name": user_info.get("Name"),
                "is_admin": True,
                "server_id": res.json().get("ServerId") 
            }
            request.session["csrf_token"] = secrets.token_urlsafe(32)
            return JSONResponse(content={"status": "success"})
        
        elif res.status_code == 401: return JSONResponse(content={"status": "error", "message": "账号或密码错误"})
        else: return JSONResponse(content={"status": "error", "message": f"Emby 连接失败: {res.status_code}"})
            
    except Exception as e: return JSONResponse(content={"status": "error", "message": f"登录异常: {str(e)}"})


@router.get("/logout")
async def api_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

@router.get("/api/csrf")
async def api_csrf(request: Request):
    user = request.session.get("user") or request.session.get("req_user")
    if not user:
        return {"status": "error"}
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return {"status": "success", "token": token}
