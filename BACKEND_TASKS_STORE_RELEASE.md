# PAYDA — ТЗ бэкенду: что нужно для выпуска в Google Play и App Store

Приложение готово к публикации: пакет `com.zeahouse.payda`, версия 1.0.0,
подпись и сборка настроены, AAB собирается. Осталось три вещи, которые целиком
на стороне бэкенда — **без них магазины отклонят сборку**, это не пожелания.

| # | Задача | Что блокирует | Объём |
|---|--------|---------------|-------|
| 1 | Удаление аккаунта: API | Google Play (User Data policy), Apple Guideline 5.1.1(v) | ~день |
| 2 | Публичные страницы сайта | Оба магазина: политика конфиденциальности обязательна | ~2 часа |
| 3 | Тестовый номер для ревьюеров | Оба магазина: ревьюер не может войти | ~2 часа |

Проверено по исходникам `AlasoftLoyaliti/app`. Ссылки на файлы и строки — по
состоянию нашей копии репозитория; она могла отстать от задеплоенного.

---

## Общее

**База:** `https://alasoft.website/api/v1`

**Конверт ответа** навешивается автоматически (`apps/common/renderers.py`),
ниже показано только содержимое `data`:

```json
{ "success": true, "data": { ... } }
{ "success": false, "error": { "code": "OTP_INVALID", "message": "…", "details": { … } } }
```

**Коды ошибок** — из `apps/common/errors.py`. Новый код нужен ровно один
(`ACCOUNT_DELETE_PENDING`), остальное переиспользуем.

**Авторизация:** Bearer access-token, роль читается из БД.

---

## Часть 1. Удаление аккаунта — ТРЕБУЕТСЯ

Экран в приложении уже готов: Профиль → «О приложении» → «Удалить аккаунт»,
с подтверждением и предупреждением, что бонусы сгорают. Пока API нет,
подтверждение открывает письмо в поддержку, и удаление делается руками. Как
только эндпоинты появятся, переключим кнопку на прямой вызов — на нашей
стороне это полчаса работы.

### 1.1 Главное, что надо знать заранее: `user.delete()` упадёт

В четырёх моделях ссылка на пользователя стоит с `on_delete=models.PROTECT`:

| модель | файл |
|---|---|
| `Transaction` | `apps/loyalty/models.py:81` |
| `Gift` | `apps/loyalty/models.py:134` |
| `CashbackLot` | `apps/loyalty/models.py:182` |
| `RedemptionRequest` | `apps/loyalty/models.py:245` |

У любого пользователя, который хоть раз что-то купил, `user.delete()` выбросит
`ProtectedError`. И это правильно: `Transaction` — неизменяемый ledger, его
записи по вашему же комментарию «не удаляются и не правятся» (§11 ТЗ), а
финансовые записи всё равно нужно хранить для учёта.

**Поэтому удаление = обезличивание (anonymize), а не `DELETE FROM users`.**
Строка пользователя остаётся, но перестаёт быть персональными данными: из неё
вычищаются телефон, ФИО, фото и идентификаторы. Магазины такой вариант
принимают — Google прямо разрешает сохранять данные, которых требует
законодательство, если это описано в политике. У нас это описано:
`ala_soft/docs/PRIVACY_POLICY.md`, разделы 5 и 6.

### 1.2 Эндпоинты

Удаление делаем **в два шага с подтверждением по SMS**, как смену номера.
Причина простая: операция необратимая, а телефон бывает разблокированным в
чужих руках. Одной кнопки для такого мало.

**а) `POST /api/v1/me/delete/request`** — код уходит на текущий номер.

```json
// запрос — тела нет

// 200
{
  "challenge_id": "0d2f…",
  "expires_in": 300,
  "resend_after": 60
}
```

**б) `POST /api/v1/me/delete/confirm`**

```json
// запрос
{ "challenge_id": "0d2f…", "code": "1234" }

// 200
{ "deleted": true }
```

После этого ответа все токены пользователя недействительны. Приложение само
почистит локальное хранилище и уйдёт на экран входа.

Отдельная ручка отмены не нужна: между `request` и `confirm` ничего не
меняется, а незавершённый челлендж протухнет сам через 5 минут.

