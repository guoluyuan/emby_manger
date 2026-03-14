import os
import json
import secrets
from fastapi.templating import Jinja2Templates

# ================= 路径配置 =================
CONFIG_DIR = "/app/config"
if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
FONT_DIR = os.path.join(CONFIG_DIR, "fonts")
if not os.path.exists(FONT_DIR):
    os.makedirs(FONT_DIR, exist_ok=True)

# ================= 资源常量 =================
FONT_URL = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/Simplified/NotoSansCJKsc-Bold.otf"
FONT_PATH = os.path.join(FONT_DIR, "NotoSansCJKsc-Bold.otf")
REPORT_COVER_URL = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=1200&auto=format&fit=crop"
FALLBACK_IMAGE_URL = "https://img.hotimg.com/a444d32a033994d5b.png"

TMDB_FALLBACK_POOL = [
    "https://image.tmdb.org/t/p/original/zfbjgQE1uSd9wiPTX4VzsLi0rGG.jpg",
    "https://image.tmdb.org/t/p/original/rLb2cs785pePbIKYQz1CADtovh7.jpg",
    "https://image.tmdb.org/t/p/original/tmU7GeKVybMWFButWEGl2M4GeiP.jpg",
    "https://image.tmdb.org/t/p/original/kXfqcdQKsToO0OUXHcrrNCHDBzO.jpg",
    "https://image.tmdb.org/t/p/original/zb6fM1CX41D9rF9hdgclu0peUmy.jpg"
]

THEMES = {
    "black_gold": {"bg": (26, 26, 26), "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (234, 179, 8)},
    "cyber":      {"bg": (46, 16, 101), "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (0, 255, 255)},
    "ocean":      {"bg": (15, 23, 42),  "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (56, 189, 248)},
    "aurora":     {"bg": (6, 78, 59),   "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (52, 211, 153)},
    "magma":      {"bg": (127, 29, 29), "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (251, 146, 60)},
    "sunset":     {"bg": (124, 45, 18), "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (253, 186, 116)},
    "concrete":   {"bg": (82, 82, 82),  "text": (255, 255, 255), "card": (255, 255, 255, 20), "highlight": (212, 212, 216)},
    "white":      {"bg": (255, 255, 255), "text": (51, 51, 51), "card": (0, 0, 0, 10), "highlight": (234, 179, 8)}
}

def _get_playback_mode_from_env():
    env_mode = os.getenv("PLAYBACK_DATA_MODE", "").strip().lower()
    if env_mode in ("api", "sqlite"):
        return env_mode
    return "sqlite"

DEFAULT_CONFIG = {
    "emby_host": os.getenv("EMBY_HOST", "http://127.0.0.1:8096").rstrip('/'),
    "emby_api_key": os.getenv("EMBY_API_KEY", "").strip(),
    "emby_public_host": "",
    "tmdb_api_key": os.getenv("TMDB_API_KEY", "").strip(),
    "proxy_url": "",
    "hidden_users": [],
    "tg_bot_token": "",
    "tg_chat_id": "",     
    "enable_bot": False,  
    "enable_notify": False,
    "enable_library_notify": False,
    # 🔥 新增：细颗粒度事件开关
    "notify_user_login": False,   
    "notify_item_deleted": False, 
    "webhook_token": "",
    "calendar_cache_ttl": 86400,
    "scheduled_tasks": [],
    "emby_public_url": "", 
    "welcome_message": "",
    "client_download_url": "",
    "moviepilot_url": "",
    "moviepilot_token": "",
    "pulse_url": "",
    "server_type": "emby",
    "secret_key": "",
    "cors_origins": [],
    "playback_data_mode": _get_playback_mode_from_env()
}

class ConfigManager:
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    self.config.update(saved)
            except Exception as e: 
                print(f"⚠️ Config Load Error: {e}")
    
    def save(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e: 
            print(f"⚠️ Config Save Error: {e}")

    def get(self, key, default=None): 
        return self.config.get(key, default if default is not None else DEFAULT_CONFIG.get(key))
    
    def __getitem__(self, key):
        return self.config.get(key, DEFAULT_CONFIG.get(key))

    def __setitem__(self, key, value):
        self.config[key] = value
        self.save()

    def set(self, key, value): 
        self.config[key] = value
        self.save()
    
    def get_all(self): 
        return self.config

cfg = ConfigManager()
templates = Jinja2Templates(directory="templates")

# Allow env to override playback mode for containerized deployments.
env_playback_mode = os.getenv("PLAYBACK_DATA_MODE", "").strip().lower()
if env_playback_mode in ("api", "sqlite") and cfg.get("playback_data_mode") != env_playback_mode:
    cfg.set("playback_data_mode", env_playback_mode)

# Ensure critical secrets are not left at insecure defaults.
env_secret = os.getenv("SECRET_KEY", "").strip()
if env_secret:
    SECRET_KEY = env_secret
else:
    if not cfg.get("secret_key"):
        cfg.set("secret_key", secrets.token_urlsafe(32))
    SECRET_KEY = cfg.get("secret_key")

if not cfg.get("webhook_token") or cfg.get("webhook_token") == "embypulse":
    cfg.set("webhook_token", secrets.token_urlsafe(24))

PORT = 10307
DB_PATH = os.getenv("DB_PATH", "./emby-data/playback_reporting.db")

def save_config():
    cfg.save()
