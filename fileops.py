"""Confined, bounded-memory file operations with no overwrite on exFAT."""
import os
import secrets
import shutil
from pathlib import Path
from publishing import filename, MEDIA


def guard(desk,path,stamp=None):
    if not path.is_file() or path.is_symlink():raise ValueError('Select a regular downloaded file')
    st=path.stat()
    if stamp!=f'{st.st_size}:{st.st_mtime_ns}':raise ValueError('File changed. Refresh the file list first.')
    for j in desk.rows("SELECT * FROM jobs WHERE status IN ('running','queued','retry','paused','blocked')"):
        if j['layout']==1 and path.is_relative_to(desk.root/j['folder']/j['subdir']):
            raise ValueError('Cancel the legacy download before changing its files')
    for r in desk.rows('SELECT p.target,j.folder,j.subdir FROM published p JOIN jobs j ON p.job=j.id WHERE p.complete=0'):
        if path==desk.root/r['folder']/r['subdir']/r['target']:raise ValueError('This file is still being saved')
    for t in desk.rpc.call('torrent-get',{'fields':['downloadDir','files']}).get('torrents',[]):
        if any(path.resolve()==(Path(t['downloadDir'])/f['name']).resolve() for f in t.get('files',[])):
            raise ValueError('Remove the torrent from its queue before changing its files')
    return st


def target_name(value,source):
    if not isinstance(value,str) or not value.strip() or len(value.encode())>150 or any(c in value for c in '/\\'):
        raise ValueError('Enter a short filename without a folder path')
    value=value.strip()
    stem=Path(value).stem if Path(value).suffix.lower() in MEDIA or Path(value).suffix.lower()==source.suffix.lower() else value
    return filename(stem)+source.suffix


def manage(desk,data):
    action=data.get('action')
    if action not in ('rename','copy','move'):raise ValueError('Choose rename, copy or move')
    with desk.file_lock:
        source=desk.destination(data.get('path',''),reserve=False)
        st=guard(desk,source,data.get('stamp'))
        folder=source.parent if action=='rename' else desk.destination(data.get('folder',''),reserve=False)
        if not folder.is_dir():raise ValueError('Choose an existing destination folder')
        name=target_name(data.get('name'),source) if action=='rename' else source.name
        if source==folder/name:raise ValueError('Choose a different name or destination')
        oldfolder=source.parent.relative_to(desk.root).as_posix();oldfolder='' if oldfolder=='.' else oldfolder
        olddate=desk.rows('SELECT * FROM file_dates WHERE folder=? AND name=?',(oldfolder,source.name))
        taken={p.name.casefold() for p in folder.iterdir()}
        base=Path(name)
        for i in range(100000):
            target=folder/(base.stem+(f' ({i})' if i else '')+base.suffix)
            if target.name.casefold() not in taken:break
        else:raise ValueError('Too many duplicate filenames')
        temporary=folder/('.zedair-copy-'+secrets.token_hex(12))
        reserved=False
        try:
            if action=='copy':
                if desk.storage()['free']-st.st_size<desk.get_setting('reserve_gib',5)*1024**3:raise ValueError('Not enough SSD space for this copy and the free-space reserve')
                with source.open('rb') as src,temporary.open('xb') as dst:
                    shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
                after=source.stat()
                if (after.st_size,after.st_mtime_ns)!=(st.st_size,st.st_mtime_ns):raise ValueError('Source changed during copy; please retry')
                shutil.copystat(source,temporary)
            desk.destination('' if folder==desk.root else folder.relative_to(desk.root).as_posix(),reserve=False)
            # Publication uses the same file lock. Never overwrite an existing target.
            marker=('ZEDAIR-FILEOP:'+secrets.token_hex(12)).encode()
            with target.open('xb') as stream:stream.write(marker)
            reserved=True
            if target.is_symlink() or target.read_bytes()!=marker:raise ValueError('Destination changed during operation')
            os.replace(temporary if action=='copy' else source,target);reserved=False
            tf=folder.relative_to(desk.root).as_posix();tf='' if tf=='.' else tf
            stat=target.stat()
            date=olddate[0]['downloaded'] if olddate and olddate[0]['size']==st.st_size and olddate[0]['mtime_ns']==st.st_mtime_ns else st.st_mtime
            with desk.lock:
                desk.db.execute('INSERT OR REPLACE INTO file_dates VALUES(?,?,?,?,?)',(tf,target.name,stat.st_size,stat.st_mtime_ns,date))
                if action!='copy':desk.db.execute('DELETE FROM file_dates WHERE folder=? AND name=?',(oldfolder,source.name))
                desk.db.commit()
            return {'path':target.relative_to(desk.root).as_posix()}
        finally:
            temporary.unlink(missing_ok=True)
            if reserved and target.is_file() and not target.is_symlink() and target.stat().st_size==len(marker) and target.read_bytes()==marker:target.unlink()


def delete_folder(desk,data):
    if data.get('confirm') is not True:raise ValueError('Confirm deleting this empty folder')
    with desk.file_lock:
        path=desk.destination(data.get('path',''),reserve=False)
        if path==desk.root or not path.is_dir():raise ValueError('Choose an empty subfolder, not Zedair itself')
        for j in desk.rows("SELECT * FROM jobs WHERE status IN ('running','queued','retry','paused','blocked')"):
            if (desk.root/j['folder']/j['subdir']).is_relative_to(path):raise ValueError('A queued download uses this folder')
        for t in desk.rpc.call('torrent-get',{'fields':['downloadDir']}).get('torrents',[]):
            if Path(t['downloadDir']).resolve().is_relative_to(path.resolve()):raise ValueError('A torrent uses this folder')
        try:path.rmdir()
        except OSError as exc:raise ValueError('Folder is not empty or could not be removed') from exc
