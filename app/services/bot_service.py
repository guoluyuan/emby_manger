import threading
import time
import requests
import datetime
import io
import logging
import urllib.parse
import json 
import re
import ipaddress
import sqlite3
from collections import defaultdict
from app.core.config import cfg, REPORT_COVER_URL, FALLBACK_IMAGE_URL
from app.core.database import query_db, get_base_filter, add_sys_notification, DB_PATH
from app.services.report_service import report_gen, HAS_PIL
from app.core.event_bus import bus

logger = logging.getLogger("uvicorn")

def get_admin_id():
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    if not key or not host: return None
    try:
        res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=5)
        if res.status_code == 200:
            users = res.json()
            for u in users:
                if u.get("Policy", {}).get("IsAdministrator"): return u['Id']
            if users: return users[0]['Id']
    except: pass
    return None

# 🔥 新增：初始化静音规则数据库
def init_notify_rules_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS notify_mutes (
            user_id TEXT,
            event_type TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, event_type)
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to create notify_mutes table: {e}")

init_notify_rules_db()

class SystemDaemon:
    def __init__(self):
        self.running = False
        self.schedule_thread = None 
        self.library_queue = []
        self.library_lock = threading.Lock()
        self.library_thread = None
        self.last_check_min = -1
        self.last_sync_min = -1
        bus.subscribe("webhook.received", self.on_webhook_event)
        
    def start(self):
        if self.running: return
        self.running = True
        self.schedule_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.schedule_thread.start()
        self.library_thread = threading.Thread(target=self._library_notify_loop, daemon=True)
        self.library_thread.start()
        print("🧠 System Daemon Started (Event Subsystem Online)")
    def stop(self): self.running = False

    def on_webhook_event(self, event: str, data: dict):
        if "item.added" in event or "library.new" in event:
            item = data.get("Item", {})
            if item.get("Id"):
                self.add_library_task(item)
                if item.get("Type") == "Episode":
                    from app.services.calendar_service import calendar_service
                    calendar_service.mark_episode_ready(item.get("SeriesId"), item.get("ParentIndexNumber"), item.get("IndexNumber"))
                    self._clear_gap_record_async(item)
        elif "playback.start" in event: bus.publish("notify.playback.start", data)
        elif "playback.stop" in event: bus.publish("notify.playback.stop", data)
        elif "auth" in event or "login" in event: bus.publish("notify.user.login", data)
        elif "delete" in event or "remove" in event: bus.publish("notify.item.deleted", data)

    def _auto_finish_request(self, tmdb_id):
        if not tmdb_id: return
        try:
            tid = int(tmdb_id)
            query_db("UPDATE media_requests SET status = 2, updated_at = CURRENT_TIMESTAMP WHERE tmdb_id = ? AND status IN (0, 1, 4)", (tid,))
        except Exception as e: pass

    def _clear_gap_record_async(self, item: dict):
        try:
            if item.get("Type") != "Episode": return
            series_id = str(item.get("SeriesId"))
            season = int(item.get("ParentIndexNumber", -1))
            episode = int(item.get("IndexNumber", -1))
            if season == -1 or episode == -1: return

            query_db("DELETE FROM gap_records WHERE series_id=? AND season_number=? AND episode_number=?", (series_id, season, episode))
            try:
                from app.routers.gaps import state_lock, scan_state
                with state_lock:
                    if scan_state.get("results"):
                        for s in scan_state["results"]:
                            if str(s.get("series_id")) == series_id:
                                s["gaps"] = [ep for ep in s.get("gaps", []) if not (int(ep.get("season")) == season and int(ep.get("episode")) == episode)]
                                if len(s["gaps"]) == 0 and s.get("tmdb_status") in ["Ended", "Canceled"]:
                                    try: query_db("INSERT OR IGNORE INTO gap_perfect_series (series_id, tmdb_id, series_name) VALUES (?, ?, ?)", (series_id, s.get("tmdb_id"), s.get("series_name")))
                                    except: pass
                        scan_state["results"] = [s for s in scan_state["results"] if len(s.get("gaps", [])) > 0]
                        query_db("INSERT OR REPLACE INTO gap_scan_cache (id, result_json, updated_at) VALUES (1, ?, datetime('now', 'localtime'))", (json.dumps(scan_state["results"]),))
            except: pass
        except Exception as e: pass

    def add_library_task(self, item):
        with self.library_lock:
            if not any(x.get('Id') == item.get('Id') for x in self.library_queue):
                self.library_queue.append(item)

    def _library_notify_loop(self):
        while self.running:
            try:
                with self.library_lock: has_data = len(self.library_queue) > 0
                if not has_data: time.sleep(2); continue

                idle_time = 0; last_len = 0; max_wait = 0
                while idle_time < 15 and max_wait < 120:
                    time.sleep(3)
                    idle_time += 3; max_wait += 3
                    with self.library_lock:
                        curr_len = len(self.library_queue)
                        if curr_len > last_len: idle_time = 0; last_len = curr_len
                
                items_to_process = []
                with self.library_lock:
                    items_to_process = self.library_queue[:]
                    self.library_queue = [] 
                
                if items_to_process: self._process_library_group(items_to_process)
            except Exception as e: time.sleep(5)

    def _process_library_group(self, items):
        groups = defaultdict(list)
        for item in items:
            itype = item.get('Type')
            if itype in ['Episode', 'Season'] and item.get('SeriesId'): groups[str(item.get('SeriesId'))].append(item)
            elif itype == 'Series': groups[str(item.get('Id'))].append(item)
            else: groups[str(item.get('Id'))].append(item)

        for group_id, group_items in groups.items():
            try:
                is_tv = any(x.get('Type') in ['Episode', 'Season', 'Series'] for x in group_items)
                if is_tv:
                    fresh_episodes = self._check_fresh_episodes(group_id)
                    if fresh_episodes: self._push_episode_group(group_id, fresh_episodes)
                    else:
                        series_item = next((x for x in group_items if x.get('Type') == 'Series'), None)
                        if series_item: self._push_single_item(series_item)
                        else:
                            episodes_only = [x for x in group_items if x.get('Type') == 'Episode']
                            if episodes_only: self._push_episode_group(group_id, episodes_only)
                else: self._push_single_item(group_items[0])
                time.sleep(2) 
            except Exception as e: pass

    def _check_fresh_episodes(self, series_id):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        admin_id = get_admin_id()
        if not admin_id: return []
        try:
            url = f"{host}/emby/Users/{admin_id}/Items"
            params = { "ParentId": series_id, "Recursive": "true", "IncludeItemTypes": "Episode", "Limit": 1000, "SortBy": "DateCreated", "SortOrder": "Descending", "Fields": "DateCreated,Name,ParentIndexNumber,IndexNumber", "api_key": key }
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200: return []
            items = res.json().get("Items", [])
            if not items: return []
            fresh_list = []; last_time = None
            for i, item in enumerate(items):
                curr_time = self._parse_emby_time(item.get("DateCreated"))
                if not curr_time: 
                    if i == 0: fresh_list.append(item)
                    break
                if i == 0: fresh_list.append(item); last_time = curr_time
                else:
                    delta = abs((last_time - curr_time).total_seconds())
                    if delta <= 120: fresh_list.append(item); last_time = curr_time 
                    else: break 
            return fresh_list
        except Exception as e: return []

    def _parse_emby_time(self, date_str):
        if not date_str: return None
        try:
            clean_str = date_str.replace('Z', '')[:26]
            if '.' in clean_str: return datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S.%f")
            else: return datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
        except: return None

    def _push_episode_group(self, series_id, episodes):
        try:
            for ep in episodes:
                s_idx = ep.get('ParentIndexNumber'); e_idx = ep.get('IndexNumber')
                if s_idx is None or e_idx is None: continue
                res = query_db("SELECT id FROM gap_records WHERE series_id=? AND season_number=? AND episode_number=? AND status=2", (series_id, s_idx, e_idx))
                if res:
                    query_db("DELETE FROM gap_records WHERE id=?", (res[0]['id'],))
                    bus.publish("notify.gap_cleared", {"s_idx": s_idx, "e_idx": e_idx})
        except Exception as e: pass

        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        admin_id = get_admin_id()
        series_info = {}
        try:
            url = f"{host}/emby/Users/{admin_id}/Items/{series_id}?api_key={key}"
            res = requests.get(url, timeout=10)
            if res.status_code == 200: series_info = res.json()
        except: pass
        if not series_info: series_info = episodes[0]

        st_tmdb = series_info.get("ProviderIds", {}).get("Tmdb")
        if st_tmdb: self._auto_finish_request(st_tmdb)
        bus.publish("notify.library.new_episode", { "series_id": series_id, "episodes": episodes, "series_info": series_info })

    def _push_single_item(self, item):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        try:
            url = f"{host}/emby/Items/{item['Id']}?api_key={key}"
            res = requests.get(url, timeout=10)
            if res.status_code == 200: item = res.json()
        except: pass
        tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
        if tmdb_id: self._auto_finish_request(tmdb_id)
        bus.publish("notify.library.new_item", item)

    def _scheduler_loop(self):
        while self.running:
            try:
                now = datetime.datetime.now()
                if now.minute != self.last_check_min:
                    self.last_check_min = now.minute
                    if now.hour == 9 and now.minute == 0:
                        self._check_user_expiration()
                        bus.publish("notify.daily_report")
                if now.minute % 10 == 0 and now.minute != self.last_sync_min:
                    self.last_sync_min = now.minute
                    self._sync_pending_requests()
                time.sleep(5)
            except: time.sleep(60)

    def _sync_pending_requests(self):
        try:
            rows = query_db("SELECT tmdb_id, media_type, season FROM media_requests WHERE status IN (1, 4)")
            if not rows: return
            host = cfg.get("emby_host"); key = cfg.get("emby_api_key")
            admin_id = get_admin_id()
            if not admin_id: return
            for r in rows:
                tid = r['tmdb_id']; mtype = r['media_type']; sn = r['season']
                type_filter = "Movie" if mtype == "movie" else "Series"
                url = f"{host}/emby/Users/{admin_id}/Items?AnyProviderIdEquals=tmdb.{tid}&IncludeItemTypes={type_filter}&Recursive=true&api_key={key}"
                res = requests.get(url, timeout=5).json()
                if res.get("Items"):
                    if mtype == "movie": query_db("UPDATE media_requests SET status = 2, updated_at = CURRENT_TIMESTAMP WHERE tmdb_id = ?", (tid,))
                    else:
                        sid = res["Items"][0]["Id"]
                        s_res = requests.get(f"{host}/emby/Shows/{sid}/Seasons?api_key={key}&UserId={admin_id}", timeout=5).json()
                        local_seasons = [s.get("IndexNumber") for s in s_res.get("Items", [])]
                        if sn in local_seasons: query_db("UPDATE media_requests SET status = 2, updated_at = CURRENT_TIMESTAMP WHERE tmdb_id = ? AND season = ?", (tid, sn))
                time.sleep(0.5) 
        except Exception as e: pass

    def _check_user_expiration(self):
        try:
            users = query_db("SELECT user_id, expire_date FROM users_meta WHERE expire_date IS NOT NULL AND expire_date != ''")
            if not users: return
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
            for u in users:
                if u['expire_date'] < today:
                    try: requests.post(f"{host}/emby/Users/{u['user_id']}/Policy?api_key={key}", json={"IsDisabled": True})
                    except: pass
        except: pass


