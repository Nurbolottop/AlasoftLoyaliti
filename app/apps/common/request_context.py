"""Что приложение сообщает о себе в заголовках (BACKEND_TZ, раздел 2).

- ``X-App: client | business`` — какое из двух мобильных приложений вызывает
  API. От него зависят роль в ``GET /me`` (раздел 5.2), лента уведомлений
  и устройство, на которое уходит push (раздел 9).
- ``Accept-Language: ru | ky`` — язык строк, которые видит человек.
"""

APP_CLIENT = 'client'
APP_BUSINESS = 'business'

SUPPORTED_LANGUAGES = ('ru', 'ky')


def request_app(request):
    """'client', 'business' или None (старые версии приложений заголовок не шлют)."""
    if request is None:
        return None
    value = (request.META.get('HTTP_X_APP') or '').strip().lower()
    return value if value in (APP_CLIENT, APP_BUSINESS) else None


def request_language(request, default='ru'):
    """Язык ответа: Accept-Language, иначе язык профиля, иначе русский."""
    if request is None:
        return default
    header = (request.META.get('HTTP_ACCEPT_LANGUAGE') or '').strip().lower()
    for part in header.split(','):
        code = part.split(';')[0].strip()[:2]
        if code in SUPPORTED_LANGUAGES:
            return code
    user = getattr(request, 'user', None)
    language = getattr(user, 'language', None) if user is not None else None
    return language if language in SUPPORTED_LANGUAGES else default


def notification_app_for(request, user=None):
    """Чья лента: по X-App, без заголовка — по сохранённой роли (как раньше)."""
    from apps.notifications.models import NotificationApp
    from apps.users.models import Role

    app = request_app(request)
    if app == APP_BUSINESS:
        return NotificationApp.BUSINESS
    if app == APP_CLIENT:
        return NotificationApp.CLIENT
    user = user or getattr(request, 'user', None)
    if getattr(user, 'role', None) == Role.ORGANIZATION_ADMIN:
        return NotificationApp.BUSINESS
    return NotificationApp.CLIENT
