#!/usr/bin/env bash
# Установка на VPS (Ubuntu / Debian). Запуск из папки проекта:
#
#     ./install.sh 100.x.y.z        # Tailscale-адрес телефона
#
# Всё ставится в эту папку и в ~/.local/bin текущего пользователя: sudo не
# нужен, системные пакеты не трогаются, другим проектам на сервере не мешаем.
# Запускать повторно можно: доставит недостающее и ничего не сломает.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
export PATH="$HOME/.local/bin:$PATH"

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ✅ %s\n' "$*"; }
warn() { printf '   ⚠️  %s\n' "$*"; }

PHONE="${1:-}"
if [ -n "$PHONE" ]; then
  case "$PHONE" in *:*) ;; *) PHONE="$PHONE:5555" ;; esac
  if ! printf '%s' "$PHONE" | grep -Eq '^100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]+$'; then
    echo "Адрес телефона должен выглядеть как 100.x.y.z — его показывает Tailscale (tailscale status)."
    exit 1
  fi
fi

say "1/5 uv — менеджер Python-окружений"
if command -v uv >/dev/null 2>&1; then
  ok "уже стоит: $(uv --version)"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  ok "поставлен: $(uv --version)"
fi

say "2/5 Python-окружение и библиотеки"
# uv сам скачает подходящий Python, если системного нет или он старый.
[ -x .venv/bin/python ] || uv venv .venv --python ">=3.10" -q
uv pip install --python .venv/bin/python -q -r requirements.txt
ok "uiautomator2 и adb на месте (adb идёт внутри библиотеки adbutils)"

say "3/5 команда phone"
chmod +x phone
mkdir -p "$HOME/.local/bin"
ln -sf "$HERE/phone" "$HOME/.local/bin/phone"
grep -qs '.local/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
ok "phone -> $HOME/.local/bin/phone"

say "4/5 Claude Code"
if command -v claude >/dev/null 2>&1; then
  ok "уже стоит: $(claude --version 2>/dev/null)"
elif curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1; then
  ok "поставлен: $(claude --version 2>/dev/null)"
else
  warn "не поставился автоматически — см. https://code.claude.com/docs/en/setup"
fi

say "5/5 адрес телефона"
if [ -n "$PHONE" ]; then
  printf '{\n  "device": {"address": "%s"}\n}\n' "$PHONE" > config.local.json
  ok "записан в config.local.json: $PHONE"
elif [ -f config.local.json ]; then
  ok "уже задан в config.local.json"
else
  warn "не задан. Запусти ещё раз с адресом: ./install.sh 100.x.y.z"
fi

say "проверки"
if .venv/bin/python -m pytest -q tests >/dev/null 2>&1; then
  ok "тесты прошли"
else
  warn "тесты упали — посмотри: .venv/bin/python -m pytest tests"
fi
if command -v tailscale >/dev/null 2>&1; then
  ok "Tailscale есть, адрес сервера: $(tailscale ip -4 2>/dev/null | head -1)"
else
  warn "Tailscale не установлен — это шаг 2 инструкции"
fi

cat <<EOF

Готово! Дальше:
  1. source ~/.bashrc      (или выйди и зайди на сервер заново)
  2. phone connect         (на телефоне нажми «Разрешить» и поставь галочку «Всегда»)
  3. cd $HERE && claude
EOF
