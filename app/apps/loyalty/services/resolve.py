"""Идентификация клиента по QR-токену или 6-значному коду (ТЗ общее §7)."""

from apps.common.errors import DomainError, ErrorCode
from apps.users.models import Role, User


def _get_client(**lookup):
    user = User.objects.filter(role=Role.USER, **lookup).first()
    if user is None:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Клиент не найден', status_code=404)
    if not user.is_active:
        raise DomainError(code=ErrorCode.USER_BLOCKED, message='Клиент заблокирован', status_code=403)
    if not user.is_registration_complete:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Клиент не завершил регистрацию', status_code=404)
    return user


def resolve_by_qr(qr_payload: str):
    """QR идентифицирует пользователя, но сам по себе не меняет баланс."""
    token = (qr_payload or '').strip()
    if token.startswith('alasoft://u/'):
        token = token[len('alasoft://u/'):]
    if not token:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Пустой QR', status_code=404)
    return _get_client(qr_token=token)


def resolve_by_code(public_code: str):
    code = (public_code or '').strip()
    if not code.isdigit() or len(code) != 6:
        raise DomainError(
            code=ErrorCode.VALIDATION_ERROR,
            message='Код должен состоять из 6 цифр',
            status_code=400,
        )
    return _get_client(public_code=code)
