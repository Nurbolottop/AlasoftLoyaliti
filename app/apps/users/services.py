"""OTP, PIN и выдача токенов (ТЗ backend §5)."""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.common.net import client_ip
from apps.common.errors import DomainError, ErrorCode, PermissionDeniedError
from apps.notifications.models import NotificationEvent
from apps.notifications.services import notify_user, send_sms
from apps.users.models import OtpChallenge, OtpPurpose, OtpStatus, Role, User, UserDevice

logger = logging.getLogger('alasoft.auth')


def is_test_account(phone) -> bool:
    """Номер из белого списка для ревьюеров магазинов."""
    return bool(phone) and phone in settings.OTP_TEST_ACCOUNTS


def _generate_code(phone=None) -> str:
    """Код подтверждения для номера.

    Приоритет: белый список ревьюеров → OTP_STATIC_CODE (временно, пока нет
    SMS-провайдера) → случайный. Хранение только хеша, TTL, счётчик попыток и
    rate limiting во всех случаях одинаковые.
    """
    test_code = settings.OTP_TEST_ACCOUNTS.get(phone) if phone else None
    if test_code:
        return test_code

    if settings.OTP_STATIC_CODE:
        logger.warning(
            'OTP_STATIC_CODE активен: подтверждение номера проходит по '
            'фиксированному коду. Отключить после подключения SMS-провайдера.'
        )
        return settings.OTP_STATIC_CODE

    length = settings.OTP_CODE_LENGTH
    return ''.join(str(secrets.randbelow(10)) for _ in range(length))


def _login_reason(user):
    """Входы тестовых аккаунтов должны быть отличимы в аудите."""
    return 'test_account' if is_test_account(user.phone) else ''


def _deliver_code(phone, code, *, user=None, language='ru'):
    """Ставит SMS в очередь. Тестовым номерам ничего не шлём — их код известен."""
    if is_test_account(phone):
        logger.info('Тестовый аккаунт %s: SMS не отправляется', phone)
        return
    send_sms(phone, NotificationEvent.OTP, context={'code': code},
             language=language, user=user)


def request_otp(*, phone, purpose, request=None, device_id=''):
    """Создаёт челлендж и ставит SMS в очередь. Код нигде не хранится открытым."""
    user = User.objects.filter(phone=phone).first()

    if purpose == OtpPurpose.REGISTER and user and user.is_registration_complete:
        purpose = OtpPurpose.LOGIN
    if purpose in (OtpPurpose.LOGIN, OtpPurpose.PIN_RESET):
        if user is None or not user.is_registration_complete:
            raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Пользователь не найден', status_code=404)
    if user is not None and not user.is_active:
        raise DomainError(code=ErrorCode.USER_BLOCKED, message='Аккаунт заблокирован', status_code=403)

    now = timezone.now()
    recent = (
        OtpChallenge.objects.filter(phone=phone, purpose=purpose)
        .order_by('-created_at')
        .first()
    )
    if recent and (now - recent.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - recent.created_at).total_seconds())
        raise DomainError(
            code=ErrorCode.OTP_COOLDOWN,
            message='Повторная отправка возможна позже',
            status_code=429,
            details={'retry_after': max(wait, 1)},
        )

    # Старые ожидающие челленджи по этому номеру больше не действуют.
    OtpChallenge.objects.filter(
        phone=phone, purpose=purpose, status=OtpStatus.PENDING
    ).update(status=OtpStatus.EXPIRED)

    code = _generate_code(phone)
    challenge = OtpChallenge(
        phone=phone,
        purpose=purpose,
        status=OtpStatus.PENDING,
        max_attempts=settings.OTP_MAX_ATTEMPTS,
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        request_ip=client_ip(request),
        device_id=device_id or '',
    )
    challenge.set_code(code)
    challenge.save()

    _deliver_code(phone, code, user=user, language=getattr(user, 'language', 'ru'))

    result = {
        'challenge_id': str(challenge.id),
        'purpose': purpose,
        'expires_in': settings.OTP_TTL_SECONDS,
        'resend_after': settings.OTP_RESEND_COOLDOWN_SECONDS,
        'is_new_user': user is None or not user.is_registration_complete,
    }
    if settings.OTP_DEBUG_RETURN_CODE:
        result['debug_code'] = code
    return result


