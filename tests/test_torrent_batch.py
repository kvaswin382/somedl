import base64
import time
from unittest.mock import Mock
from test_desk import Fixture
from server import APIError
from torrent_add import add,fresh_copy,dismiss

KEY='a'*40
META=base64.b64encode(b'd4:infodee').decode()


class TorrentBatchTests(Fixture):
    def setUp(self):
        super().setUp()
        self.old=self.desk.root/'Old';self.old.mkdir();(self.old/'video.bin').write_bytes(b'keep me')
        self.item={'id':1,'hashString':KEY,'name':'Example','downloadDir':str(self.old)}
        self.duplicate=True
        def rpc(method,args=None):
            if method=='torrent-add':return {'torrent-duplicate' if self.duplicate else 'torrent-added':self.item.copy()}
            if method=='torrent-get':return {'torrents':[self.item.copy()]}
            if method=='torrent-set-location':self.item['downloadDir']=args['location']
            return {}
        self.desk.rpc.call=Mock(side_effect=rpc)

    def submit(self,**options):return add(self.desk,{'folder':'Torrents','metainfo':META,**options})
    def methods(self):return [c.args[0] for c in self.desk.rpc.call.call_args_list]

    def test_duplicate_without_confirmation_does_not_start_stop_move_or_remove(self):
        result=self.submit()
        self.assertEqual(result['status'],'duplicate')
        self.assertEqual(self.methods(),['torrent-add','torrent-get'])
        self.assertTrue(self.desk.rpc.call.call_args_list[0].args[1]['paused'])
        self.assertEqual((self.old/'video.bin').read_bytes(),b'keep me')
        self.assertFalse((self.desk.root/result['folder']).exists())
        with self.assertRaises(APIError):fresh_copy(self.desk,{'token':result['token']})
        self.assertEqual(self.methods(),['torrent-add','torrent-get'])
        dismiss(self.desk,{'token':result['token']})
        self.assertNotIn(result['token'],self.desk.torrent_confirmations)
        self.assertEqual(self.methods(),['torrent-add','torrent-get'])

    def test_fresh_copy_preserves_old_bytes_and_does_not_move_delete_or_duplicate_entry(self):
        result=self.submit();self.desk.rpc.call.reset_mock()
        done=fresh_copy(self.desk,{'token':result['token'],'confirm':True})
        self.assertEqual(self.methods(),['torrent-get','torrent-stop','torrent-set-location','torrent-verify','torrent-start'])
        move=self.desk.rpc.call.call_args_list[2].args[1]
        self.assertIs(move['move'],False);self.assertEqual(move['ids'],[KEY])
        self.assertEqual(list((self.desk.root/done['folder']).iterdir()),[])
        self.assertEqual((self.old/'video.bin').read_bytes(),b'keep me')
        count=len(self.methods());self.assertEqual(fresh_copy(self.desk,{'token':result['token'],'confirm':True}),done)
        self.assertEqual(len(self.methods()),count)

    def test_paused_new_and_duplicate_torrents_stay_paused(self):
        result=self.submit(paused=True);fresh_copy(self.desk,{'token':result['token'],'confirm':True})
        self.assertNotIn('torrent-start',self.methods())
        self.duplicate=False;self.desk.rpc.call.reset_mock()
        self.assertEqual(self.submit(paused=True)['status'],'added')
        self.assertEqual(self.methods(),['torrent-add'])
        self.submit();self.assertEqual(self.methods()[-1],'torrent-start')

    def test_stale_expired_unowned_or_held_confirmation_has_no_side_effects(self):
        for kind in ('stale','expired','unowned','hold'):
            with self.subTest(kind=kind):
                self.item['downloadDir']=str(self.old);self.desk.set_setting('hold',False)
                result=self.submit();self.desk.rpc.call.reset_mock()
                if kind=='stale':self.item['downloadDir']=str(self.desk.root/'Elsewhere')
                if kind=='expired':self.desk.torrent_confirmations[result['token']]['expires']=time.time()-1
                if kind=='unowned':self.item['downloadDir']=str(self.base)
                if kind=='hold':self.desk.set_setting('hold',True)
                with self.assertRaises(APIError):fresh_copy(self.desk,{'token':result['token'],'confirm':True})
                self.assertFalse(any(m in self.methods() for m in ('torrent-stop','torrent-set-location','torrent-start')))

    def test_failed_verify_leaves_torrent_stopped_without_touching_old_files(self):
        result=self.submit();original=self.desk.rpc.call.side_effect
        def fail(method,args=None):
            if method=='torrent-verify':raise APIError('Fixture verification failure')
            return original(method,args)
        self.desk.rpc.call.side_effect=fail
        with self.assertRaisesRegex(APIError,'old files were kept'):fresh_copy(self.desk,{'token':result['token'],'confirm':True})
        self.assertEqual(self.methods()[-1],'torrent-stop');self.assertNotIn('torrent-start',self.methods())
        self.assertEqual((self.old/'video.bin').read_bytes(),b'keep me')
        with self.assertRaisesRegex(APIError,'attempt failed'):fresh_copy(self.desk,{'token':result['token'],'confirm':True})

    def test_bad_input_and_fresh_folder_collision_do_not_mutate_existing_torrent(self):
        for options in ({'metainfo':''},{'magnet':'not a magnet'},{'paused':'yes'},{'folder':'../outside'}):
            with self.subTest(options=options),self.assertRaises((APIError,ValueError)):self.submit(**options)
        self.assertFalse(self.desk.rpc.call.called)
        result=self.submit();target=self.desk.root/result['folder'];target.mkdir();(target/'other.txt').write_bytes(b'other')
        self.desk.rpc.call.reset_mock()
        with self.assertRaises(FileExistsError):fresh_copy(self.desk,{'token':result['token'],'confirm':True})
        self.assertNotIn('torrent-stop',self.methods());self.assertEqual((target/'other.txt').read_bytes(),b'other')
