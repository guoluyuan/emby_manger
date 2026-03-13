import sqlite3
import time
import random
from app.core.config import DB_PATH


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


def generate_captcha(request, ttl_seconds: int = 300):
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    request.session["captcha_answer"] = str(a + b)
    request.session["captcha_expires"] = int(time.time()) + ttl_seconds
    return {"question": f"{a} + {b} = ?", "expires_in": ttl_seconds}


def validate_captcha(request, provided: str):
    ans = request.session.get("captcha_answer")
    exp = int(request.session.get("captcha_expires") or 0)
    now = int(time.time())
    if not ans:
        return False, "请先获取验证码"
    if now > exp:
        return False, "验证码已过期，请刷新"
    if str(provided or "").strip() != str(ans):
        return False, "验证码错误"
    request.session.pop("captcha_answer", None)
    request.session.pop("captcha_expires", None)
    return True, ""