def _validate_code(challenge, code):
    """Сверяет код и ведёт счётчик попыток. Общая часть для всех сценариев."""
    if not challenge.check_code(code):
        challenge.attempts += 1
        if challenge.attempts >= challenge.max_attempts:
            challenge.status = OtpStatus.FAILED
            challenge.save(update_fields=['attempts', 'status', 'updated_at'])
            raise DomainError(
                code=ErrorCode.OTP_TOO_MANY_ATTEMPTS,
                message='Превышено число попыток, запросите код заново',
                status_code=429,
            )
        challenge.save(update_fields=['attempts', 'updated_at'])
        raise DomainError(
            code=ErrorCode.OTP_INVALID,
            message='Неверный код',
            details={'attempts_left': challenge.max_attempts - challenge.attempts},
        )


def verify_otp(*, challenge_id, code, phone=None, request=None):
    """Проверяет код и выдаёт одноразовый verification_token."""
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        if challenge is None:
            raise DomainError(code=ErrorCode.OTP_INVALID, message='Код не найден', status_code=404)
        if phone and challenge.phone != phone:
            raise DomainError(code=ErrorCode.OTP_INVALID, message='Неверный код')
        if challenge.status == OtpStatus.VERIFIED and challenge.verification_token:
            if challenge.verification_expires_at and challenge.verification_expires_at > timezone.now():
                return _verify_result(challenge)
        if challenge.status != OtpStatus.PENDING:
            raise DomainError(code=ErrorCode.OTP_INVALID, message='Код уже использован или недействителен')
        if challenge.is_expired:
            challenge.status = OtpStatus.EXPIRED
            challenge.save(update_fields=['status', 'updated_at'])
            raise DomainError(code=ErrorCode.OTP_EXPIRED, message='Срок действия кода истёк')

        _validate_code(challenge, code)

        challenge.status = OtpStatus.VERIFIED
        challenge.verified_at = timezone.now()
        challenge.issue_verification_token()
        challenge.save(update_fields=[
            'status', 'verified_at', 'verification_token', 'verification_expires_at', 'updated_at'
        ])

    return _verify_result(challenge)


def _verify_result(challenge):
    user = User.objects.filter(phone=challenge.phone).first()
    exists = bool(user and user.is_registration_complete)
    result = {
        'verification_token': challenge.verification_token,
        'expires_in': settings.OTP_VERIFICATION_TTL_SECONDS,
        'phone': challenge.phone,
        'purpose': challenge.purpose,
        'user_exists': exists,
        'has_pin': bool(user and user.has_pin),
        'role': user.role if exists else None,
    }
    if exists:
        # Имя отдаём только после успешной проверки кода: на этом шаге
        # звонящий уже доказал владение номером (экран «Аккаунт найден»).
        result['first_name'] = user.first_name
        result['last_name'] = user.last_name
        result['full_name'] = user.full_name
    return result


def consume_verification(*, token, phone, purposes):
    """Одноразовое использование подтверждённого OTP."""
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(verification_token=token, phone=phone, status=OtpStatus.VERIFIED)
            .first()
        )
        if challenge is None or challenge.purpose not in purposes:
            raise DomainError(
                code=ErrorCode.OTP_VERIFICATION_REQUIRED,
                message='Требуется подтверждение по SMS',
                status_code=403,
            )
        if challenge.verification_expires_at and challenge.verification_expires_at <= timezone.now():
            raise DomainError(
                code=ErrorCode.OTP_EXPIRED,
                message='Подтверждение устарело, запросите код заново',
            )
        challenge.status = OtpStatus.CONSUMED
        challenge.consumed_at = timezone.now()
        challenge.verification_token = ''
        challenge.save(update_fields=['status', 'consumed_at', 'verification_token', 'updated_at'])
        return challenge


