from fastapi import APIRouter, Request, Response
from app.schemas.models import BotSettingsModel
from app.core.config import cfg
from app.services.bot_service import bot
import requests
import threading
import base64
import struct
import hashlib
import xml.etree.ElementTree as ET
import logging

logger = logging.getLogger("uvicorn")

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None

router = APIRouter()

@router.get("/api/bot/settings")
def api_get_bot_settings(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    return {"status": "success", "data": cfg.get_all()}

@router.post("/api/bot/settings")
def api_save_bot_settings(data: BotSettingsModel, request: Request):
    if not request.session.get("user"): return {"status": "error"}
    cfg.set("tg_bot_token", data.tg_bot_token); cfg.set("tg_chat_id", data.tg_chat_id)
    cfg.set("enable_bot", data.enable_bot)
    cfg.set("enable_notify", data.enable_notify)
    cfg.set("enable_library_notify", data.enable_library_notify) 
    
    cfg.set("wecom_corpid", data.wecom_corpid)
    cfg.set("wecom_corpsecret", data.wecom_corpsecret)
    cfg.set("wecom_agentid", data.wecom_agentid)
    cfg.set("wecom_touser", data.wecom_touser or "@all")
    cfg.set("wecom_proxy_url", data.wecom_proxy_url or "https://qyapi.weixin.qq.com")
    cfg.set("wecom_token", data.wecom_token)
    cfg.set("wecom_aeskey", data.wecom_aeskey)
    
    bot.stop()
    if data.enable_bot: threading.Timer(1.0, bot.start).start()
    return {"status": "success", "message": "配置已保存"}

@router.post("/api/bot/test")
def api_test_bot(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    token = cfg.get("tg_bot_token"); chat_id = cfg.get("tg_chat_id"); proxy = cfg.get("proxy_url")
    if not token: return {"status": "error", "message": "请先保存配置"}
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        res = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": "🎉 测试消息"}, proxies=proxies, timeout=10)
        return {"status": "success"} if res.status_code == 200 else {"status": "error", "message": f"API Error: {res.text}"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/bot/test_wecom")
def api_test_wecom(request: Request):
    if not request.session.get("user"): return {"status": "error"}
    corpid = cfg.get("wecom_corpid"); corpsecret = cfg.get("wecom_corpsecret"); agentid = cfg.get("wecom_agentid")
    proxy_url = cfg.get("wecom_proxy_url", "https://qyapi.weixin.qq.com").rstrip('/')
    touser = cfg.get("wecom_touser", "@all")
    
    if not corpid or not corpsecret or not agentid:
        return {"status": "error", "message": "请填写完整的企业微信基础配置"}
    try:
        token_res = requests.get(f"{proxy_url}/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}", timeout=5).json()
        if token_res.get("errcode") != 0: return {"status": "error", "message": f"Token 获取失败: {token_res.get('errmsg')}"}
        access_token = token_res["access_token"]
        msg_res = requests.post(
            f"{proxy_url}/cgi-bin/message/send?access_token={access_token}",
            json={
                "touser": touser, "msgtype": "markdown", "agentid": int(agentid),
                "markdown": {"content": "🎉 <font color=\"info\">企业微信通道测试成功！</font>\n\n> EmbyPulse 已成功接入代理推送与双向交互通道。"}
            }, timeout=10).json()
        if msg_res.get("errcode") == 0: return {"status": "success"}
        else: return {"status": "error", "message": f"发送失败: {msg_res.get('errmsg')}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_playback_url(item_id):
    base_url = cfg.get("emby_public_url") or cfg.get("emby_host")
    if base_url.endswith('/'): base_url = base_url[:-1]
    return f"{base_url}/web/index.html#!/item?id={item_id}"

@router.post("/api/bot/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != cfg.get("tg_bot_token"): return {"status": "error", "message": "Invalid Token"}
    data = await request.json()
    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]
        if text.startswith("/search"):
            keyword = text.replace("/search", "").strip()
            if not keyword:
                send_tg_msg(chat_id, "🔍 请输入关键词，例如: /search 你的名字")
            else:
                items = search_emby(keyword)
                if not items: send_tg_msg(chat_id, "TxT 未找到相关资源")
                else:
                    msg = f"🔍 搜索结果: {keyword}\n\n"
                    for item in items[:5]:
                        link = get_playback_url(item['Id'])
                        msg += f"🎬 <b>{item['Name']}</b> ({item.get('ProductionYear', 'N/A')})\n🔗 <a href='{link}'>点击播放</a>\n\n"
                    send_tg_msg(chat_id, msg)
        elif text == "/start":
            send_tg_msg(chat_id, "👋 欢迎使用 EmbyPulse 机器人！\n支持指令:\n/search <关键词> - 搜索资源")
    return {"status": "success"}

def search_emby(keyword):
    key = cfg.get("emby_api_key"); host = cfg.get("emby_host")
    try:
        url = f"{host}/emby/Items?api_key={key}&Recursive=true&SearchTerm={keyword}&IncludeItemTypes=Movie,Series&Limit=5"
        res = requests.get(url, timeout=5)
        if res.status_code == 200: return res.json().get("Items", [])
    except: pass
    return []

def send_tg_msg(chat_id, text):
    token = cfg.get("tg_bot_token"); proxy = cfg.get("proxy_url")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id,"text": text,"parse_mode": "HTML"}, proxies=proxies, timeout=10)
    except: pass

# ================= 🔥 企微 API 回调交互 (增强查错与防护版) =================
def decrypt_wecom_data(encrypt_msg):
    if not AES: 
        raise Exception("环境缺少 pycryptodome 依赖，请在 requirements.txt 中添加并重新 build 镜像")
    aeskey = cfg.get("wecom_aeskey") or ""
    if not aeskey: 
        raise Exception("系统未配置 wecom_aeskey")
    
    aes_key_bytes = base64.b64decode(aeskey + "=")
    cipher = AES.new(aes_key_bytes, AES.MODE_CBC, aes_key_bytes[:16])
    decrypted = cipher.decrypt(base64.b64decode(encrypt_msg))
    pad = decrypted[-1]
    decrypted = decrypted[:-pad]
    msg_len = struct.unpack("!I", decrypted[16:20])[0]
    return decrypted[20:20+msg_len].decode('utf-8')

def check_wecom_signature(msg_signature, timestamp, nonce, encrypt_msg):
    token = cfg.get("wecom_token") or ""
    sort_list = [token, timestamp, nonce, encrypt_msg]
    sort_list.sort()
    sha = hashlib.sha1()
    sha.update("".join(sort_list).encode('utf-8'))
    return sha.hexdigest() == msg_signature

@router.get("/api/bot/wecom_webhook")
async def wecom_webhook_get(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    try:
        # 1. 验证签名
        if not check_wecom_signature(msg_signature, timestamp, nonce, echostr):
            logger.error("WeCom Webhook: 签名校验不通过 (可能是 Token 不匹配)")
            return "Signature Error"
        
        # 2. 解密字符串
        msg = decrypt_wecom_data(echostr)
        return Response(content=msg, media_type="text/plain")
        
    except Exception as e: 
        logger.error(f"WeCom Webhook 解析崩溃: {str(e)}")
        return str(e)

@router.post("/api/bot/wecom_webhook")
async def wecom_webhook_post(request: Request, msg_signature: str = "", timestamp: str = "", nonce: str = ""):
    try:
        body = await request.body()
        xml_tree = ET.fromstring(body)
        encrypt_msg = xml_tree.find("Encrypt").text
        
        if not check_wecom_signature(msg_signature, timestamp, nonce, encrypt_msg):
            return "Signature Error"
            
        xml_content = decrypt_wecom_data(encrypt_msg)
        msg_tree = ET.fromstring(xml_content)
        
        from_user = msg_tree.find("FromUserName").text
        msg_type = msg_tree.find("MsgType").text
        
        command_text = ""
        if msg_type == "text":
            command_text = msg_tree.find("Content").text
        elif msg_type == "event" and msg_tree.find("Event").text == "click":
            command_text = msg_tree.find("EventKey").text
            
        if command_text:
            threading.Thread(target=bot._handle_message, args=(command_text, from_user, "wecom")).start()
            
        return Response(content="success", media_type="text/plain")
    except Exception as e:
        logger.error(f"WeCom Post 解析崩溃: {str(e)}")
        return "Error"
