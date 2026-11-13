"""Telegram response handling with a mocked transport; no real messages are sent."""
import io,json,unittest,urllib.error
from unittest.mock import patch
import notifications

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.settings={'telegram_bot_token':'123:private_token','telegram_chat_id':'-1001','telegram_admin_id':'42','telegram_pin_critical':True}
    def response(self,payload=None):
        return io.BytesIO(json.dumps(payload or {'ok':True,'result':{'message_id':7}}).encode())
    def error(self,code=400,description='Bad Request: chat not found',**parameters):
        return urllib.error.HTTPError('https://api.telegram.org/bot123:private_token/sendMessage',code,'private_token',{},self.response({'ok':False,'error_code':code,'description':description,'parameters':parameters}))
    def test_http_error_body_is_reported_with_guidance(self):
        with patch('notifications.urllib.request.urlopen',side_effect=self.error()):result=notifications.send(self.settings,'test')
        self.assertFalse(result['ok']);self.assertIn('HTTP 400 · Bad Request: chat not found',result['errors'][0]);self.assertIn('bot is a member',result['errors'][0]);self.assertNotIn('private_token',str(result))
    def test_description_redacts_token_and_signed_query(self):
        description='Bad Request: 123:private_token https://api.telegram.org/bot123:private_token/sendMessage https://example.com/?signature=secret cookie=secret'
        with patch('notifications.urllib.request.urlopen',side_effect=self.error(description=description)):result=notifications.send(self.settings,'test')
        self.assertNotIn('private_token',str(result));self.assertNotIn('signature=',str(result));self.assertNotIn('cookie=secret',str(result))
    def test_non_json_http_error_never_displays_raw_body(self):
        error=urllib.error.HTTPError('https://example.com',400,'secret',{},io.BytesIO(b'<html>private_token</html>'))
        with patch('notifications.urllib.request.urlopen',side_effect=error):result=notifications.send(self.settings,'test')
        self.assertIn('HTTP 400',str(result));self.assertIn('non-JSON',str(result));self.assertNotIn('private_token',str(result))
    def test_json_failure_with_success_http_status(self):
        with patch('notifications.urllib.request.urlopen',return_value=self.response({'ok':False,'error_code':403,'description':'Forbidden: bot was kicked'})):
            result=notifications.send(self.settings,'test')
        self.assertFalse(result['ok']);self.assertIn('bot was kicked',str(result))
    def test_group_migration_retries_once_then_pins_new_group(self):
        with patch('notifications.urllib.request.urlopen',side_effect=[self.error(description='group upgraded',migrate_to_chat_id=-1001234567890),self.response(),self.response(),self.response()]) as request:
            result=notifications.send(self.settings,'<b>CPU</b>',critical=True)
        self.assertTrue(result['ok']);self.assertEqual(result['migrations'],{'-1001':'-1001234567890'})
        calls=[(c.args[0].full_url.rsplit('/',1)[1],json.loads(c.args[0].data)['chat_id']) for c in request.call_args_list]
        self.assertEqual(calls,[('sendMessage','-1001'),('sendMessage','-1001234567890'),('pinChatMessage','-1001234567890'),('sendMessage','42')])
    def test_migration_loop_is_bounded_and_not_persisted(self):
        with patch('notifications.urllib.request.urlopen',side_effect=[self.error(migrate_to_chat_id=-1002),self.error(migrate_to_chat_id=-1003)]) as request:
            result=notifications.send(self.settings,'test')
        self.assertEqual(request.call_count,2);self.assertFalse(result['ok']);self.assertEqual(result['migrations'],{})
    def test_invalid_migration_does_not_change_recipient(self):
        for value in (42,True,'-1002','https://example.com'):
            with patch('notifications.urllib.request.urlopen',side_effect=self.error(migrate_to_chat_id=value)) as request:
                result=notifications.send(self.settings,'test')
            self.assertEqual(request.call_count,1);self.assertFalse(result['ok'])
    def test_rejected_photo_falls_back_to_text_only_in_group(self):
        with patch('notifications.urllib.request.urlopen',side_effect=[self.error(description='Bad Request: PHOTO_INVALID_DIMENSIONS'),self.response()]) as request:
            result=notifications.send(self.settings,'Download failed',b'fake jpeg')
        self.assertTrue(result['ok']);self.assertTrue(result['warnings'])
        self.assertEqual(json.loads(request.call_args.args[0].data),{'chat_id':'-1001','text':'Download failed'})
        self.assertTrue(request.call_args.args[0].full_url.endswith('/sendMessage'))
    def test_network_timeout_does_not_retry_ambiguous_delivery(self):
        with patch('notifications.urllib.request.urlopen',side_effect=TimeoutError('123:private_token')) as request:
            result=notifications.send(self.settings,'test')
        self.assertEqual(request.call_count,1);self.assertNotIn('private_token',str(result));self.assertIn('network',str(result))
    def test_rate_limit_shows_wait_without_retrying(self):
        with patch('notifications.urllib.request.urlopen',side_effect=self.error(429,'Too Many Requests',retry_after=42)) as request:
            result=notifications.send(self.settings,'test')
        self.assertEqual(request.call_count,1);self.assertIn('Retry after 42 seconds',str(result))

if __name__=='__main__':unittest.main()
