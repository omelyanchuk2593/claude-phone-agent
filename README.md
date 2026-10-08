# claude-phone-agent

![Claude управляет телефоном](https://raw.githubusercontent.com/omelyanchuk2593/claude-phone-agent/main/docs/img/ill-cover.png)

**Claude Code на твоём VPS управляет твоим Android-телефоном** — смотрит на экран, нажимает
кнопки, пишет текст. Например, ведёт Instagram: «открой директ и покажи, кто писал»,
«ответь Маше, что созвон в 15:00».

Телефон может быть где угодно: дома на зарядке или у тебя в кармане. Связь идёт через
[Tailscale](https://tailscale.com) — личную зашифрованную сеть между твоими устройствами.

![Как это устроено](https://raw.githubusercontent.com/omelyanchuk2593/claude-phone-agent/main/docs/img/diagram-scheme.png)

## ✨ Что это даёт

Всё, что человек делает на телефоне пальцами, агент может сделать сам — **по команде или на
автопилоте**: писать первым и отвечать в директ, выкладывать контент, прогревать аккаунт, искать
клиентов, разбирать конкурентов. И не только в Instagram, а в любом приложении.

![Два режима](https://raw.githubusercontent.com/omelyanchuk2593/claude-phone-agent/main/docs/img/diagram-modes.png)

![Что можно делать](https://raw.githubusercontent.com/omelyanchuk2593/claude-phone-agent/main/docs/img/diagram-possibilities.png)

## 📖 Пошаговая инструкция

**[docs/GUIDE.md](docs/GUIDE.md)** — с нуля, со скриншотами, понятно даже школьнику. Около 40 минут.

## Коротко, если ты уже всё умеешь

1. Tailscale на сервере и на телефоне, один аккаунт.
2. На телефоне: режим разработчика → «Отладка по USB».
3. Телефон кабелем к компьютеру: `setup/usb-tcpip.ps1` (Windows) или `setup/usb-tcpip.sh`
   (macOS/Linux) — включает ADB по сети на порту 5555.
4. На сервере:
   ```bash
   git clone https://github.com/omelyanchuk2593/claude-phone-agent.git
   cd claude-phone-agent
   ./install.sh 100.x.y.z     # Tailscale-адрес телефона
   source ~/.bashrc
   phone connect              # на телефоне: «Разрешить»
   claude
   ```

## Команда `phone`

| Команда | Что делает |
|---|---|
| `phone status` | экран, замок, сколько не трогали, что открыто, батарея |
| `phone ui` | список элементов экрана с номерами `[N]` |
| `phone tap N -u` | нажать элемент и показать новый экран |
| `phone tap --text "…"` | нажать по тексту (`--desc`, `--id`, `--xy X Y`, `--pic X Y`) |
| `phone type "текст"` | ввести текст (кириллица работает) |
| `phone scroll down -n 3` | листать |
| `phone back` / `home` / `wake` | кнопки |
| `phone open instagram` | запустить приложение |
| `phone url https://instagram.com/ник` | открыть ссылку |
| `phone shot` | скриншот |

## Защита

- **Телефон личный.** Если владелец держит его в руках (касание меньше 45 с назад) или
  телефон заблокирован, действие отменяется. PIN бот не вводит никогда.
- **Экран мог измениться.** `tap N` сверяет элемент со свежим экраном и не жмёт вслепую.
- **Опасные кнопки** (Unfollow, Block, Delete, Удалить…) — только с `--confirm`.
- **Одна сессия за раз.** Если телефоном управляет один Claude, второй получит отказ.
- Свой adb-сервер на порту 5137: не мешает другим проектам на сервере.

## ⚠️ Ответственность

Автоматизация действий в Instagram может нарушать правила площадки. Не делай массовых
рассылок и подписок: аккаунт могут ограничить. Используй на свой страх и риск.

## Лицензия

MIT
