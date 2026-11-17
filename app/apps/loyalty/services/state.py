"""Доступ к агрегату состояния и общие проверки организации."""

from django.db import transaction

from apps.common.errors import DomainError, ErrorCode
from apps.loyalty.models import UserOrganizationState
from apps.organizations.models import LoyaltyType, OrganizationStatus


def ensure_organization_operational(organization, expected_type=None):
    if organization.status != OrganizationStatus.ACTIVE:
        raise DomainError(
            code=ErrorCode.ORGANIZATION_BLOCKED,
            message='Организация заблокирована',
            status_code=403,
        )
    if expected_type and organization.loyalty_type != expected_type:
        raise DomainError(
            code=ErrorCode.LOYALTY_TYPE_MISMATCH,
            message=f'Организация работает по программе {organization.loyalty_type}',
        )

    program = organization.program
    if program is None:
        raise DomainError(
            code=ErrorCode.PROGRAM_INACTIVE,
            message='Программа лояльности не настроена',
        )
    if not program.is_active:
        raise DomainError(
            code=ErrorCode.PROGRAM_INACTIVE,
            message='Программа лояльности отключена',
        )
    return program


CUSTOMER_ROLES = ('USER', 'ORGANIZATION_ADMIN')


def ensure_client(user):
    """Покупатель: любой человек, кроме директора платформы (BACKEND_TZ 5.2)."""
    if user is None:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Клиент не найден', status_code=404)
    if user.role not in CUSTOMER_ROLES:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Клиент не найден', status_code=404)
    if not user.is_active:
        raise DomainError(code=ErrorCode.USER_BLOCKED, message='Клиент заблокирован', status_code=403)
    return user


def get_state(user, organization):
    state, _ = UserOrganizationState.objects.get_or_create(user=user, organization=organization)
    return state


def peek_state(user, organization):
    """Состояние для показа, без записи в БД.

    Сканирование QR не делает покупателя клиентом заведения: если записи
    нет, отдаём нулевое состояние, не сохраняя его (BACKEND_TZ 7.6).
    """
    state = UserOrganizationState.objects.filter(user=user, organization=organization).first()
    return state or UserOrganizationState(user=user, organization=organization)


def is_member(user, organization):
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return UserOrganizationState.objects.filter(
        user=user, organization=organization, joined_at__isnull=False,
    ).exists()


def operation_fields(*, service=None, employee=None, cash_paid=None):
    """Поля операции: услуга (копия названия и цены), сотрудник, оплата деньгами."""
    fields = {'cash_paid': cash_paid}
    if service is not None:
        fields.update(service=service, service_name=service.name, service_price=service.price)
    if employee is not None:
        fields.update(employee=employee, employee_name=employee.display_name)
    return fields


def lock_state(user, organization):
    """Блокирует строку состояния на время транзакции (ТЗ backend §23)."""
    assert transaction.get_connection().in_atomic_block, 'lock_state требует atomic-блок'
    state, _ = UserOrganizationState.objects.get_or_create(user=user, organization=organization)
    return UserOrganizationState.objects.select_for_update().get(pk=state.pk)


def program_summary(organization, language='ru'):
    """Публичное описание условий программы для каталога."""
    if organization.loyalty_type == LoyaltyType.VISIT:
        program = getattr(organization, 'visit_program', None)
        if program is None:
            return None
        return {
            'type': LoyaltyType.VISIT,
            'target_visits': program.target_visits,
            'reward_count': program.reward_count,
            'reward_title': program.reward_title(language),
            'is_active': program.is_active,
        }

    program = getattr(organization, 'cashback_program', None)
    if program is None:
        return None
    return {
        'type': LoyaltyType.CASHBACK,
        'cashback_rate_bps': program.cashback_rate_bps,
        'max_spend_percent_bps': program.max_spend_percent_bps,
        'expiry_days': program.expiry_days,
        'min_purchase_amount': program.min_purchase_amount,
        'is_active': program.is_active,
    }