### 1.3 Что именно происходит при `confirm`

Одной транзакцией (`@transaction.atomic`):

**Обезличить `User`** — строка остаётся, персональных данных в ней нет:

| поле | новое значение | зачем |
|---|---|---|
| `phone` | `f'DELETED-{uuid4().hex[:12]}'` | ровно 20 символов, влезает в `max_length`; **освобождает реальный номер**, человек сможет зарегистрироваться заново |
| `first_name`, `last_name`, `email` | `''` | |
| `birth_date` | `None` | |
| `avatar` | `.delete(save=False)` | удалить файл с диска, не только ссылку |
| `public_code` | `None` | освобождает 6-значный код для повторной выдачи |
| `qr_token` | `None` | старый QR перестаёт резолвиться у кассиров |
| `pin_hash`, `pin_updated_at` | `''` / `None` | |
| `is_active` | `False` | **сразу вырубает все выданные access-токены** — `simplejwt` в `get_user()` проверяет `is_active` |
| `is_registration_complete` | `False` | |
| `deleted_at` | `timezone.now()` | новое поле, нужно для миграции и отчётности |

**Удалить связанное** (каскад отработает сам, но делайте явно — так виден
порядок и не остаётся мусора при частичном откате):

| таблица | действие |
|---|---|
| `UserDevice` | удалить все + отозвать refresh-токены (`token.blacklist()`) |
| `OtpChallenge` | удалить все, кроме текущего челленджа удаления |
| `Notification` | удалить все |
| `UserOrganizationState` | удалить все — **здесь и лежат балансы**, бонусы сгорают |

**Погасить, но не удалять** (PROTECT, удалить нельзя):

| таблица | действие |
|---|---|
| `CashbackLot` | `status=CANCELLED`, `remaining_amount=0` для всех со `status=ACTIVE` |
| `Gift` | `status=CANCELLED`, `cancelled_at=now()` для `AVAILABLE` и `PENDING_REDEMPTION` |
| `RedemptionRequest` | отменить все `PENDING` — иначе у кассира в организации навсегда зависнет запрос от несуществующего клиента |
| `Transaction` | **не трогать**, это ledger; записи остаются и указывают на обезличенную строку |

**Аудит:**

```python
AuditLog.objects.filter(actor_id=user.id).update(actor_phone='')
```

Телефон в аудите — это персональные данные, их надо вычистить. Сами записи
остаются: без них не расследовать спорную операцию.

И записать сам факт удаления новым действием `ACCOUNT_DELETED`
(`apps/audit/models.py:7`) — **без** телефона в `before`.

**Уведомление:** SMS на старый номер перед обезличиванием, что аккаунт удалён,
через `NotificationEvent.SECURITY_ALERT` — как уже сделано в
`confirm_phone_change` (`apps/users/services.py:517`). Отправлять до очистки,
иначе номер уже потерян.

### 1.4 Отдельный случай: администратор организации

Администратору организации удалять свой аккаунт через эту ручку **нельзя** —
иначе заведение остаётся без доступа к своей же программе лояльности, а
сотрудник может так «уволить» себя вместе с организацией.

```python
if request.user.role != Role.USER:
    raise PermissionDeniedError(message='Удаление доступно только клиентам')
```

В приложении кнопка администратору тоже не показывается — мы это закроем на
своей стороне, как только вы подтвердите, что решение такое же. Для магазинов
этого достаточно: требование удаления касается пользовательских аккаунтов, а
доступ администратора выдаёт и отзывает директор.

### 1.5 Ошибки

| код | HTTP | когда |
|---|---|---|
| `AUTHENTICATION_REQUIRED` | 401 | нет токена |
| `PERMISSION_DENIED` | 403 | роль не `USER` (см. 1.4) |
| `OTP_COOLDOWN` | 429 | повторный `request` до истечения `resend_after`, `details.retry_after` в секундах |
| `OTP_INVALID` | 404 / 422 | челлендж не найден, чужой, уже использован |
| `OTP_EXPIRED` | 422 | истёк |
| `OTP_TOO_MANY_ATTEMPTS` | 422 | исчерпаны попытки |
| `ACCOUNT_DELETE_PENDING` | 409 | **новый код**: есть незавершённые `RedemptionRequest` в статусе `PENDING` — см. ниже |