@transaction.atomic
def complete_registration(*, phone, verification_token, pin, first_name='', last_name='',
                          language='ru', device=None, request=None):
    """Создаёт профиль, идентификаторы и PIN после подтверждения SMS."""
    consume_verification(
        token=verification_token, phone=phone,
        purposes=(OtpPurpose.REGISTER, OtpPurpose.LOGIN),
    )

    user = User.objects.select_for_update().filter(phone=phone).first()
    if user and user.is_registration_complete:
        raise DomainError(
            code=ErrorCode.USER_ALREADY_EXISTS,
            message='Пользователь уже зарегистрирован',
            status_code=409,
        )

    if user is None:
        user = User(phone=phone, role=Role.USER)
        user.set_unusable_password()

    user.first_name = first_name or user.first_name
    user.last_name = last_name or user.last_name
    user.language = language or user.language
    user.set_pin(pin)
    user.is_registration_complete = True
    user.save()
    user.ensure_identity()

    log_action(AuditAction.PIN_SET, actor=user, request=request, entity_type='User', entity_id=user.id)
    tokens = issue_tokens(user, device=device, request=request)
    return user, tokens


@transaction.atomic
def reset_pin(*, phone, verification_token, pin, device=None, request=None):
    consume_verification(token=verification_token, phone=phone, purposes=(OtpPurpose.PIN_RESET,))

    user = User.objects.select_for_update().filter(phone=phone).first()
    if user is None or not user.is_registration_complete:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Пользователь не найден', status_code=404)

    user.set_pin(pin)
    user.save(update_fields=['pin_hash', 'pin_updated_at', 'failed_pin_attempts', 'pin_locked_until'])

    log_action(AuditAction.PIN_RESET, actor=user, request=request, entity_type='User', entity_id=user.id)
    return user, issue_tokens(user, device=device, request=request)


@transaction.atomic
def otp_login(*, phone, verification_token, device=None, request=None):
    """Вход по подтверждённому SMS-коду, без изменения PIN.

    Нужен, когда пользователь не помнит PIN или входит на новом устройстве:
    reset_pin для этого не годится — он требует задать новый PIN, а purpose
    LOGIN раньше не принимал ни один эндпоинт.
    """
    consume_verification(
        token=verification_token, phone=phone,
        purposes=(OtpPurpose.LOGIN, OtpPurpose.REGISTER),
    )

    user = User.objects.select_for_update().filter(phone=phone).first()
    if user is None or not user.is_registration_complete:
        raise DomainError(code=ErrorCode.USER_NOT_FOUND, message='Пользователь не найден', status_code=404)
    if not user.is_active:
        raise DomainError(code=ErrorCode.USER_BLOCKED, message='Аккаунт заблокирован', status_code=403)

    # Подтверждение по SMS снимает блокировку от перебора PIN.
    user.reset_pin_attempts()

    log_action(AuditAction.LOGIN, actor=user, request=request, entity_type='User',
               entity_id=user.id, reason=_login_reason(user))
    return user, issue_tokens(user, device=device, request=request)


