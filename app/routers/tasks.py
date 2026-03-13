import sqlite3
import requests
from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.core.config import cfg
from app.core.database import DB_PATH, query_db
# 🔥 引入核心适配器
from app.core.media_adapter import media_api
import re

router = APIRouter()

# ==========================================
# 1. 基础字典 (涵盖常见官方任务与百大主流插件)
# ==========================================
COMMON_TASK_DICT = {
    # --- Emby 官方核心维护任务 ---
    "Scan media library": "扫描媒体库",
    "Refresh Guide": "刷新电视指南",
    "Refresh channels": "刷新直播频道",
    "Clean up collections and playlists": "清理合集与播放列表",
    "Refresh people": "刷新演员与人物信息",
    "Refresh network shows": "刷新网络剧集",
    "Clean up image cache": "清理图像缓存",
    "Download missing subtitles": "下载缺失的字幕",
    "Extract chapter images": "提取视频章节图片",
    "Refresh local IP addresses": "刷新本地 IP 地址",
    "Check for application updates": "检查系统更新",
    "Check for plugin updates": "检查插件更新",
    "Optimize database": "优化数据库结构",
    "Vacuum database": "压缩与清理数据库 (Vacuum)",
    "Remove old watch history": "移除陈旧的播放历史",
    "Sync Playstate": "同步播放状态",
    "Update Plugins": "自动更新插件",
    "Update server": "自动更新服务器",
    "Cache images": "缓存图像",
    "Backup database": "备份服务器数据库",
    "Auto Organize": "自动整理媒体",
    "Generate Intro Video": "生成片头视频",
    "Rotate log file": "轮转并清理日志文件",
    "Clean up sync directories": "清理同步目录",
    "Convert media": "转换媒体格式",
    "Refresh library metadata": "刷新媒体库元数据",
    "Scan local network": "扫描本地局域网设备",
    "Download missing plugin updates": "下载缺失的插件更新",
    "Remove old sync jobs": "移除陈旧的同步任务",
    
    # --- 影视搜刮器类插件 (Jav / MetaTube / Douban / TMDB / Bangumi) ---
    "Scrape Jav": "JavScraper 搜刮器同步",
    "Update JavScraper Index": "更新 JavScraper 索引",
    "MetaTube: Update Subscriptions": "MetaTube: 更新订阅",
    "MetaTube: Auto Update Metadata": "MetaTube: 自动更新元数据",
    "TMDb: Refresh metadata": "TMDb: 刷新元数据",
    "TheMovieDb: Refresh metadata": "TheMovieDb: 刷新元数据",
    "OMDb: Refresh metadata": "OMDb: 刷新元数据",
    "TVDb: Refresh metadata": "TVDb: 刷新元数据",
    "Douban: Refresh metadata": "豆瓣(Douban): 刷新元数据",
    "Bgm.tv: Refresh metadata": "Bgm.tv: 刷新动漫元数据",
    "Bangumi: Refresh metadata": "Bangumi: 刷新动漫元数据",
    "AniDB: Refresh metadata": "AniDB: 刷新动漫元数据",
    "Kitsu: Refresh metadata": "Kitsu: 刷新动漫元数据",
    
    # --- 字幕与图像获取插件 (Open Subtitles / Fanart / Shooter / Thunder) ---
    "Open Subtitles: Download missing subtitles": "Open Subtitles: 下载缺失字幕",
    "Subscene: Download missing subtitles": "Subscene: 下载缺失字幕",
    "Shooter: Download missing subtitles": "伪射手(Shooter): 下载缺失字幕",
    "Thunder: Download missing subtitles": "迅雷(Thunder): 下载缺失字幕",
    "Fanart.tv: Download missing images": "Fanart.tv: 下载缺失的海报与艺术图",
    "Screen Grabber: Extract chapter images": "截屏器: 提取视频章节预览图",
    
    # --- 高级工具与扩展插件 (Trakt / Intro Skipper / Auto Box Sets) ---
    "Trakt.tv: Sync Library": "Trakt.tv: 同步媒体库",
    "Trakt.tv: Import Playstates": "Trakt.tv: 导入播放状态",
    "Trakt: Sync Library": "Trakt: 同步媒体库",
    "Trakt: Import Playstates": "Trakt: 导入播放状态",
    "Auto Box Sets: Create Collections": "Auto Box Sets: 自动创建电影合集",
    "Intro Skipper: Analyze Audio": "跳过片头(Intro Skipper): 分析音频指纹",
    "Intro Skipper: Analyze Video": "跳过片头(Intro Skipper): 分析视频画面",
    "Theme Songs: Download theme songs": "主题曲: 下载剧集主题曲",
    "Theme Videos: Download theme videos": "主题视频: 下载剧集主题背景视频",
    
    # --- 统计与通知类插件 (Playback Reporting / Webhooks / Statistics) ---
    "Playback Reporting: Backup database": "播放统计: 备份统计数据库",
    "Playback Reporting: Aggregate Data": "播放统计: 聚合计算历史数据",
    "EmbyStat: Refresh data": "EmbyStat: 刷新统计数据",
    "Statistics: Calculate statistics": "数据看板: 计算全站数据",
    "Statistics: Clean up old data": "数据看板: 清理过期数据",
    "Webhooks: Send test webhook": "Webhooks: 发送测试通知",
    "Slack: Send test notification": "Slack: 发送测试通知",
    "Telegram: Send test notification": "Telegram: 发送测试通知",
    "Discord: Send test notification": "Discord: 发送测试通知",
    
    # --- IPTV 与直播电视源 ---
    "M3U: Refresh guide": "M3U: 刷新直播节目单",
    "XmlTV: Refresh guide": "XmlTV: 刷新直播节目单",
    "HDHomeRun: Refresh guide": "HDHomeRun: 刷新直播节目单"
}

