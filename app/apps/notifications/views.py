"""Лента уведомлений и их настройки (BACKEND_TZ 9.5, 9.6).

Лента своя у каждого приложения: TOPTO видит события покупателя, TOPTO
Business — события заведения. Приложение определяется по X-App.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.errors import ErrorCode, NotFoundError
from apps.common.request_context import notification_app_for
from apps.notifications.models import Notification, NotificationChannel
from apps.notifications.services import unread_count, user_settings


class NotificationSerializer(serializers.ModelSerializer):
    data = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ['id', 'event', 'title', 'body', 'data', 'is_read', 'created_at']

    def get_data(self, obj) -> dict:
        from apps.notifications.tasks import push_data
        data = push_data(obj)
        data.pop('notification_id', None)
        data.pop('event', None)
        return data


def feed(request):
    return Notification.objects.filter(
        user=request.user, app=notification_app_for(request),
        channel=NotificationChannel.PUSH, is_silent=False,
    )


@extend_schema(tags=['notifications'])
class NotificationListView(ListAPIView):
    """Лента; в meta.unread — число непрочитанных."""

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Notification.objects.none()
        return feed(self.request).order_by('-created_at')

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['unread'] = unread_count(self.request.user, notification_app_for(self.request))
        return response


@extend_schema(tags=['notifications'], request=None, responses={204: None})
class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        updated = Notification.objects.filter(
            pk=pk, user=request.user, channel=NotificationChannel.PUSH,
        ).update(is_read=True)
        if not updated:
            raise NotFoundError(code=ErrorCode.NOTIFICATION_NOT_FOUND, message='Уведомление не найдено')
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['notifications'], request=None, responses={204: None})
class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        feed(request).filter(is_read=False).update(is_read=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationSettingsSerializer(serializers.Serializer):
    operations = serializers.BooleanField(required=False)
    expiring = serializers.BooleanField(required=False)
    news = serializers.BooleanField(required=False)


@extend_schema(tags=['notifications'], request=NotificationSettingsSerializer,
               responses=NotificationSettingsSerializer)
class NotificationSettingsView(APIView):
    """REDEMPTION_REQUESTED не отключается: без него не оплатить бонусами."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(user_settings(request.user))

    def patch(self, request):
        serializer = NotificationSettingsSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.notification_settings = {**user_settings(user), **serializer.validated_data}
        user.save(update_fields=['notification_settings'])
        return Response(user_settings(user))

