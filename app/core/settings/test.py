from core.settings.base import *  # noqa: F403

DEBUG = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
ALLOWED_HOSTS = ['*']

# Быстрый хешер: Argon2 покрыт отдельными проверками, в тестах он лишний.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'alasoft-tests',
    }
}

# Задачи выполняются синхронно, брокер не нужен.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

SMS_PROVIDER = 'console'
PUSH_PROVIDER = 'console'
OTP_DEBUG_RETURN_CODE = True
OTP_RESEND_COOLDOWN_SECONDS = 0

LOGGING['root']['level'] = 'ERROR'  # noqa: F405
