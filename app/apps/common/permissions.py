"""RBAC-скоупы: USER / ORGANIZATION_ADMIN / DIRECTOR (ТЗ backend §3)."""

from rest_framework.permissions import BasePermission

from apps.common.errors import ErrorCode, PermissionDeniedError


class IsUser(BasePermission):
    message = 'Доступно только пользователю приложения'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == 'USER')


class IsOrganizationAdmin(BasePermission):
    message = 'Доступно только администратору организации'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == 'ORGANIZATION_ADMIN')


class IsDirector(BasePermission):
    message = 'Доступно только директору AlaSoft'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == 'DIRECTOR')


def current_organization(request):
    """Организация администратора берётся ТОЛЬКО из серверного состояния.

    organization_id из body/query недоверенный по определению (ТЗ backend §3).
    """
    membership = getattr(request.user, 'active_admin_membership', None)
    if membership is None:
        raise PermissionDeniedError(
            code=ErrorCode.PERMISSION_DENIED,
            message='Администратор не привязан к активной организации',
        )
    return membership.organization
