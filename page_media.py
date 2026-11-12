"""Bounded static player discovery. No JavaScript execution or thumbnail downloads."""
import html
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from media import SafeRedirect, validate_url
import platforms

USER_AGENT='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
MEDIA=re.compile(r'\.(?:mp4|webm|m4v|mov|m3u8|mpd)(?:$|[?#])',re.I)
ADS=re.compile(r'(?:doubleclick|adservice|exoclick|trafficjunky|popads|juicyads)',re.I)

class PlayerHTML(HTMLParser):
    def __init__(self,base):
        super().__init__();self.base=base;self.media=[];self.embeds=[];self.title=[];self.in_title=False;self.script=False;self.script_type='';self.text=[];self.group=0;self.downloads=[];self.images=[]
    def add(self,url,group,height=0):
        if not isinstance(url,str):return
        url=urllib.parse.urljoin(self.base,html.unescape(url.replace('\\/','/')))
        if urllib.parse.urlsplit(url).scheme not in ('http','https') or ADS.search(urllib.parse.urlsplit(url).hostname or ''):return
        if MEDIA.search(url):
            match=re.search(r'(\d{3,4})[pP]',str(height)+' '+url)
            try:resolution=int(height)
            except (ValueError,TypeError):resolution=int(match[1]) if match else 0
            self.media.append({'url':url,'group':group,'height':resolution,'kind':'manifest' if re.search(r'\.(?:m3u8|mpd)(?:$|[?#])',url,re.I) else 'video','headers':{'referer':self.base,'user-agent':USER_AGENT}})
    def embed(self,url):
        if not url:return
        url=urllib.parse.urljoin(self.base,html.unescape(url))
        if urllib.parse.urlsplit(url).scheme in ('http','https') and not ADS.search(urllib.parse.urlsplit(url).hostname or '') and url not in self.embeds:self.embeds.append(url)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='title':self.in_title=True
        if tag=='video':self.group+=1;self.add(a.get('src'),self.group,a.get('height',0))
        if tag=='source':self.add(a.get('src'),self.group,a.get('size') or a.get('label') or a.get('res') or 0)
        if tag=='iframe':self.embed(a.get('src') or a.get('data-src'))
        if tag=='a' and a.get('href') and ('download' in a or re.search(r'(?:^|\s)(?:download|download-link|download-button)(?:$|\s)',a.get('class',''))):
            self.downloads.append(urllib.parse.urljoin(self.base,html.unescape(a['href'])))
        if tag=='meta' and a.get('property')=='og:image':self.images.append(urllib.parse.urljoin(self.base,html.unescape(a.get('content',''))))
        if tag=='meta' and a.get('property') in ('og:video','og:video:url','og:video:secure_url'):
            value=a.get('content','');self.add(value,0);self.embed(value) if not MEDIA.search(value) else None
        if tag=='script':self.script=True;self.script_type=a.get('type','');self.text=[]
    def handle_data(self,data):
        if self.in_title:self.title.append(data)
        if self.script:self.text.append(data)
    def handle_endtag(self,tag):
        if tag=='title':self.in_title=False
        if tag!='script' or not self.script:return
        self.script=False;text=''.join(self.text)
        if self.script_type=='application/ld+json':
            try:
                nodes=[json.loads(text)]
                while nodes:
                    node=nodes.pop()
                    if isinstance(node,list):nodes.extend(node)
                    elif isinstance(node,dict):
                        if node.get('@type')=='VideoObject':self.add(node.get('contentUrl'),0);self.embed(node.get('embedUrl'))
                        nodes.extend(v for v in node.values() if isinstance(v,(dict,list)))
            except (ValueError,RecursionError):pass
        # Explicit player source keys only; never collect arbitrary script URLs.
        for value in re.findall(r'''(?:["']?(?:file|src|video_url|hls)["']?\s*:\s*)["']((?:https?:)?(?:\\?/){2}[^"'\s]+)["']''',text):self.add(value,0)


def choose(candidates,quality='best',mp4=False):
    unique={c['url']:c for c in candidates};items=list(unique.values())
    if mp4:items=[c for c in items if c['kind']=='manifest' or urllib.parse.urlsplit(c['url']).path.lower().endswith(('.mp4','.m4v'))]
    if quality.isdigit():items=[c for c in items if not c.get('height') or c['height']<=int(quality)]
    if not items:raise ValueError('Page did not expose a compatible player source')
    groups={c.get('group',0) for c in items}
    if len(groups)>1:
        # Metadata often repeats the exact main player URL; duplicates were collapsed above.
        raise ValueError('Page exposes more than one player; browser selection required')
    manifests=[c for c in items if c['kind']=='manifest']
    if len(manifests)==1:return manifests[0]
    if len(items)==1:return items[0]
    heights={c.get('height',0) for c in items}
    if 0 not in heights and len(heights)==len(items):return max(items,key=lambda c:c['height'])
    raise ValueError('Multiple unranked media sources; browser selection required')


def discover(url,cookie='',quality='best',mp4=False):
    jar=platforms.load_jar(__import__('pathlib').Path(cookie)) if cookie else http.cookiejar.MozillaCookieJar()
    opener=urllib.request.build_opener(SafeRedirect(),urllib.request.HTTPCookieProcessor(jar))
    pending=[(url,0,None)];seen=set();title='';problems=[]
    while pending and len(seen)<6:
        current,depth,referer=pending.pop(0)
        if current in seen:continue
        seen.add(current)
        try:
            validate_url(current)
            headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/xhtml+xml','Accept-Encoding':'identity'}
            if referer:headers['Referer']=referer
            with opener.open(urllib.request.Request(current,headers=headers),timeout=15) as response:
                final=response.geturl();validate_url(final)
                if MEDIA.search(final) or response.headers.get('Content-Type','').startswith('video/'):
                    return {'url':final,'kind':'manifest' if '.m3u8' in final or '.mpd' in final else 'video','headers':{'referer':referer or url,'user-agent':USER_AGENT}},title,jar
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Player page exceeds 2 MiB')
                parser=PlayerHTML(final);parser.feed(raw.decode('utf-8','replace'))
            if depth==0:title=html.unescape(''.join(parser.title)).strip()[:240]
            if not parser.media and len(set(parser.downloads))==1:
                return {'url':parser.downloads[0],'kind':'file','headers':{'referer':final,'user-agent':USER_AGENT}},title,jar
            if not parser.media and parser.images and (urllib.parse.urlsplit(final).hostname or '').endswith(('ibb.co','imgbb.com')):
                return {'url':parser.images[0],'kind':'image','headers':{'referer':final,'user-agent':USER_AGENT}},title,jar
            if parser.media:
                try:return choose(parser.media,quality,mp4),title,jar
                except ValueError as exc:problems.append(str(exc))
            if depth<3:pending.extend((child,depth+1,final) for child in parser.embeds[:6])
        except Exception as exc:problems.append(type(exc).__name__+': '+str(exc)[:160])
    raise ValueError('Static player discovery found no unambiguous media after '+str(len(seen))+' pages. '+('; '.join(problems[-2:])))
