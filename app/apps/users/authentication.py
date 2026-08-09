from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.users.models import Role


class AlaSoftJWTAuthentication(JWTAuthentication):
    """JWT + серверная сверка роли и привязки администратора.

    Роль и организация в токене вспомогательные: источником истины остаётся
    БД, поэтому отозванный доступ администратора перестаёт работать сразу
    (ТЗ backend §3).
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        if user.role == Role.ORGANIZATION_ADMIN:
            membership = user.active_admin_membership
            if membership is None or membership.organization.status != 'ACTIVE':
                from rest_framework_simplejwt.exceptions import AuthenticationFailed
                raise AuthenticationFailed('Доступ администратора отозван', code='admin_access_revoked')

        return user
