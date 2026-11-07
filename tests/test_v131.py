import json
import os
import unittest
from unittest.mock import patch
from test_desk import Fixture
import test_v13 as previous
from media import build_command
from publishing import publish
import platforms


class ProfileRecoveryTests(Fixture):
    def test_upgrade_repairs_false_count_and_publish_phase_once(self):
        platforms.save_cookies(self.desk.state,'instagram',{'fields':{'sessionid':'fixture-session'}})
        jid=self.desk.add_profile({'platform':'instagram','profile':'fixture'})['id']
        self.desk.update_job(jid,items=90,status='error',phase='publish')
        direct=self.create_job('https://instagram.com/p/direct/')
        self.desk.update_job(direct,items=7,phase='publish')
        stage=self.desk.staging(jid);(stage/'.profile-archive.sqlite3').write_bytes(b'old archive retained')
        self.desk.write("DELETE FROM settings WHERE key='instagram_download_fix_v131'")
        self.desk.close();self.desk=type(self.desk)(self.config)
        job=self.desk.job(jid)
        self.assertEqual((job['items'],job['phase'],job['status']),(0,'download','error'))
        self.assertEqual(self.desk.job(direct)['items'],7);self.assertEqual(self.desk.job(direct)['phase'],'publish')
        args=build_command(job,stage,None,self.desk.settings(),self.desk.state)
        self.assertIn('--Print',args);self.assertNotIn('--print',args)
        conf=json.loads((self.desk.state/('gallery-'+jid+'.json')).read_text())
        self.assertTrue(conf['extractor']['archive'].endswith('.profile-downloads-v2.sqlite3'))
        self.assertEqual((stage/'.profile-archive.sqlite3').read_bytes(),b'old archive retained')
        self.desk.update_job(jid,items=4)
        self.desk.close();self.desk=type(self.desk)(self.config)
        self.assertEqual(self.desk.job(jid)['items'],4)

    def test_publish_dates_survive_history_clear_and_preserve_source_mtime(self):
        jid=self.create_job();job=self.desk.job(jid);stage=self.desk.staging(jid)
        source=stage/'old-source.mp4';source.write_bytes(b'payload');os.utime(source,(1234,1234))
        output=self.desk.destination('YouTube',create=True)
        with patch('publishing.time.time',return_value=9000):publish(self.desk,job,stage,output)
        row=self.desk.rows('SELECT * FROM file_dates')[0]
        self.assertEqual(row['downloaded'],9000);self.assertEqual(row['folder'],'YouTube')
        self.assertEqual((output/'old-source.mp4').stat().st_mtime,1234)
        self.desk.write('DELETE FROM jobs WHERE id=?',(jid,))
        self.assertEqual(self.desk.rows('SELECT * FROM file_dates')[0]['downloaded'],9000)


class FileSortingTests(Fixture):
    request=previous.ProfileHTTPTests.request
    login=previous.ProfileHTTPTests.login
    setUp=previous.ProfileHTTPTests.setUp
    tearDown=previous.ProfileHTTPTests.tearDown

    def put(self,name,size,date):
        p=self.desk.root/name;p.write_bytes(b'x'*size);os.utime(p,(date,date));return p

    def names(self,query=''):
        status,result,_=self.request('/api/folders'+query)
        self.assertEqual(status,200);return [e['name'] for e in result['entries']],result

    def test_six_orders_newest_default_and_folders_first(self):
        self.login();self.put('Zebra.jpg',10,100);self.put('alpha.jpg',30,300);self.put('Beta.jpg',20,200)
        (self.desk.root/'Folder').mkdir();(self.desk.root/'.private').write_text('hidden')
        (self.desk.root/'link').symlink_to(self.base)
        expected={'name-asc':['alpha.jpg','Beta.jpg','Zebra.jpg'],'name-desc':['Zebra.jpg','Beta.jpg','alpha.jpg'],
                  'size-asc':['Zebra.jpg','Beta.jpg','alpha.jpg'],'size-desc':['alpha.jpg','Beta.jpg','Zebra.jpg'],
                  'date-asc':['Zebra.jpg','Beta.jpg','alpha.jpg'],'date-desc':['alpha.jpg','Beta.jpg','Zebra.jpg']}
        for mode,names in expected.items():
            sort,order=mode.split('-');actual,result=self.names('?sort='+sort+'&order='+order)
            self.assertEqual(actual,['Folder']+names);self.assertEqual(result['total'],4)
        self.assertEqual(self.names()[0],['Folder']+expected['date-desc'])
        for query in ('?sort=bad','?order=bad'):
            self.assertEqual(self.request('/api/folders'+query)[0],400)
        self.assertEqual(self.request('/api/folders?sort=date&order=desc',cookie=False)[0],401)

    def test_sort_applies_to_whole_directory_before_pagination(self):
        self.login()
        for i in range(31):self.put(f'file-{i:02}.jpg',i+1,100+i)
        first,data=self.names('?limit=24&sort=date&order=desc');second,tail=self.names('?limit=24&offset=24&sort=date&order=desc')
        self.assertEqual(first+second,[f'file-{i:02}.jpg' for i in reversed(range(31))])
        self.assertTrue(data['more']);self.assertFalse(tail['more'])
        # Equal date/size keys retain a deterministic filename order across pages.
        for p in self.desk.root.glob('*.jpg'):os.utime(p,(100,100))
        names,_=self.names('?limit=31&sort=date&order=desc')
        self.assertEqual(names,sorted(names))

    def test_recorded_download_date_takes_priority_but_not_for_replaced_file(self):
        self.login();p=self.put('download.jpg',5,100);self.put('import.jpg',10,200)
        stat=p.stat();self.desk.write('INSERT INTO file_dates VALUES(?,?,?,?,?)',('','download.jpg',stat.st_size,stat.st_mtime_ns,300))
        names,data=self.names();self.assertEqual(names,['download.jpg','import.jpg']);self.assertEqual(data['entries'][0]['date_source'],'downloaded')
        self.put('download.jpg',6,90)
        names,data=self.names();self.assertEqual(names,['import.jpg','download.jpg']);self.assertEqual(data['entries'][1]['date_source'],'modified')

    def test_delete_removes_saved_date_record(self):
        self.login();p=self.put('delete.jpg',3,100);stat=p.stat()
        self.desk.write('INSERT INTO file_dates VALUES(?,?,?,?,?)',('','delete.jpg',3,stat.st_mtime_ns,400))
        with patch.object(self.desk.rpc,'call',return_value={'torrents':[]}):
            self.desk.delete_file({'confirm':True,'path':'delete.jpg','stamp':str(stat.st_size)+':'+str(stat.st_mtime_ns)})
        self.assertEqual(self.desk.rows('SELECT * FROM file_dates'),[])
