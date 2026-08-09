from datetime import timedelta
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# PATHS (ПУТИ)
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# =============================================================================
# SECURITY (БЕЗОПАСНОСТЬ)
# =============================================================================
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise Exception("SECRET_KEY не задан в переменных окружения")

_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '').strip()
ALLOWED_HOSTS = [host.strip() for host in _allowed_hosts_env.split(',') if host.strip()]

_csrf_trusted_origins_env = os.getenv('CSRF_TRUSTED_ORIGINS', '').strip()
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in _csrf_trusted_origins_env.split(',') if origin.strip()
]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Argon2id — основной хешер для PIN и паролей директоров (ТЗ backend §5, §24).
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]

# =============================================================================
# APPLICATIONS (ПРИЛОЖЕНИЯ)
# =============================================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    'drf_spectacular',
    'django_celery_beat',

    # Local apps
    'apps.common',
    'apps.users',
    'apps.organizations',
    'apps.loyalty',
    'apps.notifications',
    'apps.audit',
    'apps.director',
]

# =============================================================================
# MIDDLEWARE (ПРОМЕЖУТОЧНЫЕ ОБРАБОТЧИКИ)
# =============================================================================

MIDDLEWARE = [
    'apps.common.middleware.RequestIDMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# =============================================================================
# URLS & WSGI (МАРШРУТЫ И WSGI)
# =============================================================================

ROOT_URLCONF = 'core.urls'
WSGI_APPLICATION = 'core.wsgi.application'

# =============================================================================
# TEMPLATES (ШАБЛОНЫ)
# =============================================================================

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# =============================================================================
# DATABASE (БАЗА ДАННЫХ)
# =============================================================================

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
        'HOST': os.getenv('POSTGRES_HOST'),
        'PORT': int(os.getenv('POSTGRES_PORT', 5432)),
    }
}

AUTH_USER_MODEL = 'users.User'

# =============================================================================
# CACHE / REDIS
# =============================================================================

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'IGNORE_EXCEPTIONS': True,
        },
        'KEY_PREFIX': os.getenv('COMPOSE_PROJECT_NAME', 'alasoft'),
    }
}

# =============================================================================
# CELERY (ОЧЕРЕДИ И SCHEDULER)
# =============================================================================

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', REDIS_URL)
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'UTC'
CELERY_ENABLE_UTC = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_DEFAULT_QUEUE = 'default'
# В dev/тестах задачи выполняются синхронно, если брокера нет.
CELERY_TASK_ALWAYS_EAGER = os.getenv('CELERY_TASK_ALWAYS_EAGER', 'false').lower() == 'true'
CELERY_TASK_EAGER_PROPAGATES = False

# =============================================================================
# PASSWORD VALIDATION (ВАЛИДАЦИЯ ПАРОЛЕЙ)
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# =============================================================================
# INTERNATIONALIZATION (ИНТЕРНАЦИОНАЛИЗАЦИЯ)
# =============================================================================

LANGUAGE_CODE = os.getenv('LANGUAGE_CODE', 'ru')
TIME_ZONE = os.getenv('TIME_ZONE', 'Asia/Bishkek')
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ('ru', 'Русский'),
    ('ky', 'Кыргызча'),
]

# =============================================================================
# STATIC & MEDIA FILES (СТАТИЧЕСКИЕ И МЕДИА ФАЙЛЫ)
# =============================================================================

STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Загрузка логотипов: только изображения и не больше лимита (ТЗ backend §24).
LOGO_MAX_SIZE_BYTES = int(os.getenv('LOGO_MAX_SIZE_BYTES', 2 * 1024 * 1024))
LOGO_ALLOWED_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp']

DJANGO_RESIZED_DEFAULT = {
    'size': [512, 512],
    'quality': 85,
    'keep_meta': False,
}

