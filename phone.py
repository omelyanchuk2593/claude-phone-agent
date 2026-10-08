#!/usr/bin/env python3
"""phone — управление телефоном с сервера. Для Claude Code и для рук.

    phone connect                  подключиться, показать модель и версию Instagram
    phone status                   экран, замок, простой, что открыто, батарея
    phone ui [--find СЛОВО]        элементы экрана с номерами
    phone shot [--out FILE]        скриншот (уменьшенный, --width N) -> путь к PNG
    phone tap 12                   нажать элемент №12 из последнего `phone ui`
    phone tap --text "Message"     ... или --desc / --id / --xy X Y / --pic X Y
    phone type "Привет"            ввести текст в поле (в фокусе или --into N)
    phone scroll down|up [-n 3]    листать содержимое
    phone swipe left|right|up|down свайп пальцем
    phone back | home | wake
    phone open instagram           запустить приложение
    phone url https://instagram.com/username   открыть ссылку
    phone adb shell getprop ...    сырой adb к этому телефону

У действий есть флаги: -u/--ui (после действия напечатать экран) и --force
(действовать, даже если телефон, похоже, в руках у владельца).

Коды выхода: 0 ок, 2 телефон занят/заблокирован, 3 нет связи,
4 экран не тот/элемент не найден/нужен --confirm, 1 прочее.
"""

import argparse
import os
import subprocess
import sys

from device import (Device, DeviceError, DeviceUnavailable, OwnerBusy,
                    ScreenChanged, INSTAGRAM, claim_phone)

# Команды, которые меняют экран. Смотреть (status/ui/shot) можно всем сразу,
# нажимать — одной сессии за раз.
ACTIONS = {"tap", "type", "scroll", "swipe", "back", "home", "open", "url", "wake"}


def print_ui(dev, snap, find=None):
    dev.save_ui(snap)
    print(f"экран {snap.width}x{snap.height} | приложения: {', '.join(snap.packages()) or '?'}"
          f" | элементов: {len(snap.items)}")
    for i, node in enumerate(snap.items):
        if find and find.casefold() not in f"{node.text} {node.desc} {node.rid}".casefold():
            continue
        print(node.line(i))


def after_action(dev, args):
    if getattr(args, "ui", False):
        print("---")
        print_ui(dev, dev.snapshot())


def node_at(snap, x, y):
    """Самый маленький кликабельный узел под точкой: что мы на самом деле нажмём."""
    hits = [n for n in snap.items if n.clickable
            and n.bounds[0] <= x <= n.bounds[2] and n.bounds[1] <= y <= n.bounds[3]]
    return min(hits, key=lambda n: n.area) if hits else None


def resolve_saved(dev, snap, index):
    """Элемент №index из последнего `phone ui`, найденный на СВЕЖЕМ экране.

    Между `ui` и `tap` экран мог уехать: пришло сообщение, лента
    прокрутилась. Нажимаем только если элемент на месте, либо сдвинулся,
    но однозначно узнаётся по id/тексту/описанию.
    """
    saved = dev.load_ui()
    if not saved or not 0 <= index < len(saved["items"]):
        raise ScreenChanged(f"нет элемента [{index}] в последнем `phone ui` — сними экран заново")
    ident = saved["items"][index]
    exact = [n for n in snap.items if n.same_as(ident)]
    if exact:
        return exact[0], None
    moved = [n for n in snap.items if n.same_as(ident, with_bounds=False)]
    if len(moved) == 1 and (ident["text"] or ident["desc"] or ident["rid"]):
        return moved[0], "элемент сдвинулся, нажимаю на новом месте"
    raise ScreenChanged(f"экран изменился после `phone ui`: элемента [{index}] на месте нет. "
                        f"Сними экран заново (`phone ui`)")


def cmd_connect(dev, args):
    info = dev.info()
    props = dev.shell("getprop ro.product.model; getprop ro.build.version.release").split()
    model, android = (props + ["?", "?"])[:2]
    print(f"подключён: {dev.addr}")
    print(f"телефон: {model} ({info.get('productName')}), Android {android}, "
          f"экран {info.get('displayWidth')}x{info.get('displayHeight')}, "
          f"{'включён' if info.get('screenOn') else 'погашен'}")
    print(f"Instagram: {dev.ig_version() or 'не установлен'}")


