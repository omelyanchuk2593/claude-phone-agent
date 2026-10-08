"""Транспорт до телефона: adb + uiautomator2. Единственное место, где они есть.

Идеи и грабли взяты из боевого Instagram-бота, который месяцами работал на
живом телефоне через Tailscale:

* экран читается ОДНИМ дампом иерархии и разбирается локально: каждый вопрос
  к телефону стоит сетевой поездки через Tailscale;
* текст вводится через setText (accessibility API), а не `adb shell input
  text`: тот не умеет кириллицу, а send_keys из uiautomator2 подменяет
  клавиатуру и пишет в буфер обмена владельца;
* телефон ЛИЧНЫЙ: прежде чем нажимать, проверяем, не держит ли его владелец.
  Наши собственные нажатия система тоже считает активностью, поэтому время
  своего последнего касания храним в файле между запусками.
"""

import json
import random
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
INSTAGRAM = "com.instagram.android"
APP_ALIASES = {"instagram": INSTAGRAM, "ig": INSTAGRAM}

DEFAULTS = {
    # Tailscale-адрес телефона и порт ADB, например "100.101.102.103:5555".
    # Задаётся в config.local.json (его пишет ./install.sh 100.x.y.z).
    "address": "",
    # Сколько секунд тишины на экране нужно, чтобы считать, что владелец
    # телефон отложил. Наши нажатия сюда не считаются (см. own_touch).
    "owner_idle_seconds": 45,
    "http_timeout": 30,
    "tap_pause": [0.6, 1.4],
    "shot_width": 540,
}

# Допуск при сравнении «последняя активность на телефоне» с «нашим последним
# касанием». Обе величины меряются с точностью до секунд плюс сетевая задержка.
OWN_TOUCH_TOLERANCE = 15

# Нажатие на такие элементы требует явного --confirm. Промах здесь не
# откатывается: отписка, блокировка, удаление переписки, выход из аккаунта.
DANGER_RE = re.compile(
    r"(?i)\b(unfollow|block|report|delete|remove|log ?out|unsend|"
    r"отписаться|заблокировать|пожаловаться|удалить|выйти|отменить отправку)\b")


class DeviceError(Exception):
    pass


class DeviceUnavailable(DeviceError):
    """Нет связи: телефон не в сети, adb не авторизован, Tailscale уснул."""


class OwnerBusy(DeviceError):
    """Телефоном сейчас пользуется владелец, или он заблокирован."""


class PhoneTaken(OwnerBusy):
    """Телефоном прямо сейчас управляет другая сессия (другой Claude или человек)."""


class ScreenChanged(DeviceError):
    """Экран изменился между `ui` и действием, нажимать вслепую нельзя."""


# Сколько секунд после последнего действия телефон «закреплён» за сессией.
# Опасна не одновременность команд, а их ПЕРЕМЕЖЕНИЕ: Claude A снял экран и
# собирается нажать [12], а Claude B между делом открыл другой диалог.
LEASE_SECONDS = 90


