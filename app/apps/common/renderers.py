"""Единый envelope ответов API (ТЗ backend §22)."""

from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    """Оборачивает успешные ответы в {success, data, meta}.

    Ошибки формирует apps.common.exceptions.api_exception_handler — он кладёт
    в data готовый конверт с ключом ``error``, повторно его не заворачиваем.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        renderer_context = renderer_context or {}
        response = renderer_context.get('response')

        if data is None:
            payload = {'success': True, 'data': None}
        elif isinstance(data, dict) and 'error' in data and data.get('success') is False:
            payload = data
        elif isinstance(data, dict) and set(data.keys()) <= {'success', 'data', 'meta'} and 'data' in data:
            payload = data
        elif isinstance(data, dict) and 'results' in data and 'pagination' in data:
            # Постраничный ответ из apps.common.pagination
            payload = {'success': True, 'data': data['results'], 'meta': {'pagination': data['pagination']}}
        else:
            payload = {'success': True, 'data': data}

        if response is not None and response.status_code >= 400 and 'error' not in payload:
            payload = {
                'success': False,
                'error': {'code': 'INTERNAL_ERROR', 'message': 'Ошибка запроса', 'details': data},
            }

        return super().render(payload, accepted_media_type, renderer_context)
