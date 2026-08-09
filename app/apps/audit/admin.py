from django.contrib import admin

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'action', 'actor_type', 'actor_phone', 'entity_type', 'organization']
    list_filter = ['action', 'actor_type', 'organization']
    search_fields = ['actor_phone', 'entity_id', 'request_id']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
