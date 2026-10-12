"""Маркер Instagram API (Instagram Login): проверка и продление.

    .venv/bin/python ig_token.py check     # чей маркер и сколько дней ему осталось
    .venv/bin/python ig_token.py refresh   # продлить на 60 дней (cron, раз в неделю)

Маркер живёт 60 дней. Продлить можно, когда ему больше суток. Без продления
через два месяца API перестанет отвечать молча, поэтому cron продлевает его каждую
неделю. Инструкция: docs/INSTAGRAM_API.md.

Файлы рядом со скриптом (только на сервере, права 600, в .gitignore):
  .ig-token       — сам маркер, одной строкой
  .ig-token.json  — когда продлён и до какого времени действует
Сам маркер никогда не печатается и не попадает в лог.

Уведомление о сбое в Telegram — по желанию: положи токен своего бота в .bot-token
и добавь в config.local.json {"ig_token": {"notify_chat_id": ТВОЙ_ID}}.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://graph.instagram.com"
VERSION = "v26.0"
LIFETIME = 60 * 86400          # срок жизни маркера, если в .ig-token.json ещё пусто
WARN_DAYS = 14                 # check предупреждает, когда осталось меньше


class ApiError(Exception):
    pass


def token_path():
    return Path(os.environ.get("IG_TOKEN_FILE") or HERE / ".ig-token")


def meta_path():
    p = token_path()
    return p.with_name(p.name + ".json")


def read_token():
    p = token_path()
    if not p.exists():
        raise ApiError(f"нет файла маркера {p}")
    return p.read_text().strip()


def write_private(path, text):
    """Атомарно и сразу с правами 600: маркер не бывает виден другим даже миг."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def call(path, params=None, token=None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # В тексте ошибки Meta маркера нет; URL с маркером не печатаем никогда.
        try:
            err = json.load(e).get("error", {})
            msg = f"{err.get('type', '')} {err.get('code', '')}: {err.get('message', '')}".strip()
        except Exception:
            msg = f"HTTP {e.code}"
        raise ApiError(msg) from None
    except urllib.error.URLError as e:
        raise ApiError(f"сеть: {e.reason}") from None


def expires_at():
    try:
        return json.loads(meta_path().read_text())["expires_at"]
    except Exception:
        return token_path().stat().st_mtime + LIFETIME


def days_left(now=None):
    return (expires_at() - (now or time.time())) / 86400


def refresh(now=None):
    now = now or time.time()
    data = call("refresh_access_token", {"grant_type": "ig_refresh_token", "access_token": read_token()})
    new, ttl = data.get("access_token"), int(data.get("expires_in") or 0)
    if not new or ttl <= 0:
        raise ApiError("Meta не вернула новый маркер")
    write_private(token_path(), new + "\n")
    write_private(meta_path(), json.dumps({"refreshed_at": int(now), "expires_at": int(now + ttl)}) + "\n")
    return ttl / 86400


def whoami():
    return call(f"{VERSION}/me", {"fields": "username,account_type"}, token=read_token())


def notify(text):
    """Владельцу в Telegram через бота проекта. Нет бота или chat id — молча пропускаем."""
    try:
        bot = (HERE / ".bot-token").read_text().strip()
        cfg = json.loads((HERE / "config.local.json").read_text())
        chat = cfg["ig_token"]["notify_chat_id"]
    except Exception:
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": f"⚠️ {HERE.name}: {text}"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{bot}/sendMessage", body, timeout=20)
    except Exception:
        pass


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    try:
        if cmd == "refresh":
            days = refresh()
            me = whoami()
            print(f"{stamp} продлён: @{me.get('username')}, действует ещё {days:.0f} дн.")
        elif cmd == "check":
            me = whoami()
            left = days_left()
            print(f"@{me.get('username')} ({me.get('account_type')}), маркер действует ещё {left:.0f} дн.")
            if left < WARN_DAYS:
                notify(f"маркер Instagram API истекает через {left:.0f} дн., а продление не сработало")
                sys.exit(3)
        else:
            sys.exit(__doc__)
    except ApiError as e:
        print(f"{stamp} ❌ {cmd}: {e}", file=sys.stderr)
        notify(f"маркер Instagram API: {cmd} не удался — {e}. Лог: {HERE}/.cache/ig-token.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