Про `ACCOUNT_DELETE_PENDING`: решите сами, нужен ли он. Вариант «просто отменить
все pending-запросы при удалении» проще и нам подходит — тогда код не нужен
вовсе. Вариант «не давать удаляться, пока кассир ждёт подтверждения списания»
честнее по отношению к заведению. Скажите, что выбрали, — приложение покажет
соответствующее сообщение.

### 1.6 Реализация

**1.** Новый purpose, `apps/users/models.py:210`:

```python
class OtpPurpose(models.TextChoices):
    REGISTER = 'REGISTER', 'Регистрация'
    LOGIN = 'LOGIN', 'Вход'
    PIN_RESET = 'PIN_RESET', 'Сброс PIN'
    PHONE_CHANGE = 'PHONE_CHANGE', 'Смена номера'
    ACCOUNT_DELETE = 'ACCOUNT_DELETE', 'Удаление аккаунта'   # новое
```

**2.** Поле в `User` + миграция:

```python
deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
```

**3.** Действие аудита, `apps/audit/models.py`:

```python
ACCOUNT_DELETED = 'ACCOUNT_DELETED', 'Аккаунт удалён'
```

**4.** Сервис, `apps/users/services.py` — рядом с блоком «смена номера».
`request_account_deletion` — это `request_phone_change`
(`apps/users/services.py:410`) с двумя отличиями: код уходит на **текущий**
номер и нет проверки «номер занят». Можно скопировать.

```python
# --- удаление аккаунта ------------------------------------------------------

def request_account_deletion(*, user, request=None):
    """Код уходит на ТЕКУЩИЙ номер. Аккаунт не трогаем до подтверждения."""
    if user.role != Role.USER:
        raise PermissionDeniedError(message='Удаление доступно только клиентам')

    now = timezone.now()
    recent = (
        OtpChallenge.objects.filter(user=user, purpose=OtpPurpose.ACCOUNT_DELETE)
        .order_by('-created_at').first()
    )
    if recent and (now - recent.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - recent.created_at).total_seconds())
        raise DomainError(
            code=ErrorCode.OTP_COOLDOWN, message='Повторная отправка возможна позже',
            status_code=429, details={'retry_after': max(wait, 1)},
        )

    OtpChallenge.objects.filter(
        user=user, purpose=OtpPurpose.ACCOUNT_DELETE, status=OtpStatus.PENDING,
    ).update(status=OtpStatus.EXPIRED)

    code = _generate_code()
    challenge = OtpChallenge(
        phone=user.phone,
        purpose=OtpPurpose.ACCOUNT_DELETE,
        user=user,
        status=OtpStatus.PENDING,
        max_attempts=settings.OTP_MAX_ATTEMPTS,
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        request_ip=_client_ip(request),
    )
    challenge.set_code(code)
    challenge.save()

    send_sms(user.phone, NotificationEvent.OTP, context={'code': code},
             language=user.language, user=user)

    result = {
        'challenge_id': str(challenge.id),
        'expires_in': settings.OTP_TTL_SECONDS,
        'resend_after': settings.OTP_RESEND_COOLDOWN_SECONDS,
    }
    if settings.OTP_DEBUG_RETURN_CODE:
        result['debug_code'] = code
    return result


@transaction.atomic
def confirm_account_deletion(*, user, challenge_id, code, request=None):
    """Обезличивает пользователя. Ledger не трогает — записи PROTECT."""
    challenge = (
        OtpChallenge.objects.select_for_update(of=('self',))
        .filter(pk=challenge_id, user=user, purpose=OtpPurpose.ACCOUNT_DELETE)
        .first()
    )
    if challenge is None:
        raise DomainError(code=ErrorCode.OTP_INVALID,
                          message='Запрос на удаление не найден', status_code=404)
    if challenge.status != OtpStatus.PENDING:
        raise DomainError(code=ErrorCode.OTP_INVALID, message='Код уже использован')
    if challenge.is_expired:
        challenge.status = OtpStatus.EXPIRED
        challenge.save(update_fields=['status', 'updated_at'])
        raise DomainError(code=ErrorCode.OTP_EXPIRED, message='Срок действия кода истёк')

    _validate_code(challenge, code)

    old_phone = user.phone
    now = timezone.now()

    # 1. Погасить всё, что нельзя удалить (PROTECT).
    CashbackLot.objects.filter(user=user, status=CashbackLotStatus.ACTIVE).update(
        status=CashbackLotStatus.CANCELLED, remaining_amount=0, updated_at=now,
    )
    Gift.objects.filter(
        user=user, status__in=[GiftStatus.AVAILABLE, GiftStatus.PENDING_REDEMPTION],
    ).update(status=GiftStatus.CANCELLED, cancelled_at=now, updated_at=now)
    RedemptionRequest.objects.filter(
        user=user, status=RedemptionStatus.PENDING,
    ).update(status=RedemptionStatus.CANCELLED, updated_at=now)

    # 2. Удалить то, что можно.
    UserOrganizationState.objects.filter(user=user).delete()   # здесь балансы
    Notification.objects.filter(user=user).delete()
    UserDevice.objects.filter(user=user).delete()
    OtpChallenge.objects.filter(user=user).exclude(pk=challenge.pk).delete()

    # 3. Уведомить, пока номер ещё известен.
    send_sms(old_phone, NotificationEvent.SECURITY_ALERT,
             context={'subject': 'аккаунт PAYDA удалён'},
             language=user.language, user=None)

    # 4. Обезличить.
    if user.avatar:
        user.avatar.delete(save=False)
    user.phone = f'DELETED-{uuid.uuid4().hex[:12]}'   # ровно 20 символов
    user.first_name = ''
    user.last_name = ''
    user.email = ''
    user.birth_date = None
    user.public_code = None
    user.qr_token = None
    user.pin_hash = ''
    user.pin_updated_at = None
    user.is_active = False
    user.is_registration_complete = False
    user.deleted_at = now
    user.save()

    # 5. Аудит: факт фиксируем, телефон вычищаем.
    log_action(AuditAction.ACCOUNT_DELETED, actor=user, request=request,
               entity_type='User', entity_id=user.id)
    AuditLog.objects.filter(actor_id=user.id).update(actor_phone='')

    challenge.delete()
    return True
```

