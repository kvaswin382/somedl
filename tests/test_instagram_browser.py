import contextlib
import http.cookiejar
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import instagram_browser as ig
import instagram_fallback as fallback


def photo(code='Wanted',user='alice',**extra):
    return {'shortcode':code,'owner':{'username':user},'display_resources':[{'src':'https://cdn.example/low.jpg','config_width':320,'config_height':320},{'src':'https://cdn.example/high.jpg','config_width':1440,'config_height':1440}],**extra}


class InstagramSelectionTests(unittest.TestCase):
    def test_exact_carousel_not_recommendation(self):
        selection=ig.Selection({'kind':'post','code':'Wanted'})
        video={'is_video':True,'video_versions':[{'url':'https://cdn.example/360.mp4','height':360,'width':640},{'url':'https://cdn.example/1080.mp4','height':1080,'width':1920}]}
        selection.ingest({'data':[photo('Advert'),photo(carousel_media_count=2,carousel_media=[photo(),video])]})
        items=selection.result()
        self.assertEqual([i['key'] for i in items],['Wanted_01','Wanted_02'])
        self.assertEqual([i['url'] for i in items],['https://cdn.example/high.jpg','https://cdn.example/1080.mp4'])
        self.assertTrue(selection.complete)

    def test_no_video_thumbnail_substitution(self):
        selection=ig.Selection({'kind':'post','code':'Wanted'})
        selection.ingest(photo(__typename='GraphVideo'))
        with self.assertRaisesRegex(ValueError,'full video'):selection.result()

    def test_incomplete_carousel_rejected(self):
        selection=ig.Selection({'kind':'post','code':'Wanted'})
        selection.ingest(photo(carousel_media_count=3,carousel_media=[photo()]))
        with self.assertRaisesRegex(ValueError,'incomplete carousel'):selection.result()

    def test_wrong_owner_and_unbound_pagination(self):
        selection=ig.Selection({'kind':'posts','user':'alice'})
        selection.ingest({'items':[photo(user='bob')],'more_available':False})
        self.assertFalse(selection.complete);self.assertFalse(selection.items)
        selection.ingest({'username':'alice','id':'123','edge_owner_to_timeline_media':{'edges':[{'node':photo()}],'page_info':{'has_next_page':False}}})
        self.assertTrue(selection.complete);self.assertEqual(len(selection.items),1)

    def test_profile_end_does_not_hide_missing_media(self):
        selection=ig.Selection({'kind':'posts','user':'alice'})
        selection.ingest({'username':'alice','edge_owner_to_timeline_media':{'edges':[
            {'node':photo('Good')},{'node':photo('Broken',is_video=True)}], 'page_info':{'has_next_page':False}}})
        with self.assertRaisesRegex(ValueError,'full video'):selection.result()

    def test_specific_story_id_and_highlight(self):
        selection=ig.Selection({'kind':'stories','user':'alice','id':'222'})
        items=[{**photo(),'pk':'111'},{**photo(),'pk':'222'}]
        selection.ingest({'id':'123','user':{'username':'alice'},'items':items})
        self.assertEqual([i['key'] for i in selection.result()],['story_222'])
        other=ig.Selection({'kind':'highlight','id':'987'})
        other.ingest({'id':'highlight:999','user':{'username':'alice'},'items':items})
        self.assertFalse(other.items)
        other.ingest({'id':'highlight:987','user':{'username':'alice'},'items':items})
        self.assertEqual(len(other.result()),2)

    def test_full_highlight_catalog(self):
        selection=ig.Selection({'kind':'highlights','user':'alice'})
        selection.ingest({'username':'alice','edge_highlight_reels':{'edges':[{'node':{'id':'highlight:123'}}],'page_info':{'has_next_page':False}}})
        self.assertTrue(selection.complete)
        self.assertEqual(selection.result(),[{'collection_url':'https://www.instagram.com/stories/highlights/123/'}])

    def test_quality_cap_and_no_downgrade(self):
        selection=ig.Selection({'kind':'post','code':'Wanted'})
        selection.ingest(photo());selection.ingest(photo(display_resources=[{'src':'https://cdn.example/small.jpg','config_width':100,'config_height':100}]))
        self.assertTrue(selection.result()[0]['url'].endswith('high.jpg'))
        video=photo(is_video=True,video_versions=[{'url':'https://cdn.example/720.mp4','height':720,'width':1280},{'url':'https://cdn.example/1080.mp4','height':1080,'width':1920}])
        self.assertTrue(ig.post(video,'720')[0]['url'].endswith('720.mp4'))

    def test_dash_video_and_audio(self):
        manifest='<MPD><Period><AdaptationSet mimeType="video/mp4"><Representation width="1920" height="1080"><BaseURL>https://cdn.example/video.mp4</BaseURL></Representation></AdaptationSet><AdaptationSet mimeType="audio/mp4"><Representation bandwidth="128000"><BaseURL>https://cdn.example/audio.m4a</BaseURL></Representation></AdaptationSet></Period></MPD>'
        item=ig.post(photo(is_video=True,video_dash_manifest=manifest))[0]
        self.assertEqual(item['audio_url'],'https://cdn.example/audio.m4a')
        self.assertIsNone(ig.dash_sources('<!DOCTYPE MPD>'+manifest))

    def test_story_target_keeps_specific_id(self):
        from instagram_story import target
        self.assertEqual(target('https://www.instagram.com/stories/Alice/123/','stories')[1],'https://www.instagram.com/stories/alice/123/')
        self.assertEqual(ig.target({'url':'https://www.instagram.com/p/DcnXVKzjr6L/?img_index=1'})['code'],'DcnXVKzjr6L')