def pin_login(*, phone, pin, device=None, request=None):
    user = User.objects.filter(phone=phone).first()
    if user is None or not user.is_registration_complete or not user.has_pin:
        # Не раскрываем, существует ли номер.
        raise DomainError(code=ErrorCode.INVALID_CREDENTIALS, message='Неверный телефон или PIN', status_code=401)
    if not user.is_active:
        raise DomainError(code=ErrorCode.USER_BLOCKED, message='Аккаунт заблокирован', status_code=403)
    if user.is_pin_locked:
        raise DomainError(
            code=ErrorCode.PIN_LOCKED,
            message='Вход временно заблокирован, восстановите доступ по SMS',
            status_code=429,
            details={'locked_until': user.pin_locked_until},
        )

    if not user.check_pin(pin):
        user.register_failed_pin()
        log_action(
            AuditAction.LOGIN_FAILED, actor=user, request=request,
            entity_type='User', entity_id=user.id, after={'reason': 'PIN_INVALID'},
        )
        raise DomainError(code=ErrorCode.PIN_INVALID, message='Неверный PIN', status_code=401)

    user.reset_pin_attempts()
    log_action(AuditAction.LOGIN, actor=user, request=request, entity_type='User',
               entity_id=user.id, reason=_login_reason(user))
    return user, issue_tokens(user, device=device, request=request)


def director_login(*, phone, password, request=None):
    user = User.objects.filter(phone=phone, role=Role.DIRECTOR).first()
    if user is None or not user.check_password(password):
        log_action(
            AuditAction.LOGIN_FAILED, actor=user, request=request,
            entity_type='User', entity_id=getattr(user, 'id', ''), after={'reason': 'PASSWORD_INVALID'},
        )
        raise DomainError(code=ErrorCode.INVALID_CREDENTIALS, message='Неверные учётные данные', status_code=401)
    if not user.is_active:
        raise DomainError(code=ErrorCode.USER_BLOCKED, message='Аккаунт заблокирован', status_code=403)

    log_action(AuditAction.LOGIN, actor=user, request=request, entity_type='User', entity_id=user.id)
    return user, issue_tokens(user, request=request)


def upsert_device(user, device_data):
    """Регистрирует устройство и его FCM-токен."""
    if not device_data or not device_data.get('device_id'):
        return None
    device, _ = UserDevice.objects.update_or_create(
        user=user,
        device_id=device_data['device_id'],
        defaults={
            'platform': device_data.get('platform', 'android'),
            'device_name': device_data.get('device_name', ''),
            'app_version': device_data.get('app_version', ''),
            'fcm_token': device_data.get('fcm_token', ''),
            'language': device_data.get('language', user.language),
            'is_active': True,
            'last_seen_at': timezone.now(),
        },
    )
    return device


def issue_tokens(user, *, device=None, request=None):
    """Короткий access + ротируемый refresh, привязанный к устройству."""
    refresh = RefreshToken.for_user(user)
    refresh['role'] = user.role
    refresh['phone'] = user.phone

    if user.role == Role.ORGANIZATION_ADMIN:
        membership = user.active_admin_membership
        refresh['organization_id'] = str(membership.organization_id) if membership else None

    device_obj = upsert_device(user, device) if isinstance(device, dict) else device
    if device_obj is not None:
        refresh['device_id'] = device_obj.device_id
        device_obj.refresh_jti = refresh['jti']
        device_obj.last_seen_at = timezone.now()
        device_obj.save(update_fields=['refresh_jti', 'last_seen_at', 'updated_at'])

    User.objects.filter(pk=user.pk).update(last_login_at=timezone.now())

    access = refresh.access_token
    access['role'] = user.role
    if user.role == Role.ORGANIZATION_ADMIN:
        access['organization_id'] = refresh.get('organization_id')

    return {
        'access': str(access),
        'refresh': str(refresh),
        'access_expires_in': int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
        'refresh_expires_in': int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
        'token_type': 'Bearer',
    }


def logout(*, refresh_token, user, device_id='', request=None):
    try:
        token = RefreshToken(refresh_token)
        token.blacklist()
    except Exception:
        raise DomainError(code=ErrorCode.TOKEN_INVALID, message='Некорректный refresh-токен', status_code=401)

    if device_id:
        UserDevice.objects.filter(user=user, device_id=device_id).update(
            is_active=False, fcm_token='', refresh_jti=''
        )
    log_action(AuditAction.LOGOUT, actor=user, request=request, entity_type='User', entity_id=user.id)
    return True