> `send_sms(..., user=None)` в пункте 3 намеренно: `Notification` мы только что
> удалили, и запись о новой отправке снова привязала бы её к удаляемому
> пользователю.

**5.** Вьюхи, `apps/users/views.py` — по образцу `MePhoneChangeRequestView`:

```python
@extend_schema(tags=['user'], request=None, responses=OpenApiTypes.OBJECT)
class MeDeleteRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(services.request_account_deletion(user=request.user, request=request))


@extend_schema(tags=['user'], request=AccountDeleteConfirmSerializer, responses=OpenApiTypes.OBJECT)
class MeDeleteConfirmView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AccountDeleteConfirmSerializer

    def post(self, request):
        data = validated(AccountDeleteConfirmSerializer, request)
        services.confirm_account_deletion(
            user=request.user, challenge_id=data['challenge_id'],
            code=data['code'], request=request,
        )
        return Response({'deleted': True})
```

`AccountDeleteConfirmSerializer` — копия `PhoneChangeConfirmSerializer`:
`challenge_id` (UUID) и `code` (строка).

**6.** URL, в `me_urlpatterns` (`apps/users/urls.py`):

```python
path('me/delete/request', views.MeDeleteRequestView.as_view(), name='me-delete-request'),
path('me/delete/confirm', views.MeDeleteConfirmView.as_view(), name='me-delete-confirm'),
```

### 1.7 Чек-лист приёмки

- [ ] `POST /me/delete/request` под токеном клиента → 200, SMS с кодом пришла
- [ ] повторный `request` сразу → 429 `OTP_COOLDOWN` с `details.retry_after`
- [ ] `POST /me/delete/confirm` с верным кодом → 200 `{"deleted": true}`
- [ ] сразу после этого `GET /me` со старым access-токеном → 401
- [ ] **у пользователя с транзакциями** удаление проходит, `ProtectedError` нет
- [ ] `SELECT * FROM transactions WHERE user_id = …` — записи на месте
- [ ] `SELECT phone, first_name, public_code FROM users WHERE id = …` —
      `DELETED-…`, пусто, `NULL`
