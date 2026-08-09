import logging
from contextvars import ContextVar

_request_id: ContextVar = ContextVar('request_id', default='-')

# Ключи, которые нельзя писать в логи ни при каких условиях (ТЗ backend §27).
SENSITIVE_KEYS = {
    'pin', 'pin_code', 'new_pin', 'code', 'otp', 'otp_code', 'password',
    'token', 'access', 'refresh', 'access_token', 'refresh_token',
    'verification_token', 'qr_token', 'authorization',
}


def set_request_id(value):
    _request_id.set(value or '-')


def get_request_id():
    return _request_id.get()


def scrub(data):
    """Рекурсивно маскирует чувствительные значения перед логированием/аудитом."""
    if isinstance(data, dict):
        return {
            k: ('***' if str(k).lower() in SENSITIVE_KEYS else scrub(v))
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [scrub(item) for item in data]
    return data


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True
