from django.conf import settings
from django.core.exceptions import ValidationError


def validate_logo_size(value):
    """Safe logo upload: ограничение размера файла (ТЗ backend §24)."""
    size = getattr(value, 'size', None)
    if size and size > settings.LOGO_MAX_SIZE_BYTES:
        limit_mb = settings.LOGO_MAX_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(f'Файл больше {limit_mb} МБ')