- [ ] по старому 6-значному коду и QR `resolve-code` / `resolve-qr` отвечают
      `USER_NOT_FOUND`
- [ ] на освободившийся номер можно зарегистрироваться заново, аккаунт чистый
- [ ] у кассира в `/admin/redemptions` нет зависших `PENDING` от удалённого
- [ ] `POST /me/delete/request` под токеном `ORGANIZATION_ADMIN` → 403
- [ ] `SELECT actor_phone FROM audit_logs WHERE actor_id = …` — пусто

---

## Часть 2. Публичные страницы сайта — ТРЕБУЕТСЯ

Оба магазина требуют URL политики конфиденциальности, а Google Play —
отдельным полем ещё и URL страницы удаления аккаунта. Сейчас на
`alasoft.website` публичных страниц нет вообще: в `core/urls.py` только
`/health`, `/api/v1/`, схема и `/django-admin/`. `app/templates/homepage.html`
существует, но его никакой view не рендерит.

### Три URL

| URL | Готовый текст |
|---|---|
| `https://alasoft.website/privacy` | `ala_soft/docs/PRIVACY_POLICY.md` |
| `https://alasoft.website/account-delete` | `ala_soft/docs/ACCOUNT_DELETION.md` |
| `https://alasoft.website/terms` | нужно написать (типовое пользовательское соглашение) |

Тексты первых двух написаны и лежат в репозитории приложения — их надо
свёрстать в HTML, содержание менять не нужно. В политике заполните
плейсхолдеры `[ЮРЛИЦО]`, `[АДРЕС]`, `[ИНН]`, `[EMAIL]`, `[ТЕЛЕФОН]`: магазины
требуют, чтобы оператор персональных данных был назван конкретно.

### Требования магазинов к этим страницам

Их проверяет живой человек, и придирается он к следующему:

1. **Открываются без авторизации и без редиректа на логин.** Ревьюер не будет
   регистрироваться. Форма входа на этой странице = отказ.
2. **Отдают 200, а не 404/403/500**, и работают на момент проверки — ревью
   может прийти через неделю после отправки.
3. **Не закрыты от индексации и не за Basic Auth.** Если сайт целиком под
   `auth_basic` в nginx — эти три пути надо вынести из-под него.
4. **Страница удаления описывает, какие данные удаляются, какие остаются и
   почему, и в какой срок.** В `ACCOUNT_DELETION.md` эти разделы уже есть, не
   вырезайте их — именно их читает ревьюер Google.
5. **HTTPS.**

### Как сделать

Шаблоны уже подключены: `TEMPLATES.DIRS = [BASE_DIR / 'templates']`
(`core/settings/base.py:98`), в каталоге лежат `header.html` и `footer.html`.
Нужны три шаблона и три строки в `urls.py`.

Все три страницы — статика, никаких форм и обработчиков. `/account-delete`
только **объясняет, как удалить аккаунт в приложении**; самого удаления через
сайт нет и не планируется.

`app/core/urls.py`:

```python
from django.views.generic import TemplateView

urlpatterns = [
    path('health', healthcheck, name='health'),

    # Публичные страницы. Требование Google Play и App Store: доступны
    # без авторизации, иначе ревьюер магазина их не увидит.
    path('privacy', TemplateView.as_view(template_name='legal/privacy.html'), name='privacy'),
    path('terms', TemplateView.as_view(template_name='legal/terms.html'), name='terms'),
    path('account-delete', TemplateView.as_view(template_name='legal/account-delete.html'), name='account-delete'),

    path('api/v1/', include((api_v1, 'v1'))),
    …
]
```

Готовая вёрстка обеих страниц лежит в `site/` — файлы самодостаточные, можно
класть в `app/templates/legal/` как есть.

### Одна вещь для поддержки

