"""Нормализация телефона в E.164 (ТЗ backend §4, §5)."""

import phonenumbers
from django.conf import settings

from apps.common.errors import DomainError, ErrorCode


def normalize_phone(raw: str) -> str:
    if not raw:
        raise DomainError(code=ErrorCode.INVALID_PHONE, message='Телефон не указан', status_code=400)

    value = str(raw).strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    region = None if value.startswith('+') else settings.PHONE_DEFAULT_REGION

    try:
        parsed = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException:
        raise DomainError(code=ErrorCode.INVALID_PHONE, message='Некорректный номер телефона', status_code=400)

    if not phonenumbers.is_valid_number(parsed):
        raise DomainError(code=ErrorCode.INVALID_PHONE, message='Некорректный номер телефона', status_code=400)

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def mask_phone(phone: str) -> str:
    """Минимизация PII в выдаче: +996555***789."""
    if not phone or len(phone) < 7:
        return '***'
    return f'{phone[:7]}***{phone[-3:]}'
