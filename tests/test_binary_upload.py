"""Real HTTP coverage for bounded, authenticated binary file uploads."""
import http.client
import json
import socket
import threading
import time
from test_desk import Fixture
from server import Handler,Server
from storage_tools import upload,UPLOAD_CHUNK_BYTES


class BinaryUploadTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server=Server(('127.0.0.1',0),Handler);self.server.desk=self.desk
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        token,csrf=self.desk.new_session()
        self.headers={'Cookie':'zedair_session='+token,'X-Zedair-CSRF':csrf,'Content-Type':'application/octet-stream'}

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()

    def start(self,size,name='file.bin'):
        return upload(self.desk,{'action':'start','size':size,'name':name})['id']

    def send(self,key,offset,body,extra=None):
        headers=dict(self.headers,**{'X-Zedair-Upload':key,'X-Zedair-Offset':str(offset)});headers.update(extra or {})
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            connection.request('POST','/api/files/upload',body=body,headers=headers)
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def test_multichunk_exact_bytes_no_overwrite_or_replayed_append(self):
        content=bytes(range(256))*(UPLOAD_CHUNK_BYTES//256)+b'last chunk'
        (self.desk.root/'file.bin').write_bytes(b'existing')
        key=self.start(len(content))
        code,result=self.send(key,0,content[:UPLOAD_CHUNK_BYTES])
        self.assertEqual((code,result['offset']),(200,UPLOAD_CHUNK_BYTES))
        self.assertEqual(self.send(key,0,b'replayed')[0],400)
        self.assertEqual(self.send(key,UPLOAD_CHUNK_BYTES,content[UPLOAD_CHUNK_BYTES:])[0],200)
        result=upload(self.desk,{'action':'finish','id':key})
        self.assertEqual((self.desk.root/result['path']).read_bytes(),content)
        self.assertEqual((self.desk.root/'file.bin').read_bytes(),b'existing')
        self.assertFalse(self.desk.tasks.uploads)

    def test_auth_csrf_origin_and_declared_length_rejected_before_body(self):
        key=self.start(20)
        cases=[({'Cookie':''},401),({'X-Zedair-CSRF':'wrong'},403),({'Origin':'https://other.example'},403),
               ({'Content-Length':str(UPLOAD_CHUNK_BYTES+1)},413),({'Content-Length':'0'},413),
               ({'Content-Length':'bad'},411),({'Transfer-Encoding':'chunked'},400),
               ({'X-Zedair-Offset':'-1'},400)]
        for extra,status in cases:
            with self.subTest(extra=extra):
                # No bytes sent: validation must not wait for the advertised body.
                self.assertEqual(self.send(key,0,None,dict({'Content-Length':'20'},**extra))[0],status)
        self.assertEqual(self.desk.tasks.uploads[key]['temp'].stat().st_size,0)

    def test_interrupted_chunk_is_not_appended_and_can_be_retried(self):
        key=self.start(12)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request('POST','/api/files/upload',body=b'part',headers=dict(self.headers,**{
            'X-Zedair-Upload':key,'X-Zedair-Offset':'0','Content-Length':'12'}))
        connection.sock.shutdown(socket.SHUT_WR)
        response=connection.getresponse();self.assertEqual(response.status,400);response.read();connection.close()
        self.assertEqual(self.desk.tasks.uploads[key]['offset'],0)
        self.assertEqual(self.desk.tasks.uploads[key]['temp'].stat().st_size,0)
        self.assertEqual(self.send(key,0,b'complete now')[0],200)

    def test_slow_network_read_does_not_hold_global_locks(self):
        key=self.start(20)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request('POST','/api/files/upload',body=b'a',headers=dict(self.headers,**{
            'X-Zedair-Upload':key,'X-Zedair-Offset':'0','Content-Length':'20'}))
        try:
            # A second upload and the UI's database access must remain responsive.
            second=self.start(1,'second.bin')
            self.assertEqual(self.send(second,0,b'b')[0],200)
            self.assertTrue(self.desk.file_lock.acquire(timeout=1));self.desk.file_lock.release()
            self.assertTrue(self.desk.lock.acquire(timeout=1));self.desk.lock.release()
        finally:
            connection.sock.shutdown(socket.SHUT_WR)
            response=connection.getresponse();response.read();connection.close()

    def test_size_and_free_space_checks_preserve_partial_upload(self):
        key=self.start(2)
        self.assertEqual(self.send(key,0,b'too much')[0],400)
        self.desk.set_setting('reserve_gib',1000000)
        self.assertEqual(self.send(key,0,b'ok')[0],409)
        self.assertEqual(self.desk.tasks.uploads[key]['temp'].stat().st_size,0)