На странице есть абзац «если войти не получается — напишите на support@».
Он нужен для тех, кто потерял симку или сменил номер: сами они кнопку в
приложении нажать не смогут. Таких обращений будут единицы, но обрабатывать
их чем-то надо.

Проще всего — действие в `django-admin`: в `UserAdmin` добавьте пункт
«Удалить аккаунт (обезличить)», вызывающий тело `confirm_account_deletion`
без OTP-шага (код в него уже вводить не нужно — владельца проверяет
поддержка). Отдельного веб-флоу с формой и SMS не требуется.

---

## Часть 3. Тестовый номер для ревьюеров магазинов — ТРЕБУЕТСЯ

Вход в PAYDA возможен только по SMS-коду на реальный номер. Ревьюеры Apple и
Google находятся в США, кыргызскую симку в руки не возьмут и SMS не получат.
Apple отклоняет такое по Guideline 2.1 практически всегда, формулировка —
«we were unable to sign in with the provided credentials».

Механизма тестового номера сейчас нет. `OTP_DEBUG_RETURN_CODE`
(`core/settings/base.py:285`) не подходит: он глобальный и возвращает код в
ответе API **для всех номеров** — на проде это дыра, ваш же README это
подчёркивает.

### Что нужно

Белый список номеров с фиксированным кодом, работающий **только** для номеров
из списка. Всё остальное поведение не меняется.

`core/settings/base.py`:

```python
# Номера для ревью в магазинах: SMS не отправляется, код фиксированный.
# Формат: "+996700000000:0000,+996700000001:1111"
OTP_TEST_ACCOUNTS = dict(
    pair.split(':', 1)
    for pair in os.getenv('OTP_TEST_ACCOUNTS', '').split(',')
    if ':' in pair
)
```

В `request_otp` (`apps/users/services.py:40`) — вместо генерации и отправки:

```python
test_code = settings.OTP_TEST_ACCOUNTS.get(phone)
code = test_code or _generate_code()
…
if test_code is None:
    send_sms(phone, NotificationEvent.OTP, context={'code': code}, …)
```

То же самое в `otp_login` и везде, где отправляется код для входа.

### Требования к этому механизму

- Список задаётся **переменной окружения**, не в коде и не в БД.
- Пустая переменная = механизм выключен. По умолчанию он выключен.
- Никогда не возвращать код в ответе API — только фиксировать его на сервере.
- В логах и аудите такие входы должны быть отличимы: заведите
  `AuditAction.LOGIN` с `reason='test_account'` или отдельный флаг.
- Номер лучше взять из диапазона, который вам физически не принадлежит, чтобы
  он никогда не совпал с настоящим клиентом. Например `+996 700 000 000`.
- Аккаунт заранее наполните: пара организаций, немного бонусов, история из
  нескольких операций. Пустой экран ревьюер трактует как «приложение не
  работает» — это отдельная причина отказа (Apple Guideline 2.1, «app
  incomplete»).

### Что нужно от вас нам

Пришлите номер и код — мы впишем их в Play Console («Доступ к приложению») и в
App Store Connect («Sign-in required» → демо-аккаунт). **Без этого сборку
отправлять на проверку бессмысленно.**

Хорошо бы ещё второй такой аккаунт с ролью `ORGANIZATION_ADMIN`: в приложении
есть сканер QR и админский раздел, и ревьюер Apple обычно проверяет, что
запрошенное разрешение камеры действительно используется. Без доступа к
админской части он видит приложение, которое просит камеру непонятно зачем.

---

## Порядок, в котором это полезнее всего делать

1. **Часть 3** — тестовый номер. Два часа работы, а без него нельзя даже
   отправить сборку на проверку.
2. **Часть 2** — страницы сайта. Тексты готовы, нужна вёрстка и роуты.
3. **Часть 1** — API удаления. Самое трудоёмкое. Формально выпуститься можно
   и без него: кнопка в приложении есть, подтверждение открывает письмо
   в поддержку, а удаление делается руками через `django-admin`. Но это
   работает только пока пользователей мало.

Клиентскую часть по каждому пункту сделаем в тот же день, как появится на
`alasoft.website`. Скажите, когда задеплоите.
