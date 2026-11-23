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
        self.exists=True
        def rpc(method,args=None):
            if method=='torrent-add':
                if self.exists:return {'torrent-duplicate':self.item.copy()}
                self.exists=True;self.item['downloadDir']=args['download-dir']
                return {'torrent-added':self.item.copy()}
            if method=='torrent-get':return {'torrents':[self.item.copy()] if self.exists else []}
            if method=='torrent-remove':
                self.assertIs(args['delete-local-data'],False);self.exists=False
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
        with self.assertRaisesRegex(APIError,'original torrent file'):fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META+'x'})
        dismiss(self.desk,{'token':result['token']})
        self.assertNotIn(result['token'],self.desk.torrent_confirmations)
        self.assertEqual(self.methods(),['torrent-add','torrent-get'])

    def test_fresh_copy_preserves_old_bytes_and_does_not_move_delete_or_duplicate_entry(self):
        result=self.submit();self.desk.rpc.call.reset_mock()
        done=fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
        self.assertEqual(self.methods(),['torrent-get','torrent-stop','torrent-remove','torrent-add','torrent-verify','torrent-start'])
        removal=self.desk.rpc.call.call_args_list[2].args[1]
        self.assertIs(removal['delete-local-data'],False);self.assertEqual(removal['ids'],[KEY])
        replacement=self.desk.rpc.call.call_args_list[3].args[1]
        self.assertEqual(replacement['download-dir'],str(self.desk.root/done['folder']));self.assertIs(replacement['paused'],True)
        self.assertEqual(list((self.desk.root/done['folder']).iterdir()),[])
        self.assertEqual((self.old/'video.bin').read_bytes(),b'keep me')
        count=len(self.methods());self.assertEqual(fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META}),done)
        self.assertEqual(len(self.methods()),count)

    def test_paused_new_and_duplicate_torrents_stay_paused(self):
        result=self.submit(paused=True);fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
        self.assertNotIn('torrent-start',self.methods())
        self.exists=False;self.desk.rpc.call.reset_mock()
        self.assertEqual(self.submit(paused=True)['status'],'added')
        self.assertEqual(self.methods(),['torrent-add'])
        self.exists=False;self.submit();self.assertEqual(self.methods()[-1],'torrent-start')

    def test_stale_expired_unowned_or_held_confirmation_has_no_side_effects(self):
        for kind in ('stale','expired','unowned','hold'):
            with self.subTest(kind=kind):
                self.item['downloadDir']=str(self.old);self.desk.set_setting('hold',False)
                result=self.submit();self.desk.rpc.call.reset_mock()
                if kind=='stale':self.item['downloadDir']=str(self.desk.root/'Elsewhere')
                if kind=='expired':self.desk.torrent_confirmations[result['token']]['expires']=time.time()-1
                if kind=='unowned':self.item['downloadDir']=str(self.base)
                if kind=='hold':self.desk.set_setting('hold',True)
                with self.assertRaises(APIError):fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
                self.assertFalse(any(m in self.methods() for m in ('torrent-stop','torrent-set-location','torrent-start')))

    def test_failed_verify_leaves_torrent_stopped_without_touching_old_files(self):
        result=self.submit();original=self.desk.rpc.call.side_effect
        def fail(method,args=None):
            if method=='torrent-verify':raise APIError('Fixture verification failure')
            return original(method,args)
        self.desk.rpc.call.side_effect=fail
        with self.assertRaisesRegex(APIError,'old files were kept'):fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
        self.assertEqual(self.methods()[-1],'torrent-stop');self.assertNotIn('torrent-start',self.methods())
        self.assertTrue(self.exists);self.assertEqual(self.item['downloadDir'],str(self.old))
        self.assertEqual((self.old/'video.bin').read_bytes(),b'keep me')
        with self.assertRaisesRegex(APIError,'attempt failed'):fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})

    def test_bad_input_and_fresh_folder_collision_do_not_mutate_existing_torrent(self):
        for options in ({'metainfo':''},{'magnet':'not a magnet'},{'paused':'yes'},{'folder':'../outside'}):
            with self.subTest(options=options),self.assertRaises((APIError,ValueError)):self.submit(**options)
        self.assertFalse(self.desk.rpc.call.called)
        result=self.submit();target=self.desk.root/result['folder'];target.mkdir();(target/'other.txt').write_bytes(b'other')
        self.desk.rpc.call.reset_mock()
        with self.assertRaises(FileExistsError):fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
        self.assertNotIn('torrent-stop',self.methods());self.assertEqual((target/'other.txt').read_bytes(),b'other')

    def test_failed_readd_restores_old_registration_paused(self):
        result=self.submit();original=self.desk.rpc.call.side_effect
        def fail(method,args=None):
            if method=='torrent-add' and args['download-dir']!=str(self.old):raise APIError('Fixture add failure')
            return original(method,args)
        self.desk.rpc.call.side_effect=fail
        with self.assertRaisesRegex(APIError,'restored and paused'):fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
        self.assertTrue(self.exists);self.assertEqual(self.item['downloadDir'],str(self.old))
        self.assertEqual((self.old/'video.bin').read_bytes(),b'keep me')
        self.assertNotIn('torrent-start',self.methods())

    def test_file_selection_and_priorities_are_retained(self):
        self.item['fileStats']=[{'wanted':False,'priority':1},{'wanted':True,'priority':-1}]
        result=self.submit()
        fresh_copy(self.desk,{'token':result['token'],'confirm':True,'metainfo':META})
        replacement=[c.args[1] for c in self.desk.rpc.call.call_args_list if c.args[0]=='torrent-add'][-1]
        self.assertEqual(replacement['files-unwanted'],[0]);self.assertEqual(replacement['files-wanted'],[1])
        self.assertEqual(replacement['priority-high'],[0]);self.assertEqual(replacement['priority-low'],[1])

    def test_duplicate_magnet_requests_metadata_without_mutating_existing_torrent(self):
        result=add(self.desk,{'folder':'Torrents','magnet':'magnet:?xt=urn:btih:'+KEY})
        self.assertEqual(result['status'],'duplicate');self.assertIs(result['can_fresh'],False)
        self.assertFalse(self.desk.torrent_confirmations)
        self.assertEqual(self.methods(),['torrent-add','torrent-get'])
