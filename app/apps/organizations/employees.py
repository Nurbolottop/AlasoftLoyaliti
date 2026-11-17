"""Сотрудники заведения (BACKEND_TZ 5, 7.11).

Должность ADMIN даёт роль администратора, остальные — сотрудника. Один
человек — сотрудник одного заведения. Удаление мягкое: операции удалённого
сотрудника остаются в истории с его именем.
"""

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.errors import ConflictError, ErrorCode, NotFoundError
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_staff
from apps.organizations.models import EMPLOYEE_PERMISSIONS, EmployeePosition, OrganizationAdmin
from apps.users.models import AppKind, Role, User

ACCESS_FIELDS = ('position', 'is_active', *EMPLOYEE_PERMISSIONS)


def live_employees(organization):
    return OrganizationAdmin.objects.filter(organization=organization, deleted_at__isnull=True)


def get_employee(organization, employee_id):
    employee = (
        live_employees(organization).select_related('user', 'organization').filter(pk=employee_id).first()
    )
    if employee is None:
        raise NotFoundError(code=ErrorCode.EMPLOYEE_NOT_FOUND, message='Сотрудник не найден')
    return employee


def _active_admins(organization):
    return live_employees(organization).filter(position=EmployeePosition.ADMIN, is_active=True)


def _snapshot(employee):
    return {
        'full_name': employee.full_name,
        'position': employee.position,
        'is_active': employee.is_active,
        **{name: getattr(employee, name) for name in EMPLOYEE_PERMISSIONS},
    }


def revoke_business_sessions(user):
    """Отзывает токены TOPTO Business. Сессии покупателя (TOPTO) не трогаем.

    Учётка старой схемы (роль ORGANIZATION_ADMIN) пользуется только Business,
    поэтому у неё отзываются все токены.
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    devices = user.devices.all()
    if user.role != Role.ORGANIZATION_ADMIN:
        devices = devices.filter(app=AppKind.BUSINESS)
    jtis = [jti for jti in devices.values_list('refresh_jti', flat=True) if jti]
    tokens = OutstandingToken.objects.filter(user=user)
    if user.role != Role.ORGANIZATION_ADMIN:
        tokens = tokens.filter(jti__in=jtis)
    for token in tokens:
        BlacklistedToken.objects.get_or_create(token=token)
    devices.update(is_active=False, fcm_token='', refresh_jti='')


def _notify_access_changed(employee):
    notify_staff(employee.user, NotificationEvent.STAFF_ACCESS_CHANGED, organization=employee.organization,
                 context={'employee_id': str(employee.id)})


@transaction.atomic
def create_employee(*, organization, data, actor, request=None):
    phone = data['phone']
    user = User.objects.select_for_update().filter(phone=phone).first()
    if user is not None and user.role == Role.DIRECTOR:
        raise ConflictError(code=ErrorCode.EMPLOYEE_PHONE_TAKEN, message='Номер занят служебной учётной записью')
    if user is not None and OrganizationAdmin.objects.filter(user=user, deleted_at__isnull=True).exists():
        raise ConflictError(
            code=ErrorCode.EMPLOYEE_PHONE_TAKEN, message='Этот номер уже сотрудник заведения',
        )

    first_name, _, last_name = data['full_name'].partition(' ')
    if user is None:
        # Нет пользователя — создаём сразу зарегистрированным (7.11)
        user = User(phone=phone, role=Role.USER, first_name=first_name, last_name=last_name.strip(),
                    is_registration_complete=True)
        user.set_unusable_password()
        user.save()
        user.ensure_identity()
    elif not user.is_registration_complete:
        user.first_name = user.first_name or first_name
        user.last_name = user.last_name or last_name.strip()
        user.is_registration_complete = True
        user.save(update_fields=['first_name', 'last_name', 'is_registration_complete'])
        user.ensure_identity()

    employee = OrganizationAdmin(
        organization=organization, user=user, created_by=actor,
        full_name=data['full_name'], position=data['position'],
        is_active=data.get('is_active', True),
        **{name: data.get(name, False) for name in EMPLOYEE_PERMISSIONS},
    )
    if data.get('avatar'):
        employee.avatar = data['avatar']
    employee.save()

    log_action(AuditAction.EMPLOYEE_CREATED, actor=actor, request=request,
               entity_type='Employee', entity_id=employee.id, organization=organization,
               after={**_snapshot(employee), 'phone': phone})
    return employee


@transaction.atomic
def update_employee(*, employee, data, actor, request=None):
    employee = OrganizationAdmin.objects.select_for_update().select_related('user', 'organization').get(
        pk=employee.pk)
    before = _snapshot(employee)

    demoting = (
        employee.position == EmployeePosition.ADMIN and employee.is_active
        and (data.get('position', EmployeePosition.ADMIN) != EmployeePosition.ADMIN
             or data.get('is_active') is False)
    )
    if demoting and not _active_admins(employee.organization).exclude(pk=employee.pk).exists():
        raise ConflictError(code=ErrorCode.LAST_ADMIN, message='Нельзя убрать последнего администратора')

    if 'phone' in data and data['phone'] != employee.user.phone:
        # Номер — это учётка человека; другой номер — другой человек
        raise ConflictError(
            code=ErrorCode.EMPLOYEE_PHONE_TAKEN,
            message='Номер сотрудника не меняется: удалите сотрудника и добавьте заново',
        )

    for field in ('full_name', *ACCESS_FIELDS):
        if field in data:
            setattr(employee, field, data[field])
    if 'avatar' in data:
        if data['avatar'] is None and employee.avatar:
            employee.avatar.delete(save=False)
        employee.avatar = data['avatar']
    if before['is_active'] and not employee.is_active:
        employee.deactivated_at = timezone.now()
    elif not before['is_active'] and employee.is_active:
        employee.deactivated_at = None
    employee.save()

    after = _snapshot(employee)
    log_action(AuditAction.EMPLOYEE_UPDATED, actor=actor, request=request,
               entity_type='Employee', entity_id=employee.id, organization=employee.organization,
               before=before, after=after)

    access_changed = any(before[name] != after[name] for name in ACCESS_FIELDS)
    if access_changed:
        if not employee.is_active:
            revoke_business_sessions(employee.user)
        _notify_access_changed(employee)
    return employee


@transaction.atomic
def delete_employee(*, employee, actor, request=None):
    employee = OrganizationAdmin.objects.select_for_update().select_related('user', 'organization').get(
        pk=employee.pk)
    if employee.user_id == getattr(actor, 'id', None):
        raise ConflictError(code=ErrorCode.CANNOT_DELETE_SELF, message='Нельзя удалить самого себя')
    if (employee.position == EmployeePosition.ADMIN and employee.is_active
            and not _active_admins(employee.organization).exclude(pk=employee.pk).exists()):
        raise ConflictError(code=ErrorCode.LAST_ADMIN, message='Нельзя удалить последнего администратора')

    now = timezone.now()
    employee.is_active = False
    employee.deactivated_at = employee.deactivated_at or now
    employee.deleted_at = now
    employee.save(update_fields=['is_active', 'deactivated_at', 'deleted_at', 'updated_at'])

    log_action(AuditAction.EMPLOYEE_DELETED, actor=actor, request=request,
               entity_type='Employee', entity_id=employee.id, organization=employee.organization,
               before=_snapshot(employee))
    revoke_business_sessions(employee.user)
    _notify_access_changed(employee)