# 简要中文说明（未覆盖的会使用“任务名 + 系统任务”兜底）
COMMON_TASK_DESC_DICT = {
    "Scan media library": "重新扫描媒体库文件与目录变更",
    "Refresh Guide": "刷新直播电视的节目单与指南数据",
    "Refresh channels": "刷新直播频道列表",
    "Clean up image cache": "清理封面与图片缓存，释放空间",
    "Download missing subtitles": "自动下载缺失字幕",
    "Extract chapter images": "生成章节预览图",
    "Optimize database": "优化数据库索引结构",
    "Vacuum database": "压缩数据库，清理空闲空间",
    "Remove old watch history": "清理过期的播放历史记录",
    "Check for plugin updates": "检查并更新插件",
    "Check for application updates": "检查并更新服务器程序"
}

def _contains_cjk(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text or ""))

# ==========================================
# 2. 初始化自定义别名表
# ==========================================
def ensure_task_translation_schema():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS task_translations (
                        original_name TEXT PRIMARY KEY,
                        translated_name TEXT
                    )''')
        conn.commit()
        conn.close()
    except Exception as e:
        pass

ensure_task_translation_schema()

class TranslationModel(BaseModel):
    original_name: str
    translated_name: str

@router.post("/api/tasks/translate")
async def translate_task(data: TranslationModel, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    
    orig = data.original_name.strip()
    trans = data.translated_name.strip()
    if not orig: return {"status": "error", "message": "原名不能为空"}
    
    try:
        # 如果填写了翻译，就存入或更新；如果留空，就删除该自定义翻译，恢复默认
        if trans:
            query_db("INSERT OR REPLACE INTO task_translations (original_name, translated_name) VALUES (?, ?)", (orig, trans))
        else:
            query_db("DELETE FROM task_translations WHERE original_name = ?", (orig,))
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# 3. 核心 API：获取并应用混合翻译
# ==========================================
@router.get("/api/tasks")
async def get_tasks(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    
    try:
        # 🚀 替换为 media_api
        res = media_api.get("/ScheduledTasks", timeout=5)
        tasks = res.json()
        
        custom_trans_rows = query_db("SELECT original_name, translated_name FROM task_translations")
        custom_dict = {r['original_name']: r['translated_name'] for r in custom_trans_rows} if custom_trans_rows else {}
        
        groups = {}
        for t in tasks:
            cat = t.get('Category', '未分类')
            orig_name = t.get('Name', '')
            orig_desc = t.get('Description', '') or ''
            
            t['OriginalName'] = orig_name 
            
            if orig_name in custom_dict: t['Name'] = custom_dict[orig_name]
            elif orig_name in COMMON_TASK_DICT: t['Name'] = COMMON_TASK_DICT[orig_name]

            # 任务说明中文化（若已有中文则保留）
            if orig_desc and not _contains_cjk(orig_desc):
                t['Description'] = COMMON_TASK_DESC_DICT.get(orig_name) or f"{t.get('Name', orig_name)}（系统任务）"
                
            if cat not in groups: groups[cat] = []
            groups[cat].append(t)
            
        result = [{"title": k, "tasks": v} for k, v in groups.items()]
        
        cat_trans = {
            "Library": "媒体库扫描", "Application": "系统与应用",
            "Maintenance": "日常维护", "Live TV": "电视直播",
            "Sync": "状态同步", "Plugins": "插件自动化"
        }
        for r in result:
            if r["title"] in cat_trans: r["title"] = cat_trans[r["title"]]
            
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        # 🚀 替换为 media_api
        media_api.post(f"/ScheduledTasks/Running/{task_id}", timeout=5)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    try:
        # 🚀 替换为 media_api
        media_api.delete(f"/ScheduledTasks/Running/{task_id}", timeout=5)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
