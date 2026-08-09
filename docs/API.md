# AlaSoft API v1

Базовый префикс: `/api/v1`. Интерактивная документация — `/api/docs/`, машинная схема — `/api/schema/`.

## Конверт ответа

Успех:

```json
{ "success": true, "data": { ... }, "meta": { "pagination": { ... } } }
```

Ошибка:

```json
{ "success": false, "error": { "code": "CASHBACK_LIMIT_EXCEEDED", "message": "...", "details": { ... } } }
```

`code` стабилен и предназначен для машинной обработки, `message` — для показа пользователю.

### Коды ошибок

| Код | Когда |
|---|---|
| `OTP_INVALID` / `OTP_EXPIRED` / `OTP_TOO_MANY_ATTEMPTS` / `OTP_COOLDOWN` | SMS-подтверждение |
| `OTP_VERIFICATION_REQUIRED` | нет действительного verification_token |
| `PIN_INVALID` / `PIN_LOCKED` / `INVALID_CREDENTIALS` | вход |
| `USER_NOT_FOUND` / `USER_BLOCKED` / `USER_ALREADY_EXISTS` | клиент |
| `ORGANIZATION_BLOCKED` / `ORGANIZATION_NOT_FOUND` | организация |
| `LOYALTY_TYPE_MISMATCH` / `LOYALTY_TYPE_LOCKED` / `PROGRAM_INACTIVE` | программа лояльности |
| `CASHBACK_LIMIT_EXCEEDED` / `INSUFFICIENT_CASHBACK` / `INVALID_AMOUNT` | cashback |
| `GIFT_NOT_AVAILABLE` / `GIFT_NOT_FOUND` | подарки |
| `REDEMPTION_EXPIRED` / `REDEMPTION_NOT_PENDING` / `REDEMPTION_NOT_FOUND` | подтверждение списания |
| `OPERATION_NOT_REVERSIBLE` / `TRANSACTION_NOT_FOUND` | отмена |
| `IDEMPOTENCY_CONFLICT` / `IDEMPOTENCY_KEY_REQUIRED` | идемпотентность |
| `PERMISSION_DENIED` / `AUTHENTICATION_REQUIRED` / `TOKEN_INVALID` / `RATE_LIMITED` | доступ |

## Деньги и проценты

Все суммы — **целые числа в тыйынах** (1 сом = 100 тыйын), проценты — в **basis points** (500 bps = 5%). Float не используется нигде. Время — UTC.

## Идемпотентность

Заголовок `Idempotency-Key` **обязателен** для: `POST /admin/visits`, `/admin/cashback/earn`, `/admin/cashback/redeem-request`, `/admin/gifts/{id}/redeem-request`. Опционален для отмен.

- повтор с тем же ключом и телом → тот же ответ, операция не повторяется;
- тот же ключ с другим телом → `409 IDEMPOTENCY_CONFLICT`;
- ключ уникален в разрезе актора.

## Роли и скоупы

| Роль | Как входит | Что доступно |
|---|---|---|
| `USER` | телефон + SMS → PIN | `/me/*`, каталог, подтверждение списаний |
| `ORGANIZATION_ADMIN` | телефон + PIN | `/admin/*` только своей организации |
| `DIRECTOR` | телефон + пароль | `/director/*`, вся платформа |

`organization_id` из тела запроса **никогда** не используется: организация администратора берётся из серверного состояния.

---

## Auth — `/api/v1/auth`

| Метод | Endpoint | Описание |
|---|---|---|
| POST | `/otp/request` | `{phone, purpose: REGISTER\|LOGIN\|PIN_RESET}` → `challenge_id` |
| POST | `/otp/verify` | `{challenge_id, phone, code}` → `verification_token` |
| POST | `/register/complete` | `{phone, verification_token, pin, first_name, device}` → профиль + токены |
| POST | `/pin/login` | `{phone, pin, device}` → токены |
| POST | `/pin/reset` | `{phone, verification_token, pin}` — только после OTP |
| POST | `/director/login` | `{phone, password}` |
| POST | `/refresh` | `{refresh}` → новая пара, старый refresh гасится |
| POST | `/logout` | `{refresh, device_id}` |

## USER — `/api/v1`

