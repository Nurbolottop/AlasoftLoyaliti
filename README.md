# AlaSoft — backend

Универсальная платформа лояльности: одно приложение и один постоянный QR клиента для всех подключённых организаций, при этом каждая организация ведёт независимую программу и не видит данные соседей.

**Стек:** Django 5.2 · DRF · PostgreSQL 14 · Redis 6 · Celery · Gunicorn · Docker Compose

> Реализация по ТЗ v1.0 (`AlaSoft_01_Общее_ТЗ`, `AlaSoft_03_ТЗ_Backend`). ТЗ предлагало PHP/Laravel/MySQL; проект собран на Django/PostgreSQL — все требования (ledger, идемпотентность, tenant isolation, очереди, scheduler, OpenAPI) закрыты один в один.

---

## Содержание

- [Быстрый старт](#быстрый-старт)
- [Что реализовано](#что-реализовано)
- [Архитектура домена](#архитектура-домена)
- [API](#api)
- [Роли и доступы](#роли-и-доступы)
- [Структура проекта](#структура-проекта)
- [Переменные окружения](#переменные-окружения)
- [Разработка](#разработка)
- [Очереди и планировщик](#очереди-и-планировщик)
- [Тесты](#тесты)
- [Деплой](#деплой)
- [Несколько проектов на одном сервере](#несколько-проектов-на-одном-сервере)
- [Типовые проблемы](#типовые-проблемы)
- [Что осталось за рамками MVP](#что-осталось-за-рамками-mvp)

---

## Быстрый старт

```bash
./scripts/init-project.sh alasoft     # сгенерирует .env: порты, SECRET_KEY, пароль БД
docker compose up -d --build
docker compose exec web python manage.py seed_demo
```

`seed_demo` создаёт директора, категории, две демо-организации (VISIT и CASHBACK), их администраторов и тестового клиента, и печатает все доступы.

| Точка | Адрес |
|---|---|
| API | `http://127.0.0.1:${WEB_PORT}/api/v1` |
| Swagger | `/api/docs/` |
| OpenAPI-схема | `/api/schema/` |
| Health | `/health` |
| Django-admin (служебный) | `/django-admin/` |

Директор для боевого стенда заводится отдельно:

```bash
docker compose exec web python manage.py create_director \
  --phone +996700000001 --password 'СильныйПароль' --staff
```

---

## Что реализовано

| Блок ТЗ | Статус |
|---|---|
| SMS-регистрация, PIN, ротация refresh, сессии устройств | ✅ |
| Постоянный QR + глобально уникальный 6-значный код | ✅ |
| Каталог организаций, «Мои карты», история ru/ky | ✅ |
| VISIT: N+1, подарки отдельными сущностями | ✅ |
| CASHBACK: ставка, лимит списания, лоты со сроками, FIFO | ✅ |
| Подтверждение списаний пользователем (RedemptionRequest) | ✅ |
| Отмены с компенсирующими транзакциями и причиной | ✅ |
| Идемпотентность критических POST | ✅ |
| RBAC + tenant isolation с серверной сверкой | ✅ |
| Audit log, статистика организации и платформы | ✅ |
| SMS/push через адаптеры, очереди, scheduler | ✅ |
| OpenAPI/Swagger, 88 тестов по критическим кейсам | ✅ |

---

## Архитектура домена

**Backend — единственный источник истины.** Ни одна сумма не приходит с клиента как готовая: `quote` считает сервер, лимиты проверяет сервер, организацию определяет токен администратора.

### Деньги

Все суммы — целые числа в **тыйынах** (1 сом = 100 тыйын), проценты — в **basis points** (500 bps = 5%). Float не используется нигде, округление всегда вниз.

### Ledger

`transactions` неизменяем: записи не удаляются и не правятся. Отмена создаёт связанную компенсирующую запись типа `REVERSAL`, а исходная переходит в статус `REVERSED`.

`user_organization_states` — агрегат для быстрого чтения. Историческим источником остаются `transactions` и `cashback_lots`; агрегат меняется только внутри транзакции с `SELECT FOR UPDATE`.

### VISIT

Каждое подтверждённое обслуживание — `+1` независимо от суммы. При достижении `target_visits` создаётся отдельный `Gift` со статусом `AVAILABLE`, прогресс уменьшается на порог (подарки накапливаются). Использование подарка проходит только через подтверждение клиента.

### CASHBACK

```
max_allowed = min(available_cashback, purchase_total × max_spend_percent_bps)
cash_paid   = purchase_total − spend
earn        = cash_paid × cashback_rate_bps        # только с денег, не с кэшбэка
```

Каждое начисление — отдельный **лот** со своим `expires_at`. Списание идёт FIFO по ближайшему сроку сгорания. Параметры программы снапшотятся в `metadata` транзакции, поэтому смена настроек не переписывает историю.

Пример из ТЗ: чек 1000 сом, списание 300 → `cash_paid` 700, начисление при 5% = 35 сом отдельным лотом.

### Подтверждение и конкурентность

`RedemptionRequest` переходит `PENDING → CONFIRMED` атомарным compare-and-set. Повторный confirm возвращает прежний результат без второго списания. Строки состояния и лотов блокируются `FOR UPDATE`, поэтому параллельные запросы не уводят баланс ниже нуля.

### Отмены

| Исходная операция | Правило |
|---|---|
| `VISIT_EARN` | прогресс компенсируется; связанный неиспользованный подарок аннулируется, использованный — блокирует отмену |
| `CASHBACK_EARN` | лот обнуляется; если часть уже потрачена — только директор с `force` |
| `CASHBACK_SPEND` | стоимость возвращается в исходные лоты с сохранением их сроков |
| `GIFT_REDEEM` | подарок возвращается в `AVAILABLE` |

Причина обязательна, повторная отмена запрещена, всё пишется в аудит.

---

## API

Полный справочник — [docs/API.md](docs/API.md), интерактивный — `/api/docs/`.

Единый конверт ответа:

```json
{ "success": true, "data": {...}, "meta": {...} }
{ "success": false, "error": { "code": "CASHBACK_LIMIT_EXCEEDED", "message": "...", "details": {...} } }
```

Идемпотентность: заголовок `Idempotency-Key` обязателен для начислений, списаний и запросов на подтверждение. Повтор с тем же телом отдаёт сохранённый ответ, с другим — `409 IDEMPOTENCY_CONFLICT`.

---

## Роли и доступы

| Роль | Вход | Скоуп |
|---|---|---|
| `USER` | телефон + SMS → PIN | свой профиль, каталог, подтверждения |
| `ORGANIZATION_ADMIN` | телефон + PIN | только своя организация |
| `DIRECTOR` | телефон + пароль | вся платформа |

Организация администратора берётся **из серверного состояния**, а не из тела запроса. При отзыве доступа или блокировке организации выданный токен перестаёт работать сразу — роль и привязка сверяются с БД на каждом запросе.

Панель директора — отдельное web-приложение поверх `/api/v1/director/*`; `/django-admin/` остаётся служебным инструментом эксплуатации.

---

## Структура проекта

```
app/
├── core/
│   ├── settings/{base,dev,prod,test}.py
│   ├── celery.py                    # очереди и beat-расписание
│   └── urls.py                      # /api/v1 + swagger
├── apps/
│   ├── common/                      # конверт ответа, ошибки, идемпотентность,
│   │   │                            # деньги, permissions, request-id, схема
│   │   └── management/commands/seed_demo.py
│   ├── users/                       # User, устройства, OTP, PIN, JWT
│   ├── organizations/               # организации, программы, категории, админы
│   ├── loyalty/
│   │   ├── models.py                # ledger, состояния, подарки, лоты, запросы
│   │   ├── services/                # ← вся бизнес-логика
│   │   │   ├── visits.py            # VISIT engine
│   │   │   ├── cashback.py          # quote / earn / FIFO / expiry
│   │   │   ├── redemptions.py       # подтверждения
│   │   │   ├── reversal.py          # отмены
│   │   │   ├── resolve.py           # QR и 6-значный код
│   │   │   └── statistics.py
│   │   ├── views_user.py / views_admin.py
│   │   └── tasks.py
│   ├── notifications/               # SMS/push адаптеры, шаблоны ru/ky, очереди
│   ├── audit/                       # журнал действий
│   ├── director/                    # API панели директора
│   └── tests/                       # 88 тестов
├── docker/Dockerfile
└── scripts/{entrypoint,init-project}.sh
```

Бизнес-логика живёт в `services/`, вьюхи только валидируют вход, проверяют доступ и оборачивают ответ.

---

## Переменные окружения

Шаблон со всеми переменными и пометками — [.envtest](.envtest). Ключевое:

| Переменная | Назначение |
|---|---|
| `COMPOSE_PROJECT_NAME` | префикс контейнеров, volume и сети |
| `SECRET_KEY`, `POSTGRES_PASSWORD` | секреты, уникальные на каждый стенд |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | домены (origins — со схемой) |
| `WEB_PORT` / `DB_PORT` / `REDIS_PORT` | порты на хосте |
| `SMS_PROVIDER` | `console` (dev) или `paysoft` |
| `PUSH_PROVIDER` | `console` (dev) или `fcm` |
| `OTP_DEBUG_RETURN_CODE` | **на проде строго `false`** — иначе код придёт в ответе API |
| `OTP_*`, `PIN_*`, `THROTTLE_*` | TTL, лимиты попыток, rate limiting |
| `REDEMPTION_TTL_SECONDS` | сколько живёт запрос на подтверждение |

Секреты только в `.env` / secret store — в репозиторий не попадают.

---

## Разработка

```bash
docker compose up -d --build        # поднять стек (web, worker, beat, db, redis)
docker compose logs -f web          # логи API
docker compose logs -f worker beat  # очереди и планировщик
docker compose down                 # остановить
```

Код примонтирован в контейнер, dev-сервер перезагружается сам. Пересборка нужна только при изменении `requirements.txt` или `Dockerfile`.

```bash
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web python manage.py shell
docker compose exec web python manage.py seed_demo
docker compose exec web python manage.py create_director --phone +996... --password '...'
```

**Миграции создаются локально и коммитятся.** На сервере `entrypoint.sh` выполняет только `migrate` — `makemigrations` там нет намеренно.

В dev `SMS_PROVIDER=console`: код подтверждения виден в логах воркера. Для ручных проверок можно временно включить `OTP_DEBUG_RETURN_CODE=true`, тогда код придёт в ответе `/auth/otp/request`.

---

## Очереди и планировщик

Celery-воркер и beat поднимаются вместе со стеком. Расписание — в [app/core/celery.py](app/core/celery.py):

| Задача | Периодичность | Что делает |
|---|---|---|
| `loyalty.expire_cashback_lots` | каждые 15 мин | сжигает просроченные лоты, пишет `CASHBACK_EXPIRE` |
| `loyalty.cleanup_expired_redemptions` | каждую минуту | `PENDING → EXPIRED`, возвращает подарок в `AVAILABLE` |
| `loyalty.notify_expiring_cashback` | ежедневно 09:00 UTC | предупреждение о скором сгорании |
| `users.cleanup_otp_challenges` | ежечасно | чистит OTP |
| `common.cleanup_idempotency_records` | ежедневно | чистит старые ключи идемпотентности |

Все джобы идемпотентны: повторный запуск не сжигает дважды. Есть и ручные аналоги:

```bash
docker compose exec web python manage.py expire_cashback_lots
docker compose exec web python manage.py cleanup_expired_redemptions
```

Ошибка отправки push/SMS не откатывает бизнес-транзакцию: задачи ставятся в очередь только после успешного commit.

---

## Тесты

```bash
docker compose exec web pytest          # 88 тестов
docker compose exec web pytest -k cashback -v
```

Покрыты критические кейсы ТЗ §31: порог VISIT, повтор idempotency-key, формула `5% / 1000 / spend 300`, превышение лимита 30%, double confirm, сгорание лота, доступ администратора к чужой организации, отмены всех типов, IDOR и брутфорс PIN.

Тесты используют `core.settings.test`: локальный кеш, синхронные задачи, отдельная БД.

---

## Деплой

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f web
```

| | dev | prod |
|---|---|---|
| Сервер | runserver | gunicorn, 3 воркера, timeout 120 |
| Настройки | `core.settings.dev` | `core.settings.prod` |
| Рестарт | нет | `restart: always` |
| Порты БД/Redis | проброшены | закрыты |
| Публикация web | `127.0.0.1:${WEB_PORT}` | `127.0.0.1` (только через nginx) |

### nginx на хосте

```nginx
server {
    listen 80;
    server_name example.com;
    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ { alias /path/to/project/app/staticfiles/; }
    location /media/  { alias /path/to/project/app/media/; }
}
```

`X-Forwarded-Proto` обязателен — на него опирается `SECURE_PROXY_SSL_HEADER`. HTTPS: `certbot --nginx -d example.com`.

### Обновление

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Миграции и `collectstatic` выполняет `entrypoint.sh` при старте.

### Бэкапы

```bash
# дамп
docker compose exec db pg_dump -U alasoft_user -d alasoft > backup_$(date +%F).sql
# восстановление
docker compose exec -T db psql -U alasoft_user -d alasoft < backup.sql
```

Ledger (`transactions`, `cashback_lots`, `audit_logs`) не очищается деплой-скриптами. Перед destructive-миграцией — дамп и план отката. Проверку восстановления стоит поставить на регулярную основу.

### Чек-лист перед продом

- `OTP_DEBUG_RETURN_CODE=false`
- `SMS_PROVIDER=paysoft` с заполненными кредами, `PUSH_PROVIDER=fcm` с service-account
- свои `SECRET_KEY` и пароль БД, домены в `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`
- HTTPS работает; после этого можно включить HSTS в `prod.py`
- `docker compose exec web python manage.py check --deploy`
- автоматический бэкап БД и проверка restore

---

## Несколько проектов на одном сервере

`COMPOSE_PROJECT_NAME` изолирует контейнеры, volume, сеть и образы автоматически. Вручную задаются только порты на хосте и секреты — ровно это и закрывает `init-project.sh`, который проверяет занятость имени и подбирает свободные порты, заглядывая и в `.env` соседних проектов.

```bash
docker ps -a --format 'table {{.Label "com.docker.compose.project"}}\t{{.Ports}}'
```

Занять одно имя дважды скрипт не даст: конфликт порта виден сразу по ошибке запуска, а два проекта с одним `COMPOSE_PROJECT_NAME` молча начали бы делить volume с данными.

---

## Типовые проблемы

**`port is already allocated`** — поменяй `WEB_PORT` / `DB_PORT` / `REDIS_PORT`. Кто занял: `lsof -i :8080`.

**`SECRET_KEY не задан`** — нет `.env`. Запусти `./scripts/init-project.sh alasoft` или `cp .envtest .env`.

**Не приходит SMS в dev** — так и задумано: `SMS_PROVIDER=console`, код в логах воркера (`docker compose logs worker`).

**Push не доставляется** — при `PUSH_PROVIDER=console` он только логируется. Для FCM нужны `FCM_PROJECT_ID`, `FCM_CREDENTIALS_FILE` и пакет `google-auth`.

**`FOR UPDATE cannot be applied to the nullable side of an outer join`** — блокировка строки вместе с `select_related` по nullable FK. Нужен `select_for_update(of=('self',))`.

**Задачи не выполняются** — не поднят `worker`/`beat`: `docker compose up -d worker beat`.

**Админ получает 401 после выдачи прав** — токен старый: доступ сверяется с БД, нужно перелогиниться.

**CSRF-ошибки на проде** — проверь `CSRF_TRUSTED_ORIGINS` (со схемой) и что nginx передаёт `X-Forwarded-Proto`.

---

## Что осталось за рамками MVP

По ТЗ §20 сознательно не входит: саморегистрация организаций, платные тарифы, акции и массовые рассылки, POS и онлайн-оплата, рефералы, филиальная модель.

Дополнительно стоит учесть при выходе в прод:

- **Мультиадминность.** Схема (`OrganizationAdmin`) уже поддерживает историю привязок, но ограничение «один активный админ на организацию» задано constraint'ом — снимается одной миграцией.
- **Статистика на больших объёмах.** Сейчас считается запросами по ledger; при росте нужна периодическая агрегация (задел — `AggregateStatistics` из ТЗ §26).
- **Мониторинг 5xx.** Логи структурированы и несут `request_id`, но внешний error-tracker (Sentry) не подключён.
- **FCM.** Адаптер написан и вызывается через очередь; для боевой отправки нужен service-account и `google-auth` в образе.
