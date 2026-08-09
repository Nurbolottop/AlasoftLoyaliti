"""Стабильные machine-readable коды ошибок API (ТЗ backend §22)."""


class ErrorCode:
    # auth / identity
    OTP_INVALID = 'OTP_INVALID'
    OTP_EXPIRED = 'OTP_EXPIRED'
    OTP_TOO_MANY_ATTEMPTS = 'OTP_TOO_MANY_ATTEMPTS'
    OTP_COOLDOWN = 'OTP_COOLDOWN'
    OTP_VERIFICATION_REQUIRED = 'OTP_VERIFICATION_REQUIRED'
    PIN_INVALID = 'PIN_INVALID'
    PIN_LOCKED = 'PIN_LOCKED'
    PIN_ALREADY_SET = 'PIN_ALREADY_SET'
    USER_NOT_FOUND = 'USER_NOT_FOUND'
    USER_ALREADY_EXISTS = 'USER_ALREADY_EXISTS'
    USER_BLOCKED = 'USER_BLOCKED'
    INVALID_PHONE = 'INVALID_PHONE'
    INVALID_CREDENTIALS = 'INVALID_CREDENTIALS'
    TOKEN_INVALID = 'TOKEN_INVALID'
    AUTHENTICATION_REQUIRED = 'AUTHENTICATION_REQUIRED'
    PERMISSION_DENIED = 'PERMISSION_DENIED'

    # organizations
    ORGANIZATION_NOT_FOUND = 'ORGANIZATION_NOT_FOUND'
    ORGANIZATION_BLOCKED = 'ORGANIZATION_BLOCKED'
    LOYALTY_TYPE_MISMATCH = 'LOYALTY_TYPE_MISMATCH'
    LOYALTY_TYPE_LOCKED = 'LOYALTY_TYPE_LOCKED'
    PROGRAM_INACTIVE = 'PROGRAM_INACTIVE'
    ADMIN_ALREADY_EXISTS = 'ADMIN_ALREADY_EXISTS'

    # loyalty
    CASHBACK_LIMIT_EXCEEDED = 'CASHBACK_LIMIT_EXCEEDED'
    INSUFFICIENT_CASHBACK = 'INSUFFICIENT_CASHBACK'
    GIFT_NOT_AVAILABLE = 'GIFT_NOT_AVAILABLE'
    GIFT_NOT_FOUND = 'GIFT_NOT_FOUND'
    REDEMPTION_NOT_FOUND = 'REDEMPTION_NOT_FOUND'
    REDEMPTION_EXPIRED = 'REDEMPTION_EXPIRED'
    REDEMPTION_NOT_PENDING = 'REDEMPTION_NOT_PENDING'
    REDEMPTION_ALREADY_PENDING = 'REDEMPTION_ALREADY_PENDING'
    OPERATION_NOT_REVERSIBLE = 'OPERATION_NOT_REVERSIBLE'
    TRANSACTION_NOT_FOUND = 'TRANSACTION_NOT_FOUND'
    INVALID_AMOUNT = 'INVALID_AMOUNT'

    # infra
    IDEMPOTENCY_CONFLICT = 'IDEMPOTENCY_CONFLICT'
    IDEMPOTENCY_KEY_REQUIRED = 'IDEMPOTENCY_KEY_REQUIRED'
    VALIDATION_ERROR = 'VALIDATION_ERROR'
    NOT_FOUND = 'NOT_FOUND'
    METHOD_NOT_ALLOWED = 'METHOD_NOT_ALLOWED'
    RATE_LIMITED = 'RATE_LIMITED'
    CONFLICT = 'CONFLICT'
    INTERNAL_ERROR = 'INTERNAL_ERROR'


class DomainError(Exception):
    """Бизнес-ошибка домена. Транслируется в единый error-envelope."""

    default_code = ErrorCode.VALIDATION_ERROR
    default_message = 'Операция невозможна'
    default_status = 422

    def __init__(self, code=None, message=None, status_code=None, details=None):
        self.code = code or self.default_code
        self.message = message or self.default_message
        self.status_code = status_code or self.default_status
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(DomainError):
    default_code = ErrorCode.NOT_FOUND
    default_message = 'Объект не найден'
    default_status = 404


class PermissionDeniedError(DomainError):
    default_code = ErrorCode.PERMISSION_DENIED
    default_message = 'Недостаточно прав'
    default_status = 403


class ConflictError(DomainError):
    default_code = ErrorCode.CONFLICT
    default_message = 'Конфликт состояния'
    default_status = 409
