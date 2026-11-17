from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.users.models import Role


class AlaSoftJWTAuthentication(JWTAuthentication):
    """JWT + серверная сверка привязки администратора.

    Роль и организация в токене вспомогательные: источником истины остаётся
    БД, поэтому отозванный доступ перестаёт работать сразу (ТЗ backend §3).

    Учётка с ролью ORGANIZATION_ADMIN без действующей записи сотрудника —
    отозванный администратор старой схемы: её токены не принимаются вовсе.
    Сотрудники новой схемы (роль USER) остаются покупателями в TOPTO, а в
    /admin/* их не пустит IsOrganizationStaff. Заблокированное заведение
    читать можно — запись запрещает та же проверка прав.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        if user.role == Role.ORGANIZATION_ADMIN and user.active_admin_membership is None:
            from rest_framework_simplejwt.exceptions import AuthenticationFailed
            raise AuthenticationFailed('Доступ администратора отозван', code='admin_access_revoked')

        return user
