import sqlite3
import time
import random
import base64
import io
import secrets
from typing import Optional
import secrets
import string
from app.core.config import DB_PATH, FONT_DIR

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


def get_client_ip(request):
    xfwd = request.headers.get("x-forwarded-for")
    if xfwd:
        return xfwd.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_login_attempts_table():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS login_attempts ("
        "ip TEXT, scope TEXT, failed_count INTEGER DEFAULT 0, "
        "locked_until INTEGER DEFAULT 0, last_failed INTEGER DEFAULT 0, "
        "PRIMARY KEY (ip, scope))"
    )
    conn.commit()
    conn.close()


def get_lock_status(ip: str, scope: str):
    ensure_login_attempts_table()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT failed_count, locked_until FROM login_attempts WHERE ip=? AND scope=?", (ip, scope))
    row = cur.fetchone()
    conn.close()
    now = int(time.time())
    if row and row["locked_until"] and row["locked_until"] > now:
        return True, int(row["locked_until"]), int(row["failed_count"] or 0)
    if row and row["locked_until"] and row["locked_until"] <= now:
        reset_failures(ip, scope)
    return False, 0, int(row["failed_count"] or 0) if row else 0


def record_failure(ip: str, scope: str, max_fail: int = 3, lock_seconds: int = 3600):
    ensure_login_attempts_table()
    now = int(time.time())
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT failed_count, locked_until FROM login_attempts WHERE ip=? AND scope=?", (ip, scope))
    row = cur.fetchone()
    failed = int(row["failed_count"] or 0) + 1 if row else 1
    locked_until = int(row["locked_until"] or 0) if row else 0

    if locked_until and locked_until > now:
        conn.close()
        return locked_until, failed

    if failed >= max_fail:
        locked_until = now + lock_seconds

    cur.execute(
        "INSERT INTO login_attempts (ip, scope, failed_count, locked_until, last_failed) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(ip, scope) DO UPDATE SET failed_count=excluded.failed_count, "
        "locked_until=excluded.locked_until, last_failed=excluded.last_failed",
        (ip, scope, failed, locked_until, now)
    )
    conn.commit()
    conn.close()
    return locked_until, failed


def reset_failures(ip: str, scope: str):
    ensure_login_attempts_table()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE login_attempts SET failed_count=0, locked_until=0, last_failed=0 WHERE ip=? AND scope=?", (ip, scope))
    conn.commit()
    conn.close()


def _load_captcha_font(size: int = 34) -> Optional["ImageFont.ImageFont"]:
    if not _HAS_PIL:
        return None
    try:
        if FONT_DIR:
            for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf"):
                try:
                    return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
                except Exception:
                    continue
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def _build_captcha_image(code: str, width: int = 180, height: int = 56) -> Optional[bytes]:
    if not _HAS_PIL:
        return None
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # 背景噪点
    for _ in range(120):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        draw.point((x, y), fill=(random.randint(160, 220), random.randint(160, 220), random.randint(160, 220)))
    # 干扰线
    for _ in range(4):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(random.randint(80, 160), random.randint(80, 160), random.randint(80, 160)), width=2)
    font = _load_captcha_font(34)
    # 文字
    spacing = width // (len(code) + 1)
    for i, ch in enumerate(code):
        try:
            bbox = font.getbbox(ch)
            ch_w = bbox[2] - bbox[0]
            ch_h = bbox[3] - bbox[1]
        except Exception:
            ch_w, ch_h = font.getsize(ch)
        x = spacing * (i + 1) - (ch_w // 2) + random.randint(-2, 2)
        y = max(4, (height - ch_h) // 2 + random.randint(-3, 3))
        draw.text((x, y), ch, font=font, fill=(random.randint(20, 80), random.randint(20, 80), random.randint(20, 80)))
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def generate_captcha(request, ttl_seconds: int = 300):
    # 字母 + 数字验证码（避开易混淆字符）
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(5))
    request.session["captcha_answer"] = code
    request.session["captcha_expires"] = int(time.time()) + ttl_seconds

    img_bytes = _build_captcha_image(code)
    if img_bytes:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        return {"image": f"data:image/png;base64,{b64}", "expires_in": ttl_seconds}
    # 兜底：无 PIL 时返回文字（但仍可用）
    return {"question": f"验证码：{code}", "expires_in": ttl_seconds}


def validate_captcha(request, provided: str):
    ans = request.session.get("captcha_answer")
    exp = int(request.session.get("captcha_expires") or 0)
    now = int(time.time())
    if not ans:
        return False, "请先获取验证码"
    if now > exp:
        return False, "验证码已过期，请刷新"
    if str(provided or "").strip().upper() != str(ans).upper():
        return False, "验证码错误"
    request.session.pop("captcha_answer", None)
    request.session.pop("captcha_expires", None)
    return True, ""
