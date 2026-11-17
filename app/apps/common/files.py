"""Проверка загружаемых картинок (BACKEND_TZ §2, §10)."""

from django.conf import settings

from apps.common.errors import DomainError, ErrorCode

ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}


def check_image(file, *, max_bytes=None):
    """JPEG, PNG, WebP до 5 МБ. Иначе 413 FILE_TOO_LARGE / 415 UNSUPPORTED_FILE_TYPE."""
    if file is None:
        return file
    max_bytes = max_bytes or settings.IMAGE_MAX_SIZE_BYTES
    if getattr(file, 'size', 0) and file.size > max_bytes:
        raise DomainError(
            code=ErrorCode.FILE_TOO_LARGE,
            message=f'Файл больше {max_bytes // (1024 * 1024)} МБ',
            status_code=413,
            details={'max_bytes': max_bytes},
        )
    name = (getattr(file, 'name', '') or '').lower()
    extension = name.rsplit('.', 1)[-1] if '.' in name else ''
    # Pillow после проверки ImageField кладёт настоящий формат в .image
    image_format = (getattr(getattr(file, 'image', None), 'format', '') or '').lower()
    content_type = (getattr(file, 'content_type', '') or '').lower()
    allowed = (
        image_format in {'jpeg', 'png', 'webp'}
        or (not image_format and (content_type in ALLOWED_IMAGE_TYPES or extension in ALLOWED_IMAGE_EXTENSIONS))
    )
    if not allowed:
        raise DomainError(
            code=ErrorCode.UNSUPPORTED_FILE_TYPE,
            message='Поддерживаются только JPEG, PNG и WebP',
            status_code=415,
        )
    return file
