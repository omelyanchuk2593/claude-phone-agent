#!/usr/bin/env bash
# Включить ADB по сети на телефоне. Запускать на КОМПЬЮТЕРЕ с macOS или Linux,
# телефон подключён USB-кабелем, «Отладка по USB» включена.
#
#     bash usb-tcpip.sh
#
# Если adb ещё нет, скрипт сам скачает официальные platform-tools от Google
# в ~/platform-tools. Повторять после каждой перезагрузки телефона.
set -euo pipefail

ADB="$(command -v adb || true)"
if [ -z "$ADB" ]; then
  case "$(uname -s)" in
    Darwin) os=darwin ;;
    Linux) os=linux ;;
    *) echo "Неизвестная система: $(uname -s)"; exit 1 ;;
  esac
  ADB="$HOME/platform-tools/adb"
  if [ ! -x "$ADB" ]; then
    echo "Скачиваю platform-tools (adb) с сайта Google..."
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/pt.zip" "https://dl.google.com/android/repository/platform-tools-latest-$os.zip"
    unzip -q -o "$tmp/pt.zip" -d "$HOME"
    rm -rf "$tmp"
  fi
fi

"$ADB" start-server >/dev/null 2>&1 || true
echo "Жду телефон по USB. Если на экране телефона появился вопрос «Разрешить отладку по USB?» —"
echo "поставь галочку «Всегда разрешать с этого компьютера» и нажми «Разрешить»."

state=""
for _ in $(seq 1 60); do
  state="$("$ADB" -d get-state 2>/dev/null || true)"
  [ "$state" = "device" ] && break
  sleep 2
done
if [ "$state" != "device" ]; then
  echo
  echo "Телефон так и не появился. Проверь кабель (он должен передавать данные),"
  echo "«Отладку по USB» на телефоне и вопрос «Разрешить отладку?» на экране."
  "$ADB" devices
  exit 1
fi

echo "Телефон найден: $("$ADB" -d shell getprop ro.product.model | tr -d '\r')"
"$ADB" -d tcpip 5555
sleep 2
echo
echo "Готово! ADB по сети включён на порту 5555. Кабель можно отключать."
echo "Дальше — на сервере: phone connect"