class NotificationBot:
    def __init__(self):
        self.running = False
        self.poll_thread = None
        self.offset = 0
        self.user_cache = {}
        self.ip_cache = {} 
        self.wecom_token = None
        self.wecom_token_expires = 0
        self.delete_cache = {}
        
        bus.subscribe("notify.library.new_episode", self.on_library_new_episode)
        bus.subscribe("notify.library.new_item", self.on_library_new_item)
        bus.subscribe("notify.gap_cleared", self.on_gap_cleared)
        bus.subscribe("notify.playback.start", lambda data: self.on_playback_event(data, "start"))
        bus.subscribe("notify.playback.stop", lambda data: self.on_playback_event(data, "stop"))
        bus.subscribe("notify.user.login", self.on_user_login)
        bus.subscribe("notify.item.deleted", self.on_item_deleted)
        bus.subscribe("notify.daily_report", self.on_daily_report)
        bus.subscribe("notify.risk.alert", self.on_risk_alert)

    # 🔥 核心拦截器：判断是否在静音黑名单内
    def _is_muted(self, user_id, event_type):
        if not user_id: return False
        try:
            res = query_db("SELECT 1 FROM notify_mutes WHERE user_id = ? AND event_type = ?", (user_id, event_type))
            return bool(res)
        except:
            return False

    def start(self):
        if self.running: return
        if not cfg.get("tg_bot_token") and not cfg.get("wecom_corpid"): return
        self.running = True
        self._set_commands()
        self._set_wecom_menu() 
        if cfg.get("tg_bot_token"):
            self.poll_thread = threading.Thread(target=self._polling_loop, daemon=True)
            self.poll_thread.start()
        logger.info("🤖 Notification Bot Started")
    def stop(self): self.running = False

    def on_risk_alert(self, data):
        uid = data.get("user_id", "")
        username = data.get("username", "未知")
        current = data.get("current", 0)
        limit = data.get("limit", 0)
        devices_info = data.get("devices_info", "未知设备")
        
        msg = (f"🚨 <b>【风控预警】 账号并发越界</b>\n\n"
               f"👤 <b>涉事用户：</b>{username}\n"
               f"📈 <b>当前并发：</b>{current} / 额度 {limit}\n"
               f"📱 <b>违规设备：</b>\n{devices_info}\n\n"
               f"⚠️ <i>天眼系统已记录，请立即进行处置！</i>")
        
        keyboard = {"inline_keyboard": []}
        if uid:
            keyboard["inline_keyboard"].append([{"text": "🚫 立即封禁此违规账号", "callback_data": f"risk_ban_{uid}"}])
            
        admin_url = cfg.get("pulse_url") or cfg.get("emby_public_url")
        if admin_url:
            risk_url = f"{admin_url.rstrip('/')}/risk"
            keyboard["inline_keyboard"].append([{"text": "🛡️ 前往风控大盘拔网线", "url": risk_url}])
            
        self.send_message("sys_notify", msg, reply_markup=keyboard if keyboard["inline_keyboard"] else None, platform="all")

        try:
            add_sys_notification(
                notify_type="risk",
                title=f"🚨 并发越界: {username}",
                message=f"当前并发 {current} / 额度 {limit}，请立即处理！",
                action_url="/risk"
            )
        except Exception as e:
            logger.error(f"写入风控通知失败: {e}")

    def on_gap_cleared(self, data):
        if not cfg.get("enable_library_notify"): return
        s_idx = data["s_idx"]; e_idx = data["e_idx"]
        msg = (f"🎉 <b>残卷补全成功！</b>\n\n📺 剧集已刮削：<b>S{str(s_idx).zfill(2)}E{str(e_idx).zfill(2)}</b>\n"
               f"✅ 状态：缺集工单已自动核销闭环\n<i>拼图已圆满，强迫症得到治愈。</i>")
        self.send_message("sys_notify", msg, platform="all")

    def on_library_new_episode(self, data):
        if not cfg.get("enable_library_notify"): return
        series_id = data["series_id"]; episodes = data["episodes"]; series_info = data["series_info"]

        season_groups = defaultdict(list)
        for ep in episodes: season_groups[ep.get('ParentIndexNumber', 1)].append(ep)
            
        season_strs = []; total_eps = 0
        def zf(num): return str(num).zfill(2)

        for s_idx in sorted(season_groups.keys()):
            s_eps = season_groups[s_idx]
            ep_indices = sorted(list(set([e.get('IndexNumber', 0) for e in s_eps if e.get('IndexNumber') is not None])))
            total_eps += len(ep_indices)
            if len(ep_indices) > 1:
                ranges = []; start = ep_indices[0]; end = ep_indices[0]
                for idx in ep_indices[1:]:
                    if idx == end + 1: end = idx
                    else:
                        ranges.append(f"E{zf(start)}" if start == end else f"E{zf(start)}-E{zf(end)}")
                        start = idx; end = idx
                ranges.append(f"E{zf(start)}" if start == end else f"E{zf(start)}-E{zf(end)}")
                season_strs.append(f"S{zf(s_idx)}{', '.join(ranges)}")
            elif len(ep_indices) == 1:
                season_strs.append(f"S{zf(s_idx)}E{zf(ep_indices[0])}")

        final_ep_str = ", ".join(season_strs)
        title_suffix = f"{final_ep_str} (共{total_eps}集)" if total_eps > 1 else final_ep_str
        
        if total_eps == 1 and len(episodes) == 1:
            ep_name = episodes[0].get('Name', '')
            if ep_name and "Episode" not in ep_name and "第" not in ep_name: title_suffix += f" {ep_name}"

        series_name = series_info.get('Name', '未知剧集')
        year = series_info.get("ProductionYear", "")
        rating = series_info.get("CommunityRating", "N/A")
        overview = series_info.get("Overview", "暂无简介...") 
        if len(overview) > 150: overview = overview[:140] + "..."
        
        base_url = cfg.get("emby_public_url") or cfg.get("emby_host")
        if base_url.endswith('/'): base_url = base_url[:-1]
        play_url = f"{base_url}/web/index.html#!/item?id={series_id}&serverId={series_info.get('ServerId','')}"

        caption = (f"📺 <b>新入库 剧集 {series_name}</b> {title_suffix}\n\n📌 年份：{year}  |  ⭐ 评分：{rating}\n"
                   f"🕒 时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n📝 <b>剧情简介：</b>\n{overview}")

        keyboard = {"inline_keyboard": [[{"text": "▶️ 立即播放", "url": play_url}]]}
        primary_io = self._download_emby_image(series_id, 'Primary')
        backdrop_io = self._download_emby_image(series_id, 'Backdrop') 
        tg_img = primary_io or backdrop_io or REPORT_COVER_URL
        wecom_img = backdrop_io or primary_io or REPORT_COVER_URL
        self.send_photo("sys_notify", tg_img, caption, reply_markup=keyboard, platform="all", wecom_photo_io=wecom_img)

    def on_library_new_item(self, item):
        if not cfg.get("enable_library_notify"): return
        name = item.get("Name", "未知")
        year = item.get("ProductionYear", "")
        rating = item.get("CommunityRating", "N/A")
        overview = item.get("Overview", "暂无简介...")
        if len(overview) > 150: overview = overview[:140] + "..."
        
        type_raw = item.get("Type")
        type_cn = "电影"; type_icon = "🎬"
        if type_raw in ["Series", "Episode"]: type_cn = "剧集"; type_icon = "📺"
        
        base_url = cfg.get("emby_public_url") or cfg.get("emby_host")
        if base_url.endswith('/'): base_url = base_url[:-1]
        play_url = f"{base_url}/web/index.html#!/item?id={item['Id']}&serverId={item.get('ServerId','')}"

        caption = (f"{type_icon} <b>新入库 {type_cn} {name}</b> ({year})\n\n⭐ 评分：{rating} / 10\n"
                   f"🕒 时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n📝 <b>剧情简介：</b>\n{overview}")
        
        keyboard = {"inline_keyboard": [[{"text": "▶️ 立即播放", "url": play_url}]]}
        primary_io = self._download_emby_image(item['Id'], 'Primary')
        backdrop_io = self._download_emby_image(item['Id'], 'Backdrop')
        tg_img = primary_io or backdrop_io or REPORT_COVER_URL
        wecom_img = backdrop_io or primary_io or REPORT_COVER_URL
        self.send_photo("sys_notify", tg_img, caption, reply_markup=keyboard, platform="all", wecom_photo_io=wecom_img)

    def _format_ticks(self, ticks):
        if not ticks: return "00:00:00"
        try:
            total_seconds = int(int(ticks) / 10000000)
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            s = total_seconds % 60
            return f"{h:02}:{m:02}:{s:02}"
        except:
            return "00:00:00"

    def on_playback_event(self, data, action):
        if not cfg.get("enable_notify"): return
        try:
            session = data.get("Session") or data
            item = data.get("Item") or session.get("NowPlayingItem") or {}
            user = data.get("User") or session
            
            user_name = user.get("Name") or user.get("UserName") or "未知用户"
            user_id = user.get("Id") or session.get("UserId")
            
            # 🔥 拦截器：如果用户被静音了播放通知，直接抛弃不发
            if self._is_muted(user_id, "playback"):
                # 静音规则命中，不做日志输出以免刷屏
                return

            title = item.get("Name") or "未知内容"
            ep_info = ""; raw_type = item.get("Type", "")
            type_map = {"Episode": "剧集", "Movie": "电影", "Audio": "音乐", "MusicVideo": "MV", "LiveTvProgram": "直播", "TvChannel": "频道"}
            type_cn = type_map.get(raw_type, "媒体")
            
            if raw_type == "Episode" and item.get("SeriesName"): 
                idx = item.get("IndexNumber", 0); parent_idx = item.get("ParentIndexNumber", 1)
                ep_info = f" S{str(parent_idx).zfill(2)}E{str(idx).zfill(2)} 第 {idx} 集"
                title = f"{item.get('SeriesName')}"
            elif raw_type == "Audio" and item.get("Artists"):
                artist_str = ", ".join(item.get("Artists"))
                title = f"{title} - {artist_str}"
            
            emoji = "▶️" if action == "start" else "⏹️"; act = "开始播放" if action == "start" else "停止播放"
            ip = session.get("RemoteEndPoint") or data.get("RemoteEndPoint") or "127.0.0.1"
            loc = self._get_location(ip)
            
            play_state = session.get("PlayState", {})
            playback_info = data.get("PlaybackInfo", {})
            
            pos_ticks = data.get("PlaybackPositionTicks") or data.get("PositionTicks") or playback_info.get("PositionTicks") or play_state.get("PositionTicks") or 0
            run_ticks = item.get("RunTimeTicks") or session.get("NowPlayingItem", {}).get("RunTimeTicks") or data.get("RunTimeTicks") or 0
            
            try: pos_ticks = int(pos_ticks)
            except: pos_ticks = 0
            try: run_ticks = int(run_ticks)
            except: run_ticks = 0

            target_id = item.get("Id")
            
            if target_id and (run_ticks <= 0 or pos_ticks <= 0):
                try:
                    host = cfg.get("emby_host")
                    key = cfg.get("emby_api_key")
                    if run_ticks <= 0:
                        detail_res = requests.get(f"{host}/emby/Items/{target_id}?api_key={key}", timeout=2).json()
                        run_ticks = int(detail_res.get("RunTimeTicks") or 0)
                    if pos_ticks <= 0 and session.get("Id"):
                        sess_res = requests.get(f"{host}/emby/Sessions?api_key={key}", timeout=2).json()
                        for s in sess_res:
                            if s.get("Id") == session.get("Id"):
                                pos_ticks = int(s.get("PlayState", {}).get("PositionTicks") or 0)
                                break
                except: pass
            
            if run_ticks <= 1:
                progress_html = f"⏱️ <b>进度</b>：🟢 实时流/未知总时长"
            else:
                pct = int((pos_ticks / run_ticks) * 100)
                pct = min(max(pct, 0), 100)
                pos_str = self._format_ticks(pos_ticks)
                run_str = self._format_ticks(run_ticks)
                progress_html = f"⏱️ <b>进度</b>：<code>{pos_str} / {run_str} ({pct}%)</code>"

            client = session.get("Client") or data.get("Client") or "未知端"
            device = session.get("DeviceName") or data.get("DeviceName") or "未知设备"

            msg = (f"{emoji} <b>【{user_name}】{act} {type_cn} {title}</b>{ep_info}\n\n"
                   f"{progress_html}\n"
                   f"🌐 <b>地址</b>：{ip} ({loc})\n"
                   f"📱 <b>设备</b>：{client} on {device}\n"
                   f"🕒 <b>时间</b>：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            target_jump_id = target_id
            if raw_type == "Episode" and item.get("SeriesId"): target_jump_id = item.get("SeriesId")
            elif raw_type == "Audio" and item.get("AlbumId"): target_jump_id = item.get("AlbumId")
            
            base_url = cfg.get("emby_public_url") or cfg.get("emby_host")
            if base_url and base_url.endswith('/'): base_url = base_url[:-1]
            play_url = f"{base_url}/web/index.html#!/item?id={target_jump_id}&serverId={item.get('ServerId','')}" if base_url else "#"
            keyboard = {"inline_keyboard": [[{"text": "🔗 跳转详情", "url": play_url}]]}

            primary_io = self._download_emby_image(target_jump_id, 'Primary') 
            backdrop_io = self._download_emby_image(target_jump_id, 'Backdrop')
            if not primary_io and not backdrop_io:
                primary_io = self._download_emby_image(item.get("Id"), 'Primary')
                backdrop_io = self._download_emby_image(item.get("Id"), 'Backdrop')

            tg_img = primary_io or backdrop_io or REPORT_COVER_URL
            wecom_img = backdrop_io or primary_io or REPORT_COVER_URL
            self.send_photo("sys_notify", tg_img, msg, reply_markup=keyboard, platform="all", wecom_photo_io=wecom_img)
        except Exception as e: 
            logger.error(f"[Bot] Playback event error: {e}")

    def on_user_login(self, data):
        if not cfg.get("notify_user_login"): return
        try:
            user = data.get("User") or {}
            session = data.get("Session") or {}
            user_id = user.get("Id") or data.get("UserId")
            user_name = user.get("Name") or data.get("Title") or data.get("UserName") or "未知账号"
            
            # 🔥 拦截器：如果用户被静音了登录通知，直接抛弃不发
            if self._is_muted(user_id, "login"):
                # 静音规则命中，不做日志输出以免刷屏
                return

            ip = session.get("RemoteEndPoint") or data.get("RemoteEndPoint") or "127.0.0.1"
            loc = self._get_location(ip)
            client = session.get("Client") or data.get("Client") or data.get("AppName") or "未知设备"
            dev_name = session.get("DeviceName") or data.get("DeviceName") or "未知终端"
            
            msg = (f"🔐 <b>安全预警：账号登录</b>\n\n"
                   f"👤 <b>用户：</b>{user_name}\n"
                   f"🌐 <b>网络：</b>{ip} ({loc})\n"
                   f"📱 <b>设备：</b>{client} ({dev_name})\n"
                   f"🕒 <b>时间：</b>{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            avatar_io = self._download_user_image(user_id) if user_id else None
            fallback_img = "https://api.dicebear.com/9.x/notionists/png?seed=" + urllib.parse.quote(user_name)
            tg_img = avatar_io or fallback_img
            self.send_photo("sys_notify", tg_img, msg, platform="all", wecom_photo_io=tg_img)
        except Exception as e: logger.error(f"登录通知组装异常: {e}")

    def on_item_deleted(self, data):
        if not cfg.get("notify_item_deleted"): return
        try:
            item = data.get("Item") or data
            raw_type = item.get("Type", "")
            title = item.get("Name") or item.get("Title") or "未知资源"
            series_name = item.get("SeriesName")
            season_num = item.get("ParentIndexNumber")
            ep_num = item.get("IndexNumber")
            year = item.get("ProductionYear", "")
            item_id = str(item.get("Id", ""))
            unique_name = f"{series_name}_{season_num}_{ep_num}_{title}" if series_name else title
            
            now = time.time()
            if (item_id and item_id in self.delete_cache and (now - self.delete_cache[item_id] < 300)) or \
               (unique_name and unique_name in self.delete_cache and (now - self.delete_cache[unique_name] < 300)):
                return  
                
            if item_id: self.delete_cache[item_id] = now
            if unique_name: self.delete_cache[unique_name] = now
            self.delete_cache = {k: v for k, v in self.delete_cache.items() if now - v < 600}
            
            year_str = f" ({year})" if year else ""
            del_type = "媒体"
            
            if raw_type == "Movie": del_type = "电影"
            elif raw_type == "Series": del_type = "整剧"
            elif raw_type == "Season":
                del_type = "整季"
                s_num = ep_num if ep_num is not None else season_num
                title = f"{series_name or title} - 第 {s_num} 季" if s_num else f"{series_name or title}"
            elif raw_type == "Episode" or (series_name and ep_num is not None):
                del_type = "单集"
                s_str = str(season_num).zfill(2) if season_num is not None else "01"
                e_str = str(ep_num).zfill(2) if ep_num is not None else "XX"
                title = f"{series_name or '未知剧集'} S{s_str}E{e_str} {title}"
            
            msg = (f"🗑️ <b>系统告警：{del_type}被删除</b>\n\n"
                   f"🎬 <b>内容：</b>{title}{year_str}\n"
                   f"🕒 <b>时间：</b>{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                   f"<i>* 该项目已从媒体库物理存储中被永久移除。</i>")
            
            primary_io = self._download_emby_image(item.get("Id"), 'Primary') if item.get("Id") else None
            backdrop_io = self._download_emby_image(item.get("Id"), 'Backdrop') if item.get("Id") else None
            if not primary_io and not backdrop_io and item.get("SeriesId"): primary_io = self._download_emby_image(item.get("SeriesId"), 'Primary')
            
            tmdb_img_url = None
            if not primary_io and not backdrop_io:
                tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
                if not tmdb_id and item.get("SeriesProviderIds"): tmdb_id = item.get("SeriesProviderIds", {}).get("Tmdb")
                tmdb_key = cfg.get("tmdb_api_key")
                if tmdb_id and tmdb_key:
                    try:
                        m_type = "movie" if raw_type == "Movie" else "tv"
                        req_url = f"https://api.themoviedb.org/3/{m_type}/{tmdb_id}?api_key={tmdb_key}"
                        tmdb_res = requests.get(req_url, proxies=self._get_proxies(), timeout=5)
                        if tmdb_res.status_code == 200:
                            p_path = tmdb_res.json().get("poster_path")
                            if p_path: tmdb_img_url = f"https://image.tmdb.org/t/p/w500{p_path}"
                    except: pass
            
            tg_img = primary_io or backdrop_io or tmdb_img_url or REPORT_COVER_URL
            self.send_photo("sys_notify", tg_img, msg, platform="all", wecom_photo_io=tg_img)
        except Exception as e: logger.error(f"删除通知组装异常: {e}")

    def on_daily_report(self):
        chat_id = "sys_notify"
        where = "WHERE DateCreated >= date('now', '-1 day', 'start of day') AND DateCreated < date('now', 'start of day')"
        res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where}")
        count = res[0]['c'] if res else 0
        if count == 0:
            yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            msg = (f"📅 <b>昨日日报 ({yesterday_str})</b>\n\n😴 昨天服务器静悄悄，大家都去现充了吗？\n\n📊 活跃用户：0 人\n⏳ 播放时长：0 小时")
            self.send_message(chat_id, msg, platform="all")
        else: self._cmd_stats(chat_id, 'yesterday', platform="all")

    def _get_proxies(self):
        proxy = cfg.get("proxy_url")
        return {"http": proxy, "https": proxy} if proxy else None

    def _download_user_image(self, user_id):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        if not key or not host or not user_id: return None
        try:
            url = f"{host}/emby/Users/{user_id}/Images/Primary?maxHeight=400&maxWidth=400&quality=90&api_key={key}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200: return io.BytesIO(res.content)
        except: pass
        return None

    def _get_username(self, user_id):
        if user_id in self.user_cache: return self.user_cache[user_id]
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        if not key or not host: return user_id
        try:
            res = requests.get(f"{host}/emby/Users?api_key={key}", timeout=2)
            if res.status_code == 200:
                for u in res.json(): self.user_cache[u['Id']] = u['Name']
        except: pass
        return self.user_cache.get(user_id, "Unknown User")

    def _get_subnet_key(self, ip):
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.version == 6:
                parts = ip_obj.exploded.split(':')
                return ':'.join(parts[:4]) + '::/64' 
            return ip
        except: return ip

    def _clean_location(self, loc):
        if not loc: return ""
        loc = re.sub(r'(中国|省|市|自治区|自治州|特别行政区|移动|联通|电信|铁通|教育网|广电|通信|数据中心|IDC)', ' ', loc)
        loc = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s]', ' ', loc)
        loc = re.sub(r'\s+', ' ', loc).strip() 
        return loc

    def _get_location(self, ip):
        if not ip: return "未知"
        is_ipv6 = False
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local: return "局域网"
            is_ipv6 = (ip_obj.version == 6)
        except: pass
        
        cache_key = self._get_subnet_key(ip)
        if cache_key in self.ip_cache: return self.ip_cache[cache_key]

        loc = ""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        if not loc:
            try:
                res = requests.get(f"https://open.ipw.cn/api/ip/location?ip={ip}", headers=headers, timeout=3)
                if res.status_code == 200:
                    d = res.json().get('data', {})
                    if d.get('province') or d.get('city'):
                        loc = f"{d.get('province', '')} {d.get('city', '')}"
            except: pass

        if not loc:
            try:
                res = requests.get(f"https://ip.zxinc.org/api.php?type=json&ip={ip}", headers=headers, timeout=3)
                if res.status_code == 200:
                    d = res.json().get('data', {})
                    if d.get('location'):
                        loc = d.get('location') 
            except: pass

        if not loc:
            try:
                res = requests.get(f"https://whois.pconline.com.cn/ipJson.jsp?ip={ip}&json=true", headers=headers, timeout=3)
                if res.status_code == 200:
                    d = res.json()
                    if d.get('pro') or d.get('city'):
                        loc = f"{d.get('pro', '')} {d.get('city', '')}"
            except: pass

        loc = self._clean_location(loc)
        if not loc: loc = "IPv6 节点" if is_ipv6 else "未知地区"
            
        if loc != "未知地区":
            if len(self.ip_cache) > 1000: self.ip_cache.clear()
            self.ip_cache[cache_key] = loc 
            
        return loc

    def _download_emby_image(self, item_id, img_type='Primary', image_tag=None):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        if not key or not host: return None
        try:
            url = f"{host}/emby/Items/{item_id}/Images/{img_type}?maxHeight=800&maxWidth=600&quality=90"
            url += f"&tag={image_tag}" if image_tag else f"&api_key={key}"
            res = requests.get(url, timeout=15)
            if res.status_code == 200: return io.BytesIO(res.content)
        except: pass
        return None

    def _get_wecom_token(self):
        corpid = cfg.get("wecom_corpid"); corpsecret = cfg.get("wecom_corpsecret")
        proxy_url = cfg.get("wecom_proxy_url", "https://qyapi.weixin.qq.com").rstrip('/')
        if not corpid or not corpsecret: return None
        if self.wecom_token and time.time() < self.wecom_token_expires: return self.wecom_token
        try:
            res = requests.get(f"{proxy_url}/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}", timeout=5).json()
            if res.get("errcode") == 0:
                self.wecom_token = res["access_token"]
                self.wecom_token_expires = time.time() + res["expires_in"] - 60
                return self.wecom_token
        except: pass
        return None

    def _html_to_wecom_text(self, html_text, inline_keyboard=None):
        text = html_text.replace("<b>", "【").replace("</b>", "】").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
        text = re.sub(r"<a\s+href=['\"](.*?)['\"]>(.*?)</a>", r"\2: \1", text)
        if inline_keyboard and "inline_keyboard" in inline_keyboard:
            text += "\n\n"
            for row in inline_keyboard["inline_keyboard"]:
                for btn in row:
                    if "text" in btn and "url" in btn: text += f"🔗 {btn['text']}: {btn['url']}\n"
        return text.strip()

    def _set_wecom_menu(self):
        token = self._get_wecom_token(); agentid = cfg.get("wecom_agentid")
        proxy_url = cfg.get("wecom_proxy_url", "https://qyapi.weixin.qq.com").rstrip('/')
        if not token or not agentid: return
        
        menu_data = {
            "button": [
                {
                    "name": "数据大盘",
                    "sub_button": [
                        {"type": "click", "name": "📈 今日日报", "key": "/stats"},
                        {"type": "click", "name": "📅 本周周报", "key": "/weekly"},
                        {"type": "click", "name": "🗓️ 本月月报", "key": "/monthly"}
                    ]
                },
                {
                    "name": "媒体大厅",
                    "sub_button": [
                        {"type": "click", "name": "🟢 正在播放", "key": "/now"},
                        {"type": "click", "name": "🆕 最近入库", "key": "/latest"},
                        {"type": "click", "name": "📜 播放记录", "key": "/recent"}
                    ]
                },
                {
                    "name": "系统运维",
                    "sub_button": [
                        {"type": "click", "name": "🔍 资源搜索", "key": "/search"},
                        {"type": "click", "name": "📡 系统探针", "key": "/check"},
                        {"type": "click", "name": "🤖 帮助菜单", "key": "/help"}
                    ]
                }
            ]
        }
        
        try: 
            res = requests.post(f"{proxy_url}/cgi-bin/menu/create?access_token={token}&agentid={agentid}", json=menu_data, timeout=5)
            res_data = res.json()
            if res_data.get("errcode") == 0:
                pass
            else:
                logger.error(f"❌ [企微助手] 菜单推送失败！错误码: {res_data.get('errcode')}, 详情: {res_data.get('errmsg')}")
        except Exception as e: 
            logger.error(f"❌ [企微助手] 菜单请求发生网络异常: {e}")

    def _send_wecom_message(self, text, inline_keyboard=None, touser="@all"):
        token = self._get_wecom_token(); agentid = cfg.get("wecom_agentid")
        proxy_url = cfg.get("wecom_proxy_url", "https://qyapi.weixin.qq.com").rstrip('/')
        if not token or not agentid: return
        try:
            requests.post(f"{proxy_url}/cgi-bin/message/send?access_token={token}", json={"touser": touser, "msgtype": "text", "agentid": int(agentid), "text": {"content": self._html_to_wecom_text(text, inline_keyboard)}}, timeout=10)
        except: pass

    def _send_wecom_photo(self, photo_bytes, html_text, inline_keyboard=None, touser="@all"):
        token = self._get_wecom_token(); agentid = cfg.get("wecom_agentid")
        proxy_url = cfg.get("wecom_proxy_url", "https://qyapi.weixin.qq.com").rstrip('/')
        if not token or not agentid: return
        
        pic_url = REPORT_COVER_URL
        try:
            if photo_bytes:
                upload_res = requests.post(f"{proxy_url}/cgi-bin/media/uploadimg?access_token={token}", files={"media": ("image.jpg", photo_bytes, "image/jpeg")}, timeout=15)
                if upload_res.status_code == 200 and upload_res.text.strip(): pic_url = upload_res.json().get("url", REPORT_COVER_URL)
        except: pass

        try:
            plain_text = re.sub(r'<[^>]+>', '', html_text).strip()
            lines = [line.strip() for line in plain_text.split('\n')]
            title = lines[0][:35] + "..." if lines and len(lines[0].encode('utf-8')) > 120 else (lines[0] if lines else "EmbyPulse 通知")
            desc = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines[1:]).strip()) if len(lines) > 1 else ""
            if len(desc.encode('utf-8')) > 500: desc = desc[:150] + "..."

            jump_url = cfg.get("emby_public_url") or cfg.get("emby_host") or "https://emby.media"
            if inline_keyboard and "inline_keyboard" in inline_keyboard:
                try: jump_url = inline_keyboard["inline_keyboard"][0][0]["url"]
                except: pass
            else:
                links = re.findall(r"href=['\"](.*?)['\"]", html_text)
                if links: jump_url = links[0]

            item_id_match = re.search(r'id=([a-zA-Z0-9]+)', jump_url)
            if item_id_match and pic_url == REPORT_COVER_URL:
                base_emby = (cfg.get("emby_public_url") or cfg.get("emby_host")).rstrip('/')
                pic_url = f"{base_emby}/emby/Items/{item_id_match.group(1)}/Images/Primary?maxHeight=800&maxWidth=600&api_key={cfg.get('emby_api_key')}"

            res = requests.post(f"{proxy_url}/cgi-bin/message/send?access_token={token}", json={"touser": touser, "msgtype": "news", "agentid": int(agentid), "news": {"articles": [{"title": title, "description": desc, "url": jump_url, "picurl": pic_url}]}}, timeout=10)
            if res.status_code != 200 or res.json().get("errcode", 0) != 0: self._send_wecom_message(html_text, inline_keyboard, touser)
        except: self._send_wecom_message(html_text, inline_keyboard, touser)

    def send_photo(self, chat_id, photo_io, caption, parse_mode="HTML", reply_markup=None, platform="all", wecom_photo_io=None):
        photo_bytes = None
        if isinstance(photo_io, str):
            try: 
                res = requests.get(photo_io, proxies=self._get_proxies() if "tmdb" in photo_io.lower() else None, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if res.status_code == 200: photo_bytes = res.content
            except: pass
        else: photo_bytes = photo_io.read()

        wecom_photo_bytes = photo_bytes
        if wecom_photo_io is not None and wecom_photo_io != photo_io:
            if isinstance(wecom_photo_io, str):
                try: 
                    res = requests.get(wecom_photo_io, proxies=self._get_proxies() if "tmdb" in wecom_photo_io.lower() else None, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                    if res.status_code == 200: wecom_photo_bytes = res.content
                except: pass
            else: wecom_photo_bytes = wecom_photo_io.read()

        if platform in ["all", "wecom"] and cfg.get("wecom_corpid"):
            threading.Thread(target=self._send_wecom_photo, args=(wecom_photo_bytes, caption, reply_markup, chat_id if platform == "wecom" else cfg.get("wecom_touser", "@all"))).start()

        if platform in ["all", "tg"] and cfg.get("tg_bot_token"):
            tg_cid = chat_id if platform == "tg" else cfg.get("tg_chat_id")
            if tg_cid:
                try:
                    data = {"chat_id": tg_cid, "caption": caption, "parse_mode": parse_mode}
                    if reply_markup: data["reply_markup"] = json.dumps(reply_markup)
                    if photo_bytes: 
                        r = requests.post(f"https://api.telegram.org/bot{cfg.get('tg_bot_token')}/sendPhoto", data=data, files={"photo": ("image.jpg", io.BytesIO(photo_bytes), "image/jpeg")}, proxies=self._get_proxies(), timeout=20)
                        if r.status_code != 200: self.send_message(tg_cid, caption, parse_mode, reply_markup, platform="tg")
                    else: 
                        self.send_message(tg_cid, caption, parse_mode, reply_markup, platform="tg")
                except: 
                    self.send_message(tg_cid, caption, parse_mode, reply_markup, platform="tg")

    def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None, platform="all"):
        if platform in ["all", "wecom"] and cfg.get("wecom_corpid"):
            threading.Thread(target=self._send_wecom_message, args=(text, reply_markup, chat_id if platform == "wecom" else cfg.get("wecom_touser", "@all"))).start()

        if platform in ["all", "tg"] and cfg.get("tg_bot_token"):
            tg_cid = chat_id if platform == "tg" else cfg.get("tg_chat_id")
            if tg_cid:
                try:
                    data = {"chat_id": tg_cid, "text": text, "parse_mode": parse_mode}
                    if reply_markup: data["reply_markup"] = json.dumps(reply_markup)
                    requests.post(f"https://api.telegram.org/bot{cfg.get('tg_bot_token')}/sendMessage", json=data, proxies=self._get_proxies(), timeout=10)
                except: pass

    def _polling_loop(self):
        token = cfg.get("tg_bot_token"); admin_id = str(cfg.get("tg_chat_id"))
        while self.running:
            try:
                res = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"offset": self.offset, "timeout": 30}, proxies=self._get_proxies(), timeout=35)
                if res.status_code == 200:
                    for u in res.json().get("result", []):
                        self.offset = u["update_id"] + 1
                        if "message" in u:
                            cid = str(u["message"]["chat"]["id"]); 
                            if admin_id and cid != admin_id: continue
                            self._handle_message(u["message"].get("text", ""), cid, platform="tg")
                        elif "callback_query" in u:
                            cq = u["callback_query"]
                            cid = str(cq["message"]["chat"]["id"])
                            if admin_id and cid != admin_id: continue
                            threading.Thread(target=self._handle_callback, args=(cq,)).start()
                else: time.sleep(5)
            except: time.sleep(5)

    def _handle_callback(self, cq):
        data = cq.get("data", ""); cid = str(cq["message"]["chat"]["id"])
        mid = cq["message"]["message_id"]; cq_id = cq["id"]; token = cfg.get("tg_bot_token")
        proxies = self._get_proxies() 
        
        try: requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={"callback_query_id": cq_id}, proxies=proxies, timeout=5)
        except: pass

        if data.startswith("risk_ban_"):
            uid = data.replace("risk_ban_", "")
            from app.services.risk_service import ban_user, log_risk_action
            
            operator = cq.get('from', {}).get('first_name', 'Admin')
            target_username = self._get_username(uid) 
            
            if ban_user(uid):
                log_risk_action(uid, target_username, "ban", f"机器快捷执法 (操作人: {operator})")
                action_text = f"✅ 已成功封禁该违规账号！\n(执行人: {operator})"
            else:
                action_text = "❌ 封禁失败，可能 API 权限不足。"
                
            msg_obj = cq["message"]
            orig_text = msg_obj.get("text", "风控警报")
            new_text = f"{orig_text}\n\n━━━━━━━━━━━━━━\n{action_text}"
            try: requests.post(f"https://api.telegram.org/bot{token}/editMessageText", json={"chat_id": cid, "message_id": mid, "text": new_text, "reply_markup": {"inline_keyboard": []}}, proxies=proxies, timeout=5)
            except: pass
            return

        if data.startswith("feed_"):
            parts = data.split("_")
            action = parts[1]; feed_id = int(parts[2])
            status_map = {"fix": 1, "done": 2, "reject": 3}
            status_text = {"fix": "🛠️ 已标记：修复中", "done": "✅ 已标记：修复完成", "reject": "❌ 已标记：暂不处理(忽略)"}
            
            if action in status_map:
                query_db("UPDATE media_feedback SET status = ? WHERE id = ?", (status_map[action], feed_id))
                msg_obj = cq["message"]
                operator = cq.get('from', {}).get('first_name', 'Admin')
                if "caption" in msg_obj:
                    orig_text = msg_obj.get("caption", "资源报错工单")
                    new_text = f"{orig_text}\n\n━━━━━━━━━━━━━━\n{status_text[action]}\n(操作人: {operator})"
                    try: requests.post(f"https://api.telegram.org/bot{token}/editMessageCaption", json={"chat_id": cid, "message_id": mid, "caption": new_text, "reply_markup": {"inline_keyboard": []}}, proxies=proxies, timeout=5)
                    except: pass
                else:
                    orig_text = msg_obj.get("text", "资源报错工单")
                    new_text = f"{orig_text}\n\n━━━━━━━━━━━━━━\n{status_text[action]}\n(操作人: {operator})"
                    try: requests.post(f"https://api.telegram.org/bot{token}/editMessageText", json={"chat_id": cid, "message_id": mid, "text": new_text, "reply_markup": {"inline_keyboard": []}}, proxies=proxies, timeout=5)
                    except: pass
            return

        if data.startswith("req_"):
            parts = data.split("_")
            action = parts[1] 
            
            if action == "reject" and len(parts) > 2 and parts[2] == "menu":
                tid = parts[3]
                reasons = ["影片未上映", "剧集未开播", "未找到可用资源", "质量太差等待洗版"]
                keyboard = {"inline_keyboard": [
                    [{"text": reasons[0], "callback_data": f"req_reject_do_{tid}_0"}, {"text": reasons[1], "callback_data": f"req_reject_do_{tid}_1"}],
                    [{"text": reasons[2], "callback_data": f"req_reject_do_{tid}_2"}, {"text": reasons[3], "callback_data": f"req_reject_do_{tid}_3"}],
                    [{"text": "🔙 取消返回", "callback_data": f"req_back_{tid}"}]
                ]}
                try: requests.post(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", json={"chat_id": cid, "message_id": mid, "reply_markup": keyboard}, proxies=proxies, timeout=5)
                except: pass
                return
            
            elif action == "back":
                tid = parts[2]; admin_url = cfg.get("pulse_url") or "http://127.0.0.1:10307"
                keyboard = {"inline_keyboard": [
                    [{"text": "🚀 推送 MP", "callback_data": f"req_approve_{tid}"}, {"text": "✋ 手动接单", "callback_data": f"req_manual_{tid}"}],
                    [{"text": "❌ 拒绝求片", "callback_data": f"req_reject_menu_{tid}"}, {"text": "💻 网页审批", "url": f"{admin_url}/requests_admin"}]
                ]}
                try: requests.post(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", json={"chat_id": cid, "message_id": mid, "reply_markup": keyboard}, proxies=proxies, timeout=5)
                except: pass
                return

            tid = parts[2]; reject_reason = None
            if action == "reject" and len(parts) > 2 and parts[2] == "do":
                tid = parts[3]; r_idx = int(parts[4])
                reasons = ["影片未上映", "剧集未开播", "未找到可用资源", "质量太差等待洗版"]
                reject_reason = reasons[r_idx]; action_db = "reject"
            else:
                action_db = action

            rows = query_db("SELECT season, title, media_type, year FROM media_requests WHERE tmdb_id = ? AND status = 0", (tid,))
            if not rows:
                try: requests.post(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", json={"chat_id": cid, "message_id": mid, "reply_markup": {"inline_keyboard": []}}, proxies=proxies, timeout=5)
                except: pass
                return
                
            if action_db == "approve":
                mp_url = cfg.get("moviepilot_url"); mp_token = cfg.get("moviepilot_token")
                for r in rows:
                    if mp_url and mp_token:
                        payload = { "name": r["title"], "tmdbid": int(tid), "year": str(r["year"]), "type": "电影" if r["media_type"]=="movie" else "电视剧" }
                        if r["media_type"] == "tv": payload["season"] = r['season']
                        try: requests.post(f"{mp_url.rstrip('/')}/api/v1/subscribe/", json=payload, headers={"X-API-KEY": mp_token.strip().strip("'\"")}, timeout=10)
                        except: pass
                    query_db("UPDATE media_requests SET status = 1 WHERE tmdb_id = ? AND season = ?", (tid, r['season']))
                action_text = "✅ 已审批：推送 MP 自动下载"
            elif action_db == "manual":
                for r in rows: query_db("UPDATE media_requests SET status = 4 WHERE tmdb_id = ? AND season = ?", (tid, r['season']))
                action_text = "✅ 已审批：管理员手动接单"
            elif action_db == "reject":
                for r in rows: query_db("UPDATE media_requests SET status = 3, reject_reason = ? WHERE tmdb_id = ? AND season = ?", (reject_reason, tid, r['season']))
                action_text = f"❌ 已拒绝 ({reject_reason})"
                
            msg_obj = cq["message"]
            operator = cq.get('from', {}).get('first_name', 'Admin')
            if "caption" in msg_obj:
                orig_caption = msg_obj.get("caption", "求片请求")
                new_caption = f"{orig_caption}\n\n━━━━━━━━━━━━━━\n{action_text}\n(操作人: {operator})"
                try: requests.post(f"https://api.telegram.org/bot{token}/editMessageCaption", json={"chat_id": cid, "message_id": mid, "caption": new_caption, "reply_markup": {"inline_keyboard": []}}, proxies=proxies, timeout=5)
                except: pass
            else:
                orig_text = msg_obj.get("text", "求片请求")
                new_text = f"{orig_text}\n\n━━━━━━━━━━━━━━\n{action_text}\n(操作人: {operator})"
                try: requests.post(f"https://api.telegram.org/bot{token}/editMessageText", json={"chat_id": cid, "message_id": mid, "text": new_text, "reply_markup": {"inline_keyboard": []}}, proxies=proxies, timeout=5)
                except: pass

    def _set_commands(self):
        token = cfg.get("tg_bot_token")
        if not token: return
        cmds = [{"command": "search", "description": "🔍 搜索资源"}, {"command": "stats", "description": "📊 今日日报"}, {"command": "weekly", "description": "📅 本周周报"}, {"command": "monthly", "description": "🗓️ 本月月报"}, {"command": "yearly", "description": "📜 年度总结"}, {"command": "now", "description": "🟢 正在播放"}, {"command": "latest", "description": "🆕 最近入库"}, {"command": "recent", "description": "📜 最近播放记录"}, {"command": "check", "description": "📡 系统探针"}, {"command": "help", "description": "🤖 帮助菜单"}]
        try: requests.post(f"https://api.telegram.org/bot{token}/setMyCommands", json={"commands": cmds}, proxies=self._get_proxies(), timeout=10)
        except: pass

    def _handle_message(self, text, cid, platform="tg"):
        text = text.strip()
        if text.startswith("/search"): self._cmd_search(cid, text, platform)
        elif text.startswith("/stats"): self._cmd_stats(cid, 'day', platform)
        elif text.startswith("/weekly"): self._cmd_stats(cid, 'week', platform)
        elif text.startswith("/monthly"): self._cmd_stats(cid, 'month', platform)
        elif text.startswith("/yearly"): self._cmd_stats(cid, 'year', platform)
        elif text.startswith("/now"): self._cmd_now(cid, platform)
        elif text.startswith("/latest"): self._cmd_latest(cid, platform)
        elif text.startswith("/recent"): self._cmd_recent(cid, platform)
        elif text.startswith("/check"): self._cmd_check(cid, platform)
        elif text.startswith("/help"): self._cmd_help(cid, platform)

    def _cmd_latest(self, cid, platform):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        try:
            user_id = get_admin_id()
            if not user_id: return self.send_message(cid, "❌ 错误: 无法获取 Emby 用户身份", platform=platform)
            fields = "DateCreated,Name,SeriesName,Type,ParentIndexNumber,IndexNumber"
            url = f"{host}/emby/Users/{user_id}/Items/Latest"
            params = {"IncludeItemTypes": "Movie,Episode", "Limit": 8, "Fields": fields, "api_key": key}
            
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200: return self.send_message(cid, f"❌ 查询失败", platform=platform)
            
            items = res.json()
            if not items: return self.send_message(cid, "📭 最近没有新入库的资源", platform=platform)

            msg = "🆕 <b>最近入库 (Top 8)</b>\n\n"
            for i in items:
                name = i.get("Name", "未知")
                item_type = i.get("Type")
                
                if item_type == "Episode" and i.get("SeriesName"):
                    s_idx = str(i.get("ParentIndexNumber", 0)).zfill(2) if i.get("ParentIndexNumber") is not None else "01"
                    e_idx = str(i.get("IndexNumber", 0)).zfill(2) if i.get("IndexNumber") is not None else "XX"
                    name = f"《{i.get('SeriesName')}》 S{s_idx}E{e_idx} {name}"
                elif item_type == "Movie":
                    name = f"《{name}》"
                
                date_raw = i.get("DateCreated")
                date_str = date_raw[:10] if date_raw else "未知时间"
                type_icon = "🎬" if item_type == "Movie" else "📺"
                
                msg += f"{type_icon} <code>{date_str}</code> | <b>{name}</b>\n"
                
            self.send_message(cid, msg.strip(), platform=platform)
        except Exception as e:
            logger.error(f"[Bot] latest query error: {e}")
            self.send_message(cid, f"❌ 查询异常", platform=platform)

    def _extract_tech_info(self, item):
        sources = item.get("MediaSources", [])
        if not sources: return "📼 未知"
        info_parts = []
        video = next((s for s in sources[0].get("MediaStreams", []) if s.get("Type") == "Video"), None)
        if video:
            w = video.get("Width", 0)
            if w >= 3800: res = "4K"
            elif w >= 1900: res = "1080P"
            elif w >= 1200: res = "720P"
            else: res = "SD"
            extra = []
            v_range = video.get("VideoRange", "")
            title = video.get("DisplayTitle", "").upper()
            if "HDR" in v_range or "HDR" in title: extra.append("HDR")
            if "DOVI" in title or "DOLBY VISION" in title: extra.append("DoVi")
            res_str = f"{res} {' '.join(extra)}"
            info_parts.append(res_str.strip())
            bitrate = sources[0].get("Bitrate", 0)
            if bitrate > 0: info_parts.append(f"{round(bitrate / 1000000, 1)}Mbps")
        return " | ".join(info_parts) if info_parts else "📼 未知"

    def _cmd_search(self, chat_id, text, platform):
        parts = text.split(' ', 1)
        if len(parts) < 2: return self.send_message(chat_id, "🔍 请使用: /search 关键词", platform=platform)
        keyword = parts[1].strip()
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        try:
            user_id = get_admin_id()
            if not user_id: return self.send_message(chat_id, "❌ 错误: 无法获取 Emby 用户身份", platform=platform)

            fields = "ProductionYear,Type,Id" 
            url = f"{host}/emby/Users/{user_id}/Items"
            params = {"SearchTerm": keyword, "IncludeItemTypes": "Movie,Series", "Recursive": "true", "Fields": fields, "Limit": 5, "api_key": key}
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200: return self.send_message(chat_id, f"❌ 搜索失败", platform=platform)
            items = res.json().get("Items", [])
            if not items: return self.send_message(chat_id, f"📭 未找到与 <b>{keyword}</b> 相关的资源", platform=platform)
            
            top = items[0]
            type_raw = top.get("Type")
            tech_info_str = "查询中..."; ep_count_str = ""; details = {}

            try:
                if type_raw == "Series":
                    meta_url = f"{host}/emby/Users/{user_id}/Items/{top['Id']}?Fields=Overview,CommunityRating,Genres,RecursiveItemCount&api_key={key}"
                    details = requests.get(meta_url, timeout=5).json()
                    ep_count = details.get("RecursiveItemCount", 0)
                    ep_count_str = f"📊 共 {ep_count} 集"
                    sample_url = f"{host}/emby/Users/{user_id}/Items?ParentId={top['Id']}&Recursive=true&IncludeItemTypes=Episode&Limit=1&Fields=MediaSources&api_key={key}"
                    sample_res = requests.get(sample_url, timeout=5)
                    if sample_res.status_code == 200 and sample_res.json().get("Items"):
                        tech_info_str = self._extract_tech_info(sample_res.json().get("Items")[0])
                else:
                    detail_url = f"{host}/emby/Users/{user_id}/Items/{top['Id']}?Fields=Overview,CommunityRating,Genres,MediaSources&api_key={key}"
                    details = requests.get(detail_url, timeout=8).json()
                    tech_info_str = self._extract_tech_info(details)
            except Exception: tech_info_str = "暂无技术信息"

            name = details.get("Name", top.get("Name"))
            year = details.get("ProductionYear", top.get("ProductionYear"))
            year_str = f"({year})" if year else ""
            rating = details.get("CommunityRating", "N/A")
            genres = " / ".join(details.get("Genres", [])[:3]) or "未分类"
            overview = details.get("Overview", "暂无简介")
            if len(overview) > 120: overview = overview[:120] + "..."
            
            type_icon = "🎬" if type_raw == "Movie" else "📺"
            info_line = f"{ep_count_str} | {tech_info_str}" if type_raw == "Series" else tech_info_str
            
            base_url = cfg.get("emby_public_url") or cfg.get("emby_host")
            if base_url.endswith('/'): base_url = base_url[:-1]
            play_url = f"{base_url}/web/index.html#!/item?id={top.get('Id')}&serverId={top.get('ServerId')}"

            caption = (f"{type_icon} <b>{name}</b> {year_str}\n"
                       f"⭐️ {rating}  |  🎭 {genres}\n"
                       f"💿 {info_line}\n\n"
                       f"📝 <b>剧情简介：</b>\n{overview}\n")
            
            if len(items) > 1:
                caption += "\n🔎 <b>其他结果：</b>\n"
                for i, sub in enumerate(items[1:]):
                    sub_year = f"({sub.get('ProductionYear')})" if sub.get('ProductionYear') else ""
                    sub_type = "📺" if sub.get("Type") == "Series" else "🎬"
                    caption += f"{sub_type} {sub.get('Name')} {sub_year}\n"
            
            keyboard = {"inline_keyboard": [[{"text": "▶️ 立即播放", "url": play_url}]]}
            primary_io = self._download_emby_image(top.get("Id"), 'Primary')
            backdrop_io = self._download_emby_image(top.get("Id"), 'Backdrop')

            tg_img = primary_io or backdrop_io or REPORT_COVER_URL
            wecom_img = backdrop_io or primary_io or REPORT_COVER_URL
            self.send_photo(chat_id, tg_img, caption.strip(), reply_markup=keyboard, platform=platform, wecom_photo_io=wecom_img)
        except Exception as e:
            self.send_message(chat_id, "❌ 搜索时发生错误", platform=platform)

    def _cmd_stats(self, chat_id, period='day', platform="tg"):
        where, params = get_base_filter('all') 
        titles = {'day': '今日日报', 'yesterday': '昨日日报', 'week': '本周周报', 'month': '本月月报', 'year': '年度报告'}
        title_cn = titles.get(period, '数据报表')
        if period == 'week': where += " AND DateCreated > date('now', '-7 days')"
        elif period == 'month': where += " AND DateCreated > date('now', 'start of month')"
        elif period == 'year': where += " AND DateCreated > date('now', 'start of year')"
        elif period == 'yesterday': where += " AND DateCreated >= date('now', '-1 day', 'start of day') AND DateCreated < date('now', 'start of day')"
        else: where += " AND DateCreated > date('now', 'start of day')"
        try:
            plays_res = query_db(f"SELECT COUNT(*) as c FROM PlaybackActivity {where}", params)
            if not plays_res: raise Exception("DB Error")
            plays = plays_res[0]['c']
            dur_res = query_db(f"SELECT SUM(PlayDuration) as c FROM PlaybackActivity {where}", params)
            dur = dur_res[0]['c'] if dur_res and dur_res[0]['c'] else 0
            hours = round(dur / 3600, 1)
            users_res = query_db(f"SELECT COUNT(DISTINCT UserId) as c FROM PlaybackActivity {where}", params)
            users = users_res[0]['c'] if users_res else 0
            
            top_users = query_db(f"SELECT UserId, SUM(PlayDuration) as t FROM PlaybackActivity {where} GROUP BY UserId ORDER BY t DESC LIMIT 5", params)
            user_str = ""
            if top_users:
                for i, u in enumerate(top_users):
                    name = self._get_username(u['UserId'])
                    h = round(u['t'] / 3600, 1)
                    prefix = ['🥇','🥈','🥉'][i] if i < 3 else f"{i+1}."
                    user_str += f"{prefix} {name} ({h}h)\n"
            else: user_str = "暂无数据\n"
            
            tops = query_db(f"SELECT ItemName, COUNT(*) as c FROM PlaybackActivity {where} GROUP BY ItemName ORDER BY c DESC LIMIT 10", params)
            top_content = ""
            if tops:
                for i, item in enumerate(tops):
                    prefix = ['🥇','🥈','🥉'][i] if i < 3 else f"{i+1}."
                    top_content += f"{prefix} {item['ItemName']} ({item['c']}次)\n"
            else: top_content = "暂无数据\n"
            
            yesterday_date = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%m-%d")
            title_display = f"{title_cn} ({yesterday_date})" if period == 'yesterday' else title_cn
            
            caption = (f"📊 <b>EmbyPulse {title_display}</b>\n\n"
                       f"📈 <b>数据大盘</b>\n"
                       f"▶️ 总播放量：{plays} 次\n"
                       f"⏱️ 活跃时长：{hours} 小时\n"
                       f"👥 活跃人数：{users} 人\n\n"
                       f"🏆 <b>活跃用户 Top 5</b>\n"
                       f"{user_str}\n"
                       f"🔥 <b>热门内容 Top 10</b>\n"
                       f"{top_content}")
            
            if HAS_PIL:
                img = report_gen.generate_report('all', period)
                if img: self.send_photo(chat_id, img, caption.strip(), platform=platform)
                else: self.send_message(chat_id, caption.strip(), platform=platform)
            else: self.send_photo(chat_id, REPORT_COVER_URL, caption.strip(), platform=platform)
        except Exception as e:
            self.send_message(chat_id, f"❌ 统计失败", platform=platform)

    def _cmd_now(self, cid, platform):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        try:
            res = requests.get(f"{host}/emby/Sessions?api_key={key}", timeout=5)
            sessions = [s for s in res.json() if s.get("NowPlayingItem")]
            if not sessions: return self.send_message(cid, "🟢 当前无人在看", platform=platform)
            
            msg = f"🟢 <b>当前正在播放 ({len(sessions)} 人)</b>\n\n"
            for s in sessions:
                item = s.get('NowPlayingItem', {})
                title = item.get('Name', '未知')
                if item.get("Type") == "Episode" and item.get("SeriesName"):
                    title = f"《{item.get('SeriesName')}》 {title}"
                elif item.get("Type") == "Movie":
                    title = f"《{title}》"
                
                client = s.get("Client", "未知端")
                username = s.get('UserName', '未知用户')
                
                play_state = s.get('PlayState', {})
                pos_ticks = play_state.get('PositionTicks', 0)
                run_ticks = item.get('RunTimeTicks', 1) or 1
                pct = int((pos_ticks / run_ticks) * 100)
                pct = min(max(pct, 0), 100)
                
                filled = int(pct / 10)
                bar = "█" * filled + "⚪️" * (10 - filled)
                
                msg += f"👤 <b>{username}</b> ({client})\n📺 {title}\n⏳ <code>[{bar}] {pct}%</code>\n\n"
            self.send_message(cid, msg.strip(), platform=platform)
        except: self.send_message(cid, "❌ 连接失败", platform=platform)

    def _cmd_recent(self, cid, platform):
        try:
            rows = query_db("SELECT UserId, ItemName, DateCreated FROM PlaybackActivity ORDER BY DateCreated DESC LIMIT 10")
            if not rows: return self.send_message(cid, "📭 无记录", platform=platform)
            
            msg = "📜 <b>最近播放记录 (Top 10)</b>\n\n"
            for r in rows:
                date = r['DateCreated'][5:16].replace('T', ' ')
                name = self._get_username(r['UserId'])
                item_name = r['ItemName'].replace(' - ', ' ')
                msg += f"▫️ <code>{date}</code> | 👤 <b>{name}</b> > {item_name}\n"
            self.send_message(cid, msg.strip(), platform=platform)
        except Exception as e: 
            self.send_message(cid, f"❌ 查询失败", platform=platform)

    def _cmd_check(self, cid, platform):
        key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
        start = time.time()
        try:
            res = requests.get(f"{host}/emby/System/Info?api_key={key}", timeout=5)
            if res.status_code == 200:
                info = res.json()
                delay = int((time.time()-start)*1000)
                version = info.get('Version', '未知')
                os_name = info.get('OperatingSystem', '未知')
                
                movie_count = series_count = ep_count = 0
                try:
                    c_res = requests.get(f"{host}/emby/Items/Counts?api_key={key}", timeout=3).json()
                    movie_count = c_res.get('MovieCount', 0)
                    series_count = c_res.get('SeriesCount', 0)
                    ep_count = c_res.get('EpisodeCount', 0)
                except: pass
                
                active_users = 0
                try:
                    s_res = requests.get(f"{host}/emby/Sessions?api_key={key}", timeout=3).json()
                    active_users = len([s for s in s_res if s.get("NowPlayingItem")])
                except: pass

                msg = (f"📡 <b>Emby 服务器状态探针</b>\n\n"
                       f"🟢 <b>运行状态</b>：在线 (响应延迟: {delay}ms)\n"
                       f"🏷️ <b>系统版本</b>：Emby Server {version}\n"
                       f"💻 <b>宿主环境</b>：{os_name}\n\n"
                       f"📊 <b>媒体库容量</b>\n"
                       f"🎬 电影：{movie_count} 部\n"
                       f"📺 剧集：{series_count} 部 (共 {ep_count} 集)\n\n"
                       f"👥 <b>当前活跃</b>：{active_users} 人正在观看")
                self.send_message(cid, msg, platform=platform)
        except: self.send_message(cid, "❌ 离线或无法连接到服务器", platform=platform)

    def _cmd_help(self, cid, platform):
        msg = ("🤖 <b>EmbyPulse 智能助理指南</b>\n\n"
               "📊 <b>数据报表指令</b>\n"
               "/stats - 获取今日播放大盘与用户排行\n"
               "/weekly - 获取本周全站数据周报\n"
               "/monthly - 获取本月活跃度月报\n"
               "/yearly - 获取年度全景总结数据\n\n"
               "🎬 <b>媒体库与状态指令</b>\n"
               "/now - 查看当前服务器有谁正在播放\n"
               "/latest - 获取最近新入库的 8 部影视剧\n"
               "/recent - 查看本站最近的 10 条播放历史\n"
               "/search [关键词] - 搜索影视资源并获取直达链接\n\n"
               "🛠 <b>系统管理指令</b>\n"
               "/check - 测试 Emby 服务器连通性与容量大盘\n"
               "/help - 获取本帮助菜单")
        self.send_message(cid, msg.strip(), platform=platform)

class EmbyPulseOrchestrator:
    def __init__(self):
        self.daemon = SystemDaemon()
        self.notifier = NotificationBot()
        
    def start(self):
        self.daemon.start()
        self.notifier.start()
        
    def stop(self):
        self.daemon.stop()
        self.notifier.stop()
        
    def push_now(self, user_id, period, theme):
        return self.notifier._cmd_stats("sys_notify", period, platform="all")
        
    def add_library_task(self, item):
        self.daemon.add_library_task(item)
        
    def push_playback_event(self, data, action="start"):
        bus.publish("webhook.received", f"playback.{action}", data)

    def _handle_message(self, text, cid, platform="tg"):
        self.notifier._handle_message(text, cid, platform)

    def _handle_callback(self, cq):
        self.notifier._handle_callback(cq)

    def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None, platform="all"):
        self.notifier.send_message(chat_id, text, parse_mode, reply_markup, platform)

    def send_photo(self, chat_id, photo_io, caption, parse_mode="HTML", reply_markup=None, platform="all", wecom_photo_io=None):
        self.notifier.send_photo(chat_id, photo_io, caption, parse_mode, reply_markup, platform, wecom_photo_io)

bot = EmbyPulseOrchestrator()
