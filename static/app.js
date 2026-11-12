'use strict';
const $ = (q, root = document) => root.querySelector(q);
const $$ = (q, root = document) => [...root.querySelectorAll(q)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const E = escapeHTML;
let selectedPlatform='instagram', settingsTab='general', toolPlatform='instagram';
let fileSort=localStorage.getItem('zedair-file-sort')||'date-desc';
if(!['date-desc','date-asc','name-asc','name-desc','size-desc','size-asc'].includes(fileSort))fileSort='date-desc';
let filePageSize=Number(localStorage.getItem('zedair-page-size'))||24;
if(![24,50,100].includes(filePageSize))filePageSize=24;
let lastFullRefresh=0;
let queueOffset=0,queuePageSize=Number(localStorage.getItem('zedair-queue-page-size'))||10;
if(![10,25,50].includes(queuePageSize))queuePageSize=10;
const selectedFiles=new Map();let organizeItems=[];
let previewPath='',previewKind='',previewRequest=0,previewBusy=false;
let fileView=localStorage.getItem('zedair-file-view')==='grid'?'grid':'list',fileOffset=0,fileEntries=[],fileTotal=0,fileRequest=0,loadedFilePath=null;
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
function openDialog(id){const dialog=$('#'+id);for(const form of $$('form',dialog))form.noValidate=true; if(!dialog.open)dialog.showModal();}
function closeDialog(dialog){if(dialog.id==='previewDialog')clearPreview();dialog.close();}
function uiMessage(text,{title='Confirm',accept='Confirm',input=false,danger=false}={}){
  if($('#messageDialog').open)return Promise.resolve(input?null:false);
  $('#messageTitle').textContent=title;$('#messageText').textContent=text;$('#messageAccept').textContent=accept;
  $('#messageAccept').classList.toggle('danger',danger);$('#messageInputLabel').hidden=!input;$('#messageInput').value='';
  return new Promise(resolve=>{const dialog=$('#messageDialog');let result=input?null:false;
    $('#messageForm').onsubmit=event=>{event.preventDefault();if(input&&!$('#messageInput').value.trim()){toast('Enter a folder name.');$('#messageInput').focus();return;}result=input?$('#messageInput').value.trim():true;dialog.close();};
    $('#messageCancel').onclick=()=>dialog.close();dialog.addEventListener('close',()=>resolve(result),{once:true});
    openDialog('messageDialog');(input?$('#messageInput'):$('#messageCancel')).focus();
  });
}
function engineName(j){return j.kind==='instagram'?'Instagram engine':j.kind==='threads'?'Threads engine':j.engine==='browser'?'Browser engine':j.engine==='page'?'Page engine':j.engine==='direct'?'HTTP file engine':j.engine==='gallery'?'Gallery engine':'Video engine';}
function jobMatches(j,type){return type==='all'||type==='active'&&j.status==='running'||type==='waiting'&&['queued','retry','blocked','paused'].includes(j.status)||type==='error'&&j.status==='error'||type==='done'&&['done','cancelled'].includes(j.status);}
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
async function refresh(tick=false){
  if(busy)return;busy=true;clearTimeout(timer);
  try{
    if(tick&&!document.hidden&&page==='queue'&&state?.jobs.some(j=>j.status==='running')&&Date.now()-lastFullRefresh<4000){
      const progress=await api('progress');
      const old=state.jobs.filter(j=>j.status==='running').map(j=>j.id).sort().join(',');
      if(progress.jobs.map(j=>j.id).sort().join(',')===old){for(const live of progress.jobs)Object.assign(state.jobs.find(j=>j.id===live.id),live);renderJobs(true);renderOverview();return;}
    }
    state=await api('state');lastFullRefresh=Date.now(); $('#connection').textContent='● Connected';$('#connection').classList.remove('offline');
    renderOverview(); if(page==='queue')renderJobs(); if(page==='torrents')renderTorrents(); if(page==='speed')renderSpeed(); if(page==='tools')renderProfileJobs(); if(page==='settings'&&settingsTab==='system'&&!document.hidden)await renderSystem();
  }catch(error){$('#connection').textContent='● Reconnecting';$('#connection').classList.add('offline');}
  finally{busy=false;if(!$('#shell').hidden)timer=setTimeout(()=>refresh(true),document.hidden?15000:page==='queue'&&state?.jobs.some(j=>j.status==='running')?1000:4000);}
}
function renderOverview(){
  const s=state, jobs=s.jobs;
  const running=jobs.filter(j=>j.status==='running'), torrents=s.torrents.filter(t=>t.status===4);const total=$('#aggregateSpeed');if(total){total.hidden=running.length+torrents.length<2;total.textContent='↓ '+bytes(running.reduce((n,j)=>n+(Number(j.speed)||0),0)+torrents.reduce((n,t)=>n+(Number(t.rateDownload)||0),0))+'/s total';}
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
function renderJobs(fast=false){
  if(fast){for(const j of state.jobs.filter(j=>j.status==='running')){const node=$('[data-progress-job="'+j.id+'"]');if(!node)continue;const percent=Math.max(0,Math.min(100,Number(j.progress)||0));$('.job-title',node).textContent=j.name;$('.engine-badge',node).textContent=engineName(j);$('.job-state span:last-child',node).textContent=j.profile_mode?'Working…':percent.toFixed(1)+'%';$('.progress i',node).style.width=percent+'%';$('.job-speed',node).textContent=j.profile_mode?`${j.items||0} files this job · ${j.profile_mode} · total unknown`:`${bytes(j.speed)}/s · ETA ${eta(j.eta)}`;}return;}

  const query=$('#searchJobs').value.trim().toLowerCase();
  for(const b of $$('[data-filter]')){const count=state.jobs.filter(j=>jobMatches(j,b.dataset.filter)).length;const label={all:'All',active:'Active',waiting:'Waiting',error:'Attention',done:'Finished'}[b.dataset.filter];b.innerHTML=label+(count?`<span class="tab-count">${count}</span>`:'');}
  const jobs=state.jobs.filter(j=>(!query||`${j.name} ${j.url} ${j.folder}`.toLowerCase().includes(query))&&(filter==='all'||(filter==='active'&&j.status==='running')||(filter==='waiting'&&['queued','retry','blocked','paused'].includes(j.status))||(filter==='error'&&j.status==='error')||(filter==='done'&&['done','cancelled'].includes(j.status))));
  if(!jobs.length){$('#queuePageLabel').textContent='0 of 0';$('#queuePrev').disabled=true;$('#queueNext').disabled=true;$('#jobs').innerHTML=empty('↙',state.jobs.length?'No matches here.':'No downloads yet.',state.jobs.length?'Try another filter or search.':'Add a link to start downloading.',state.jobs.length?'':'<button class="secondary" data-open="downloadDialog">＋ Add download</button>');return;}
  if(queueOffset>=jobs.length)queueOffset=Math.max(0,Math.floor((jobs.length-1)/queuePageSize)*queuePageSize);
  $('#queuePrev').disabled=queueOffset===0;$('#queueNext').disabled=queueOffset+queuePageSize>=jobs.length;$('#queuePageSize').value=queuePageSize;$('#queuePageLabel').textContent=`${queueOffset+1}–${Math.min(queueOffset+queuePageSize,jobs.length)} of ${jobs.length}`;
  const rowsHTML=jobs.slice(queueOffset,queueOffset+queuePageSize).map(j=>{
    const active=['queued','running','retry','blocked'].includes(j.status);
    const info=j.profile_mode?`${j.items||0} files this job · ${j.profile_mode} · ${j.status==='running'?'total unknown':labels[j.status]||j.status}`:j.status==='running'?`${bytes(j.speed)}/s · ETA ${eta(j.eta)}`:j.status==='retry'?`Retry in ${eta(Math.max(1,j.next_try-Date.now()/1000))}`:j.status==='done'?'On the SSD':`Attempt ${j.attempts} · ${engineName(j)}`;
    const percent=Math.max(0,Math.min(100,Number(j.progress)||0));
    return `<article class="job" data-progress-job="${j.id}"><div class="source-icon ${E(j.kind)}"><img class="queue-favicon" src="/api/favicon?id=${j.id}" alt=""><span class="source-fallback" aria-hidden="true">${symbols[j.kind]||'↙'}</span></div><div class="job-info"><span class="job-title" title="${E(j.name)}">${E(j.name)}</span><div class="job-meta" title="${E(shortFolder(j))}">${E(shortFolder(j))}</div><span class="engine-badge">${E(engineName(j))}</span>${j.status==='error'?`<span class="attention-note">${E(j.detail)}</span>`:''}</div><div class="job-progress"><div class="job-state"><span class="pill ${E(j.status)}">${E(labels[j.status]||j.status)}</span><span>${j.status==='running'?(j.profile_mode?'Working…':percent.toFixed(1)+'%'):''}</span></div><div class="progress"><i style="width:${percent}%"></i></div><div class="job-speed">${E(info)}</div></div><div class="job-controls">${active?button('Ⅱ','Pause',`data-job="${j.id}" data-action="pause"`):j.status!=='done'?button('▶','Resume / retry',`data-job="${j.id}" data-action="retry"`):button('▤','Open destination',`data-job-folder="${E(join(j.folder,j.subdir))}"`)}${button('⋯','Details and log',`data-detail="${j.id}"`)}${button('×','Remove from queue',`data-job="${j.id}" data-action="remove"`)}</div></article>`;
  }).join('');
  reconcileJobs(rowsHTML);
}
function renderTorrents(){
  $('#torrentAlert').hidden=!state.torrent_error;$('#torrentAlert').textContent=state.torrent_error;
  if(!state.torrents.length){$('#torrents').innerHTML=empty('⇄','No torrents yet.','Add a magnet link or upload a .torrent file. Files download directly into your chosen SSD folder.');return;}
  const status=['Paused','Verify queued','Verifying','Queued','Downloading','Seed queued','Seeding'];
  $('#torrents').innerHTML=state.torrents.map(t=>`<article class="job"><div class="source-icon torrent">⇄</div><div><div class="job-title">${E(t.name)}</div><div class="job-meta">${bytes(t.totalSize)} <b>·</b> Ratio ${Number(t.uploadRatio||0).toFixed(2)}</div>${t.errorString?`<p class="form-error">${E(t.errorString)}</p>`:''}</div><div class="job-progress"><div class="job-state"><span class="pill ${t.status===4?'running':''}">${E(status[t.status]||'Waiting')}</span><span>${(100*t.percentDone).toFixed(1)}%</span></div><div class="progress"><i style="width:${100*t.percentDone}%"></i></div><div class="job-speed">↓ ${bytes(t.rateDownload)}/s · ↑ ${bytes(t.rateUpload)}/s</div></div><div class="job-controls">${button(t.status===0?'▶':'Ⅱ',t.status===0?'Resume':'Pause',`data-torrent="${E(t.hashString)}" data-action="${t.status===0?'resume':'pause'}"`)}${button('▤','Select files',`data-torrent-files="${E(t.hashString)}"`)}${button('×','Remove torrent, keep files',`data-torrent="${E(t.hashString)}" data-action="remove"`)}</div></article>`).join('');
}
async function setPage(next){
  page=next; for(const name of ['queue','torrents','files','tools','speed','settings'])$('#'+name+'Page').hidden=name!==next;
  $$('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===next));
  $('#pageCrumb').textContent=next==='queue'?'DOWNLOADS':next.toUpperCase();
  if(!state)return;
  if(next==='queue')renderJobs();if(next==='torrents')renderTorrents();if(next==='files')await loadFiles();if(next==='settings')renderSettings();if(next==='speed')renderSpeed();if(next==='tools'){renderTools();renderProfileJobs();}
}
async function loadFiles(){
  if(loadedFilePath!==filePath){fileOffset=0;loadedFilePath=filePath;selectedFiles.clear();}
  const request=++fileRequest;
  $('#filePath').textContent='Zedair/'+filePath;$('#filesUp').disabled=!filePath;
  try{const result=await api('folders?path='+encodeURIComponent(filePath)+'&offset='+fileOffset+'&limit='+filePageSize+'&sort='+fileSort.split('-')[0]+'&order='+fileSort.split('-')[1]);if(request!==fileRequest)return;
    fileEntries=result.entries;fileTotal=result.total??result.entries.length;
    if(!fileEntries.length&&fileOffset){fileOffset=Math.max(0,fileOffset-filePageSize);return loadFiles();}
    for(const entry of fileEntries){const key=join(filePath,entry.name);if(selectedFiles.has(key)&&selectedFiles.get(key)!==entry.stamp)selectedFiles.delete(key);}
    renderFiles();
  }catch(error){if(request===fileRequest)$('#fileList').innerHTML=empty('!','Storage unavailable',error.message);}
}
function renderFiles(){
  $('#fileSort').value=fileSort;$('#filePageSize').value=String(filePageSize);$('#deleteFolder').disabled=!filePath;
  $('#fileList').classList.toggle('file-grid',fileView==='grid');
  $$('[data-file-view]').forEach(b=>{b.classList.toggle('selected',b.dataset.fileView===fileView);b.setAttribute('aria-pressed',String(b.dataset.fileView===fileView));});
  $('#filesCount').textContent=fileTotal+' items';$('#filesPrev').disabled=fileOffset===0;$('#filesNext').disabled=fileOffset+filePageSize>=fileTotal;
  $('#filesPageLabel').textContent=fileTotal?`${fileOffset+1}–${Math.min(fileOffset+filePageSize,fileTotal)} of ${fileTotal}`:'No files';
  $('#fileList').innerHTML=fileEntries.length?fileEntries.map(entry=>{
    const path=join(filePath,entry.name),attrs=entry.directory?`data-file-folder="${E(path)}"`:`data-preview-file="${E(path)}" data-preview-kind="${E(entry.preview)}"`;
    const icon=entry.directory?'▰':entry.preview==='video'?'▷':entry.preview==='image'?'▧':entry.preview==='audio'?'♫':'▤';
    const selection=entry.directory?'':`<label class="file-select"><input type="checkbox" data-select-file="${E(path)}" data-stamp="${E(entry.stamp)}" aria-label="Select ${E(entry.name)}" ${selectedFiles.has(path)?'checked':''}></label>`;
    const actions=entry.directory?'':`<div class="file-actions"><button class="secondary compact" data-rename-file="${E(path)}" data-stamp="${E(entry.stamp)}">Rename</button><a class="secondary compact" href="/api/file?path=${encodeURIComponent(path)}" download aria-label="Download ${E(entry.name)}">Download</a><button class="secondary compact danger" data-delete-file="${E(path)}" data-stamp="${E(entry.stamp)}" aria-label="Delete ${E(entry.name)}">Delete</button></div>`;
    if(fileView==='grid'){
      const media=entry.preview==='image'?`<span class="file-thumb"><span class="thumb-fallback">▧</span><img src="/api/preview?path=${encodeURIComponent(path)}" loading="lazy" decoding="async" alt="${E(entry.name)}"></span>`:`<span class="file-thumb placeholder"><span>${icon}</span><small>${entry.directory?'FOLDER':entry.preview==='video'?'VIDEO · PREVIEW TO PLAY':entry.preview==='audio'?'AUDIO':'FILE'}</small></span>`;
      const tag=entry.directory||entry.preview?'button':'div';
      return `<article class="file-card ${selectedFiles.has(path)?'is-selected':''}">${selection}<${tag} class="file-tile" ${tag==='button'?attrs:''}>${media}<strong>${E(entry.name)}</strong><small>${entry.directory?'Open folder':bytes(entry.size)}</small></${tag}>${actions}</article>`;
    }
    if(entry.directory)return `<button class="file-row" ${attrs}><span>${icon}</span><strong>${E(entry.name)}</strong><small>Open folder →</small></button>`;
    const tag=entry.preview?'button':'div';return `<div class="file-entry ${selectedFiles.has(path)?'is-selected':''}">${selection}<${tag} class="file-row" ${tag==='button'?attrs:''}><span>${icon}</span><strong>${E(entry.name)}${entry.preview?'<span class="file-preview-label"> · Preview</span>':''}</strong><small>${bytes(entry.size)}</small></${tag}>${actions}</div>`;
  }).join(''):empty('▤','This folder is empty.','');
  renderSelection();
}
$('#fileSort').value=fileSort;
$('#fileSort').onchange=()=>{fileSort=$('#fileSort').value;localStorage.setItem('zedair-file-sort',fileSort);fileOffset=0;loadFiles();};
$('#filePageSize').value=String(filePageSize);
$('#filePageSize').onchange=()=>{filePageSize=Number($('#filePageSize').value);localStorage.setItem('zedair-page-size',String(filePageSize));fileOffset=0;loadFiles();};
$('#filesPrev').onclick=()=>{fileOffset=Math.max(0,fileOffset-filePageSize);loadFiles();};
$('#filesNext').onclick=()=>{fileOffset+=filePageSize;loadFiles();};
document.addEventListener('error',event=>{if(event.target.matches?.('.file-thumb img'))event.target.remove();},true);
async function loadFolders(){
  $('#folderPath').textContent='Zedair/'+folderPath;$('#folderUp').disabled=!folderPath;
  const result=await api('folders?path='+encodeURIComponent(folderPath)+'&sort=name&order=asc');
  const dirs=result.entries.filter(e=>e.directory);
  $('#folderList').innerHTML=dirs.length?dirs.map(e=>`<button class="file-row" data-folder="${E(join(folderPath,e.name))}"><span>▰</span><strong>${E(e.name)}</strong><small>→</small></button>`).join(''):empty('▤','No subfolders.','Use this folder, or create one below.');
}
const platformNames={youtube:'YouTube',facebook:'Facebook',reddit:'Reddit',instagram:'Instagram',x:'X / Twitter',threads:'Threads',other:'Other websites'};
function selectSettingsTab(tab){
  settingsTab=tab; if(tab==='system')renderSystem();if(tab==='engines')action(()=>loadPackages());if(tab==='usage')action(loadUsage); $$('[data-settings-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.settingsTab===tab));
  $$('[data-settings-panel]').forEach(p=>p.hidden=p.dataset.settingsPanel!==tab);
}
function renderSettings(){
  $('#engineVersions').innerHTML=Object.entries(state.engine_versions||{}).map(([key,value])=>`<p><strong>${E(key)}</strong> · ${E(value)}</p>`).join('');
  buildSettingControls();
  Object.entries(state.settings).forEach(([k,v])=>{const field=$(`[name="${k}"]`,$('#settingsForm'));if(field&&!savingSettings.has(k)&&field!==document.activeElement){if(field.type==='checkbox')field.checked=Boolean(v);else if(field.type==='range')setRange(field,v);else field.value=v;}});
  $('#telegramTokenStatus').textContent=state.settings.telegram_token_set?'Token saved · hidden':'No token saved';
  selectSettingsTab(settingsTab);renderPlatform();
}
function renderPlatform(){
  const key=selectedPlatform,p=state.platforms[key],c=state.cookie_status[key];
  $('#platformTabs').innerHTML=Object.entries(platformNames).map(([k,n])=>`<button class="${k===key?'selected':''}" data-platform="${k}">${n}</button>`).join('');
  const check=(name,label)=>`<label class="check"><input type="checkbox" name="${name}" ${p[name]?'checked':''}>${label}</label>`;
  const select=(name,label,choices)=>`<label>${label}<select name="${name}">${choices.map(([v,t])=>`<option value="${v}" ${p[name]===v?'selected':''}>${t}</option>`).join('')}</select></label>`;
  let fields=check('enabled','Enable this platform')+`<label>Platform folder<input name="folder" value="${E(p.folder)}" placeholder="${key==='other'?'Automatic website domain':'Folder name'}" maxlength="150" ${key==='other'?'':'required'}><small>Used only when no explicit destination is selected.</small></label>`;
  if('quality' in p)fields+=select('quality','Default video quality',[['best','Best available'],['4320','Up to 8K'],['2160','Up to 4K'],['1080','Up to 1080p'],['720','Up to 720p'],['480','Up to 480p'],['audio','Audio only']]);
  if('container' in p)fields+=select('container','Default video file format',[['auto','Automatic · any format'],['mp4','MP4 only']]);
  if('subtitles' in p)fields+=check('subtitles','English / Malayalam subtitles');
  if(key==='youtube')fields+=check('playlist','Include playlists by default')+`<label>Playlist item limit<input type="number" name="playlist_limit" min="1" max="500" value="${p.playlist_limit}" required></label>`;
  if(key==='instagram')fields+=check('require_cookies','Require session cookies before downloading')+check('warn_images','Warn if original image resolution is unavailable')+select('videos','Video source',[['dash','DASH · best available streams'],['merged','Pre-merged video']])+`<label>Delay between requests (seconds)<input type="number" name="sleep_request" min="6" max="60" value="${p.sleep_request}" required></label><p class="hint">Downloads every image/video in a single post or carousel. Highest quality available to your session; the website can limit it.</p>`;
  if(key==='facebook')fields+='<p class="hint">Direct videos and reels use yt-dlp. Whole profiles are in Tools with an optional browser collector.</p>';
  if(key==='reddit')fields+='<p class="hint">Use a Reddit post URL for hosted videos with audio. FFmpeg joins separate audio/video streams. Some posts require account cookies.</p>';
  if(key==='x')fields+=select('api','X video API',[['graphql','GraphQL (recommended)'],['legacy','Legacy'],['syndication','Syndication · public posts']])+`<p class="hint">Paste an x.com or twitter.com status URL. Video posts and animated GIFs use the video engine. Photos and whole feeds are not supported.</p>`;
  if(key==='threads')fields+='<p class="hint">Experimental: exact public posts and carousels from embedded page data. Login walls or page changes can prevent extraction.</p>';
  if(key==='other')fields+='<p class="hint">Supported video sites, including supported adult sites, use yt-dlp. Availability varies; DRM media is not supported. An empty folder uses the source hostname.</p>';
  const raw=key==='instagram'?['sessionid','csrftoken','ds_user_id']:key==='facebook'?['c_user','xs','datr','fr']:key==='x'?['auth_token','ct0']:[];
  const manual=raw.length?`<form id="sessionForm" novalidate autocomplete="off"><h3>Paste session values</h3><p class="hint">From your browser’s cookies for ${key==='x'?'x.com':key==='facebook'?'facebook.com':'instagram.com'}. Paste the value only. Saving replaces this platform’s cookie file.</p>${raw.map(n=>`<label>${n}${(key==='instagram'&&n!=='sessionid')||(key==='facebook'&&['datr','fr'].includes(n))?' (optional)':''}<input type="password" name="${n}" autocomplete="new-password" spellcheck="false" maxlength="8192" ${key==='x'||n==='sessionid'||['c_user','xs'].includes(n)?'required':''}></label>`).join('')}<button class="secondary" type="submit">Save private session</button></form>`:'';
  $('#platformEditor').innerHTML=`<div class="platform-editor"><form id="platformForm" novalidate><h3>${platformNames[key]} defaults</h3>${fields}<button class="primary" type="submit">Save platform settings</button></form><div><h3>Website authentication</h3><div class="cookie-status"><strong>${c.usable?'Session stored':c.present?'Cookies need attention':'No session stored'}</strong><small>${c.present?`${c.count} cookies · ${c.expired} expired`:'Public downloads may work without cookies.'}${c.updated?' · Saved '+E(new Date(c.updated*1000).toLocaleString()):''} This checks file contents, not live login validity.</small></div><div class="cookie-actions"><button class="secondary" data-cookie="${key}">${c.present?'Replace':'Import'} cookies.txt</button>${c.present?`<button class="secondary danger" data-cookie-delete="${key}">Remove session</button>`:''}</div>${manual}${key==='instagram'?'<p class="hint">Automatic session updates: after a successful download, cookies refreshed by Instagram are saved for the next job. Session cookies with no fixed expiry remain valid locally; only Instagram can renew or revoke the login.</p>':''}<p class="hint">Cookie values stay private on the laptop and are never returned to this page. They expire independently of your 35-day desk login. After replacing them, retry the affected job from Attention. See README for Firefox instructions.</p></div></div>`;
}
async function showDetail(id){
  const j=await api('job?id='+encodeURIComponent(id));$('#detailTitle').textContent=j.name;
  $('#detailBody').innerHTML=`<p class="detail-label">SOURCE URL</p><p class="detail-value">${E(j.url)}</p><p class="detail-label">DESTINATION</p><p class="detail-value">${E(shortFolder(j))}</p><p class="detail-label">ENGINE</p><p class="detail-value">${E(engineName(j))}</p><p class="detail-label">VIDEO OPTIONS</p><p class="detail-value">${E(j.quality==='best'?'Highest available':j.quality)} · ${j.kind==='video'&&!j.profile_mode&&j.quality!=='audio'?(j.container==='mp4'?'MP4 only':'Automatic format'):'Platform media format'}</p><p class="detail-label">STATUS</p><p class="detail-value">${E(labels[j.status])} · ${j.attempts} attempt(s)</p><p class="detail-value">${E(j.detail)}</p>${j.error_kind==='auth'?'<button class="secondary" data-open-platforms>Open platform settings</button>':''}<p class="detail-label">RECENT ENGINE OUTPUT</p><pre class="log">${E(j.log||j.last_error||j.detail||'No engine output yet.')}</pre><p class="hint">Gallery jobs report completed items rather than a reliable overall percentage. Video progress is for the current stream/item; merging can continue after it reaches 100%.</p><div class="dialog-footer"><button class="secondary" data-job="${j.id}" data-action="first">Move to front</button><button class="secondary danger" data-job="${j.id}" data-action="remove">Remove from queue</button>${['video','threads'].includes(j.kind)&&!j.profile_mode&&!['running','done'].includes(j.status)?`<button class="secondary" data-job="${j.id}" data-action="refresh">Refresh source &amp; resume</button>`:''}<button class="primary" data-job-folder="${E(join(j.folder,j.subdir))}">Open folder</button></div>`;
  openDialog('detailDialog');
}
async function showTorrentFiles(id){
  const torrent=await api('torrent?id='+encodeURIComponent(id));$('#detailTitle').textContent=torrent.name;
  if(!torrent.files.length){$('#detailBody').innerHTML='<p class="hint">Metadata is not available yet. Start the torrent briefly and reopen Files. Magnet links need peers to discover their file list.</p>';}
  else{$('#detailBody').innerHTML=`<p class="hint">Unchecking a file stops future downloading; it does not delete existing data. Shared torrent pieces may contain small portions of adjacent files.</p><form id="torrentFilesForm" novalidate data-id="${E(id)}"><div class="torrent-files">${torrent.files.map((f,i)=>`<label><input type="checkbox" name="file" value="${i}" ${torrent.fileStats[i].wanted?'checked':''}><span>${E(f.name)}<small class="hint"> · ${bytes(f.length)} · ${f.length?(100*f.bytesCompleted/f.length).toFixed(0):100}%</small></span></label>`).join('')}</div><div class="dialog-footer"><button type="button" class="secondary" data-torrent="${E(id)}" data-action="verify">Verify existing data</button><button class="primary" type="submit">Save file selection</button></div></form>`;}
  openDialog('detailDialog');
}
$('#authForm').addEventListener('submit',async event=>{event.preventDefault();const form=event.target;const button=$('button[type=submit]',form);button.disabled=true;$('#authError').textContent='';try{const data=Object.fromEntries(new FormData(form));await api(form.dataset.setup==='true'?'setup':'login',data);form.reset();await showAuth();}catch(error){$('#authError').textContent=error.message;}finally{button.disabled=false;}});
$('#downloadForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const form=event.target,data=Object.fromEntries(new FormData(form));data.urls=data.urls.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);data.filename=form.filename.value.trim();data.folder_selected=form.folder_selected.checked;data.browser_fallback=form.browser_fallback.checked;data.playlist=form.playlist.checked;data.subtitles=form.subtitles.checked;const result=await api('jobs',data);closeDialog($('#downloadDialog'));form.urls.value='';updateLinkCount();toast(`${result.ids.length} download(s) queued${result.skipped?`; ${result.skipped} duplicate(s) skipped`:''}.`);await refresh();});});
$('#torrentForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const form=event.target;const data={folder:form.folder.value,magnet:form.magnet.value.trim(),paused:form.paused.checked};if(!data.magnet){const file=form.torrent.files[0];if(!file)throw Error('Paste a magnet link or choose a .torrent file.');if(file.size>8*1024*1024)throw Error('Torrent file must be smaller than 8 MiB.');const array=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<array.length;i+=8192)binary+=String.fromCharCode(...array.subarray(i,i+8192));data.metainfo=btoa(binary);}await api('torrents',data);closeDialog($('#torrentDialog'));form.reset();toast('Torrent added.');await setPage('torrents');await refresh();});});
$('#settingsForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const data={};for(const name of ['telegram_bot_token','telegram_chat_id','telegram_admin_id','tracker_domains'])data[name]=event.target.elements[name].value;await api('settings',data);event.target.elements.telegram_bot_token.value='';toast('Connection settings saved.');await refresh();renderSettings();});});
$('#passwordForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{await api('password',Object.fromEntries(new FormData(event.target)));event.target.reset();toast('Password changed. Sign in again on each device.');await showAuth();});});
$('#folderCreate').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const path=join(folderPath,event.target.elements.name.value.trim());await api('folders',{path});event.target.reset();folderPath=path;await loadFolders();});});
$('#folderUp').onclick=()=>action(async()=>{folderPath=parent(folderPath);await loadFolders();});
$('#chooseFolder').onclick=()=>{if(folderTarget==='downloadForm')$('#downloadForm [name=folder_selected]').checked=true;$('#'+folderTarget+' [name=folder]').value=folderPath;closeDialog($('#folderDialog'));};
$('#filesUp').onclick=()=>action(async()=>{filePath=parent(filePath);await loadFiles();});
$('#refreshFiles').onclick=()=>action(loadFiles);
$('#newFileFolder').onclick=()=>action(async()=>{const name=await uiMessage('Create inside Zedair/'+filePath,{title:'New folder',accept:'Create folder',input:true});if(!name)return;await api('folders',{path:join(filePath,name.trim())});await loadFiles();});
$('#holdBtn').onclick=()=>action(async()=>{await api('hold',{hold:!state.settings.hold});await refresh();});
$('#unplugBtn').onclick=()=>action(async()=>{await api('hold',{hold:true});await refresh();toast('Queue held. Run the stop and unmount commands before unplugging.');});
$('#logoutBtn').onclick=()=>action(async()=>{await api('logout',{});await showAuth();});
$('#themeBtn').onclick=()=>{document.body.classList.toggle('dark');localStorage.setItem('zedair-theme',document.body.classList.contains('dark')?'dark':'light');};
if(localStorage.getItem('zedair-theme')==='dark')document.body.classList.add('dark');
$('#searchJobs').addEventListener('input',()=>{queueOffset=0;renderJobs();});
$('#clearHistory').onclick=()=>action(async()=>{if(!await uiMessage('Clear completed and cancelled jobs from history? Downloaded files will stay on the SSD.'))return;await api('history/clear',{});await refresh();});
$('#cookieFile').addEventListener('change',event=>action(async()=>{const file=event.target.files[0];if(!file)return;if(file.size>1024*1024)throw Error('Cookie file must be smaller than 1 MiB.');await api('cookies',{slot:cookieSlot,content:await file.text()});event.target.value='';await refresh();renderSettings();toast('Cookie file imported. It will be used by new download attempts.');}));
document.addEventListener('click',event=>{
  const el=event.target.closest('button');if(!el)return;
  action(async()=>{
    if(el.dataset.fileView){fileView=el.dataset.fileView;localStorage.setItem('zedair-file-view',fileView);renderFiles();}
    if(el.dataset.toolPlatform){toolPlatform=el.dataset.toolPlatform;renderTools();}
    if(el.hasAttribute('data-tool-cookies')){selectedPlatform=toolPlatform;settingsTab='platforms';await setPage('settings');}
    if(el.dataset.profileRetry){const limit=Number($('#profile-limit-'+el.dataset.profileRetry).value);await api('profiles/retry',{id:el.dataset.profileRetry,scroll_limit:limit});await refresh();}
    if(el.dataset.settingsTab)selectSettingsTab(el.dataset.settingsTab);
    if(el.dataset.platform){selectedPlatform=el.dataset.platform;renderPlatform();}
    if(el.hasAttribute('data-open-platforms')){closeDialog($('#detailDialog'));settingsTab='platforms';await setPage('settings');}
    if(el.dataset.deleteFile){if(!await uiMessage('Permanently delete Zedair/'+el.dataset.deleteFile+' from the SSD? This cannot be undone.'))return;await api('file/delete',{path:el.dataset.deleteFile,stamp:el.dataset.stamp,confirm:true});selectedFiles.delete(el.dataset.deleteFile);await loadFiles();toast('File deleted.');}
    if(el.dataset.renameFile)openOrganize('rename',[{path:el.dataset.renameFile,stamp:el.dataset.stamp}]);
    if(el.dataset.previewFile)showPreview(el.dataset.previewFile,el.dataset.previewKind);
    if(el.dataset.speedReport)showSpeedReport(el.dataset.speedReport);
    if(el.dataset.theme!==undefined)$('#themeBtn').click();
    if(el.dataset.logout!==undefined)$('#logoutBtn').click();
    if(el.dataset.close!==undefined)closeDialog(el.closest('dialog'));
    if(el.dataset.open)openDialog(el.dataset.open);
    if(el.dataset.page)await setPage(el.dataset.page);
    if(el.dataset.filter){queueOffset=0;filter=el.dataset.filter;$$('[data-filter]').forEach(b=>b.classList.toggle('selected',b===el));renderJobs();}
    if(el.dataset.browse){folderTarget=el.dataset.browse;folderPath=$('#'+folderTarget+' [name=folder]').value.trim();try{await loadFolders();}catch(_){folderPath='';await loadFolders();}openDialog('folderDialog');}
    if(el.dataset.folder!==undefined){folderPath=el.dataset.folder;await loadFolders();}
    if(el.dataset.fileFolder!==undefined){filePath=el.dataset.fileFolder;await loadFiles();}
    if(el.dataset.jobFolder!==undefined){$$('dialog[open]').forEach(closeDialog);filePath=el.dataset.jobFolder;await setPage('files');}
    if(el.dataset.detail)await showDetail(el.dataset.detail);
    if(el.dataset.job){if(el.dataset.action==='remove'&&!await uiMessage('Remove this item from the queue? Its transfer will stop. Downloaded files stay on the SSD.',{title:'Remove download',accept:'Remove',danger:true}))return;await api('job/action',{id:el.dataset.job,action:el.dataset.action});if($('#detailDialog').open)closeDialog($('#detailDialog'));await refresh();}
    if(el.dataset.torrent){if(el.dataset.action==='remove'&&!await uiMessage('Remove this torrent from the queue? Downloaded files will stay on the SSD.'))return;await api('torrent/action',{id:el.dataset.torrent,action:el.dataset.action});toast('Torrent updated.');await refresh();}
    if(el.dataset.torrentFiles)await showTorrentFiles(el.dataset.torrentFiles);
    if(el.dataset.cookie){cookieSlot=el.dataset.cookie;$('#cookieFile').click();}
    if(el.dataset.cookieDelete){if(!await uiMessage('Remove the saved '+platformNames[el.dataset.cookieDelete]+' session from this laptop?'))return;await api('cookies',{slot:el.dataset.cookieDelete,delete:true});await refresh();renderSettings();toast('Cookie file deleted.');}
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
async function showPreview(path,kind){
  clearPreview();previewPath=path;previewKind=kind;const request=++previewRequest;
  if(!['image','video','audio'].includes(kind))return;
  $('#previewTitle').textContent=path.split('/').pop();$('#previewError').hidden=true;
  const url='/api/preview?path='+encodeURIComponent(path);
  $('#previewDownload').href='/api/file?path='+encodeURIComponent(path);
  $('#previewBody').innerHTML='<div class="preview-stage"></div>';
  const element=document.createElement(kind==='image'?'img':kind);element.src=url;
  if(kind==='image')element.alt=path.split('/').pop();else{element.controls=true;element.preload='metadata';element.setAttribute('playsinline','');}
  element.addEventListener(kind==='image'?'load':'loadedmetadata',fitPreview,{once:true});
  element.addEventListener('error',()=>$('#previewError').hidden=false,{once:true});
  $('.preview-stage').appendChild(element);openDialog('previewDialog');fitPreview();
  $('#previewPrev').disabled=true;$('#previewNext').disabled=true;
  try{const adjacent=await adjacentPreview(path,0);if(request!==previewRequest)return;$('#previewPrev').disabled=!adjacent.previous;$('#previewNext').disabled=!adjacent.next;}catch(error){toast(error.message);}
}
function clearPreview(){const media=$('video,audio',$('#previewBody'));if(media){media.pause();media.removeAttribute('src');media.load();}$('#previewBody').replaceChildren();}
$('#previewDialog').addEventListener('close',()=>{previewRequest++;clearPreview();});
$('#startSpeed').onclick=()=>action(async()=>{if(!state.speedtest.consent&&!$('#acceptSpeedTerms').checked)throw Error('Review and accept the Ookla agreement before starting.');await api('speedtest',{action:'start',accept_terms:$('#acceptSpeedTerms').checked});await refresh();});
$('#cancelSpeed').onclick=()=>action(async()=>{await api('speedtest',{action:'cancel'});await refresh();});

function renderTools(){
  const facebook=toolPlatform==='facebook';
  $$('[data-tool-platform]').forEach(b=>b.classList.toggle('selected',b.dataset.toolPlatform===toolPlatform));
  $('#profileToolTitle').textContent=facebook?'Facebook profile · experimental':'Instagram profile';
  $('#profileToolHint').textContent=facebook?'Downloads accessible media. Facebook may limit results.':'Resumable collection with slow requests and duplicate checks.';
  $('#profileTarget').placeholder=facebook?'Username, numeric ID or Facebook profile URL':'@username or Instagram profile URL';
  const chosen=$('#profileForm [name=folder]').value.trim();$('#profileDestination').textContent='Saves to Zedair/'+(chosen||((facebook?'Facebook':'Instagram')+'/<username>'));
  $('#imagesOnlyField').hidden=facebook;$('#avatarMode').hidden=facebook;if(facebook&&['avatar','images'].includes($('#profileForm [name=mode]:checked').value))$('#profileForm [value=everything]').checked=true;
  $('#facebookScrollField').hidden=!facebook;$('#facebookInstall').hidden=!facebook||state.tools?.facebook_browser;
  $('#startProfile').disabled=facebook&&!state.tools?.facebook_browser;
  const session=state.cookie_status[toolPlatform];$('#profileSessionNotice').hidden=session?.usable;
  $('#profileSessionNotice').textContent='Add your '+(facebook?'Facebook':'Instagram')+' session in Settings before queuing a profile.';
  $('#localAddressStatus').textContent=state.tools?.local_enabled?'Local-address setup has run on this laptop. Open http://zed.local on your other devices.':'Not configured yet. Your current address continues to work.';
}
function renderProfileJobs(){
  const jobs=state.jobs.filter(j=>j.profile_mode);
  $('#profileJobs').innerHTML=jobs.length?jobs.map(j=>{
    const active=['queued','running','retry','blocked'].includes(j.status),facebook=j.cookie_profile==='facebook';
    return `<article class="profile-job"><div><strong>${E(j.name)}</strong><span class="pill ${E(j.status)}">${E(labels[j.status]||j.status)}</span><p class="hint">${E(j.profile_mode)}${j.images_only?' · images only':''} · ${j.items||0} files saved</p><p class="job-meta">${E(shortFolder(j))}</p><p class="profile-job-detail">${E(j.detail)}${facebook?' · Facebook completeness is unverified.':''}</p></div><div class="profile-job-actions">${active?button('Ⅱ','Pause profile',`data-job="${j.id}" data-action="pause"`):!['done','cancelled'].includes(j.status)?facebook?`<label>Scroll limit<input id="profile-limit-${j.id}" type="number" min="10" max="10000" value="${j.scroll_limit}"></label><button class="secondary compact" data-profile-retry="${j.id}">Resume / retry</button>`:`<button class="secondary compact" data-job="${j.id}" data-action="retry">Resume / retry</button>`:''}${button('▤','Open profile folder',`data-job-folder="${E(join(j.folder,j.subdir))}"`)}${button('⋯','Profile details and log',`data-detail="${j.id}"`)}${button('×','Remove profile job',`data-job="${j.id}" data-action="remove"`)}</div></article>`;
  }).join(''):empty('▦','No profile jobs yet.','Choose a profile and the media you want to collect.');
}
$('#profileForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{
  const form=event.target,result=await api('profiles',{platform:toolPlatform,profile:form.profile.value,mode:form.mode.value,folder:form.folder.value,scroll_limit:Number(form.scroll_limit.value)});
  toast('Profile queued → '+result.destination);await refresh();renderProfileJobs();
});});

let systemBusy=false;
async function renderSystem(){
  if(systemBusy)return;systemBusy=true;
  try{
    const data=await api('system');
    const gauge=(title,pct,detail,critical=90)=>{const known=Number.isFinite(pct),value=known?Math.max(0,Math.min(100,pct)):0;return `<article class="usage-card"><div class="usage-ring" role="img" aria-label="${E(title)}: ${known?value.toFixed(1)+' percent':'unavailable'}" data-level="${known?(value>=critical?'critical':value>=Math.min(70,critical-10)?'warning':'normal'):'unknown'}" style="--usage:${value}%"><strong>${known?Math.round(value)+'<small>%</small>':'—'}</strong></div><h3>${E(title)}</h3><p>${E(detail)}</p></article>`;};
    const size=d=>d?`${bytes(d.used)} / ${bytes(d.total)}`:'Unavailable';
    $('#systemGauges').innerHTML=gauge('CPU',data.cpu_percent,data.cpu_percent===null?'Waiting for next sample':`${data.cores} logical cores · whole laptop`,state.settings.alert_cpu_percent)+gauge('RAM',data.memory?.percent,size(data.memory),state.settings.alert_ram_percent)+gauge('Swap',data.swap?.percent,data.swap?.total?size(data.swap):'No swap configured')+gauge('System disk',data.system_disk?.percent,size(data.system_disk),state.settings.alert_disk_percent)+gauge('External SSD',data.ssd?.percent,data.ssd?size(data.ssd):'SSD unavailable');
    $('#systemDetails').innerHTML=`<div><span>Uptime</span><strong>${data.uptime_seconds===null?'Unavailable':Math.floor(data.uptime_seconds/86400)+'d '+Math.floor(data.uptime_seconds%86400/3600)+'h '+Math.floor(data.uptime_seconds%3600/60)+'m'}</strong></div><div><span>Load average · 1 / 5 / 15 min</span><strong>${data.load?.map(x=>x.toFixed(2)).join(' / ')||'Unavailable'}</strong></div><div><span>CPU temperature</span><strong>${data.temperature_c===null?'Sensor unavailable':data.temperature_c.toFixed(1)+' °C'}</strong></div><div><span>RAM available</span><strong>${data.memory?bytes(data.memory.available):'Unavailable'}</strong></div>`;
    $('#systemUpdated').textContent='Updated '+new Date(data.sampled_at*1000).toLocaleTimeString();$('#systemError').hidden=true;
  }catch(error){$('#systemError').textContent='Unable to read laptop usage: '+error.message;$('#systemError').hidden=false;$('#systemUpdated').textContent='Readings may be stale';}
  finally{systemBusy=false;}
}

async function adjacentPreview(path,direction){return api('folders?path='+encodeURIComponent(parent(path))+'&sort='+fileSort.split('-')[0]+'&order='+fileSort.split('-')[1]+'&adjacent='+encodeURIComponent(path.split('/').pop())+'&direction='+direction);}
async function stepPreview(direction){if(previewBusy)return;previewBusy=true;try{const path=previewPath;const result=await adjacentPreview(path,direction);if(path!==previewPath||!$('#previewDialog').open)return;if(result.entry)await showPreview(join(parent(path),result.entry.name),result.entry.preview);}catch(error){toast(error.message);}finally{previewBusy=false;}}
$('#previewPrev').onclick=()=>stepPreview(-1);$('#previewNext').onclick=()=>stepPreview(1);
document.addEventListener('keydown',event=>{if(!$('#previewDialog').open||!['ArrowLeft','ArrowRight'].includes(event.key)||event.target.matches('input,textarea,select'))return;event.preventDefault();stepPreview(event.key==='ArrowLeft'?-1:1);});

function renderSelection(){
  const count=selectedFiles.size;$('#selectionCount').textContent=count+' selected';
  for(const id of ['moveSelected','copySelected','clearSelection'])$('#'+id).disabled=!count;
  const visible=fileEntries.filter(e=>!e.directory);const n=visible.filter(e=>selectedFiles.has(join(filePath,e.name))).length;
  $('#selectPageFiles').checked=!!visible.length&&n===visible.length;$('#selectPageFiles').indeterminate=n>0&&n<visible.length;$('#selectPageFiles').disabled=!visible.length;
}
document.addEventListener('change',event=>{const el=event.target;if(!el.matches('[data-select-file]'))return;
  if(el.checked&&selectedFiles.size>=100){el.checked=false;toast('Select up to 100 files at a time.');return;}
  if(el.checked)selectedFiles.set(el.dataset.selectFile,el.dataset.stamp);else selectedFiles.delete(el.dataset.selectFile);
  el.closest('article,.file-entry')?.classList.toggle('is-selected',el.checked);renderSelection();
});
$('#selectPageFiles').onchange=event=>{for(const e of fileEntries.filter(e=>!e.directory)){const path=join(filePath,e.name);if(event.target.checked&&selectedFiles.size<100)selectedFiles.set(path,e.stamp);else if(!event.target.checked)selectedFiles.delete(path);}renderFiles();};
$('#clearSelection').onclick=()=>{selectedFiles.clear();renderFiles();};
function openOrganize(action,items){
  organizeItems=items;const form=$('#organizeForm');form.elements.action.value=action;form.elements.name.value=items[0].path.split('/').pop();form.folder.value=parent(items[0].path);
  $('#organizeTitle').textContent=action==='rename'?'Rename file':action==='move'?'Move files':'Copy files';$('#organizeSource').textContent=items.length===1?items[0].path:items.length+' selected files';
  $('#renameField').hidden=action!=='rename';$('#moveField').hidden=action==='rename';$('#organizeResult').hidden=true;$('#organizeSubmit').textContent=action==='rename'?'Rename':action==='move'?'Move':'Copy';openDialog('organizeDialog');
}
for(const action of ['move','copy'])$('#'+action+'Selected').onclick=()=>openOrganize(action,[...selectedFiles].map(([path,stamp])=>({path,stamp})));
$('#organizeForm').onsubmit=event=>{event.preventDefault();action(async()=>{
  const form=event.target,submit=$('#organizeSubmit'),label=submit.textContent;submit.disabled=true;submit.textContent='Working…';
  try{
    const data=Object.fromEntries(new FormData(form));let result;
    if(data.action==='rename'){result=await api('file/manage',{...data,...organizeItems[0]});selectedFiles.delete(organizeItems[0].path);}
    else{result=await api('files/manage',{...data,files:organizeItems});for(const item of result.results)selectedFiles.delete(item.source);}
    await loadFiles();
    if(result.error){organizeItems=organizeItems.filter(item=>!result.results.some(r=>r.source===item.path));$('#organizeResult').hidden=false;$('#organizeResult').textContent=result.results.length+' completed; '+result.remaining+' remaining. '+result.error;}
    else{closeDialog($('#organizeDialog'));toast(data.action==='rename'?'File renamed.':result.results.length+' files '+(data.action==='move'?'moved.':'copied.'));}
  }finally{submit.disabled=false;submit.textContent=label;}
});};
$('#deleteFolder').onclick=()=>action(async()=>{
  if(!filePath)return;const path=filePath,info=await api('folders/inspect',{path});
  if(!await uiMessage('Permanently delete Zedair/'+path+' and all subfolders? This includes '+info.files+' files ('+bytes(info.bytes)+'). This cannot be undone.',{title:'Delete folder',accept:'Delete folder',danger:true}))return;
  await api('folders/delete',{path,stamp:info.stamp,confirm:true});filePath=parent(path);selectedFiles.clear();await loadFiles();toast('Folder deleted.');
});
$('#profileForm [name=folder]').addEventListener('input',renderTools);
function fitPreview(){
  const media=$('img,video,audio',$('#previewBody'));if(!media)return;
  const width=media.naturalWidth||media.videoWidth,height=media.naturalHeight||media.videoHeight;
  const available=Math.max(160,innerHeight-240),ratio=width&&height?width/height:16/9;
  $('#previewDialog').style.width=Math.max(280,Math.min(innerWidth-32,1200,available*ratio+40))+'px';
}
window.addEventListener('resize',fitPreview);
// Use the application's status UI instead of native validation popups.
for(const form of $$('form'))form.noValidate=true;
document.addEventListener('submit',event=>{const form=event.target;form.noValidate=true;if(!form.checkValidity()){event.preventDefault();event.stopImmediatePropagation();const field=$(':invalid',form);toast(field?.validationMessage||'Check the form fields.');field?.focus();}},true);

// Maintenance uses the same authenticated, CSRF-protected API as other settings.
$('#retryAll').onclick=()=>action(async()=>{await api('jobs/retry-all',{});toast('Failed downloads queued for retry.');await refresh();});
$('#queuePrev').onclick=()=>{queueOffset=Math.max(0,queueOffset-queuePageSize);renderJobs();};
$('#queueNext').onclick=()=>{queueOffset+=queuePageSize;renderJobs();};
$('#queuePageSize').onchange=e=>{queuePageSize=Number(e.target.value);queueOffset=0;localStorage.setItem('zedair-queue-page-size',queuePageSize);renderJobs();};
document.addEventListener('load',e=>{if(e.target.matches?.('.queue-favicon'))e.target.parentElement.classList.add('favicon-loaded');},true);
document.addEventListener('error',e=>{if(e.target.matches?.('.queue-favicon')){e.target.hidden=true;e.target.parentElement.classList.remove('favicon-loaded');}},true);
$('#storiesForm').onsubmit=e=>{e.preventDefault();action(async()=>{const r=await api('instagram/stories',Object.fromEntries(new FormData(e.target)));toast('Collection queued → '+r.destination);await refresh();renderProfileJobs();});};
$('#telegramTest').onclick=()=>action(async()=>{await api('telegram/test',{});toast('Test queued. Check the result in Download engines after a few seconds.');});
$('#telegramClear').onclick=()=>action(async()=>{await api('settings',{clear_telegram_token:true});await refresh();renderSettings();});
$('#clearCache').onclick=()=>action(async()=>{const abandon=$('#removeAbandoned').checked;if(abandon&&!await uiMessage('Permanently remove cancelled and orphan partial downloads? Finished files and active jobs are kept.',{danger:true}))return;const r=await api('cache/clear',{remove_abandoned:abandon,confirm:abandon});$('#cacheResult').textContent=`Removed ${r.removed} cached items/folders · ${bytes(r.bytes)} freed`;});
async function loadUsage(){
  const period=$('#usagePeriod').value,r=await api('usage?period='+period);if($('#usagePeriod').value!==period)return;
  const peak=Math.max(1,...r.series.map(x=>x.bytes)),barWidth=600/r.series.length;
  const short=d=>period==='day'?d.slice(11)+':00':period==='year'?new Date(d+'-02').toLocaleString(undefined,{month:'short'}):d.slice(5);
  const bars=r.series.map((p,i)=>{const height=140*p.bytes/peak,x=i*barWidth+3;return `<g><title>${E(p.label)} UTC: ${bytes(p.bytes)}</title><rect class="usage-bar" x="${x}" y="${160-height}" width="${Math.max(2,barWidth-6)}" height="${Math.max(2,height)}" rx="3"/>${i%Math.ceil(r.series.length/8)===0?`<text x="${x}" y="184">${E(short(p.label))}</text>`:''}</g>`;}).join('');
  $('#usageReport').innerHTML=`<div class="usage-summary"><div><span>Recorded payload</span><strong>${bytes(r.total)}</strong></div><div><span>Source websites</span><strong>${r.domains.length}</strong></div><div><span>Calendar period</span><strong class="usage-period">${E(r.since)} <small>UTC</small></strong></div></div><div class="usage-chart"><div class="chart-caption"><span>Transfer activity</span><span>Peak ${bytes(peak===1?0:peak)}</span></div><svg viewBox="0 0 600 200" role="img" aria-label="Recorded download payload by ${period==='day'?'hour':period==='year'?'month':'day'}"><line x1="0" y1="162" x2="600" y2="162"/>${bars}</svg></div><h3>By website</h3><div class="domain-list">${r.domains.map((x,i)=>`<div class="domain-row"><span class="domain-rank">${i+1}</span><div><div class="domain-label"><strong>${E(x.domain)}</strong><span>${bytes(x.bytes)}</span></div><div class="domain-track"><i style="width:${100*x.bytes/Math.max(1,r.total)}%"></i></div></div></div>`).join('')||'<p class="hint empty-usage">No recorded downloads in this period yet. Completed transfers with byte progress will appear here.</p>'}</div><p class="hint">${E(r.note)}</p>`;
}
$('#usagePeriod').onchange=()=>action(loadUsage);
let packageTimer;
async function loadPackages(check=false){
  clearTimeout(packageTimer);
  const r=await api('engines'+(check?'?check=1':''));
  const order=['yt-dlp','gallery-dl','playwright'],managed=r.packages.filter(p=>order.includes(p.name.toLowerCase())).sort((a,b)=>order.indexOf(a.name)-order.indexOf(b.name));
  const descriptions={'yt-dlp':'Site-specific video & audio extraction','gallery-dl':'Images, galleries & Instagram collections','playwright':'Rendered playback detection with Chromium'};
  $('#packageInventory').innerHTML=`<p class="hint">Python ${E(r.python)} · ${r.checking?'Checking stable releases…':r.checked_at?'Checked '+new Date(r.checked_at*1000).toLocaleString():'Release check unavailable'}</p><div class="engine-cards">`+managed.map(p=>`<article class="engine-card"><span class="engine-mark">${p.name==='playwright'?'◈':p.name==='gallery-dl'?'▦':'↙'}</span><div><h3>${E(p.name)}</h3><p>${E(descriptions[p.name])}</p><div class="engine-version">Installed <strong>${E(p.version)}</strong>${p.latest_version?` · Latest <strong>${E(p.latest_version)}</strong>`:''}</div><span class="engine-status ${p.has_update?'available':''}">${p.has_update?'New release available':p.version==='Not installed'?'Not installed':p.latest_version?'Up to date':p.check_error?'Could not check · try again':'Waiting for release check'}</span></div>${p.has_update&&r.update_available?`<button type="button" class="primary compact" data-update-package="${E(p.name)}">Update</button>`:''}</article>`).join('')+`</div>${managed.some(p=>p.has_update)&&!r.update_available?'<p class="notice">Run upgrade.sh on the laptop to enable the managed updater.</p>':''}<details class="package-details"><summary>All Python packages (${r.packages.length})</summary><table><thead><tr><th>Package</th><th>Installed version</th></tr></thead><tbody>${r.packages.map(p=>`<tr><td>${E(p.name)}</td><td>${E(p.version)}</td></tr>`).join('')}</tbody></table></details><h3 class="tools-heading">Ubuntu-managed tools</h3><div class="system-tools">${r.system_tools.map(p=>`<div><strong>${E(p.name)}</strong><span>${E(p.version)}</span></div>`).join('')}</div>`+Object.entries(r.updates).map(([k,v])=>`<p class="hint">${E(k)}: ${E(v.status)} — ${E(v.detail)}</p>`).join('');
  $('#telegramResult').textContent=(r.telegram.ok?'Last Telegram delivery succeeded. ':'')+(r.telegram.errors||[]).concat(r.telegram.warnings||[]).join('; ');
  if(r.checking&&page==='settings'&&settingsTab==='engines')packageTimer=setTimeout(()=>action(()=>loadPackages()),2000);
}
$('#loadPackages').onclick=()=>action(()=>loadPackages(true));
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.updatePackage)action(async()=>{if(!await uiMessage('Update '+b.dataset.updatePackage+'? Downloads pause while the service updates and restarts.'))return;const r=await api('engines/update',{package:b.dataset.updatePackage});toast(r.message);});});

// Keep each icon element alive across polling; update only changed row content.
function reconcileJobs(html){
  const list=$('#jobs'),template=document.createElement('template');template.innerHTML=html;
  const existing=new Map($$('[data-progress-job]',list).map(n=>[n.dataset.progressJob,n]));
  const wanted=new Set();let cursor=list.firstElementChild;
  for(const fresh of [...template.content.children]){
    const key=fresh.dataset.progressJob;wanted.add(key);let row=existing.get(key);
    if(row){for(let i=1;i<fresh.children.length;i++){if(row.children[i].innerHTML!==fresh.children[i].innerHTML)row.children[i].innerHTML=fresh.children[i].innerHTML;}}
    else row=fresh;
    if(row!==cursor)list.insertBefore(row,cursor);cursor=row.nextElementSibling;
  }
  for(const node of [...list.children])if(!wanted.has(node.dataset.progressJob))node.remove();
}
function updateLinkCount(){const n=$('#downloadForm [name=urls]').value.split(/\r?\n/).filter(s=>s.trim()).length;const output=$('#linkCount');output.textContent=n+' '+(n===1?'link':'links');output.classList.toggle('over-limit',n>100);}
$('#downloadForm [name=urls]').addEventListener('input',updateLinkCount);
for(const dialog of $$('dialog')){
  let outside=false;
  const isOutside=e=>{const r=dialog.getBoundingClientRect();return e.target===dialog&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);};
  dialog.addEventListener('pointerdown',e=>{outside=isOutside(e);});
  dialog.addEventListener('pointerup',e=>{if(outside&&isOutside(e))closeDialog(dialog);outside=false;});
  dialog.addEventListener('cancel',()=>{if(dialog.id==='previewDialog')clearPreview();});
}
const settingGroups={
  transferControls:[
    ['simultaneous_downloads','Concurrent downloads','jobs','Browser downloads always run one at a time. Default: 1.'],
    ['reserve_gib','Keep SSD space free','GiB','Pause new downloads before the SSD fills up.'],
    ['media_kib','Website speed limit','speed','Per active website download; 0 means unlimited.'],
    ['torrent_down_kib','Torrent download limit','speed','Shared by all torrents.'],
    ['torrent_up_kib','Torrent upload limit','speed','At least 1 KiB/s.'],
    ['seed_ratio','Stop seeding at ratio','×','0 stops seeding after the download.'],
    ['retry_count','Automatic retries','retries','Waits 1, 2, 4… minutes between attempts.'],
    ['playlist_limit','Playlist item limit','items','Maximum items per playlist.']],
  browserControls:[
    ['browser_timeout','Discovery budget','s'],['browser_settle','Wait for quality variants','s'],
    ['browser_min_memory_mib','Minimum memory headroom','MiB'],['browser_max_frames','Frames inspected','frames'],
    ['browser_candidates','Media candidate limit','candidates'],['refresh_attempts','Automatic link refreshes','attempts'],['http_timeout','HTTP timeout','s']],
  alertControls:[
    ['alert_cpu_percent','CPU alert threshold','%'],['alert_ram_percent','RAM alert threshold','%'],
    ['alert_disk_percent','System disk threshold','%'],['alert_temperature_c','Temperature threshold','°C'],
    ['alert_sustain_seconds','Usage must stay high for','s'],['alert_cooldown_minutes','Repeat alert cooldown','min']]
};
const savingSettings=new Map();let settingsSaveChain=Promise.resolve();
function buildSettingControls(){
  if($('#transferControls').children.length)return;
  for(const [id,entries] of Object.entries(settingGroups))$('#'+id).innerHTML=entries.map(([key,label,unit,note])=>{
    const spec=state.settings_schema[key];return `<label class="range-control"><span class="range-heading"><span>${E(label)}</span><output id="value-${key}" for="setting-${key}"></output></span><input id="setting-${key}" name="${key}" type="range" min="${spec.min}" max="${spec.max}" step="${spec.step}" data-unit="${E(unit)}" data-autosave><span class="range-bounds"><span>${spec.min}</span><span>${spec.max}</span></span>${note?`<small>${E(note)}</small>`:''}</label>`;
  }).join('');
}
function rangeValue(field){return field._choices?field._choices[Number(field.value)]:Number(field.value);}
function rangeLabel(field,value){return field.dataset.unit==='speed'?(value===0?'Unlimited':bytes(value*1024)+'/s'):value+' '+field.dataset.unit;}
function paintRange(field){const value=rangeValue(field);$('#value-'+field.name).textContent=rangeLabel(field,value);field.setAttribute('aria-valuetext',rangeLabel(field,value));field.style.setProperty('--range-fill',100*(field.value-field.min)/(field.max-field.min||1)+'%');}
function setRange(field,value){
  if(field.dataset.unit==='speed'){
    const min=state.settings_schema[field.name].min;
    field._choices=[...new Set([min,128,256,512,1024,2048,4096,8192,16384,32768,65536,100000,Number(value)])].filter(x=>x>=min).sort((a,b)=>a-b);
    field.min=0;field.max=field._choices.length-1;field.step=1;field.value=field._choices.indexOf(Number(value));
    const bounds=$$('.range-bounds span',field.closest('label'));bounds[0].textContent=min?'1 KiB/s':'Unlimited';bounds[1].textContent='98 MiB/s';
  }else field.value=value;
  paintRange(field);
}
$('#settingsForm').addEventListener('input',e=>{if(e.target.type==='range')paintRange(e.target);});
$('#settingsForm').addEventListener('change',e=>{
  const field=e.target;if(!field.matches('[data-autosave]'))return;
  const name=field.name,value=field.type==='checkbox'?field.checked:rangeValue(field),ticket={};
  savingSettings.set(name,ticket);$('#settingsSaveStatus').textContent='Saving…';$('#settingsSaveStatus').classList.remove('save-error');
  settingsSaveChain=settingsSaveChain.then(async()=>{
    const previous=state.settings[name];
    try{await api('settings',{[name]:value});state.settings[name]=value;}
    catch(error){if(savingSettings.get(name)===ticket){if(field.type==='checkbox')field.checked=previous;else setRange(field,previous);}$('#settingsSaveStatus').classList.add('save-error');toast('Could not save '+name.replaceAll('_',' ')+': '+error.message);}
    finally{if(savingSettings.get(name)===ticket)savingSettings.delete(name);if(!savingSettings.size)$('#settingsSaveStatus').textContent=$('#settingsSaveStatus').classList.contains('save-error')?'Save failed · try again':'Saved automatically';}
  });
});