def cleanup_otp_challenges(*, now=None, keep_days=2):
    """CleanupOtpChallenges (ТЗ backend §26)."""
    now = now or timezone.now()
    expired = OtpChallenge.objects.filter(
        status=OtpStatus.PENDING, expires_at__lte=now
    ).update(status=OtpStatus.EXPIRED)
    deleted, _ = OtpChallenge.objects.filter(
        created_at__lte=now - timedelta(days=keep_days)
    ).delete()
    return {'expired': expired, 'deleted': deleted}


# --- смена номера телефона -------------------------------------------------

def request_phone_change(*, user, new_phone, request=None):
    """Шлёт код на НОВЫЙ номер. Сам номер меняется только после подтверждения."""
    if new_phone == user.phone:
        raise DomainError(
            code=ErrorCode.VALIDATION_ERROR,
            message='Это уже ваш текущий номер',
            status_code=400,
        )
    if User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
        raise DomainError(
            code=ErrorCode.USER_ALREADY_EXISTS,
            message='Этот номер уже занят другим аккаунтом',
            status_code=409,
        )

    now = timezone.now()
    recent = (
        OtpChallenge.objects.filter(user=user, purpose=OtpPurpose.PHONE_CHANGE)
        .order_by('-created_at').first()
    )
    if recent and (now - recent.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - recent.created_at).total_seconds())
        raise DomainError(
            code=ErrorCode.OTP_COOLDOWN,
            message='Повторная отправка возможна позже',
            status_code=429,
            details={'retry_after': max(wait, 1)},
        )

    OtpChallenge.objects.filter(
        user=user, purpose=OtpPurpose.PHONE_CHANGE, status=OtpStatus.PENDING
    ).update(status=OtpStatus.EXPIRED)

    code = _generate_code(new_phone)
    challenge = OtpChallenge(
        phone=new_phone,
        purpose=OtpPurpose.PHONE_CHANGE,
        user=user,
        status=OtpStatus.PENDING,
        max_attempts=settings.OTP_MAX_ATTEMPTS,
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        request_ip=client_ip(request),
    )
    challenge.set_code(code)
    challenge.save()

    _deliver_code(new_phone, code, user=user, language=user.language)

    result = {
        'challenge_id': str(challenge.id),
        'new_phone': new_phone,
        'expires_in': settings.OTP_TTL_SECONDS,
        'resend_after': settings.OTP_RESEND_COOLDOWN_SECONDS,
    }
    if settings.OTP_DEBUG_RETURN_CODE:
        result['debug_code'] = code
    return result


@transaction.atomic
def confirm_phone_change(*, user, challenge_id, code, request=None):
    """Меняет номер после подтверждения кода с нового телефона.

    public_code и qr_token остаются прежними: карты, история и балансы
    привязаны к пользователю, а не к номеру. О смене уведомляем старый номер.
    """
    challenge = (
        OtpChallenge.objects.select_for_update(of=('self',))
        .filter(pk=challenge_id, user=user, purpose=OtpPurpose.PHONE_CHANGE)
        .first()
    )
    if challenge is None:
        raise DomainError(code=ErrorCode.OTP_INVALID, message='Запрос на смену номера не найден', status_code=404)
    if challenge.status != OtpStatus.PENDING:
        raise DomainError(code=ErrorCode.OTP_INVALID, message='Код уже использован или недействителен')
    if challenge.is_expired:
        challenge.status = OtpStatus.EXPIRED
        challenge.save(update_fields=['status', 'updated_at'])
        raise DomainError(code=ErrorCode.OTP_EXPIRED, message='Срок действия кода истёк')

    _validate_code(challenge, code)

    new_phone = challenge.phone
    if User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
        raise DomainError(
            code=ErrorCode.USER_ALREADY_EXISTS,
            message='Этот номер уже занят другим аккаунтом',
            status_code=409,
        )

    old_phone = user.phone
    user.phone = new_phone
    user.save(update_fields=['phone'])

    challenge.status = OtpStatus.CONSUMED
    challenge.consumed_at = timezone.now()
    challenge.verification_token = ''
    challenge.save(update_fields=['status', 'consumed_at', 'verification_token', 'updated_at'])

    log_action(
        AuditAction.PHONE_CHANGED, actor=user, request=request,
        entity_type='User', entity_id=user.id,
        before={'phone': old_phone}, after={'phone': new_phone},
    )

    # Старый номер должен узнать о смене — на случай, если её сделал не владелец.
    send_sms(old_phone, NotificationEvent.SECURITY_ALERT,
             context={'subject': f'номер аккаунта изменён на {new_phone}'},
             language=user.language, user=user)
    notify_user(user, NotificationEvent.SECURITY_ALERT,
                context={'subject': 'Номер телефона изменён'})

    return user


