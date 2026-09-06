from django.contrib import admin, messages

from apps.users.models import OtpChallenge, Role, User, UserDevice


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['phone', 'full_name', 'role', 'public_code', 'is_active', 'deleted_at', 'date_joined']
    list_filter = ['role', 'is_active', 'is_registration_complete', 'language']
    search_fields = ['phone', 'first_name', 'last_name', 'public_code']
    readonly_fields = ['id', 'pin_hash', 'qr_token', 'public_code', 'date_joined',
                       'last_login_at', 'deleted_at']
    ordering = ['-date_joined']
    actions = ['anonymize_accounts']

    @admin.action(description='Удалить аккаунт (обезличить)')
    def anonymize_accounts(self, request, queryset):
        """Обезличивание по обращению в поддержку.

        Нужно, когда человек потерял симку и не может нажать кнопку в
        приложении. Владельца проверяет сотрудник поддержки, поэтому шага
        с SMS здесь нет — сама операция та же, что и в API.
        """
        from apps.users.services import anonymize_user

        done, skipped = 0, []
        for user in queryset:
            if user.role != Role.USER:
                skipped.append(f'{user.phone}: не клиент')
                continue
            if user.deleted_at:
                skipped.append(f'{user.phone}: уже удалён')
                continue
            anonymize_user(user=user, request=request, actor=request.user)
            done += 1

        if done:
            self.message_user(request, f'Обезличено аккаунтов: {done}', messages.SUCCESS)
        for note in skipped:
            self.message_user(request, f'Пропущен {note}', messages.WARNING)


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_id', 'platform', 'is_active', 'last_seen_at']
    list_filter = ['platform', 'is_active']
    search_fields = ['user__phone', 'device_id']
    readonly_fields = ['refresh_jti']


@admin.register(OtpChallenge)
class OtpChallengeAdmin(admin.ModelAdmin):
    list_display = ['phone', 'purpose', 'status', 'attempts', 'expires_at', 'created_at']
    list_filter = ['purpose', 'status']
    search_fields = ['phone']
    readonly_fields = ['code_hash', 'verification_token']
