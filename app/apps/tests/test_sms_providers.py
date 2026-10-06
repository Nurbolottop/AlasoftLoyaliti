from unittest import mock

import pytest
import requests

from apps.notifications.providers.sms import GreenApiSmsProvider, SmsSendError

TOKEN = 'secret-token'


@pytest.fixture
def greenapi(settings):
    settings.GREENAPI_API_URL = 'https://7105.api.greenapi.com/'
    settings.GREENAPI_ID_INSTANCE = '1101'
    settings.GREENAPI_API_TOKEN = TOKEN
    return GreenApiSmsProvider()


def test_greenapi_sends_to_whatsapp_chat(greenapi):
    response = mock.Mock(status_code=200)
    response.json.return_value = {'idMessage': 'ABC123'}
    with mock.patch('apps.notifications.providers.sms.requests.post', return_value=response) as post:
        assert greenapi.send('+996555123456', 'код 123456') == 'ABC123'

    url = post.call_args.args[0]
    assert url == f'https://7105.api.greenapi.com/waInstance1101/sendMessage/{TOKEN}'
    assert post.call_args.kwargs['json']['chatId'] == '996555123456@c.us'
    assert post.call_args.kwargs['json']['message'] == 'код 123456'


def test_greenapi_error_does_not_leak_token(greenapi):
    response = requests.Response()
    response.status_code = 401
    response.url = f'https://7105.api.greenapi.com/waInstance1101/sendMessage/{TOKEN}'
    with mock.patch('apps.notifications.providers.sms.requests.post', return_value=response):
        with pytest.raises(SmsSendError) as exc_info:
            greenapi.send('+996555123456', 'код')

    assert '401' in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)


def test_greenapi_without_credentials_fails_fast(settings):
    settings.GREENAPI_API_TOKEN = ''
    with mock.patch('apps.notifications.providers.sms.requests.post') as post:
        with pytest.raises(SmsSendError):
            GreenApiSmsProvider().send('+996555123456', 'код')
    post.assert_not_called()
