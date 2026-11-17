"""RBAC-скоупы: USER / сотрудник заведения / DIRECTOR (ТЗ backend §3, BACKEND_TZ §5).

Роль в заведении определяется записью сотрудника, а не полем ``user.role``:
один человек может быть и покупателем в TOPTO, и мастером в TOPTO Business.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.common.errors import DomainError, ErrorCode, PermissionDeniedError
from apps.common.request_context import APP_BUSINESS, APP_CLIENT, request_app

# Значение required_permission у admin-вью: только должность ADMIN
ROLE_ADMIN = 'role'
# Любой сотрудник заведения
ANY_STAFF = None


class IsUser(BasePermission):
    """Покупатель (TOPTO).

    Роль USER — всегда. Администратор заведения тоже может быть покупателем,
    но только в TOPTO (X-App: client): старое Business-приложение без
    заголовка по-прежнему не видит клиентских ручек.
    """

    message = 'Доступно только пользователю приложения'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role == 'USER':
            return True
        return user.role == 'ORGANIZATION_ADMIN' and request_app(request) == APP_CLIENT


class IsDirector(BasePermission):
    message = 'Доступно только директору AlaSoft'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == 'DIRECTOR')


def _denied(permission, message='Недостаточно прав'):
    return PermissionDeniedError(
        code=ErrorCode.PERMISSION_DENIED, message=message, details={'permission': permission},
    )


class IsOrganizationStaff(BasePermission):
    """Сотрудник заведения с нужным правом (раздел 5.3).

    Вью задаёт ``required_permission`` (атрибут или метод
    ``get_required_permission(request)``): ``'role'`` — только администратор,
    ``None`` — любой сотрудник, имя права — сотрудник с этим правом.
    Заблокированное заведение: читать можно, менять — 403 ORGANIZATION_BLOCKED.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False   # DRF ответит 401: приложение обновит токен или выйдет
        if user.role == 'DIRECTOR':
            raise _denied(ROLE_ADMIN, 'Доступно только сотрудникам заведения')

        membership = user.staff_membership
        if membership is None:
            raise _denied(ROLE_ADMIN, 'Номер не подключён к организации')
        if not membership.is_active or not user.is_active:
            raise DomainError(code=ErrorCode.USER_BLOCKED, message='Доступ сотрудника отключён', status_code=403)

        getter = getattr(view, 'get_required_permission', None)
        required = getter(request) if getter else getattr(view, 'required_permission', ROLE_ADMIN)
        if required == ROLE_ADMIN and not membership.is_admin:
            raise _denied(ROLE_ADMIN, 'Доступно только администратору заведения')
        if required not in (ROLE_ADMIN, ANY_STAFF) and not membership.has_permission(required):
            raise _denied(required)

        if request.method not in SAFE_METHODS and membership.organization.status != 'ACTIVE':
            raise DomainError(code=ErrorCode.ORGANIZATION_BLOCKED, message='Организация заблокирована',
                              status_code=403)

        request.staff = membership
        # Сервисы аудита и идемпотентности видят, что человек действует как
        # сотрудник, даже если его роль в профиле — USER.
        user._acting_staff = membership
        return True


# Старое имя: вью, где достаточно быть администратором
class IsOrganizationAdmin(IsOrganizationStaff):
    pass


def current_organization(request):
    """Организация сотрудника берётся ТОЛЬКО из серверного состояния.

    organization_id из body/query недоверенный по определению (ТЗ backend §3).
    """
    membership = getattr(request, 'staff', None) or getattr(request.user, 'active_admin_membership', None)
    if membership is None:
        raise PermissionDeniedError(
            code=ErrorCode.PERMISSION_DENIED,
            message='Администратор не привязан к активной организации',
            details={'permission': ROLE_ADMIN},
        )
    return membership.organization


def effective_role(user, request):
    """Роль для ответа GET /me (раздел 5.2)."""
    if user.role == 'DIRECTOR':
        return 'DIRECTOR'
    app = request_app(request)
    if app == APP_CLIENT:
        return 'USER'
    if app == APP_BUSINESS:
        membership = user.active_admin_membership
        if membership is None:
            return 'USER'
        return 'ORGANIZATION_ADMIN' if membership.is_admin else 'ORGANIZATION_EMPLOYEE'
    # Без заголовка (старые версии) — как раньше
    return user.role


def staff_actor(user):
    """Сотрудник, если человек сейчас действует в роли заведения."""
    return getattr(user, '_acting_staff', None)