class InstagramHandoffTests(unittest.TestCase):
    def test_empty_success_enters_fallback_and_holds_lease_through_transfer(self):
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/'stage';dest.mkdir();held=[]
            @contextlib.contextmanager
            def lease(state):
                held.append(True)
                try:yield
                finally:held.pop()
            def discover(spec,jar):
                self.assertTrue(held)
                return [{'key':'Wanted_01','url':'https://cdn.example/image','video':False,'owner':'alice'}]
            def transfer(client,candidate,stage,*args,**kwargs):
                self.assertTrue(held);path=stage/'Wanted_01.jpg';path.write_bytes(b'photo');return path
            spec={'args':[],'state':root,'dest':str(dest),'url':'https://www.instagram.com/p/Wanted/','settings':{}}
            with patch.object(fallback,'execute',return_value=(0,'')),patch.object(fallback,'browser_download_lease',lease),patch('instagram_browser.discover',side_effect=discover),patch.object(fallback.http,'inspect',return_value={'kind':'image'}),patch.object(fallback.http,'transfer',side_effect=transfer):
                self.assertEqual(fallback.run(spec),0)
            self.assertFalse(held);self.assertTrue((dest/'Wanted_01.jpg').exists())

    def test_checkpoint_and_completed_archive_do_not_trigger_browser(self):
        with tempfile.TemporaryDirectory() as root:
            spec={'args':[],'state':root,'dest':root,'url':'https://www.instagram.com/p/Wanted/'}
            with patch('instagram_browser.discover') as browser:
                for code in (75,79):
                    with patch.object(fallback,'execute',return_value=(code,'')):self.assertEqual(fallback.run(spec),code)
                with patch.object(fallback,'execute',return_value=(0,'')):self.assertEqual(fallback.run({**spec,'published':True}),0)
                browser.assert_not_called()

    def test_failed_primary_and_failed_browser_preserve_context(self):
        with tempfile.TemporaryDirectory() as root:
            spec={'args':[],'state':root,'dest':root,'url':'https://www.instagram.com/p/Wanted/'}
            with patch.object(fallback,'execute',return_value=(1,'original error')),patch('instagram_browser.discover',side_effect=ValueError('account verification required')):
                with self.assertRaisesRegex(ValueError,'gallery-dl failed.*account verification required'):fallback.run(spec)

    def test_disabled_empty_success_is_failure(self):
        with tempfile.TemporaryDirectory() as root,patch.object(fallback,'execute',return_value=(0,'')):
            with self.assertRaisesRegex(ValueError,'disabled'):fallback.run({'args':[],'dest':root,'enabled':False})


from test_desk import Fixture
class InstagramCounterTests(Fixture):
    def test_completed_browser_batch_replaces_partial_gallery_count(self):
        import io
        jid=self.create_job()
        self.desk.consume(io.BytesIO(b'ZEDITEM gallery-partial\nZEDITEM browser-one\nZEDITEM browser-two\nZEDITEMTOTAL 2\n'),jid)
        self.assertEqual(self.desk.job(jid)['items'],2)