def cmd_status(dev, args):
    st = dev.probe()
    screen = {True: "включён", False: "погашен", None: "?"}[st["screen_on"]]
    lock = {True: "заблокирован", False: "разблокирован", None: "неизвестно"}[st["locked"]]
    idle = "?" if st["idle"] is None else f"{st['idle']} с"
    battery = "?" if st["battery"] is None else f"{st['battery']}%"
    print(f"экран: {screen} ({st['wakefulness']}) | замок: {lock} ({st['lock_note']})")
    print(f"без касаний: {idle} | на экране: {st['focus']}"
          f"{'/' + st['activity'] if st['activity'] else ''}")
    print(f"батарея: {battery}{', заряжается' if st['charging'] else ''}")
    from device import owner_verdict
    reason = owner_verdict(st, dev._own_touch_ago(), int(dev.cfg["owner_idle_seconds"]))
    print(f"можно работать: {'да' if not reason else 'нет — ' + reason}")


def cmd_ui(dev, args):
    print_ui(dev, dev.snapshot(), find=args.find)


def cmd_shot(dev, args):
    if args.width:
        dev.cfg["shot_width"] = args.width
    path, scale = dev.screenshot(args.out)
    print(f"{path}")
    if scale > 1:
        print(f"масштаб: 1 px картинки = {scale:.2f} px экрана "
              f"(нажать по картинке: phone tap --pic X Y)")


def cmd_tap(dev, args):
    dev.ensure_ready(args.force)
    snap = dev.snapshot()
    note = None
    if args.index is not None:
        node, note = resolve_saved(dev, snap, args.index)
        x = y = None
    elif args.xy or args.pic:
        if args.pic:
            scale = dev.shot_scale()
            if scale is None:
                raise ScreenChanged("нет последнего скриншота — сначала `phone shot`")
            x, y = round(args.pic[0] * scale), round(args.pic[1] * scale)
        else:
            x, y = args.xy
        node = node_at(snap, x, y)
    else:
        matches = snap.find(text=args.text, desc=args.desc, rid=args.id)
        if not matches:
            raise ScreenChanged("на экране нет такого элемента")
        if len(matches) > 1:
            dev.save_ui(snap)
            listed = "\n".join(m.line(snap.items.index(m)) for m in matches[:15])
            raise ScreenChanged(f"подходит несколько элементов, выбери номер (`phone tap N`):\n{listed}")
        node, x, y = matches[0], None, None

    if node is not None and node.dangerous() and not args.confirm:
        raise ScreenChanged(f"опасная кнопка: {node.line(snap.items.index(node))}\n"
                            f"если это действительно нужно — повтори с --confirm")
    if x is None:
        dev.tap_node(node)
    else:
        dev.tap_xy(x, y)
    what = node.line(snap.items.index(node)) if node is not None else "(пустое место)"
    print(f"нажал: {what}" + (f" @{x},{y}" if x is not None else ""))
    if note:
        print(f"заметка: {note}")
    after_action(dev, args)


def cmd_type(dev, args):
    dev.ensure_ready(args.force)
    snap = dev.snapshot()
    if args.into is not None:
        node, _ = resolve_saved(dev, snap, args.into)
        if not node.editable:
            raise ScreenChanged(f"[{args.into}] это не поле ввода: {node.line(args.into)}")
    else:
        fields = [n for n in snap.items if n.editable]
        focused = [n for n in fields if n.focused]
        if focused:
            node = focused[0]
        elif len(fields) == 1:
            node = fields[0]
        else:
            listed = "\n".join(f.line(snap.items.index(f)) for f in fields) or "(полей нет)"
            raise ScreenChanged(f"не понял, в какое поле вводить — укажи --into N:\n{listed}")
    dev.set_text(node, snap, args.text)
    check = dev.snapshot()
    landed = any(args.text[:20] in n.text for n in check.items if n.editable)
    print(f"ввёл {len(args.text)} симв. в: {node.short_id or node.cls}"
          + ("" if landed or not args.text else " — ВНИМАНИЕ: в полях текста не видно, проверь экран"))
    after_action(dev, args)


def cmd_scroll(dev, args):
    dev.ensure_ready(args.force)
    finger = {"down": "up", "up": "down"}[args.direction]
    for _ in range(args.n):
        dev.swipe(finger)
    print(f"пролистал {args.direction} x{args.n}")
    after_action(dev, args)


def cmd_swipe(dev, args):
    dev.ensure_ready(args.force)
    dev.swipe(args.direction)
    print(f"свайп {args.direction}")
    after_action(dev, args)


def cmd_key(key):
    def run(dev, args):
        dev.ensure_ready(args.force)
        dev.press(key)
        print(f"нажал {key}")
        after_action(dev, args)
    return run


def cmd_wake(dev, args):
    st = dev.wake()
    print("экран включён, замок снят" if not st["locked"]
          else "замок защищён PIN-кодом — разблокируй телефон сам")


