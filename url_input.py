"""Extract only unique HTTP(S) links from pasted prose."""
import re
from urllib.parse import urlsplit,urlunsplit
from media import normalize_url,url_key


def clean(values):
    if isinstance(values,str):values=[values]
    if not isinstance(values,list) or any(not isinstance(x,str) for x in values):raise ValueError('Paste links as text')
    if sum(len(x) for x in values)>200000:raise ValueError('Link input is too large')
    result=[];seen=set()
    for text in values:
        if re.search(r'\b(?:file|ftp|javascript)://',text,re.I):raise ValueError('Only HTTP(S) download links are supported')
        for match in re.findall(r'https?://[^\s<>"\']+',text,re.I):
            value=match.rstrip('.,;!?)\\]}')
            try:
                p=urlsplit(value)
                if not p.hostname or p.username or p.password or p.scheme.lower() not in ('http','https'):continue
                if p.port is not None and not 1<=p.port<=65535:continue
                value=normalize_url(urlunsplit((p.scheme.lower(),p.netloc,p.path,p.query,'')))
                key=url_key(value)
            except ValueError:continue
            if key not in seen:result.append(value);seen.add(key)
    return result
