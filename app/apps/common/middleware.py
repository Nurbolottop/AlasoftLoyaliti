import uuid

from apps.common.logging import set_request_id

REQUEST_ID_HEADER = 'HTTP_X_REQUEST_ID'


class RequestIDMiddleware:
    """Сквозной correlation id запроса (ТЗ backend §27)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.request_id = request_id
        set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            set_request_id(None)
        response['X-Request-ID'] = request_id
        return response
