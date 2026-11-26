"""Worker-local payload metering for gallery-dl HTTP and native yt-dlp transfers."""
import atexit
import contextlib
import functools
import threading
import time

class Meter:
    def __init__(self,emit=None):
        self.pending=0;self.last=time.monotonic();self.lock=threading.RLock()
        self.emit=emit or (lambda n:print('\nZEDBYTES '+str(n),flush=True))
    def add(self,n):
        with self.lock:
            self.pending+=n
            if self.pending>=262144 or time.monotonic()-self.last>=1:self.flush()
    def flush(self):
        with self.lock:
            if self.pending:self.emit(self.pending)
            self.pending=0;self.last=time.monotonic()

_installed=False
def install():
    global _installed
    if _installed:return
    _installed=True
    import requests
    from gallery_dl.downloader.http import HttpDownloader
    from yt_dlp import YoutubeDL
    from yt_dlp.downloader.http import HttpFD
    meter=Meter();local=threading.local();atexit.register(meter.flush)
    def scope(method,flag):
        @functools.wraps(method)
        def run(*args,**kwargs):
            before=getattr(local,flag,False);setattr(local,flag,True)
            try:return method(*args,**kwargs)
            finally:setattr(local,flag,before);meter.flush()
        return run
    HttpDownloader._download_impl=scope(HttpDownloader._download_impl,'gallery')
    HttpFD.real_download=scope(HttpFD.real_download,'ytdlp')
    original=requests.Response.iter_content
    @functools.wraps(original)
    def chunks(self,*args,**kwargs):
        for block in original(self,*args,**kwargs):
            if getattr(local,'gallery',False):meter.add(len(block))
            yield block
    requests.Response.iter_content=chunks
    original_open=YoutubeDL.urlopen
    @functools.wraps(original_open)
    def urlopen(self,*args,**kwargs):
        response=original_open(self,*args,**kwargs)
        if getattr(local,'ytdlp',False):
            read=response.read
            def metered(*args,**kwargs):
                block=read(*args,**kwargs);meter.add(len(block));return block
            response.read=metered
        return response
    YoutubeDL.urlopen=urlopen
    print('\nZEDBYTES 0',flush=True)

if __name__=='__main__':
    install()
    import gallery_dl
    gallery_dl.main()
