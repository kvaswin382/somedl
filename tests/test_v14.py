import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from test_desk import Fixture
import test_v13 as previous
from media import build_command
from publishing import publish
from fileops import manage,delete_folder,inspect_folder
from browser_video import media_kind,pick,run as video_run
from instagram_profile import validate_connection,feeds_for,compatibility_posts
import platforms
from server import APIError

class FeatureTests(Fixture):
    def setUp(self):
        super().setUp();self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
    def stamp(self,p):return f'{p.stat().st_size}:{p.stat().st_mtime_ns}'
    def test_explicit_destination_and_batch100_custom_filename(self):
        result=self.desk.add_jobs({'urls':[f'https://example.com/video/{i}' for i in range(100)],'folder':'Chosen','filename':'My video'})
        self.assertEqual(len(result['ids']),100)
        j=self.desk.job(result['ids'][0]);self.assertEqual((j['subdir'],j['filename']),('','My video'))
        root=self.desk.add_jobs({'urls':['https://youtube.com/watch?v=root'],'folder_selected':True})
        self.assertEqual(self.desk.job(root['ids'][0])['subdir'],'')
        auto=self.desk.job(self.create_job());self.assertEqual(auto['subdir'],'YouTube')
        with self.assertRaises((ValueError,APIError)):self.desk.add_jobs({'urls':['https://example.com']*101})
        with self.assertRaises((ValueError,APIError)):self.desk.add_jobs({'urls':['https://example.com'],'filename':'../bad'})
    def test_custom_name_keeps_real_extension_and_numbers_outputs(self):
        jid=self.create_job();self.desk.update_job(jid,filename='My film.mp4');j=self.desk.job(jid)
        stage=self.desk.staging(jid);(stage/'a.webm').write_bytes(b'one');(stage/'b.webm').write_bytes(b'two')
        output=self.desk.destination('Chosen',create=True);publish(self.desk,j,stage,output)
        self.assertEqual(sorted(p.name for p in output.iterdir()),['My film (1).webm','My film.webm'])
    def test_rename_move_copy_collision_dates_and_bytes(self):
        root=self.desk.root;source=root/'old.jpg';source.write_bytes(b'bytes'*1000)
        self.desk.destination('B',create=True);self.desk.destination('C',create=True)
        self.desk.write('INSERT INTO file_dates VALUES(?,?,?,?,?)',('',source.name,source.stat().st_size,source.stat().st_mtime_ns,123))
        (root/'new.jpg').write_bytes(b'keep')
        result=manage(self.desk,{'action':'rename','path':'old.jpg','name':'new','stamp':self.stamp(source)})
        self.assertEqual(result['path'],'new (1).jpg');source=root/result['path']
        manage(self.desk,{'action':'copy','path':result['path'],'folder':'B','stamp':self.stamp(source)})
        self.assertTrue(source.exists());self.assertEqual((root/'B'/source.name).read_bytes(),source.read_bytes())
        result=manage(self.desk,{'action':'move','path':source.name,'folder':'C','stamp':self.stamp(source)})
        self.assertFalse(source.exists());self.assertEqual((root/result['path']).read_bytes(),b'bytes'*1000)
        dates=self.desk.rows('SELECT downloaded FROM file_dates');self.assertEqual([r['downloaded'] for r in dates],[123,123])
        self.assertEqual((root/'new.jpg').read_bytes(),b'keep')
    def test_file_guards_and_folder_confirmation(self):
        p=self.desk.root/'one.mp4';p.write_bytes(b'media');self.desk.destination('Empty',create=True)
        for data in ({'action':'move','folder':'../escape','stamp':self.stamp(p)}, {'action':'rename','name':'oops','stamp':'stale'}):
            with self.assertRaises((ValueError,APIError)):manage(self.desk,{'path':'one.mp4',**data})
        with self.assertRaises((ValueError,APIError)):delete_folder(self.desk,{'path':'','confirm':True})
        with self.assertRaises((ValueError,APIError)):delete_folder(self.desk,{'path':'Empty'})
        delete_folder(self.desk,{'path':'Empty','confirm':True,**inspect_folder(self.desk,{'path':'Empty'})});self.assertFalse((self.desk.root/'Empty').exists())
        self.desk.destination('Nonempty',create=True);(self.desk.root/'Nonempty/keep').touch()
        with self.assertRaises((ValueError,APIError)):delete_folder(self.desk,{'path':'Nonempty','confirm':True})
        self.desk.rpc.call=lambda *a,**k:{'torrents':[{'downloadDir':str(self.desk.root),'files':[{'name':'one.mp4'}]}]}
        with self.assertRaises((ValueError,APIError)):manage(self.desk,{'path':'one.mp4','action':'rename','name':'new','stamp':self.stamp(p)})
    def test_profile_images_avatar_and_long_cooldown(self):
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture'}})
        for mode in ('everything','avatar'):
            jid=self.desk.add_profile({'platform':'instagram','profile':'fixture','mode':mode,'images_only':True})['id'];job=self.desk.job(jid)
            args=build_command(job,self.desk.staging(jid),self.desk.cookie_for(job),self.desk.settings(),self.desk.state)
            spec=json.loads(Path(args[2]).read_text());self.assertFalse(spec['config']['extractor']['instagram']['videos'])
            self.assertEqual(spec['config']['extractor']['instagram']['sleep-download'],'15-40')
            self.assertEqual(job['subdir'],'Instagram/fixture')
        jid=self.desk.add_profile({'platform':'instagram','profile':'other'})['id']
        with patch('server.validate_url'),patch('server.build_command',return_value=[sys.executable,'-c',"print('429 Too Many Requests');raise SystemExit(1)"]):self.desk.run_job(self.desk.job(jid))
        job=self.desk.job(jid);self.assertEqual(job['status'],'retry');self.assertGreaterEqual(job['next_try']-job['updated'],1790)
    def test_browser_detection_selection_and_safe_failure(self):
        self.assertEqual(media_kind('https://cdn/a.m3u8?q=x'),'manifest')
        self.assertEqual(media_kind('https://cdn/a.m4s','video/mp4'),'')
        candidates=[{'url':'https://cdn/a.mp4','kind':'video'},{'url':'https://cdn/b.mp4','kind':'video'}]
        self.assertEqual(pick(candidates,{'https://cdn/b.mp4'})['url'],'https://cdn/b.mp4')
        with self.assertRaises((ValueError,APIError)):pick(candidates,set())
        with patch('browser_video.execute',return_value=(1,'DRM unsupported URL')),patch('browser_video.detect') as detect:
            self.assertEqual(video_run({'args':[],'enabled':True}),1);detect.assert_not_called()
    def test_compatibility_feed_paginates_and_refuses_silent_end(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        calls=[];cursors=[]
        responses=[{'data':{'user':{'id':'123'}}},{'items':[{'id':1,'reel':False},{'id':2,'reel':True}],'more_available':True,'next_max_id':'page2'},{'items':[{'id':3,'reel':False}],'more_available':False}]
        api=SimpleNamespace(_call=Mock(side_effect=responses))
        ext=SimpleNamespace(api=api,item='fixture',_is_reel=lambda post:post['reel'],_update_cursor=cursors.append)
        self.assertEqual([p['id'] for p in compatibility_posts(ext,{},'photos')],[1,3]);self.assertEqual(cursors,['page2',None])
        self.assertEqual(api._call.call_args.kwargs['params']['max_id'],'page2')
        api._call=Mock(side_effect=[responses[0],{'items':[]}])
        with self.assertRaises(ValueError):list(compatibility_posts(ext,{},'posts'))

    def test_avatar_remains_avatar_with_images_checkbox(self):
        self.assertEqual(feeds_for('avatar',True),['avatar']);self.assertEqual(feeds_for('everything',True),['posts'])

    def test_invalid_profile_response_cannot_be_marked_complete(self):
        for data in (None,{}, {'edges':[]}, {'edges':[],'page_info':{'has_next_page':True}}):
            with self.assertRaises((ValueError,APIError)):validate_connection(data)
        self.assertTrue(validate_connection({'edges':[],'page_info':{'has_next_page':False}}))

class NewHTTPTests(Fixture):
    setUp=previous.ProfileHTTPTests.setUp
    tearDown=previous.ProfileHTTPTests.tearDown
    request=previous.ProfileHTTPTests.request
    login=previous.ProfileHTTPTests.login
    def test_preview_neighbors_across_pages_and_file_permissions(self):
        self.assertEqual(self.request('/api/file/manage',{'action':'move'},cookie=False)[0],401)
        self.assertEqual(self.request('/api/folders/delete',{},cookie=False)[0],401)
        self.assertEqual(self.request('/api/progress',cookie=False)[0],401)
        self.login()
        with patch.object(self.desk,'snapshot',side_effect=AssertionError('Full snapshot should not run')):
            self.assertEqual(self.request('/api/progress')[0],200)
        self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
        for i in range(110):(self.desk.root/f'{i:03}.jpg').write_bytes(b'image')
        (self.desk.root/'024.txt').write_text('not previewable')
        code,result,_=self.request('/api/folders?sort=name&order=asc&adjacent=023.jpg&direction=1')
        self.assertEqual(code,200);self.assertEqual(result['entry']['name'],'024.jpg')
        code,result,_=self.request('/api/folders?sort=name&order=asc&adjacent=000.jpg&direction=-1');self.assertIsNone(result['entry'])
        self.assertEqual(self.request('/api/folders?adjacent=missing.jpg')[0],404)
        code,result,_=self.request('/api/folders?limit=100&offset=100');self.assertEqual(len(result['entries']),11)

    def test_new_file_endpoints_require_auth_and_confirm_snapshot(self):
        for route in ('/api/files/manage','/api/folders/inspect'):
            self.assertEqual(self.request(route,{},cookie=False)[0],401)
        self.login();self.desk.rpc.call=lambda *a,**k:{'torrents':[]}
        folder=self.desk.destination('Remove/Sub',create=True);(folder/'a.jpg').write_bytes(b'image')
        code,info,_=self.request('/api/folders/inspect',{'path':'Remove'});self.assertEqual(code,200);self.assertEqual(info['files'],1)
        self.assertEqual(self.request('/api/folders/delete',{'path':'Remove','confirm':True})[0],400)
        self.assertEqual(self.request('/api/folders/delete',{'path':'Remove','confirm':True,'stamp':info['stamp']})[0],200)
        self.assertFalse(folder.exists())