def claim_phone(who, path=None, now=None):
    """Закрепить телефон за сессией `who` или PhoneTaken, если он занят другой.

    Имя сессии выбирает phone.py: PHONE_SESSION, иначе claude-server внутри
    Claude Code, иначе manual.
    """
    import fcntl

    path = Path(path) if path else CACHE / "lease.json"
    now = time.time() if now is None else now
    with open(path.with_suffix(".lock"), "a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)  # чтение и запись аренды атомарно
        try:
            lease = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lease = {}
        ago = now - float(lease.get("ts", 0))
        holder = lease.get("who")
        if holder and holder != who and ago < LEASE_SECONDS:
            raise PhoneTaken(
                f"телефоном сейчас управляет «{holder}» (последнее действие {int(ago)} с назад). "
                f"Подожди {int(LEASE_SECONDS - ago) + 1} с или останови ту сессию")
        path.write_text(json.dumps({"who": who, "ts": now}), encoding="utf-8")


def load_config():
    cfg = dict(DEFAULTS)
    for name in ("config.json", "config.local.json"):
        path = HERE / name
        if path.exists():
            cfg.update(json.loads(path.read_text(encoding="utf-8")).get("device", {}))
    return cfg


# ------------------------------------------------------------------ экран

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class Node:
    def __init__(self, el):
        a = el.attrib
        self.rid = a.get("resource-id", "")
        self.cls = a.get("class", "")
        self.text = a.get("text", "")
        self.desc = a.get("content-desc", "")
        self.pkg = a.get("package", "")
        m = BOUNDS_RE.match(a.get("bounds", ""))
        self.bounds = tuple(int(v) for v in m.groups()) if m else (0, 0, 0, 0)
        self.clickable = a.get("clickable") == "true" or a.get("long-clickable") == "true"
        self.scrollable = a.get("scrollable") == "true"
        self.checkable = a.get("checkable") == "true"
        self.checked = a.get("checked") == "true"
        self.selected = a.get("selected") == "true"
        self.focused = a.get("focused") == "true"
        self.editable = "EditText" in self.cls

    @property
    def center(self):
        left, top, right, bottom = self.bounds
        return (left + right) // 2, (top + bottom) // 2

    @property
    def area(self):
        left, top, right, bottom = self.bounds
        return max(0, right - left) * max(0, bottom - top)

    @property
    def short_id(self):
        return self.rid.split(":id/")[-1]

    def identity(self):
        return {"rid": self.rid, "cls": self.cls, "text": self.text,
                "desc": self.desc, "bounds": list(self.bounds)}

    def same_as(self, ident, with_bounds=True):
        if (self.rid, self.cls, self.text, self.desc) != (
                ident["rid"], ident["cls"], ident["text"], ident["desc"]):
            return False
        return not with_bounds or list(self.bounds) == list(ident["bounds"])

    def dangerous(self):
        return bool(DANGER_RE.search(f"{self.text} {self.desc}"))

    def line(self, idx):
        def clip(s, n=80):
            s = s.replace("\n", " ⏎ ").strip()
            return s if len(s) <= n else s[:n - 1] + "…"

        parts = [f"[{idx}]", self.cls.rsplit(".", 1)[-1] or "?"]
        if self.text:
            parts.append(f'"{clip(self.text)}"')
        if self.desc and self.desc != self.text:
            parts.append(f"(desc: {clip(self.desc)})")
        if self.rid:
            parts.append(f"id={self.short_id}")
        x, y = self.center
        parts.append(f"@{x},{y}")
        flags = [name for name, on in (
            ("click", self.clickable), ("scroll", self.scrollable),
            ("edit", self.editable), ("checked", self.checked),
            ("selected", self.selected), ("focused", self.focused)) if on]
        if flags:
            parts.append("[" + ",".join(flags) + "]")
        return " ".join(parts)


class Snapshot:
    """Один дамп иерархии. Снимается одной командой и разбирается здесь."""

    def __init__(self, xml):
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            raise DeviceError(f"дамп экрана не разобрался как XML: {e}") from e
        self.xml = xml
        self.nodes = [Node(el) for el in root.iter("node")]
        self.width = max((n.bounds[2] for n in self.nodes), default=0)
        self.height = max((n.bounds[3] for n in self.nodes), default=0)
        self.items = [n for n in self.nodes if self._worth_showing(n)]

    def _worth_showing(self, n):
        left, top, right, bottom = n.bounds
        if n.area == 0 or right <= 0 or bottom <= 0:
            return False
        if self.width and left >= self.width or self.height and top >= self.height:
            return False
        return bool(n.text or n.desc or n.clickable or n.scrollable
                    or n.editable or n.checkable)

    def packages(self):
        return sorted({n.pkg for n in self.nodes if n.pkg})

    def find(self, text=None, desc=None, rid=None):
        """Узлы по тексту/описанию/id. Сначала точное совпадение, потом подстрока."""
        def norm(s):
            return (s or "").strip().casefold()

        pool = self.items
        if rid:
            pool = [n for n in pool if n.short_id == rid or n.rid == rid]
        for field, want in (("text", text), ("desc", desc)):
            if want is None:
                continue
            exact = [n for n in pool if norm(getattr(n, field)) == norm(want)]
            pool = exact or [n for n in pool if norm(want) in norm(getattr(n, field))]
        return pool


# ------------------------------------------------- состояние телефона

LOCK_RE = re.compile(
    r"(?i)\b(mDreamingLockscreen|mShowingLockscreen|isStatusBarKeyguard|"
    r"mKeyguardShowing|keyguardShowing|mIsShowing|showing)\s*=\s*(true|false)")
FOCUS_RE = re.compile(r"mCurrentFocus=Window\{\S+ \S+ ([^}/\s]+)(?:/([^}\s]+))?\}")
WAKE_RE = re.compile(r"mWakefulness=(\w+)")

# Одна поездка к телефону вместо пяти. Части разделены маркером и разбираются
# ПО ОТДЕЛЬНОСТИ: склеенный вывод однажды дал регулярке времени зацепить
# строку из чужой команды.
PROBE_CMD = (
    "dumpsys power | grep -iE 'mWakefulness=|UserActivityTime'; echo @@@; "
    "dumpsys window policy | grep -iE 'showing|keyguard|dreaming'; "
    "dumpsys activity activities | grep -iE 'KeyguardShowing'; echo @@@; "
    "dumpsys window | grep -E 'mCurrentFocus='; echo @@@; "
    "dumpsys battery | grep -E '^ *(level|AC powered|USB powered|Wireless powered):'"
)


def parse_idle_seconds(power):
    """Сколько секунд телефон никто не трогал. None = измерить не удалось.

    Берём только разностную форму «… ago»: абсолютное mLastUserActivityTime
    идёт по часам без сна, и сравнение с /proc/uptime врёт на тысячи секунд
    (так «простой» получался всегда, и защита владельца молча отключалась).
    Форм две: «(72912 ms ago)» и «+1m12s ago».
    Таймеров активности несколько, свежайший ближе к правде.
    """
    candidates = []
    for line in power.splitlines():
        if "ago" not in line.lower():
            continue
        ms = re.search(r"(\d+)\s*ms\s*ago", line, re.I)
        if ms:
            candidates.append(int(ms.group(1)) // 1000)
            continue
        hum = re.search(r"\+?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+)ms)?\s*ago",
                        line, re.I)
        if hum and any(hum.groups()):
            d, h, m, s, _ms = (int(g or 0) for g in hum.groups())
            candidates.append(d * 86400 + h * 3600 + m * 60 + s)
    return min(candidates) if candidates else None


def parse_locked(text):
    """(заблокирован?, чем определили). None = признаков keyguard нет вовсе."""
    found = LOCK_RE.findall(text)
    if not found:
        return None, "dumpsys не отдал ни одного признака keyguard"
    for key, value in found:
        if value.lower() == "true":
            return True, f"{key}=true"
    return False, ", ".join(f"{k}={v}" for k, v in found[:3])


def parse_probe(output):
    parts = (output.split("@@@") + ["", "", "", ""])[:4]
    power, lock, window, battery = parts
    wake = WAKE_RE.search(power)
    wakefulness = wake.group(1) if wake else None
    locked, lock_note = parse_locked(lock)
    focus = FOCUS_RE.search(window)
    level = re.search(r"level:\s*(\d+)", battery)
    charging = bool(re.search(r"powered:\s*true", battery))
    return {
        "wakefulness": wakefulness,
        # Dreaming/Dozing это заставка и AOD: для человека экран погашен.
        "screen_on": wakefulness == "Awake" if wakefulness else None,
        "locked": locked,
        "lock_note": lock_note,
        "idle": parse_idle_seconds(power),
        "focus": focus.group(1) if focus else None,
        "activity": focus.group(2) if focus and focus.group(2) else None,
        "battery": int(level.group(1)) if level else None,
        "charging": charging,
    }


def owner_verdict(state, own_touch_ago, need_idle):
    """None, если работать можно, иначе причина, почему нельзя.

    Осторожность важнее точности: не сумели измерить, значит считаем занятым.
    Пропущенное действие стоит ноль, драка за экран с владельцем стоит
    перехваченного ввода и нажатий в его переписке.
    """
    if state["locked"] is None:
        return f"не удалось понять, заблокирован ли телефон ({state['lock_note']})"
    if state["locked"]:
        return "телефон заблокирован; PIN бот не вводит, разблокируй его сам"
    if state["screen_on"] is False:
        return None
    idle = state["idle"]
    if idle is None:
        return "экран включён, а когда его трогали, измерить не удалось"
    if own_touch_ago is not None and abs(idle - own_touch_ago) <= OWN_TOUCH_TOLERANCE:
        return None  # последняя активность это наше же нажатие
    if idle < need_idle:
        return f"телефон в руках: последнее касание {idle} с назад (нужно {need_idle} с тишины)"
    return None


# --------------------------------------------------------------- телефон

class Device:
    def __init__(self, cfg=None, log=print):
        self.cfg = cfg or load_config()
        self.addr = self.cfg["address"]
        self.log = log
        self.d = None
        CACHE.mkdir(exist_ok=True)

    # ----- связь

    def connect(self):
        import adbutils
        import uiautomator2 as u2  # тяжёлый импорт, только когда нужен телефон

        if not self.addr:
            raise DeviceUnavailable(
                "адрес телефона не задан: запусти `./install.sh 100.x.y.z` или впиши "
                '{"device": {"address": "100.x.y.z:5555"}} в config.local.json')
        hint = (f"Проверь: Tailscale на телефоне включён (`tailscale ping {self.addr.split(':')[0]}`), "
                f"ADB по сети включён (после перезагрузки телефона — шаг 5: кабель + setup/usb-tcpip)")
        unauthorized = ("телефон не доверяет этому серверу: на экране телефона должен быть "
                        "запрос «Разрешить отладку?» — нажми «Разрешить» и поставь "
                        "галочку «Всегда разрешать с этого компьютера»")
        try:
            note = adbutils.adb.connect(self.addr, timeout=15)
        except Exception as e:
            raise DeviceUnavailable(f"{self.addr} не отвечает ({e}). {hint}") from e
        # Неразрешённый ключ adb отдаёт как «failed to authenticate», то есть
        # тоже «failed»: проверять надо раньше общего случая, иначе подсказка
        # отправит чинить Tailscale, который в порядке.
        if "authenticate" in str(note).lower():
            raise DeviceUnavailable(unauthorized)
        if "failed" in str(note).lower() or "cannot" in str(note).lower():
            raise DeviceUnavailable(f"{self.addr} не отвечает ({note}). {hint}")
        state = self._adb_state()
        if state == "unauthorized":
            raise DeviceUnavailable(unauthorized)
        if state != "device":
            raise DeviceUnavailable(f"adb видит телефон в состоянии «{state}»")
        try:
            self.d = u2.connect(self.addr)
        except Exception as e:
            raise DeviceUnavailable(f"uiautomator2 не поднялся на {self.addr}: {e}") from e
        try:
            self.d.settings["http_timeout"] = float(self.cfg["http_timeout"])
        except Exception:
            pass  # таймаут желателен, но падать из-за него на старте хуже
        return self

    def _adb_state(self):
        import adbutils
        for dev in adbutils.adb.list(extended=False):
            if dev.serial == self.addr:
                return dev.state
        return "not found"

    def shell(self, command, timeout=30):
        try:
            res = self.d.shell(command, timeout=timeout)
        except Exception as e:
            raise DeviceUnavailable(f"adb shell не выполнился: {e}") from e
        return str(getattr(res, "output", res) or "")

    def info(self):
        return self.d.info

    def ig_version(self):
        out = self.shell(f"dumpsys package {INSTAGRAM} | grep -m1 versionName")
        m = re.search(r"versionName=(\S+)", out)
        return m.group(1) if m else None

    # ----- владелец

    def probe(self):
        return parse_probe(self.shell(PROBE_CMD))

    def _own_touch_ago(self):
        try:
            return time.time() - float((CACHE / "own_touch").read_text())
        except (OSError, ValueError):
            return None

    def _mark_touch(self):
        (CACHE / "own_touch").write_text(str(time.time()))

    def wake(self):
        """Включить экран и снять НЕзащищённый замок (Smart Lock / свайп).

        Защищённый PIN-ом замок `wm dismiss-keyguard` не снимает, а показывает
        клавиатуру PIN. Её прячем и гасим экран обратно: телефон в кармане не
        должен остаться включённым с открытой клавиатурой кода.
        """
        self.shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(0.8)
        self.shell("wm dismiss-keyguard")
        time.sleep(1.2)
        state = self.probe()
        if state["locked"]:
            self.shell("input keyevent KEYCODE_BACK")
            time.sleep(0.4)
            self.shell("input keyevent KEYCODE_SLEEP")
        return state

    def ensure_ready(self, force=False):
        """Убедиться, что телефоном можно пользоваться, иначе OwnerBusy."""
        state = self.probe()
        if state["screen_on"] is False:
            state = self.wake()
            if state["locked"]:
                raise OwnerBusy("телефон заблокирован PIN-кодом; разблокируй его, "
                                "бот PIN не вводит")
            return state
        if force:
            if state["locked"]:
                raise OwnerBusy("телефон заблокирован; --force тут не поможет, разблокируй его")
            return state
        reason = owner_verdict(state, self._own_touch_ago(), int(self.cfg["owner_idle_seconds"]))
        if reason:
            raise OwnerBusy(reason + ". Если это ты и действие нужно сейчас, повтори с --force")
        return state

    # ----- экран

    def snapshot(self):
        try:
            xml = self.d.dump_hierarchy()
        except Exception as e:
            raise DeviceUnavailable(f"дамп экрана не снялся: {e}") from e
        return Snapshot(xml)

    def save_ui(self, snap):
        data = {"ts": time.time(), "items": [n.identity() for n in snap.items]}
        (CACHE / "last_ui.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def load_ui(self):
        try:
            return json.loads((CACHE / "last_ui.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def screenshot(self, out=None):
        img = self.d.screenshot()
        width = int(self.cfg["shot_width"])
        scale = img.width / width if img.width > width else 1.0
        if scale > 1.0:
            img = img.resize((width, round(img.height / scale)))
        path = Path(out) if out else CACHE / "screen.png"
        img.convert("RGB").save(path)
        (CACHE / "shot.json").write_text(json.dumps({"scale": scale, "ts": time.time()}))
        return path, scale

    def shot_scale(self):
        try:
            return float(json.loads((CACHE / "shot.json").read_text())["scale"])
        except (OSError, ValueError, KeyError):
            return None

    # ----- действия

    def _pause(self):
        low, high = self.cfg["tap_pause"]
        time.sleep(random.uniform(low, high))

    def tap_xy(self, x, y):
        self.d.click(int(x), int(y))
        self._mark_touch()
        self._pause()

    def tap_node(self, node):
        """Нажать узел не ровно в центр: идеальные центры это почерк бота."""
        left, top, right, bottom = node.bounds
        x, y = node.center
        dx, dy = (right - left) // 6, (bottom - top) // 6
        self.tap_xy(x + random.randint(-dx, dx), y + random.randint(-dy, dy))

    def set_text(self, node, snap, text):
        """Текст в поле через accessibility setText: кириллица работает,
        клавиатуру и буфер обмена владельца не трогаем."""
        if node.rid:
            sel = self.d(resourceId=node.rid, className=node.cls)
        else:
            # Без id адресуем по классу и порядку в дампе: UiSelector.instance
            # считает узлы в том же порядке обхода иерархии.
            same = [n for n in snap.nodes if n.cls == node.cls]
            sel = self.d(className=node.cls, instance=same.index(node))
        sel.set_text(text)
        self._mark_touch()
        self._pause()

    def swipe(self, direction, scale=0.55):
        """Свайп пальцем в направлении direction (up = палец идёт вверх)."""
        w, h = self.d.window_size()
        cx = w // 2 + random.randint(-w // 10, w // 10)
        cy = h // 2 + random.randint(-h // 20, h // 20)
        dx, dy = int(w * scale / 2), int(h * scale / 2)
        x1, y1, x2, y2 = {
            "up": (cx, cy + dy, cx, cy - dy),
            "down": (cx, cy - dy, cx, cy + dy),
            "left": (cx + dx, cy, cx - dx, cy),
            "right": (cx - dx, cy, cx + dx, cy),
        }[direction]
        self.d.swipe(x1, y1, x2, y2, random.uniform(0.18, 0.35))
        self._mark_touch()
        self._pause()

    def press(self, key):
        self.d.press(key)
        self._mark_touch()
        self._pause()

    def open_app(self, name):
        package = APP_ALIASES.get(name.lower(), name)
        self.d.app_start(package, use_monkey=True)
        self._mark_touch()
        time.sleep(2.5)
        return package

    def open_url(self, url, app=None):
        cmd = ["am", "start", "-a", "android.intent.action.VIEW", "-d", url]
        if app:
            cmd += ["-p", APP_ALIASES.get(app.lower(), app)]
        out = self.shell(cmd)
        self._mark_touch()
        time.sleep(2.5)
        return out