# =============================================================================
# DEFAULTS (ЗНАЧЕНИЯ ПО УМОЛЧАНИЮ)
# =============================================================================

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================================================================
# REST FRAMEWORK
# =============================================================================

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'apps.users.authentication.AlaSoftJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'apps.common.renderers.EnvelopeJSONRenderer',
    ),
    'DEFAULT_PAGINATION_CLASS': 'apps.common.pagination.DefaultPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': ('django_filters.rest_framework.DjangoFilterBackend',),
    'EXCEPTION_HANDLER': 'apps.common.exceptions.api_exception_handler',
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': (),
    'DEFAULT_THROTTLE_RATES': {
        'otp_request_phone': os.getenv('THROTTLE_OTP_PHONE', '5/hour'),
        'otp_request_ip': os.getenv('THROTTLE_OTP_IP', '20/hour'),
        'otp_verify': os.getenv('THROTTLE_OTP_VERIFY', '10/hour'),
        'pin_login': os.getenv('THROTTLE_PIN_LOGIN', '10/hour'),
        'director_login': os.getenv('THROTTLE_DIRECTOR_LOGIN', '10/hour'),
    },
    'UNAUTHENTICATED_USER': None,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'AlaSoft API',
    'DESCRIPTION': 'Универсальная платформа лояльности: USER / ORGANIZATION_ADMIN / DIRECTOR',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api/v1',
    'COMPONENT_SPLIT_REQUEST': True,
    'ENUM_NAME_OVERRIDES': {
        'TransactionTypeEnum': 'apps.loyalty.models.TransactionType.choices',
        'LoyaltyTypeEnum': 'apps.organizations.models.LoyaltyType.choices',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=int(os.getenv('ACCESS_TOKEN_LIFETIME_MINUTES', 15))),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=int(os.getenv('REFRESH_TOKEN_LIFETIME_DAYS', 30))),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': False,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'TOKEN_TYPE_CLAIM': 'token_type',
}

# =============================================================================
# ДОМЕННЫЕ НАСТРОЙКИ ЛОЯЛЬНОСТИ / БЕЗОПАСНОСТИ
# =============================================================================

OTP_CODE_LENGTH = int(os.getenv('OTP_CODE_LENGTH', 6))
OTP_TTL_SECONDS = int(os.getenv('OTP_TTL_SECONDS', 300))
OTP_MAX_ATTEMPTS = int(os.getenv('OTP_MAX_ATTEMPTS', 5))
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv('OTP_RESEND_COOLDOWN_SECONDS', 60))
OTP_VERIFICATION_TTL_SECONDS = int(os.getenv('OTP_VERIFICATION_TTL_SECONDS', 900))
# В dev удобно видеть код в ответе; в проде обязательно false.
OTP_DEBUG_RETURN_CODE = os.getenv('OTP_DEBUG_RETURN_CODE', 'false').lower() == 'true'

PIN_LENGTH = int(os.getenv('PIN_LENGTH', 4))
PIN_MAX_ATTEMPTS = int(os.getenv('PIN_MAX_ATTEMPTS', 5))
PIN_LOCKOUT_SECONDS = int(os.getenv('PIN_LOCKOUT_SECONDS', 900))

REDEMPTION_TTL_SECONDS = int(os.getenv('REDEMPTION_TTL_SECONDS', 300))
IDEMPOTENCY_RETENTION_DAYS = int(os.getenv('IDEMPOTENCY_RETENTION_DAYS', 7))
CASHBACK_EXPIRY_WARNING_DAYS = int(os.getenv('CASHBACK_EXPIRY_WARNING_DAYS', 7))

PHONE_DEFAULT_REGION = os.getenv('PHONE_DEFAULT_REGION', 'KG')

# Провайдеры SMS и push подключаются через адаптеры (ТЗ backend §25).
SMS_PROVIDER = os.getenv('SMS_PROVIDER', 'console')
SMS_PAYSOFT_URL = os.getenv('SMS_PAYSOFT_URL', '')
SMS_PAYSOFT_LOGIN = os.getenv('SMS_PAYSOFT_LOGIN', '')
SMS_PAYSOFT_PASSWORD = os.getenv('SMS_PAYSOFT_PASSWORD', '')
SMS_PAYSOFT_SENDER = os.getenv('SMS_PAYSOFT_SENDER', 'AlaSoft')

PUSH_PROVIDER = os.getenv('PUSH_PROVIDER', 'console')
FCM_PROJECT_ID = os.getenv('FCM_PROJECT_ID', '')
FCM_CREDENTIALS_FILE = os.getenv('FCM_CREDENTIALS_FILE', '')

# =============================================================================
# LOGGING
# =============================================================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {'()': 'apps.common.logging.RequestIDFilter'},
    },
    'formatters': {
        'standard': {
            'format': '[{asctime}] {levelname} {name} rid={request_id} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
            'filters': ['request_id'],
        },
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        'django.db.backends': {'level': 'WARNING', 'handlers': ['console'], 'propagate': False},
        'alasoft': {'level': os.getenv('LOG_LEVEL', 'INFO'), 'handlers': ['console'], 'propagate': False},
    },
}
