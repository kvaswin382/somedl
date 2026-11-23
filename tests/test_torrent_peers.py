"""Peer settings affect session defaults, new torrents and existing owned torrents."""
import http.client
import json
import threading
from unittest.mock import Mock
from test_desk import Fixture
from server import Server,Handler
import engine_settings


class PeerTests(Fixture):
    def test_full_slider_ranges_and_strict_validation(self):
        for key,maximum in [('torrent_peers_global',1000),('torrent_peers_per_torrent',150)]:
            self.assertEqual(engine_settings.schema()[key],{'min':10,'max':maximum,'step':1})
            for value in (10,maximum):self.assertEqual(engine_settings.checked({key:value}),{key:value})
            for invalid in (9,maximum+1,True,10.5):
                with self.subTest(key=key,value=invalid),self.assertRaises(ValueError):engine_settings.checked({key:invalid})

    def test_maximum_values_not_reduced_by_memory_ceiling_and_existing_limits_updated(self):
        self.desk.set_setting('torrent_memory_mib',128)
        self.desk.set_setting('torrent_peers_global',1000);self.desk.set_setting('torrent_peers_per_torrent',150)
        self.desk.rpc.call=Mock(side_effect=lambda method,args:{'torrents':[
            {'id':1,'downloadDir':str(self.desk.root)},
            {'id':2,'downloadDir':str(self.desk.root/'subfolder')},
            {'id':3,'downloadDir':str(self.base/'unmanaged')}]} if method=='torrent-get' else {})
        self.desk.apply_torrent_settings()
        session=self.desk.rpc.call.call_args_list[0].args[1]
        self.assertEqual(session['peer-limit-global'],1000);self.assertEqual(session['peer-limit-per-torrent'],150)
        self.desk.rpc.call.assert_called_with('torrent-set',{'ids':[1,2],'peer-limit':150})
        self.desk.set_setting('torrent_peers_global',10);self.desk.set_setting('torrent_peers_per_torrent',10)
        self.desk.apply_torrent_settings()
        self.desk.rpc.call.assert_called_with('torrent-set',{'ids':[1,2],'peer-limit':10})

    def test_empty_owned_list_never_sends_unscoped_torrent_set(self):
        self.desk.rpc.call=Mock(return_value={'torrents':[{'id':3,'downloadDir':str(self.base/'other')}]})
        self.desk.apply_torrent_settings()
        self.assertEqual([c.args[0] for c in self.desk.rpc.call.call_args_list],['session-set','torrent-get'])
        args=self.desk.rpc.call.call_args_list[0].args[1]
        self.assertEqual(args['peer-limit-global'],64);self.assertEqual(args['peer-limit-per-torrent'],20)

    def test_http_save_marks_settings_dirty_and_new_torrents_use_saved_peer_limit(self):
        site=Server(('127.0.0.1',0),Handler);site.desk=self.desk
        thread=threading.Thread(target=site.serve_forever,daemon=True);thread.start()
        token,csrf=self.desk.new_session();self.desk.rpc.call=Mock(return_value={'torrent-added':{'hashString':'a'*40,'name':'Fixture'}})
        def post(path,data):
            conn=http.client.HTTPConnection('127.0.0.1',site.server_port,timeout=5)
            try:
                conn.request('POST','/api/'+path,body=json.dumps(data),headers={
                    'Content-Type':'application/json','Cookie':'zedair_session='+token,'X-Zedair-CSRF':csrf})
                response=conn.getresponse();body=json.loads(response.read());return response.status,body
            finally:conn.close()
        try:
            for key,value in [('torrent_peers_global',1000),('torrent_peers_per_torrent',150)]:
                self.desk.torrent_settings_dirty=False
                self.assertEqual(post('settings',{key:value})[0],200)
                self.assertTrue(self.desk.torrent_settings_dirty)
                self.assertEqual(self.desk.get_setting(key),value)
            self.assertEqual(post('settings',{'torrent_peers_global':1001})[0],400)
            self.assertEqual(self.desk.get_setting('torrent_peers_global'),1000)
            self.assertEqual(post('torrents',{'magnet':'magnet:?xt=urn:btih:'+'a'*40})[0],200)
            addition=next(c for c in self.desk.rpc.call.call_args_list if c.args[0]=='torrent-add')
            self.assertEqual(addition.args[1]['peer-limit'],150)
        finally:site.shutdown();site.server_close();thread.join()
