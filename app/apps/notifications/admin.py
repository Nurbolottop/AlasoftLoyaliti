from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'channel', 'event', 'status', 'user', 'organization']
    list_filter = ['channel', 'event', 'status']
    search_fields = ['user__phone']
    date_hierarchy = 'created_at'
