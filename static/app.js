'use strict';
const $ = (q, root = document) => root.querySelector(q);
const $$ = (q, root = document) => [...root.querySelectorAll(q)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const E = escapeHTML;
let selectedPlatform='instagram', settingsTab='general';
let csrf = '', state = null, page = 'queue', filter = 'all', filePath = '', folderPath = '', folderTarget = '', cookieSlot = '', timer, toastTimer, busy = false;
const bytes = n => { n=Number(n)||0; const units=['B','KiB','MiB','GiB','TiB']; let i=0; while(n>=1024&&i<4){n/=1024;i++;} return `${n.toFixed(i>1?1:0)} ${units[i]}`; };
const eta = n => !n || n<0 ? '—' : n<60 ? `${Math.ceil(n)}s` : n<3600 ? `${Math.ceil(n/60)}m` : `${(n/3600).toFixed(1)}h`;
const labels = {running:'Downloading',queued:'Queued',retry:'Retry waiting',blocked:'Waiting for SSD',paused:'Paused',done:'Finished',error:'Needs attention',cancelled:'Cancelled'};
const symbols = {video:'↙',instagram:'▦',threads:'@'};
function toast(text){ $('#toast').textContent=text; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,7000); }
async function api(path, data){
  const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',cache:'no-store',headers:data===undefined?{}:{'Content-Type':'application/json','X-Zedair-CSRF':csrf},body:data===undefined?undefined:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok){ if(response.status===401&&!path.startsWith('login')) await showAuth(); throw new Error(result.error||'Request failed'); }
  return result;
}
async function action(fn){ try{await fn();}catch(error){toast(error.message);} }
function openDialog(id){const dialog=$('#'+id); if(!dialog.open)dialog.showModal();}
function closeDialog(dialog){if(dialog.id==='previewDialog')clearPreview();dialog.close();}
function empty(icon,title,message,button=''){return `<div class="empty"><div class="empty-icon">${icon}</div><h3>${E(title)}</h3><p>${E(message)}</p>${button}</div>`;}
function button(symbol,title,attrs){return `<button class="icon-button" title="${E(title)}" aria-label="${E(title)}" ${attrs}>${symbol}</button>`;}
function shortFolder(job){return ['Zedair',job.folder,job.subdir].filter(Boolean).join('/');}
function parent(path){return path.split('/').slice(0,-1).join('/');}
function join(...parts){return parts.filter(Boolean).join('/');}
async function showAuth(){
  clearTimeout(timer);
  const auth=await api('auth'); csrf=auth.csrf;
  $('#auth').hidden=auth.authenticated; $('#shell').hidden=!auth.authenticated;
  if(auth.authenticated){$('#identity').textContent='Signed in as '+auth.username;await refresh(); return;}
  $('#setupLabel').hidden=!auth.setup; $('#authForm').dataset.setup=String(auth.setup);
  $('#authTitle').textContent=auth.setup?'Make this desk yours.':'Welcome back.';
  $('#authEyebrow').textContent=auth.setup?'FIRST-TIME SETUP':'PRIVATE ACCESS';
  $('#authDescription').textContent=auth.setup?'Create your superuser account. Use the setup key printed by the installer.':'Sign in to your download desk.';
  $('#authSubmit').textContent=auth.setup?'Create superuser ↗':'Open desk ↗';
  $('#passwordHint').textContent=auth.setup?'Use at least 12 characters. Login sessions last 35 days.':'Stay signed in for 35 days on this browser.';
  $('#authForm [name=password]').minLength=auth.setup?12:1;
  $('#authForm [name=password]').autocomplete=auth.setup?'new-password':'current-password';
}
async function refresh(){
  if(busy)return;busy=true;clearTimeout(timer);
  try{
    state=await api('state'); $('#connection').textContent='● Connected';$('#connection').classList.remove('offline');
    renderOverview(); if(page==='queue')renderJobs(); if(page==='torrents')renderTorrents(); if(page==='speed')renderSpeed();
  }catch(error){$('#connection').textContent='● Reconnecting';$('#connection').classList.add('offline');}
  finally{busy=false;if(!$('#shell').hidden)timer=setTimeout(refresh,document.hidden?15000:4000);}
}
function renderOverview(){
  const s=state, jobs=s.jobs;
  $('#navCount').textContent=jobs.filter(j=>['queued','running','retry','blocked'].includes(j.status)).length;
  $('#torrentCount').textContent=s.torrents.length;
  $('#statActive').textContent=jobs.filter(j=>j.status==='running').length+s.torrents.filter(t=>[4,6].includes(t.status)).length;
  $('#statQueued').textContent=jobs.filter(j=>['queued','retry','blocked'].includes(j.status)).length;
  $('#statDone').textContent=jobs.filter(j=>j.status==='done').length;
  $('#statFree').textContent=s.storage.total?bytes(s.storage.free):'Offline';
  $('#storageCaption').textContent=s.storage.total?`free of ${bytes(s.storage.total)} · Zedair/`:'SSD not mounted';
  $('#diskFill').style.width=s.storage.total?100*(1-s.storage.free/s.storage.total)+'%':'0%';
  $('#storageAlert').hidden=s.storage.ready&&!s.settings.hold;
  $('#storageAlert').textContent=s.settings.hold?'Queue is held. Active transfers are stopping; resume when you are ready.':s.storage.reason;
  $('#holdBtn').textContent=s.settings.hold?'▶ Resume queue':'Ⅱ Hold queue';
}
function renderJobs(){
  const query=$('#searchJobs').value.trim().toLowerCase();
  const jobs=state.jobs.filter(j=>(!query||`${j.name} ${j.url} ${j.folder}`.toLowerCase().includes(query))&&(filter==='all'||(filter==='active'&&j.status==='running')||(filter==='waiting'&&['queued','retry','blocked','paused'].includes(j.status))||(filter==='error'&&j.status==='error')||(filter==='done'&&['done','cancelled'].includes(j.status))));
  if(!jobs.length){$('#jobs').innerHTML=empty('↙',state.jobs.length?'No matches here.':'Your next download starts here.',state.jobs.length?'Try another filter or search.':'Add a YouTube or X video, an Instagram carousel, or a Threads post. Your laptop takes it from there.',state.jobs.length?'':'<button class="secondary" data-open="downloadDialog">＋ Paste your first link</button>');return;}
  $('#jobs').innerHTML=jobs.map(j=>{
    const active=['queued','running','retry','blocked'].includes(j.status);
    const info=j.status==='running'?`${bytes(j.speed)}/s · ETA ${eta(j.eta)}`:j.status==='retry'?`Retry in ${eta(Math.max(1,j.next_try-Date.now()/1000))}`:j.status==='done'?'On the SSD':`Attempt ${j.attempts} · ${j.kind==='instagram'?'Carousel / reel':j.kind==='threads'?'Public post':'Video engine'}`;
    const percent=Math.max(0,Math.min(100,Number(j.progress)||0));
    return `<article class="job"><div class="source-icon ${E(j.kind)}">${symbols[j.kind]||'↙'}</div><div class="job-info"><span class="job-title" title="${E(j.name)}">${E(j.name)}</span><div class="job-meta" title="${E(shortFolder(j))}">${E(shortFolder(j))}</div>${j.status==='error'?`<span class="attention-note">${E(j.detail)}</span>`:''}</div><div class="job-progress"><div class="job-state"><span class="pill ${E(j.status)}">${E(labels[j.status]||j.status)}</span><span>${j.status==='running'?percent.toFixed(1)+'%':''}</span></div><div class="progress"><i style="width:${percent}%"></i></div><div class="job-speed">${E(info)}</div></div><div class="job-controls">${active?button('Ⅱ','Pause',`data-job="${j.id}" data-action="pause"`):j.status!=='done'?button('▶','Resume / retry',`data-job="${j.id}" data-action="retry"`):button('▤','Open destination',`data-job-folder="${E(join(j.folder,j.subdir))}"`)}${button('⋯','Details and log',`data-detail="${j.id}"`)}</div></article>`;
  }).join('');
}
function renderTorrents(){
  $('#torrentAlert').hidden=!state.torrent_error;$('#torrentAlert').textContent=state.torrent_error;
  if(!state.torrents.length){$('#torrents').innerHTML=empty('⇄','No torrents yet.','Add a magnet link or upload a .torrent file. Files download directly into your chosen SSD folder.');return;}
  const status=['Paused','Verify queued','Verifying','Queued','Downloading','Seed queued','Seeding'];
  $('#torrents').innerHTML=state.torrents.map(t=>`<article class="job"><div class="source-icon torrent">⇄</div><div><div class="job-title">${E(t.name)}</div><div class="job-meta">${bytes(t.totalSize)} <b>·</b> Ratio ${Number(t.uploadRatio||0).toFixed(2)}</div>${t.errorString?`<p class="form-error">${E(t.errorString)}</p>`:''}</div><div class="job-progress"><div class="job-state"><span class="pill ${t.status===4?'running':''}">${E(status[t.status]||'Waiting')}</span><span>${(100*t.percentDone).toFixed(1)}%</span></div><div class="progress"><i style="width:${100*t.percentDone}%"></i></div><div class="job-speed">↓ ${bytes(t.rateDownload)}/s · ↑ ${bytes(t.rateUpload)}/s</div></div><div class="job-controls">${button(t.status===0?'▶':'Ⅱ',t.status===0?'Resume':'Pause',`data-torrent="${E(t.hashString)}" data-action="${t.status===0?'resume':'pause'}"`)}${button('▤','Select files',`data-torrent-files="${E(t.hashString)}"`)}${button('×','Remove torrent, keep files',`data-torrent="${E(t.hashString)}" data-action="remove"`)}</div></article>`).join('');
}
async function setPage(next){
  page=next; for(const name of ['queue','torrents','files','speed','settings'])$('#'+name+'Page').hidden=name!==next;
  $$('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===next));
  $('#pageCrumb').textContent=next==='queue'?'DOWNLOADS':next.toUpperCase();
  if(!state)return;
  if(next==='queue')renderJobs();if(next==='torrents')renderTorrents();if(next==='files')await loadFiles();if(next==='settings')renderSettings();if(next==='speed')renderSpeed();
}
async function loadFiles(){
  $('#filePath').textContent='Zedair/'+filePath; $('#filesUp').disabled=!filePath;
  try{const result=await api('folders?path='+encodeURIComponent(filePath));$('#fileList').innerHTML=result.entries.length?result.entries.map(entry=>{
    const path=join(filePath,entry.name);
    if(entry.directory)return `<button class="file-row" data-file-folder="${E(path)}"><span>▰</span><strong>${E(entry.name)}</strong><small>Open folder →</small></button>`;
    const row=entry.preview?`<button class="file-row" data-preview-file="${E(path)}" data-preview-kind="${E(entry.preview)}"><span>${entry.preview==='image'?'▧':'▷'}</span><strong>${E(entry.name)}<span class="file-preview-label"> · Preview</span></strong><small>${bytes(entry.size)}</small></button>`:`<div class="file-row"><span>▤</span><strong>${E(entry.name)}</strong><small>${bytes(entry.size)}</small></div>`;
    return `<div class="file-entry">${row}<div class="file-actions"><a class="secondary compact" href="/api/file?path=${encodeURIComponent(path)}" download aria-label="Download ${E(entry.name)}">Download</a><button class="secondary compact danger" data-delete-file="${E(path)}" data-stamp="${E(entry.stamp)}" aria-label="Delete ${E(entry.name)}">Delete</button></div></div>`;
  }).join(''):empty('▤','This folder is empty.','Create a subfolder or point your next download here.');}
  catch(error){$('#fileList').innerHTML=empty('!','Storage unavailable',error.message);}
}
async function loadFolders(){
  $('#folderPath').textContent='Zedair/'+folderPath;$('#folderUp').disabled=!folderPath;
  const result=await api('folders?path='+encodeURIComponent(folderPath));
  const dirs=result.entries.filter(e=>e.directory);
  $('#folderList').innerHTML=dirs.length?dirs.map(e=>`<button class="file-row" data-folder="${E(join(folderPath,e.name))}"><span>▰</span><strong>${E(e.name)}</strong><small>→</small></button>`).join(''):empty('▤','No subfolders.','Use this folder, or create one below.');
}
const platformNames={youtube:'YouTube',instagram:'Instagram',x:'X / Twitter',threads:'Threads',other:'Other websites'};
function selectSettingsTab(tab){
  settingsTab=tab; $$('[data-settings-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.settingsTab===tab));
  $$('[data-settings-panel]').forEach(p=>p.hidden=p.dataset.settingsPanel!==tab);
}
function renderSettings(){
  Object.entries(state.settings).forEach(([k,v])=>{const field=$(`[name="${k}"]`,$('#settingsForm'));if(field)field.value=v;});
  selectSettingsTab(settingsTab);renderPlatform();
}
function renderPlatform(){
  const key=selectedPlatform,p=state.platforms[key],c=state.cookie_status[key];
  $('#platformTabs').innerHTML=Object.entries(platformNames).map(([k,n])=>`<button class="${k===key?'selected':''}" data-platform="${k}">${n}</button>`).join('');
  const check=(name,label)=>`<label class="check"><input type="checkbox" name="${name}" ${p[name]?'checked':''}>${label}</label>`;
  const select=(name,label,choices)=>`<label>${label}<select name="${name}">${choices.map(([v,t])=>`<option value="${v}" ${p[name]===v?'selected':''}>${t}</option>`).join('')}</select></label>`;
  let fields=check('enabled','Enable this platform')+`<label>Platform folder<input name="folder" value="${E(p.folder)}" placeholder="${key==='other'?'Automatic website domain':'Folder name'}" maxlength="150" ${key==='other'?'':'required'}><small>Under the destination chosen when adding a link.</small></label>`;
  if('quality' in p)fields+=select('quality','Default video quality',[['best','Best available'],['2160','Up to 4K'],['1080','Up to 1080p'],['720','Up to 720p'],['480','Up to 480p'],['audio','Audio only']]);
  if('subtitles' in p)fields+=check('subtitles','English / Malayalam subtitles');
  if(key==='youtube')fields+=check('playlist','Include playlists by default')+`<label>Playlist item limit<input type="number" name="playlist_limit" min="1" max="500" value="${p.playlist_limit}" required></label>`;
  if(key==='instagram')fields+=check('require_cookies','Require session cookies before downloading')+check('warn_images','Warn if original image resolution is unavailable')+select('videos','Video source',[['dash','DASH · best available streams'],['merged','Pre-merged video']])+`<label>Delay between requests (seconds)<input type="number" name="sleep_request" min="6" max="60" value="${p.sleep_request}" required></label><p class="hint">Downloads every image/video in a single post or carousel. Highest quality available to your session; the website can limit it.</p>`;
  if(key==='x')fields+=select('api','X video API',[['graphql','GraphQL (recommended)'],['legacy','Legacy'],['syndication','Syndication · public posts']])+`<p class="hint">Paste an x.com or twitter.com status URL. Video posts and animated GIFs use the video engine. Photos and whole feeds are not supported.</p>`;
  if(key==='threads')fields+='<p class="hint">Experimental: exact public posts and carousels from embedded page data. Login walls or page changes can prevent extraction.</p>';
  if(key==='other')fields+='<p class="hint">Supported video sites, including supported adult sites, use yt-dlp. Availability varies; DRM media is not supported. An empty folder uses the source hostname.</p>';
  const raw=key==='instagram'?['sessionid','csrftoken','ds_user_id']:key==='x'?['auth_token','ct0']:[];
  const manual=raw.length?`<form id="sessionForm" autocomplete="off"><h3>Paste session values</h3><p class="hint">From your browser’s cookies for ${key==='x'?'x.com':'instagram.com'}. Paste the value only. Saving replaces this platform’s cookie file.</p>${raw.map(n=>`<label>${n}${key==='instagram'&&n!=='sessionid'?' (optional)':''}<input type="password" name="${n}" autocomplete="new-password" spellcheck="false" maxlength="8192" ${key==='x'||n==='sessionid'?'required':''}></label>`).join('')}<button class="secondary" type="submit">Save private session</button></form>`:'';
  $('#platformEditor').innerHTML=`<div class="platform-editor"><form id="platformForm"><h3>${platformNames[key]} defaults</h3>${fields}<button class="primary" type="submit">Save platform settings</button></form><div><h3>Website authentication</h3><div class="cookie-status"><strong>${c.usable?'Session stored':c.present?'Cookies need attention':'No session stored'}</strong><small>${c.present?`${c.count} cookies · ${c.expired} expired`:'Public downloads may work without cookies.'}${c.updated?' · Saved '+E(new Date(c.updated*1000).toLocaleString()):''} This checks file contents, not live login validity.</small></div><div class="cookie-actions"><button class="secondary" data-cookie="${key}">${c.present?'Replace':'Import'} cookies.txt</button>${c.present?`<button class="secondary danger" data-cookie-delete="${key}">Remove session</button>`:''}</div>${manual}${key==='instagram'?'<p class="hint">Automatic session updates: after a successful download, cookies refreshed by Instagram are saved for the next job. Session cookies with no fixed expiry remain valid locally; only Instagram can renew or revoke the login.</p>':''}<p class="hint">Cookie values stay private on the laptop and are never returned to this page. They expire independently of your 35-day desk login. After replacing them, retry the affected job from Attention. See README for Firefox instructions.</p></div></div>`;
}
async function showDetail(id){
  const j=await api('job?id='+encodeURIComponent(id));$('#detailTitle').textContent=j.name;
  $('#detailBody').innerHTML=`<p class="detail-label">SOURCE URL</p><p class="detail-value">${E(j.url)}</p><p class="detail-label">DESTINATION</p><p class="detail-value">${E(shortFolder(j))}</p><p class="detail-label">STATUS</p><p class="detail-value">${E(labels[j.status])} · ${j.attempts} attempt(s)</p><p class="detail-value">${E(j.detail)}</p>${j.error_kind==='auth'?'<button class="secondary" data-open-platforms>Open platform settings</button>':''}<p class="detail-label">RECENT ENGINE OUTPUT</p><pre class="log">${E(j.log||j.last_error||j.detail||'No engine output yet.')}</pre><p class="hint">Gallery jobs report completed items rather than a reliable overall percentage. Video progress is for the current stream/item; merging can continue after it reaches 100%.</p><div class="dialog-footer"><button class="secondary" data-job="${j.id}" data-action="first">Move to front</button><button class="secondary" data-job="${j.id}" data-action="cancel">Cancel job · keep files</button><button class="primary" data-job-folder="${E(join(j.folder,j.subdir))}">Open folder</button></div>`;
  openDialog('detailDialog');
}
async function showTorrentFiles(id){
  const torrent=await api('torrent?id='+encodeURIComponent(id));$('#detailTitle').textContent=torrent.name;
  if(!torrent.files.length){$('#detailBody').innerHTML='<p class="hint">Metadata is not available yet. Start the torrent briefly and reopen Files. Magnet links need peers to discover their file list.</p>';}
  else{$('#detailBody').innerHTML=`<p class="hint">Unchecking a file stops future downloading; it does not delete existing data. Shared torrent pieces may contain small portions of adjacent files.</p><form id="torrentFilesForm" data-id="${E(id)}"><div class="torrent-files">${torrent.files.map((f,i)=>`<label><input type="checkbox" name="file" value="${i}" ${torrent.fileStats[i].wanted?'checked':''}><span>${E(f.name)}<small class="hint"> · ${bytes(f.length)} · ${f.length?(100*f.bytesCompleted/f.length).toFixed(0):100}%</small></span></label>`).join('')}</div><div class="dialog-footer"><button type="button" class="secondary" data-torrent="${E(id)}" data-action="verify">Verify existing data</button><button class="primary" type="submit">Save file selection</button></div></form>`;}
  openDialog('detailDialog');
}
$('#authForm').addEventListener('submit',async event=>{event.preventDefault();const form=event.target;const button=$('button[type=submit]',form);button.disabled=true;$('#authError').textContent='';try{const data=Object.fromEntries(new FormData(form));await api(form.dataset.setup==='true'?'setup':'login',data);form.reset();await showAuth();}catch(error){$('#authError').textContent=error.message;}finally{button.disabled=false;}});
$('#downloadForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const form=event.target,data=Object.fromEntries(new FormData(form));data.urls=data.urls.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);data.playlist=form.playlist.checked;data.subtitles=form.subtitles.checked;const result=await api('jobs',data);closeDialog($('#downloadDialog'));form.urls.value='';toast(`${result.ids.length} download(s) queued${result.skipped?`; ${result.skipped} duplicate(s) skipped`:''}.`);await refresh();});});
$('#torrentForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const form=event.target;const data={folder:form.folder.value,magnet:form.magnet.value.trim(),paused:form.paused.checked};if(!data.magnet){const file=form.torrent.files[0];if(!file)throw Error('Paste a magnet link or choose a .torrent file.');if(file.size>8*1024*1024)throw Error('Torrent file must be smaller than 8 MiB.');const array=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<array.length;i+=8192)binary+=String.fromCharCode(...array.subarray(i,i+8192));data.metainfo=btoa(binary);}await api('torrents',data);closeDialog($('#torrentDialog'));form.reset();toast('Torrent added.');await setPage('torrents');await refresh();});});
$('#settingsForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{await api('settings',Object.fromEntries(new FormData(event.target)));toast('Settings saved. Media limits apply to the next job.');await refresh();});});
$('#passwordForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{await api('password',Object.fromEntries(new FormData(event.target)));event.target.reset();toast('Password changed. Sign in again on each device.');await showAuth();});});
$('#folderCreate').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const path=join(folderPath,event.target.elements.name.value.trim());await api('folders',{path});event.target.reset();folderPath=path;await loadFolders();});});
$('#folderUp').onclick=()=>action(async()=>{folderPath=parent(folderPath);await loadFolders();});
$('#chooseFolder').onclick=()=>{$('#'+folderTarget+' [name=folder]').value=folderPath;closeDialog($('#folderDialog'));};
$('#filesUp').onclick=()=>action(async()=>{filePath=parent(filePath);await loadFiles();});
$('#refreshFiles').onclick=()=>action(loadFiles);
$('#newFileFolder').onclick=()=>action(async()=>{const name=prompt('New subfolder name inside Zedair/'+filePath);if(!name)return;await api('folders',{path:join(filePath,name.trim())});await loadFiles();});
$('#holdBtn').onclick=()=>action(async()=>{await api('hold',{hold:!state.settings.hold});await refresh();});
$('#unplugBtn').onclick=()=>action(async()=>{await api('hold',{hold:true});await refresh();toast('Queue held. Run the stop and unmount commands before unplugging.');});
$('#logoutBtn').onclick=()=>action(async()=>{await api('logout',{});await showAuth();});
$('#themeBtn').onclick=()=>{document.body.classList.toggle('dark');localStorage.setItem('zedair-theme',document.body.classList.contains('dark')?'dark':'light');};
if(localStorage.getItem('zedair-theme')==='dark')document.body.classList.add('dark');
$('#searchJobs').addEventListener('input',renderJobs);
$('#clearHistory').onclick=()=>action(async()=>{if(!confirm('Clear completed and cancelled jobs from history? Downloaded files will stay on the SSD.'))return;await api('history/clear',{});await refresh();});
$('#cookieFile').addEventListener('change',event=>action(async()=>{const file=event.target.files[0];if(!file)return;if(file.size>1024*1024)throw Error('Cookie file must be smaller than 1 MiB.');await api('cookies',{slot:cookieSlot,content:await file.text()});event.target.value='';await refresh();renderSettings();toast('Cookie file imported. It will be used by new download attempts.');}));
document.addEventListener('click',event=>{
  const el=event.target.closest('button');if(!el)return;
  action(async()=>{
    if(el.dataset.settingsTab)selectSettingsTab(el.dataset.settingsTab);
    if(el.dataset.platform){selectedPlatform=el.dataset.platform;renderPlatform();}
    if(el.hasAttribute('data-open-platforms')){closeDialog($('#detailDialog'));settingsTab='platforms';await setPage('settings');}
    if(el.dataset.deleteFile){if(!confirm('Permanently delete Zedair/'+el.dataset.deleteFile+' from the SSD? This cannot be undone.'))return;await api('file/delete',{path:el.dataset.deleteFile,stamp:el.dataset.stamp,confirm:true});await loadFiles();toast('File permanently deleted.');}
    if(el.dataset.previewFile)showPreview(el.dataset.previewFile,el.dataset.previewKind);
    if(el.dataset.speedReport)showSpeedReport(el.dataset.speedReport);
    if(el.dataset.theme!==undefined)$('#themeBtn').click();
    if(el.dataset.logout!==undefined)$('#logoutBtn').click();
    if(el.dataset.close!==undefined)closeDialog(el.closest('dialog'));
    if(el.dataset.open)openDialog(el.dataset.open);
    if(el.dataset.page)await setPage(el.dataset.page);
    if(el.dataset.filter){filter=el.dataset.filter;$$('[data-filter]').forEach(b=>b.classList.toggle('selected',b===el));renderJobs();}
    if(el.dataset.browse){folderTarget=el.dataset.browse;folderPath=$('#'+folderTarget+' [name=folder]').value.trim();try{await loadFolders();}catch(_){folderPath='';await loadFolders();}openDialog('folderDialog');}
    if(el.dataset.folder!==undefined){folderPath=el.dataset.folder;await loadFolders();}
    if(el.dataset.fileFolder!==undefined){filePath=el.dataset.fileFolder;await loadFiles();}
    if(el.dataset.jobFolder!==undefined){$$('dialog[open]').forEach(closeDialog);filePath=el.dataset.jobFolder;await setPage('files');}
    if(el.dataset.detail)await showDetail(el.dataset.detail);
    if(el.dataset.job){await api('job/action',{id:el.dataset.job,action:el.dataset.action});if($('#detailDialog').open)closeDialog($('#detailDialog'));await refresh();}
    if(el.dataset.torrent){if(el.dataset.action==='remove'&&!confirm('Remove this torrent from the queue? Downloaded files will stay on the SSD.'))return;await api('torrent/action',{id:el.dataset.torrent,action:el.dataset.action});toast('Torrent updated.');await refresh();}
    if(el.dataset.torrentFiles)await showTorrentFiles(el.dataset.torrentFiles);
    if(el.dataset.cookie){cookieSlot=el.dataset.cookie;$('#cookieFile').click();}
    if(el.dataset.cookieDelete){if(!confirm('Remove the saved '+platformNames[el.dataset.cookieDelete]+' session from this laptop?'))return;await api('cookies',{slot:el.dataset.cookieDelete,delete:true});await refresh();renderSettings();toast('Cookie file deleted.');}
  });
});
document.addEventListener('submit',event=>{if(event.target.id==='torrentFilesForm'){event.preventDefault();action(async()=>{const wanted=$$('input:checked',event.target).map(i=>Number(i.value));await api('torrent/action',{id:event.target.dataset.id,action:'files',wanted});closeDialog($('#detailDialog'));toast('File selection saved.');});}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('#shell').hidden)refresh();});
showAuth().catch(error=>{$('#auth').hidden=false;$('#authError').textContent='Cannot connect to the laptop: '+error.message;});

document.addEventListener('submit',event=>{
  if(event.target.id==='platformForm'){
    event.preventDefault();action(async()=>{const form=event.target,p=state.platforms[selectedPlatform],values={};for(const [key,value] of Object.entries(p)){const input=form.elements[key];values[key]=typeof value==='boolean'?input.checked:typeof value==='number'?Number(input.value):input.value;}await api('platforms',{platform:selectedPlatform,values});await refresh();renderPlatform();toast('Platform defaults saved.');});
  }
  if(event.target.id==='sessionForm'){
    event.preventDefault();action(async()=>{const form=event.target;await api('cookies',{slot:selectedPlatform,fields:Object.fromEntries(new FormData(form))});form.reset();await refresh();renderPlatform();toast('Private session saved. Retry the affected download when ready.');});
  }
});


function renderSpeed(){
  const s=state.speedtest,active=s.status==='running';
  const display=s.status==='idle'?s.history.find(r=>r.engine==='ookla'&&r.status==='done')||s:s;
  $('#speedDown').textContent=display.download_mbps??'—';$('#speedUp').textContent=display.upload_mbps??'—';$('#speedLatency').textContent=display.latency_ms??'—';$('#speedJitter').textContent=display.jitter_ms??'—';
  $('#speedLive').textContent=active?(s.live_mbps??'—'):(display.download_mbps??'—');
  const phase={starting:'Connecting',latency:'Ping',download:'Download',upload:'Upload',done:'Complete',error:'Failed',cancelled:'Cancelled',idle:'Ready'};
  $('#speedPhase').textContent=s.status==='idle'&&display!==s?'Last result':phase[s.phase]||'Ready';$('#speedStatus').textContent=s.status==='idle'&&display!==s?'Last completed test · '+new Date(display.finished*1000).toLocaleString():s.detail;
  $('#speedProgress').style.width=(active?s.progress||0:s.status==='done'?100:0)+'%';$('#speedTrack').setAttribute('aria-valuenow',String(active?s.progress||0:s.status==='done'?100:0));
  $('#startSpeed').disabled=active||!s.installed;$('#startSpeed').textContent=active?'Testing…':'Start test';$('#cancelSpeed').hidden=!active;
  $('#speedSetup').hidden=s.installed;$('#speedAgreement').hidden=s.consent||!s.installed;
  $('#speedServer').textContent=display.server?[display.server,display.location,display.isp].filter(Boolean).join(' · '):'Nearby server selected automatically';
  const transfers=state.jobs.some(j=>j.status==='running')||state.torrents.some(t=>[4,6].includes(t.status));
  $('#speedWarning').hidden=!transfers;$('#speedWarning').textContent='Transfers are active on the laptop. Hold the queue and wait for them to stop before testing.';
  const value=v=>v===undefined||v===null?'—':E(v);
  $('#speedHistory').innerHTML=s.history.length?`<div class="table-scroll"><table><thead><tr><th>Time</th><th>Download</th><th>Upload</th><th>Ping</th><th>Status / method</th><th>Report</th></tr></thead><tbody>${s.history.map(r=>`<tr><td>${E(new Date(r.finished*1000).toLocaleString())}</td><td>${value(r.download_mbps)} Mbps</td><td>${value(r.upload_mbps)} Mbps</td><td>${value(r.latency_ms)} ms</td><td>${r.engine==='legacy-http'?'<span class="legacy-label">Legacy estimate · unreliable</span>':E(r.status==='done'?'Completed · Ookla':r.status)}</td><td><button class="secondary compact" data-speed-report="${E(r.id)}">View</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="hint">No tests yet. Your reports will appear here.</p>';
}
function showSpeedReport(id){
  const r=state.speedtest.history.find(x=>x.id===id);if(!r)return;
  $('#detailTitle').textContent='Speed test report';
  const metric=(label,value,unit)=>`<div><span>${label}</span><strong>${value??'—'} <small>${unit}</small></strong></div>`;
  $('#detailBody').innerHTML=`<p class="detail-value">${E(new Date(r.finished*1000).toLocaleString())} · ${E(r.status||'done')}</p>${r.engine==='legacy-http'?'<p class="notice">This report used the old short HTTP test. Its speeds are not reliable and are not used as the latest result.</p>':''}<div class="speed-report-grid">${metric('Download',E(r.download_mbps??'—'),'Mbps')}${metric('Upload',E(r.upload_mbps??'—'),'Mbps')}${metric('Ping',E(r.latency_ms??'—'),'ms')}${metric('Jitter',E(r.jitter_ms??'—'),'ms')}</div><p class="detail-label">SERVER / NETWORK</p><p class="detail-value">${E([r.server,r.location,r.isp].filter(Boolean).join(' · ')||'Not recorded')}</p><p class="detail-label">TEST DETAILS</p><p class="detail-value">Duration: ${E(r.duration_seconds??'—')} s · Download data: ${bytes(r.download_bytes)} · Upload data: ${bytes(r.upload_bytes)}${r.packet_loss!==undefined?' · Packet loss: '+E(r.packet_loss)+'%':''}</p><p class="hint">${E(r.detail||'')}</p>${r.result_url?`<a class="secondary" href="${E(r.result_url)}" target="_blank" rel="noopener noreferrer">Open Ookla result</a>`:''}`;
  openDialog('detailDialog');
}
function showPreview(path,kind){
  if(!['image','video','audio'].includes(kind))return;
  $('#previewTitle').textContent=path.split('/').pop();$('#previewError').hidden=true;
  const url='/api/preview?path='+encodeURIComponent(path);
  $('#previewDownload').href='/api/file?path='+encodeURIComponent(path);
  $('#previewBody').innerHTML='<div class="preview-stage"></div>';
  const element=document.createElement(kind==='image'?'img':kind);element.src=url;
  if(kind==='image')element.alt=path.split('/').pop();else{element.controls=true;element.preload='metadata';element.setAttribute('playsinline','');}
  element.addEventListener('error',()=>$('#previewError').hidden=false,{once:true});
  $('.preview-stage').appendChild(element);openDialog('previewDialog');
}
function clearPreview(){const media=$('video,audio',$('#previewBody'));if(media){media.pause();media.removeAttribute('src');media.load();}$('#previewBody').replaceChildren();}
$('#previewDialog').addEventListener('close',clearPreview);
$('#startSpeed').onclick=()=>action(async()=>{if(!state.speedtest.consent&&!$('#acceptSpeedTerms').checked)throw Error('Review and accept the Ookla agreement before starting.');await api('speedtest',{action:'start',accept_terms:$('#acceptSpeedTerms').checked});await refresh();});
$('#cancelSpeed').onclick=()=>action(async()=>{await api('speedtest',{action:'cancel'});await refresh();});