| Метод | Endpoint | Описание |
|---|---|---|
| GET | `/home` | сводка главного экрана |
| GET/PATCH | `/me` | профиль |
| GET/POST | `/me/qr` | постоянный QR и 6-значный код; POST — ротация |
| GET/POST | `/me/devices` | устройства и FCM-токены |
| GET | `/organizations` | каталог активных организаций (`?search=`, `?category=`, `?loyalty_type=`) |
| GET | `/organizations/{id}` | карточка организации |
| GET | `/categories` | категории |
| GET | `/me/loyalty` | «Мои карты» |
| GET | `/me/transactions` | история (`?organization_id=`, `?type=`) |
| GET | `/me/gifts` | подарки |
| GET | `/me/cashback` | балансы и лоты со сроками сгорания |
| GET | `/me/redemptions/pending` | запросы, ждущие подтверждения |
| POST | `/me/redemptions/{id}/confirm` | подтвердить списание |
| POST | `/me/redemptions/{id}/reject` | отклонить |

## ORGANIZATION_ADMIN — `/api/v1/admin`

| Метод | Endpoint | Описание |
|---|---|---|
| GET | `/dashboard` | сводка организации |
| POST | `/customers/resolve-qr` | `{qr}` → клиент + его состояние в этой организации |
| POST | `/customers/resolve-code` | `{code}` — 6 цифр |
| GET | `/customers` | клиенты организации |
| GET | `/customers/{id}/state` | состояние клиента |
| POST | `/visits` | `{user_id}` → +1 посещение, при пороге создаётся подарок |
| POST | `/gifts/{id}/redeem-request` | `{user_id}` → запрос подтверждения у клиента |
| POST | `/cashback/quote` | `{user_id, purchase_total, spend_amount}` — расчёт без изменения состояния |
| POST | `/cashback/earn` | покупка без списания: начисление сразу |
| POST | `/cashback/redeem-request` | покупка со списанием → ждёт подтверждения клиента |
| GET | `/redemptions` | запросы организации |
| POST | `/redemptions/{id}/cancel` | отменить свой запрос |
| GET | `/transactions` | ledger организации |
| POST | `/transactions/{id}/reverse` | `{reason}` — причина обязательна |
| GET | `/statistics` | статистика организации |

## DIRECTOR — `/api/v1/director`

| Метод | Endpoint | Описание |
|---|---|---|
| GET | `/dashboard` | сводка платформы |
| GET/POST | `/organizations` | список / создание с настройками программы |
| GET/PATCH | `/organizations/{id}` | карточка и настройки |
| POST | `/organizations/{id}/block` · `/unblock` | блокировка |
| POST/DELETE | `/organizations/{id}/admin` | создать/сбросить доступ администратора |
| GET/POST | `/categories` | категории каталога |
| GET | `/users` | поиск по телефону, имени, ID, 6-значному коду |
| GET | `/users/{id}` | клиент и его балансы по организациям |
| GET | `/transactions` | глобальный ledger |
| POST | `/transactions/{id}/reverse` | `{reason, force}` — `force` для эскалированных случаев |
| GET | `/statistics` | платформа или `?organization_id=` |
| GET | `/audit` | журнал аудита |

---

## Ключевые сценарии

### VISIT: посещение и подарок

```
POST /admin/customers/resolve-code  {"code":"366689"}
POST /admin/visits                  {"user_id":"..."}      Idempotency-Key: <uuid>
  → при достижении target: gifts_created[]
POST /admin/gifts/{id}/redeem-request {"user_id":"..."}     Idempotency-Key: <uuid>
  → подарок переходит в PENDING_REDEMPTION
POST /me/redemptions/{id}/confirm   (со стороны клиента)
  → GIFT_REDEEM, подарок USED
```

### CASHBACK: чек 1000 сом со списанием 300

```
POST /admin/cashback/quote {"user_id":"...","purchase_total":100000,"spend_amount":30000}
  → {"cash_paid":70000,"earn_amount":3500,"max_allowed_spend":30000}

POST /admin/cashback/redeem-request {...}   Idempotency-Key: <uuid>
  → PENDING, баланс ещё не тронут

POST /me/redemptions/{id}/confirm
  → списание FIFO по ближайшему expires_at + начисление 3500 новым лотом
```

Покупка без списания идёт мимо подтверждения: `POST /admin/cashback/earn`.

### Отмена

```
POST /admin/transactions/{id}/reverse {"reason":"ошибка кассира"}
```

Исходная запись остаётся в ledger со статусом `REVERSED`, создаётся связанная компенсирующая транзакция типа `REVERSAL`. Повторная отмена запрещена. Частично потраченное начисление админ отменить не может — только директор с `force: true`.
