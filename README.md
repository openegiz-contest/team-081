# baiterek: цифровой двойник карьера Актогай на OpenEgiz

Команда baiterek, конкурс «Цифровой рудник на OpenEgiz». Описание решения, Концепция, модули и what-if сценарии: [SUBMISSION.md](SUBMISSION.md).

## Запуск

Чистая Ubuntu 24.04 с Docker (Docker Engine, Compose v2.20+), 6 ГБ памяти для Docker. Из корня репозитория:

```bash
sudo apt-get update && sudo apt-get install -y make
make aktogay
```

`make aktogay` поднимает платформу OpenEgiz (`make up`: Ditto, Mosquitto, Telegraf, InfluxDB, Grafana), затем двойники Актогая, симулятор и 3D-стенд. Первый запуск скачивает около 2,3 ГБ образов и собирает два образа, это занимает 5–15 минут. В конце команда печатает адреса и логины.

Проверка всей цепочки, включая каждый what-if сценарий (необязательно, около 6 минут):

```bash
make aktogay-verify
```

## Что открыть

| Адрес | Что там |
|---|---|
| http://localhost:8070 | **3D-стенд**: карьер, дороги, забои, экскаваторы и самосвалы в движении по данным двойников, панель диспетчера, прогноз смены, кнопки what-if |
| http://localhost:3000/d/aktogay-haulage | Grafana: дашборд «Актогай — транспорт и диспетчеризация» (логин `admin`, пароль печатает `make aktogay`) |
| http://localhost:8080/api/2/search/things?namespaces=kz.aktogay | Двойники в Eclipse Ditto (логин `ditto`, пароль печатает `make aktogay`) |

Все библиотеки интерфейса лежат в репозитории (`team/aktogay/ui/vendor`), внешние ресурсы не нужны. Сцена работает без GPU.

## Команды

| Команда | Что делает |
|---|---|
| `make aktogay` | Платформа + двойник Актогая; после этого `make up` / `make down` запускают и останавливают его вместе с платформой |
| `make aktogay-verify` | Сквозная проверка: двойники, движение, история в InfluxDB, каждый what-if туда и обратно |
| `make aktogay-stop` | Остановить симулятор (двойники и данные остаются) |
| `make down` | Остановить всё, данные сохраняются |
| `make clean` | Остановить и **удалить** все данные и пароли |

Сценарии what-if сбрасываются при перезапуске `make aktogay`: двойники создаются заново с исходным состоянием.

## Содержимое репозитория

| Путь | Что там |
|---|---|
| `team/aktogay/sim/` | Модель карьера (`model.py`), физика самосвалов (`physics.py`), симуляция и диспетчер (`mine.py`), описание двойников (`twins.py`), шлюз MQTT ↔ Ditto (`simulator.py`), создание двойников (`setup_twins.py`) |
| `team/aktogay/ui/` | 3D-стенд (Three.js 0.160, MIT) и прокси к Ditto |
| `team/aktogay/grafana/aktogay.json` | Дашборд Grafana |
| `team/aktogay/compose.yml` | Подключение к платформе (overlay к `deploy/compose/`) |
| `team/aktogay/verify.py` | Сквозная проверка |
| `SUBMISSION.md`, `presentation.pdf` | Описание и презентация решения |
| `OPENEGIZ.md`, `OPENEGIZ.ru.md` | Документация платформы OpenEgiz v1.0.4 |
| `examples/` | Примеры платформы, в решении не используются |

## Лицензия

MIT, см. [LICENSE](LICENSE). Платформа: [aleka07/openegiz](https://github.com/aleka07/openegiz).