def cmd_open(dev, args):
    dev.ensure_ready(args.force)
    print(f"открыл {dev.open_app(args.app)}")
    after_action(dev, args)


def cmd_url(dev, args):
    dev.ensure_ready(args.force)
    app = args.app or (INSTAGRAM if "instagram.com" in args.url else None)
    out = dev.open_url(args.url, app)
    print(f"открыл ссылку{' в ' + app if app else ''}: {out.strip().splitlines()[-1] if out.strip() else 'ok'}")
    after_action(dev, args)


def cmd_adb(dev, args):
    import adbutils
    from adbutils import adb_path
    if not dev.addr:
        raise DeviceUnavailable("адрес телефона не задан: ./install.sh 100.x.y.z")
    adbutils.adb.connect(dev.addr, timeout=15)
    res = subprocess.run([adb_path(), "-s", dev.addr, *args.rest],
                         capture_output=True, text=True, timeout=120)
    if "input" in args.rest:
        # `adb shell input swipe/tap` — это наше касание. Без отметки следующая
        # команда приняла бы его за палец владельца и отказалась работать.
        dev._mark_touch()
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    return res.returncode


def build_parser():
    p = argparse.ArgumentParser(prog="phone", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def action(name, func, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("-u", "--ui", action="store_true", help="после действия напечатать экран")
        sp.add_argument("--force", action="store_true", help="не ждать, пока владелец отложит телефон")
        sp.set_defaults(func=func)
        return sp

    sub.add_parser("connect", help="подключиться").set_defaults(func=cmd_connect)
    sub.add_parser("status", help="состояние телефона").set_defaults(func=cmd_status)
    sp = sub.add_parser("ui", help="элементы экрана")
    sp.add_argument("--find", help="показать только строки с этим словом")
    sp.set_defaults(func=cmd_ui)
    sp = sub.add_parser("shot", help="скриншот")
    sp.add_argument("--out")
    sp.add_argument("--width", type=int, help="ширина картинки, px (по умолчанию из config)")
    sp.set_defaults(func=cmd_shot)

    sp = action("tap", cmd_tap, "нажать")
    sp.add_argument("index", nargs="?", type=int)
    sp.add_argument("--text")
    sp.add_argument("--desc")
    sp.add_argument("--id")
    sp.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))
    sp.add_argument("--pic", nargs=2, type=int, metavar=("X", "Y"),
                    help="координаты на последнем скриншоте")
    sp.add_argument("--confirm", action="store_true", help="разрешить опасную кнопку")

    sp = action("type", cmd_type, "ввести текст")
    sp.add_argument("text")
    sp.add_argument("--into", type=int, help="номер поля из `phone ui`")

    sp = action("scroll", cmd_scroll, "листать")
    sp.add_argument("direction", choices=["down", "up"])
    sp.add_argument("-n", type=int, default=1)

    sp = action("swipe", cmd_swipe, "свайп")
    sp.add_argument("direction", choices=["left", "right", "up", "down"])

    action("back", cmd_key("back"), "назад")
    action("home", cmd_key("home"), "домой")
    sub.add_parser("wake", help="включить экран").set_defaults(func=cmd_wake)

    sp = action("open", cmd_open, "запустить приложение")
    sp.add_argument("app", nargs="?", default="instagram")

    sp = action("url", cmd_url, "открыть ссылку")
    sp.add_argument("url")
    sp.add_argument("--app", help="пакет или instagram; по умолчанию Instagram для instagram.com")

    sp = sub.add_parser("adb", help="сырой adb к телефону")
    sp.add_argument("rest", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_adb)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    dev = Device()
    try:
        if args.cmd in ACTIONS:
            # Claude Code выставляет CLAUDECODE=1 в своих командах; Claude с
            # ноутбука ходит по ssh и называет себя сам (PHONE_SESSION=…).
            who = os.environ.get("PHONE_SESSION") or (
                "claude-server" if os.environ.get("CLAUDECODE") else "manual")
            claim_phone(who)
        if args.cmd != "adb":
            dev.connect()
        return args.func(dev, args) or 0
    except OwnerBusy as e:
        print(f"⛔ {e}", file=sys.stderr)
        return 2
    except DeviceUnavailable as e:
        print(f"📵 {e}", file=sys.stderr)
        return 3
    except ScreenChanged as e:
        print(f"⚠️ {e}", file=sys.stderr)
        return 4
    except DeviceError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("ANDROID_ADB_SERVER_PORT", "5137")
    sys.exit(main())
