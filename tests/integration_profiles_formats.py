"""Offline checks against real engines: pagination, resume archive and format choice."""
from pathlib import Path
import sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gallery_dl.extractor.instagram import InstagramAPI,InstagramPhotosExtractor,InstagramExtractor
from gallery_dl.archive import DownloadArchive
from yt_dlp import YoutubeDL
from media import video_format

class Extractor:
    def __init__(self):self.cursors=[]
    def _init_cursor(self):return None
    def _update_cursor(self,value):self.cursors.append(value);return value
extr=Extractor();api=object.__new__(InstagramAPI);api.extractor=extr
pages=[{'items':[{'id':i*2+j} for j in range(2)],'more_available':i<2,'next_max_id':str(i+1)} for i in range(3)]
calls=[]
def call(endpoint,params):
    calls.append(params['max_id']);return pages[len(calls)-1]
api._call=call
assert [x['id'] for x in api._pagination('/fixture')]==list(range(6))
assert calls==[None,'1','2'] and extr.cursors==['1','2',None]
class Feed:
    def user_feed(self,item):return [{'id':1,'product_type':'feed'},{'id':2,'product_type':'clips'},{'id':3,'media_type':2,'product_type':'feed'}]
photos=object.__new__(InstagramPhotosExtractor);photos.api=Feed();photos.item='fixture'
assert [x['id'] for x in photos.posts()]==[1,3]
with tempfile.TemporaryDirectory() as folder:
    path=str(Path(folder)/'archive.sqlite3');keygen=InstagramExtractor.archive_fmt.format_map
    archive=DownloadArchive(path,keygen,pragma=['journal_mode=WAL'])
    assert not archive.check({'media_id':'123'})
    archive.add({'media_id':'123'});archive.close()
    archive=DownloadArchive(path,keygen)
    assert archive.check({'media_id':'123'}) and not archive.check({'media_id':'124'})
    archive.close()

formats=[{'format_id':'mp4-low','url':'https://example.com/low.mp4','ext':'mp4','vcodec':'avc1','acodec':'mp4a','height':720},
         {'format_id':'mp4-high','url':'https://example.com/high.mp4','ext':'mp4','vcodec':'av01','acodec':'none','height':2160},
         {'format_id':'webm-higher','url':'https://example.com/high.webm','ext':'webm','vcodec':'vp9','acodec':'none','height':4320},
         {'format_id':'audio','url':'https://example.com/audio.m4a','ext':'m4a','vcodec':'none','acodec':'mp4a','abr':128}]
for quality,expected in [('best','mp4-high+audio'),('1080','mp4-low')]:
    with YoutubeDL({'format':video_format(quality,True),'quiet':True,'no_warnings':True,'skip_download':True}) as ydl:
        result=ydl.process_ie_result({'id':'fixture','title':'Fixture','formats':[dict(x) for x in formats]},download=False)
        assert result['format_id']==expected,result['format_id']
with YoutubeDL({'quiet':True,'no_warnings':True}) as ydl:
    selector=ydl.build_format_selector(video_format('best',True))
    assert not list(selector({'formats':[formats[2]],'has_merged_format':False,'incomplete_formats':False}))
print('PASS: real gallery-dl traverses three pages, excludes reels from posts and persists media-ID deduplication; real yt-dlp chooses highest MP4, respects a quality ceiling and rejects WebM-only selection. All network responses are fixtures.')
