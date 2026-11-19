"""Exact-post Reddit public metadata fallback; no proxies or block circumvention."""
import html
import json
import re
from urllib.parse import urlsplit
import download_http as http


def from_post(post,post_id):
    if str(post.get('id','')).lower()!=post_id.lower():raise ValueError('Reddit returned a different post')
    data=(post.get('crosspost_parent_list') or [post])[0]
    video=(data.get('secure_media') or data.get('media') or {}).get('reddit_video') or {}
    if video:
        url=video.get('dash_url') or video.get('hls_url')
        if not url and video.get('is_gif'):url=video.get('fallback_url')
        if url:return {'url':html.unescape(url),'kind':'manifest' if '.mpd' in url or '.m3u8' in url else 'video'},post.get('title','Reddit')
        raise ValueError('Reddit exposed a video-only rendition without its audio manifest; refusing an incomplete download')
    gallery=data.get('gallery_data',{}).get('items',[]);metadata=data.get('media_metadata',{})
    if gallery:
        items=[]
        for item in gallery:
            media=metadata.get(item.get('media_id'),{});source=media.get('s',{})
            url=source.get('mp4') or source.get('u') or source.get('gif')
            if not url:raise ValueError('Reddit gallery contains an unavailable item')
            items.append({'url':html.unescape(url)})
        return {'kind':'collection','items':items},post.get('title','Reddit')
    url=data.get('url_overridden_by_dest') or data.get('url','')
    if urlsplit(url).hostname in ('i.redd.it','preview.redd.it'):
        return {'url':html.unescape(url)},post.get('title','Reddit')
    raise ValueError('This post contains no native downloadable Reddit media')


def discover(url,jar,timeout=20):
    match=re.search(r'/comments/([a-z0-9]+)',urlsplit(url).path,re.I)
    if not match:raise ValueError('Reddit requires a resolved post URL')
    post_id=match[1];client=http.opener(jar);blocked=False
    for host in ('old.reddit.com','www.reddit.com'):
        endpoint=f'https://{host}/comments/{post_id}.json?raw_json=1&limit=1'
        try:
            with http.request(client,endpoint,{'Accept':'application/json'},timeout=timeout) as response:raw=response.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise ValueError('Reddit metadata exceeded the safe limit')
            payload=json.loads(raw)
            listing=payload[0] if isinstance(payload,list) else payload
            for node in listing.get('data',{}).get('children',[]):
                if node.get('kind')=='t3' and node.get('data',{}).get('id')==post_id:
                    return from_post(node['data'],post_id)
        except Exception as exc:
            if getattr(exc,'code',None) in (403,429):blocked=True
    if blocked:raise ValueError('Reddit rejects this server IP/session on its public metadata routes. Browser playback will be tried; fresh account cookies alone cannot remove an IP block.')
    raise ValueError('Reddit did not expose public metadata for this exact post')
