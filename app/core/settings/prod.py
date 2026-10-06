from core.settings.base import *

DEBUG = False

# Cookies только по HTTPS
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Приложение работает за nginx, который терминирует HTTPS.
# Без этого Django считает запрос http-запросом и ломает CSRF/редиректы.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
# Ровно один nginx: реальный IP клиента — последний адрес в X-Forwarded-For,
# всё левее него клиент мог подставить сам.
REST_FRAMEWORK['NUM_PROXIES'] = int(os.getenv('NUM_PROXIES', 1))

# Прочие security-заголовки
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# HSTS: HTTPS на проде настроен (certbot), браузер больше не пойдёт по http.
# Поддомены не включаем — на них могут жить чужие сервисы без сертификата.
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', 31536000))
# Редирект http→https делает nginx (W008). Поддомены и preload-список
# не включаем осознанно (W005, W021): preload почти не откатывается.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W008', 'security.W021']

# =============================================================================
# LOGGING — при DEBUG=False ошибки иначе уходят в никуда
# =============================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
