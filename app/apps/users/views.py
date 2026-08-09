from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.errors import DomainError, ErrorCode
from apps.users import services
from apps.users.models import UserDevice
from apps.users.serializers import (
    DirectorLoginSerializer,
    LogoutSerializer,
    MeSerializer,
    MeUpdateSerializer,
    OtpRequestSerializer,
    OtpVerifySerializer,
    PinLoginSerializer,
    PinResetSerializer,
    RefreshSerializer,
    RegisterCompleteSerializer,
    UserDeviceSerializer,
)
from apps.users.throttles import (
    DirectorLoginThrottle,
    OtpIpThrottle,
    OtpVerifyThrottle,
    PhoneScopedThrottle,
    PinLoginThrottle,
)


def validated(serializer_class, request):
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class OtpRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PhoneScopedThrottle, OtpIpThrottle]
    serializer_class = OtpRequestSerializer

    def post(self, request):
        data = validated(OtpRequestSerializer, request)
        result = services.request_otp(
            phone=data['phone'],
            purpose=data['purpose'],
            request=request,
            device_id=data.get('device_id', ''),
        )
        return Response(result)


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class OtpVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [OtpVerifyThrottle]
    serializer_class = OtpVerifySerializer

    def post(self, request):
        data = validated(OtpVerifySerializer, request)
        result = services.verify_otp(
            challenge_id=data['challenge_id'],
            code=data['code'],
            phone=data['phone'],
            request=request,
        )
        return Response(result)


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class RegisterCompleteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = RegisterCompleteSerializer

    def post(self, request):
        data = validated(RegisterCompleteSerializer, request)
        user, tokens = services.complete_registration(
            phone=data['phone'],
            verification_token=data['verification_token'],
            pin=data['pin'],
            first_name=data.get('first_name', ''),
            last_name=data.get('last_name', ''),
            language=data.get('language', 'ru'),
            device=data.get('device'),
            request=request,
        )
        return Response(
            {'user': MeSerializer(user).data, 'tokens': tokens},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class PinLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PinLoginThrottle]
    serializer_class = PinLoginSerializer

    def post(self, request):
        data = validated(PinLoginSerializer, request)
        user, tokens = services.pin_login(
            phone=data['phone'], pin=data['pin'],
            device=data.get('device'), request=request,
        )
        return Response({'user': MeSerializer(user).data, 'tokens': tokens})


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class PinResetView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PinResetSerializer

    def post(self, request):
        data = validated(PinResetSerializer, request)
        user, tokens = services.reset_pin(
            phone=data['phone'],
            verification_token=data['verification_token'],
            pin=data['pin'],
            device=data.get('device'),
            request=request,
        )
        return Response({'user': MeSerializer(user).data, 'tokens': tokens})


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class DirectorLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [DirectorLoginThrottle]
    serializer_class = DirectorLoginSerializer

    def post(self, request):
        data = validated(DirectorLoginSerializer, request)
        user, tokens = services.director_login(
            phone=data['phone'], password=data['password'], request=request
        )
        return Response({'user': MeSerializer(user).data, 'tokens': tokens})


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class TokenRefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = RefreshSerializer

    def post(self, request):
        """Rotating refresh: старый токен гасится, выдаётся новая пара."""
        data = validated(RefreshSerializer, request)
        try:
            refresh = RefreshToken(data['refresh'])
            old_jti = refresh['jti']
            user_id = refresh['user_id']
            refresh.blacklist()
        except TokenError:
            raise DomainError(code=ErrorCode.TOKEN_INVALID, message='Токен недействителен или отозван', status_code=401)

        from apps.users.models import User

        user = User.objects.filter(pk=user_id).first()
        if user is None or not user.is_active:
            raise DomainError(code=ErrorCode.TOKEN_INVALID, message='Токен недействителен', status_code=401)

        device = UserDevice.objects.filter(user=user, refresh_jti=old_jti).first()
        return Response(services.issue_tokens(user, device=device, request=request))


@extend_schema(tags=['auth'], responses=OpenApiTypes.OBJECT)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request):
        data = validated(LogoutSerializer, request)
        services.logout(
            refresh_token=data['refresh'], user=request.user,
            device_id=data.get('device_id', ''), request=request,
        )
        return Response({'detail': 'Сессия завершена'})


@extend_schema(tags=['user'])
class MeView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        serializer = MeUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MeSerializer(request.user).data)


@extend_schema(tags=['user'])
class MeDevicesView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserDeviceSerializer

    def get(self, request):
        devices = request.user.devices.filter(is_active=True)
        return Response(UserDeviceSerializer(devices, many=True).data)

    def post(self, request):
        serializer = UserDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = services.upsert_device(request.user, serializer.validated_data)
        return Response(UserDeviceSerializer(device).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['user'], request=None, responses=OpenApiTypes.OBJECT)
class MeQrView(APIView):
    """Постоянный QR клиента. Ротация — только по инициативе пользователя."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user.ensure_identity()
        return Response({
            'public_code': user.public_code,
            'qr_payload': f'alasoft://u/{user.qr_token}' if user.qr_token else None,
        })

    def post(self, request):
        user = request.user
        user.rotate_qr_token()
        return Response({
            'public_code': user.public_code,
            'qr_payload': f'alasoft://u/{user.qr_token}',
        })