# --- фото профиля -----------------------------------------------------------

def set_avatar(*, user, image, request=None):
    """Ресайз до 512px, WEBP и срезание EXIF делает поле модели."""
    user.avatar = image
    user.full_clean(exclude=[f.name for f in user._meta.fields if f.name != 'avatar'])
    user.save(update_fields=['avatar'])
    log_action(AuditAction.AVATAR_UPDATED, actor=user, request=request,
               entity_type='User', entity_id=user.id, after={'has_avatar': True})
    return user


def delete_avatar(*, user, request=None):
    if user.avatar:
        user.avatar.delete(save=False)
    user.avatar = None
    user.save(update_fields=['avatar'])
    log_action(AuditAction.AVATAR_UPDATED, actor=user, request=request,
               entity_type='User', entity_id=user.id, after={'has_avatar': False})
    return user


# --- удаление аккаунта ------------------------------------------------------

def request_account_deletion(*, user, request=None):
    """Код уходит на ТЕКУЩИЙ номер. До подтверждения аккаунт не трогаем."""
    if user.role != Role.USER:
        raise PermissionDeniedError(message='Удаление доступно только клиентам')

    now = timezone.now()
    recent = (
        OtpChallenge.objects.filter(user=user, purpose=OtpPurpose.ACCOUNT_DELETE)
        .order_by('-created_at').first()
    )
    if recent and (now - recent.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - recent.created_at).total_seconds())
        raise DomainError(
            code=ErrorCode.OTP_COOLDOWN, message='Повторная отправка возможна позже',
            status_code=429, details={'retry_after': max(wait, 1)},
        )

    OtpChallenge.objects.filter(
        user=user, purpose=OtpPurpose.ACCOUNT_DELETE, status=OtpStatus.PENDING,
    ).update(status=OtpStatus.EXPIRED)

    code = _generate_code(user.phone)
    challenge = OtpChallenge(
        phone=user.phone,
        purpose=OtpPurpose.ACCOUNT_DELETE,
        user=user,
        status=OtpStatus.PENDING,
        max_attempts=settings.OTP_MAX_ATTEMPTS,
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        request_ip=client_ip(request),
    )
    challenge.set_code(code)
    challenge.save()

    _deliver_code(user.phone, code, user=user, language=user.language)

    result = {
        'challenge_id': str(challenge.id),
        'expires_in': settings.OTP_TTL_SECONDS,
        'resend_after': settings.OTP_RESEND_COOLDOWN_SECONDS,
    }
    if settings.OTP_DEBUG_RETURN_CODE:
        result['debug_code'] = code
    return result


