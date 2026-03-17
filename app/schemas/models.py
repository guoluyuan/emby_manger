from pydantic import BaseModel
from typing import Optional, List

class LoginModel(BaseModel):
    username: str
    password: str
    captcha: str

class SettingsModel(BaseModel):
    emby_host: str
    emby_api_key: str
    tmdb_api_key: Optional[str] = ""
    proxy_url: Optional[str] = ""
    webhook_token: Optional[str] = ""
    hidden_users: List[str] = []
    emby_public_url: Optional[str] = ""  
    user_public_url: Optional[str] = ""
    user_lan_url: Optional[str] = ""
    admin_login_bg_url: Optional[str] = ""
    request_login_bg_url: Optional[str] = ""
    admin_login_bg_pc: Optional[str] = ""
    admin_login_bg_mobile: Optional[str] = ""
    request_login_bg_pc: Optional[str] = ""
    request_login_bg_mobile: Optional[str] = ""
    admin_login_bg_blur: Optional[float] = 12
    request_login_bg_blur: Optional[float] = 10
    default_invite_template_user_id: Optional[str] = ""
    welcome_message: Optional[str] = ""  
    client_download_url: Optional[str] = ""
    moviepilot_url: Optional[str] = ""
    moviepilot_token: Optional[str] = ""
    moviepilot_downloader: Optional[str] = ""
    moviepilot_save_path: Optional[str] = ""
    pulse_url: Optional[str] = ""
    cors_origins: Optional[List[str]] = []
    # 🔥 新增：双引擎模式开关
    playback_data_mode: Optional[str] = "sqlite"
    server_type: str = "emby"
    notify_user_login: bool = False     # 🔥 补上这行
    notify_item_deleted: bool = False   # 🔥 补上这行
    disable_update_check: Optional[bool] = False
# 🔥 新增风控全局字段
    enable_risk_control: Optional[bool] = False         # 全局风控总开关
    default_max_concurrent: Optional[int] = 2           # 全局默认允许并发数
    enable_risk_auto_ban: Optional[bool] = False        # Auto-ban on concurrent limit exceed

class SetupModel(BaseModel):
    emby_host: str
    emby_api_key: str
    server_type: Optional[str] = "emby"

class BotSettingsModel(BaseModel):
    tg_bot_token: str
    tg_chat_id: str
    enable_bot: bool
    enable_notify: bool
    enable_library_notify: Optional[bool] = False
    
    wecom_corpid: Optional[str] = ""
    wecom_corpsecret: Optional[str] = ""
    wecom_agentid: Optional[str] = ""
    wecom_touser: Optional[str] = "@all"
    wecom_proxy_url: Optional[str] = "https://qyapi.weixin.qq.com"
    wecom_token: Optional[str] = ""
    wecom_aeskey: Optional[str] = ""

class PushRequestModel(BaseModel):
    user_id: str
    period: str
    theme: str

class ScheduleRequestModel(BaseModel):
    user_id: str
    period: str
    theme: str

# 🔥 更新：为编辑用户增加高级权限字段
class UserUpdateModel(BaseModel):
    user_id: str
    password: Optional[str] = None
    is_disabled: Optional[bool] = None
    expire_date: Optional[str] = None 
    enable_all_folders: Optional[bool] = None
    enabled_folders: Optional[List[str]] = None
    excluded_sub_folders: Optional[List[str]] = None
    # 高级控制
    enable_downloading: Optional[bool] = None
    enable_video_transcoding: Optional[bool] = None
    enable_audio_transcoding: Optional[bool] = None
    max_parental_rating: Optional[int] = None
# 🔥 新增单用户风控字段
    max_concurrent: Optional[int] = None   # 该用户的专属最大并发数
    is_vip: Optional[bool] = False
    risk_level: Optional[str] = None       # 风控状态(例如：safe, banned)

# 🔥 更新：为新建/套用模板增加颗粒度控制选项
class NewUserModel(BaseModel):
    name: str
    password: Optional[str] = None 
    expire_date: Optional[str] = None
    template_user_id: Optional[str] = None 
    # 颗粒度复制选项
    copy_library: Optional[bool] = True
    copy_policy: Optional[bool] = True
    copy_parental: Optional[bool] = True
# 🔥 新增单用户风控字段
    max_concurrent: Optional[int] = None   # 新用户的默认最大并发数
    is_vip: Optional[bool] = False

class InviteGenModel(BaseModel):
    days: int 
    template_user_id: Optional[str] = None 
    count: Optional[int] = 1

class UserRegisterModel(BaseModel):
    code: str
    username: str
    password: str

class BatchActionModel(BaseModel):
    user_ids: List[str]
    action: str  
    value: Optional[str] = None  

class MediaRequestSubmitModel(BaseModel):
    tmdb_id: int
    media_type: str  
    title: str
    year: str = ""
    poster_path: str = ""
    overview: str = ""

class MediaRequestStatusUpdateModel(BaseModel):
    tmdb_id: int
    status: int  

# 🔥 更新：为批量操作新增颗粒度选项参数
class BatchActionModel(BaseModel):
    user_ids: List[str]
    action: str  
    value: Optional[str] = None  
    copy_library: Optional[bool] = False
    copy_policy: Optional[bool] = False
    copy_parental: Optional[bool] = False
