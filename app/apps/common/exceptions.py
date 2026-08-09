"""Единый обработчик исключений → error-envelope (ТЗ backend §22)."""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.errors import DomainError, ErrorCode

logger = logging.getLogger('alasoft.api')

_DRF_CODE_MAP = {
    drf_exceptions.NotAuthenticated: ErrorCode.AUTHENTICATION_REQUIRED,
    drf_exceptions.AuthenticationFailed: ErrorCode.TOKEN_INVALID,
    drf_exceptions.PermissionDenied: ErrorCode.PERMISSION_DENIED,
    drf_exceptions.NotFound: ErrorCode.NOT_FOUND,
    drf_exceptions.MethodNotAllowed: ErrorCode.METHOD_NOT_ALLOWED,
    drf_exceptions.Throttled: ErrorCode.RATE_LIMITED,
    drf_exceptions.ValidationError: ErrorCode.VALIDATION_ERROR,
}


def error_payload(code, message, details=None):
    return {
        'success': False,
        'error': {'code': code, 'message': message, 'details': details or {}},
    }


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response(
            error_payload(exc.code, exc.message, exc.details),
            status=exc.status_code,
        )

    if isinstance(exc, Http404):
        return Response(error_payload(ErrorCode.NOT_FOUND, 'Объект не найден'), status=404)

    if isinstance(exc, DjangoValidationError):
        return Response(
            error_payload(ErrorCode.VALIDATION_ERROR, 'Ошибка валидации',
                          {'fields': getattr(exc, 'message_dict', {'non_field': exc.messages})}),
            status=422,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception('Необработанная ошибка API: %s', exc)
        return Response(
            error_payload(ErrorCode.INTERNAL_ERROR, 'Внутренняя ошибка сервера'),
            status=500,
        )

    code = ErrorCode.INTERNAL_ERROR
    for exc_class, mapped in _DRF_CODE_MAP.items():
        if isinstance(exc, exc_class):
            code = mapped
            break

    details = {}
    message = 'Ошибка запроса'
    data = response.data

    if isinstance(exc, drf_exceptions.ValidationError):
        message = 'Ошибка валидации'
        details = {'fields': data if isinstance(data, (dict, list)) else {'non_field': data}}
        response.status_code = 422
    elif isinstance(data, dict) and 'detail' in data:
        message = str(data['detail'])
    else:
        details = {'raw': data}

    if isinstance(exc, drf_exceptions.Throttled) and exc.wait:
        details['retry_after'] = int(exc.wait)

    response.data = error_payload(code, message, details)
    return response