@transaction.atomic
def anonymize_user(*, user, request=None, actor=None, notify=True):
    """Обезличивает пользователя. Ledger не трогает — записи под PROTECT.

    Строка в users остаётся: на неё ссылаются неизменяемые транзакции. Но
    персональных данных в ней не остаётся, а телефон и 6-значный код
    освобождаются — человек может зарегистрироваться заново.

    Вызывается и из API (после подтверждения по SMS), и из django-admin, когда
    удаление просят через поддержку и владельца проверил человек.
    """
    from apps.audit.models import AuditLog
    from apps.loyalty.models import (
        CashbackLot, CashbackLotStatus, Gift, GiftStatus,
        RedemptionRequest, RedemptionStatus, UserOrganizationState,
    )
    from apps.notifications.models import Notification
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    old_phone = user.phone
    now = timezone.now()

    # 1. Погасить то, что удалить нельзя (PROTECT).
    CashbackLot.objects.filter(user=user, status=CashbackLotStatus.ACTIVE).update(
        status=CashbackLotStatus.CANCELLED, remaining_amount=0, updated_at=now,
    )
    Gift.objects.filter(
        user=user, status__in=[GiftStatus.AVAILABLE, GiftStatus.PENDING_REDEMPTION],
    ).update(status=GiftStatus.CANCELLED, cancelled_at=now, updated_at=now)
    # Иначе у кассира навсегда зависнет запрос от несуществующего клиента.
    RedemptionRequest.objects.filter(user=user, status=RedemptionStatus.PENDING).update(
        status=RedemptionStatus.CANCELLED, resolved_at=now,
        reject_reason='Аккаунт удалён', updated_at=now,
    )

    # 2. Удалить то, что можно. В states лежат балансы — бонусы сгорают.
    UserOrganizationState.objects.filter(user=user).delete()
    Notification.objects.filter(user=user).delete()
    UserDevice.objects.filter(user=user).delete()

    # Отозвать выданные refresh-токены.
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)

    # 3. Уведомить, пока номер ещё известен. user=None: Notification только
    #    что удалены, новая запись снова привязалась бы к удаляемому.
    if notify:
        send_sms(old_phone, NotificationEvent.SECURITY_ALERT,
                 context={'subject': 'аккаунт PAYDA удалён'},
                 language=user.language, user=None)

    # 4. Обезличить.
    if user.avatar:
        user.avatar.delete(save=False)
    user.phone = f'DELETED-{secrets.token_hex(6)}'   # ровно 20 символов
    user.first_name = ''
    user.last_name = ''
    user.email = ''
    user.birth_date = None
    user.public_code = None
    user.qr_token = None
    user.pin_hash = ''
    user.pin_updated_at = None
    user.is_active = False
    user.is_registration_complete = False
    user.deleted_at = now
    user.save()

    # 5. Аудит: факт фиксируем, телефон из старых записей вычищаем.
    log_action(AuditAction.ACCOUNT_DELETED, actor=actor or user, request=request,
               entity_type='User', entity_id=user.id, after={'deleted_at': now.isoformat()})
    AuditLog.objects.filter(actor_id=user.id).update(actor_phone='')

    return user


@transaction.atomic
def confirm_account_deletion(*, user, challenge_id, code, request=None):
    """Проверяет код и обезличивает аккаунт. Операция необратима."""
    if user.role != Role.USER:
        raise PermissionDeniedError(message='Удаление доступно только клиентам')

    challenge = (
        OtpChallenge.objects.select_for_update(of=('self',))
        .filter(pk=challenge_id, user=user, purpose=OtpPurpose.ACCOUNT_DELETE)
        .first()
    )
    if challenge is None:
        raise DomainError(code=ErrorCode.OTP_INVALID,
                          message='Запрос на удаление не найден', status_code=404)
    if challenge.status != OtpStatus.PENDING:
        raise DomainError(code=ErrorCode.OTP_INVALID, message='Код уже использован')
    if challenge.is_expired:
        challenge.status = OtpStatus.EXPIRED
        challenge.save(update_fields=['status', 'updated_at'])
        raise DomainError(code=ErrorCode.OTP_EXPIRED, message='Срок действия кода истёк')

    _validate_code(challenge, code)

    anonymize_user(user=user, request=request)
    OtpChallenge.objects.filter(user=user).delete()
    return True
