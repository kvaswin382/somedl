'use strict';
// These controls reuse the desk's existing dialogs, blue palette and API guard.
let remotePicker={name:'',path:'',choose:null},remoteConfigName='',taskPoll,duplicateScan='',activityBefore=null;
function updateRemoteDownloadControls(){
  const form=$('#remoteDownloadForm'),ready=!!form.remote.value;
  $('#remoteDownloadBrowse').disabled=!ready;$('button[type=submit]',form).disabled=!ready;
  $('#remoteDownloadEmpty').hidden=ready;
}
function updateRemoteLinkCount(){const n=cleanLinks($('#remoteDownloadForm').urls.value).length;$('#remoteLinkCount').textContent=n+' '+(n===1?'link':'links');$('#remoteLinkCount').classList.toggle('over-limit',n>100);}
$('#remoteDownloadForm').urls.oninput=updateRemoteLinkCount;
$('#remoteDownloadForm').remote.onchange=()=>{$('#remoteDownloadForm').remote_path.value='';updateRemoteDownloadControls();};
$('#remoteDownloadForm').onsubmit=e=>{e.preventDefault();action(async()=>{
  const form=e.target,data=Object.fromEntries(new FormData(form));
  if(!data.remote)throw Error('Add and select a remote first.');
  data.urls=cleanLinks(data.urls);data.filename=data.filename.trim();data.playlist=false;
  const button=$('button[type=submit]',form);button.disabled=true;
  try{const result=await api('jobs',data);form.urls.value='';updateRemoteLinkCount();$('#remoteDownloadResult').textContent=`${result.ids.length} cloud download(s) queued${result.skipped?`; ${result.skipped} duplicate(s) skipped`:''}. View progress in Downloads.`;toast('Cloud downloads queued.');await refresh();}
  finally{updateRemoteDownloadControls();}
});};
async function browseRemote(name,path,choose){if(!name)throw Error('Select a remote first.');remotePicker={name,path,choose};await showRemoteFolder();openDialog('remoteDialog');}
async function showRemoteFolder(){
  const r=await api('remotes/browse?remote='+encodeURIComponent(remotePicker.name)+'&path='+encodeURIComponent(remotePicker.path));
  $('#remoteLocation').textContent=r.remote+':/'+r.path;$('#remoteUp').disabled=!r.path;
  $('#remoteFolders').innerHTML=r.folders.map(n=>`<button type="button" class="file-row" data-remote-folder="${E(n)}"><span>▰</span><strong>${E(n)}</strong><small>Open →</small></button>`).join('')||'<p class="hint">No subfolders. You can use this folder.</p>';
}
$('#remoteUp').onclick=()=>action(async()=>{remotePicker.path=parent(remotePicker.path);await showRemoteFolder();});
$('#remoteMkdir').onclick=()=>action(async()=>{const name=await uiMessage('Create a folder here.',{title:'New remote folder',input:true,accept:'Create'});if(!name)return;if(/[/:\\]/.test(name)||name==='.'||name==='..')throw Error('Use one folder name.');await api('remotes/mkdir',{remote:remotePicker.name,path:join(remotePicker.path,name)});await showRemoteFolder();});
$('#remoteChoose').onclick=()=>{remotePicker.choose(remotePicker.path);closeDialog($('#remoteDialog'));};
$('#remoteDownloadBrowse').onclick=()=>action(()=>browseRemote($('#remoteDownloadForm').remote.value,$('#remoteDownloadForm').remote_path.value,p=>$('#remoteDownloadForm').remote_path.value=p));
$('#backupBrowse').onclick=()=>action(()=>browseRemote($('#backupForm').remote.value,$('#backupForm').path.value,p=>$('#backupForm').path.value=p));
$('#refreshRemotes').onclick=()=>action(loadCloud);
let cloudLoad=0;
async function loadCloud(){
  const request=++cloudLoad,r=await api('remotes');
  if(request!==cloudLoad)return;
  const form=$('#backupForm'),current=form.remote.value;
  const download=$('#remoteDownloadForm'),selected=download.remote.value,available=r.remotes.filter(n=>!(r.pending||[]).includes(n));
  download.remote.innerHTML=available.length?available.map(n=>`<option value="${E(n)}">${E(n)}:</option>`).join(''):'<option value="">Add a remote first</option>';
  if(available.includes(selected))download.remote.value=selected;
  if(download.remote.value!==selected)download.remote_path.value='';
  updateRemoteDownloadControls();
  form.remote.innerHTML=r.remotes.filter(n=>!(r.pending||[]).includes(n)).map(n=>`<option value="${E(n)}">${E(n)}</option>`).join('');if(r.remotes.includes(current))form.remote.value=current;
  $('#remoteList').innerHTML=r.remotes.map(n=>`<div class="remote-entry"><button class="secondary compact" ${(r.pending||[]).includes(n)?'data-resume-remote':'data-browse-remote'}="${E(n)}">${(r.pending||[]).includes(n)?'Continue setup:':'▰'} ${E(n)}:</button><button class="text-button danger" data-remove-remote="${E(n)}">Remove</button></div>`).join('')||'<p class="hint">No remotes configured yet.</p>';
  await loadSchedules();
  if(!form.dataset.loaded){const saved=(await api('schedules')).backup||{};if(saved.remote&&r.remotes.includes(saved.remote))form.remote.value=saved.remote;form.path.value=saved.path||'';form.dataset.loaded='1';}
  await pollTasks();
}
$('#backupForm').remote.onchange=()=>{$('#backupForm').path.value='';};
$('#newRemote').onclick=()=>action(async()=>{
  const r=await api('remotes/providers');remoteConfigName='';
  $('#remoteConfigFields').innerHTML='<label>Remote name<input name="name" required maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_.-]*"></label><label>Provider<select name="provider">'+r.providers.sort((a,b)=>a.description.localeCompare(b.description)).map(p=>`<option value="${E(p.name)}" ${p.name==='drive'?'selected':''}>${E(p.description)} (${E(p.name)})</option>`).join('')+'</select></label>';
  $('#remoteConfigError').textContent='';openDialog('remoteConfigDialog');
});
function questionField(o){
  const help=E(o.Help||'').replace(/https?:\/\/[^\s<&]+/g,url=>`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  const examples=o.Examples||[];let field;
  if(o.Exclusive||o.Type==='bool')field=`<select name="answer">${(examples.length?examples:[{Value:'true',Help:'Yes'},{Value:'false',Help:'No'}]).map(x=>`<option value="${E(x.Value)}" ${String(o.Default)===x.Value?'selected':''}>${E(x.Help||x.Value)}</option>`).join('')}</select>`;
  else if(o.IsPassword)field='<input name="answer" type="password" autocomplete="new-password">';
  else field=`<textarea name="answer" rows="3">${E(o.Default===null?'':o.Default??'')}</textarea>`;
  return `<label>${E(o.Name)}${field}</label><p class="config-help">${help}</p>${!o.Exclusive&&examples.length?'<details><summary>Suggested values</summary>'+examples.map(x=>`<p><code>${E(x.Value)}</code> — ${E(x.Help)}</p>`).join('')+'</details>':''}`;
}
$('#remoteConfigForm').onsubmit=e=>{e.preventDefault();action(async()=>{
  const button=$('button[type=submit]',e.target);button.disabled=true;
  try{const data=Object.fromEntries(new FormData(e.target));const r=await api('remotes/configure',remoteConfigName?{action:'answer',name:remoteConfigName,answer:data.answer??''}:{action:'start',...data});remoteConfigName=r.name;
    if(r.done){closeDialog($('#remoteConfigDialog'));toast('Remote configured.');await loadCloud();return;}
    $('#remoteConfigFields').innerHTML=questionField(r.option);$('#remoteConfigError').textContent=r.error||'';
  }finally{button.disabled=false;}
});};
function scheduleEditor(id){
  $('#'+id).innerHTML=`<label class="check"><input name="enabled" type="checkbox">Enable automatic ${id==='backupSchedule'?'backup':'reboot'}</label><div class="form-grid"><label>Repeat<select name="repeat"><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option><option value="custom">Custom cron</option></select></label><label>Time · Asia/Kolkata<input name="time" type="time" value="03:00"></label><label data-calendar="weekday" hidden>Weekday<select name="weekday">${['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map((x,i)=>`<option value="${i}">${x}</option>`).join('')}</select></label><label data-calendar="day" hidden>Day of month<input name="day" type="number" min="1" max="31" value="1"></label><label data-calendar="month" hidden>Month<select name="month">${Array.from({length:12},(_,i)=>`<option value="${i+1}">${new Date(2025,i,1).toLocaleString(undefined,{month:'long'})}</option>`).join('')}</select></label></div><label data-calendar="cron" hidden>Cron · minute hour day month weekday<input name="cron" maxlength="100" value="0 3 * * *"><small>Numbers, *, ranges, steps and commas. Weekday 0 is Sunday.</small></label>`;
  const repeat=$('[name=repeat]',$('#'+id));repeat.onchange=()=>{const box=$('#'+id),v=repeat.value;for(const key of ['weekday','day','month','cron'])$('[data-calendar='+key+']',box).hidden=!(key==='weekday'?v==='weekly':key==='day'?['monthly','yearly'].includes(v):key==='month'?v==='yearly':v==='custom');$('[name=time]',box).closest('label').hidden=v==='custom';};
}
scheduleEditor('backupSchedule');scheduleEditor('rebootSchedule');
function cronValue(form){const [h,m]=form.time.value.split(':').map(Number),v=form.repeat.value;if(v==='custom')return form.cron.value.trim();if(!Number.isFinite(h)||!Number.isFinite(m))throw Error('Choose a valid time.');return `${m} ${h} ${['monthly','yearly'].includes(v)?form.day.value:'*'} ${v==='yearly'?form.month.value:'*'} ${v==='weekly'?form.weekday.value:'*'}`;}
function backupValues(){const f=$('#backupForm');return {remote:f.remote.value,path:f.path.value,scope:f.scope.value,days:Number(f.days.value)};}
let schedulesLoaded=false;
async function loadSchedules(){
  if(!schedulesLoaded){const r=await api('schedules');for(const kind of ['backup','reboot']){const f=$('#'+kind+'Form'),s=r[kind]||{};f.enabled.checked=!!s.enabled;if(s.cron){f.repeat.value='custom';f.cron.value=s.cron;f.repeat.onchange();}if(kind==='backup'){if(s.remote&&[...f.remote.options].some(o=>o.value===s.remote))f.remote.value=s.remote;f.path.value=s.path||'';f.scope.value=s.scope||'system';f.days.value=s.days||30;}}schedulesLoaded=true;$('#retentionLabel').textContent=$('#backupForm').days.value+' days';}
  const r=await api('system/control');$('#systemControlResult').textContent=r.detail||'';
}
$('#backupForm').days.oninput=e=>$('#retentionLabel').textContent=e.target.value+' days';
for(const kind of ['backup','reboot'])$('#'+kind+'Form').onsubmit=e=>{e.preventDefault();action(async()=>{await api('schedules',{kind,enabled:e.target.enabled.checked,cron:cronValue(e.target),...(kind==='backup'?backupValues():{})});toast('Schedule saved in Asia/Kolkata time.');});};
$('#backupNow').onclick=()=>action(async()=>{await api('backups/start',backupValues());toast('Backup queued.');await pollTasks();});
async function pollTasks(){
  clearTimeout(taskPoll);if(!state||document.hidden)return;
  const r=await api('tasks');
  function rows(kinds){return r.tasks.filter(t=>kinds.includes(t.kind)).reverse().map(t=>`<div class="task-row"><div><strong>${E(t.kind)} · ${E(t.status)}</strong><p class="hint">${E(t.detail)}${t.bytes?' · '+bytes(t.bytes)+(t.kind==='duplicate-delete'?' checked':''):''}</p>${t.kind==='duplicate-delete'?`<progress max="100" value="${Math.max(0,Math.min(100,Number(t.progress)||0))}" aria-label="Duplicate deletion progress"></progress><p class="hint">${Number(t.removed)||0} / ${Number(t.total)||0} extra copies deleted · ${Math.floor(Number(t.progress)||0)}%</p>`:''}</div>${['running','queued'].includes(t.status)?`<button class="secondary compact" data-stop-task="${E(t.id)}">Stop safely</button>`:t.kind==='duplicates'&&t.status==='done'?`<button class="secondary compact" data-review-duplicates="${E(t.id)}">Review</button>`:''}</div>`).join('');}
  $('#storageTasks').innerHTML=rows(['duplicates','duplicate-delete','thumbnails']);$('#backupTasks').innerHTML=rows(['backup']);
  if(page==='settings'&&['backup','storage'].includes(settingsTab))taskPoll=setTimeout(()=>action(pollTasks),2500);
}
$('#scanDuplicates').onclick=()=>action(async()=>{await api('duplicates/scan',{});await pollTasks();});
$('#scanThumbnails').onclick=()=>action(async()=>{await api('thumbnails/scan',{});await pollTasks();});
async function reviewDuplicates(id){
  const r=await api('tasks?id='+encodeURIComponent(id)),t=r.tasks.find(t=>t.id===id);if(!t?.result)return;duplicateScan=id;
  const groups=t.result.groups;$('#duplicateReport').innerHTML=`<h3>${groups.length} duplicate groups · ${bytes(t.result.recoverable)} recoverable</h3>${groups.length?'<label>Keep<select id="duplicateKeep"><option value="oldest">Oldest modified copy</option><option value="newest">Newest modified copy</option><option value="manual">Choose manually below</option></select></label>':''}`+groups.map(g=>`<details class="duplicate-group"><summary>${g.files.length} copies · ${bytes(g.size)} each</summary><label>Retain this copy<select data-duplicate-hash="${E(g.hash)}">${g.files.map(f=>`<option value="${E(f.path)}">${E(f.path)} · ${new Date(f.mtime*1000).toLocaleString()}</option>`).join('')}</select></label></details>`).join('')+(groups.length?'<button class="secondary danger" id="deleteDuplicates">Delete extra copies</button>':'<p class="hint">No identical files found.</p>');
  if(groups.length)$('#deleteDuplicates').onclick=()=>action(async()=>{
    const button=$('#deleteDuplicates'),scan=duplicateScan,keep=$('#duplicateKeep').value;
    const selected=keep==='manual'?Object.fromEntries($$('[data-duplicate-hash]').map(s=>[s.dataset.duplicateHash,s.value])):{};
    if(!await uiMessage('Permanently delete the extra byte-identical copies? One copy per group will be kept. Files will be checked again.',{danger:true,accept:'Delete extra copies'}))return;
    button.disabled=true;button.textContent='Queuing deletion…';
    try{await api('duplicates/delete',{scan,keep,selected,confirm:true});$('#duplicateReport').textContent='Deletion queued. Verification, progress and any errors are shown below.';await pollTasks();}
    catch(error){button.disabled=false;button.textContent='Delete extra copies';throw error;}
  });
}
$('#deleteSelected').onclick=()=>action(async()=>{if(!await uiMessage(`Permanently delete ${selectedFiles.size} selected files?`,{danger:true,accept:'Delete files'}))return;await api('files/delete',{files:[...selectedFiles].map(([path,stamp])=>({path,stamp})),confirm:true});selectedFiles.clear();await loadFiles();});
function uploadChunk(id,offset,blob,progress){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.upload.onprogress=e=>progress(Math.min(e.loaded,blob.size));
    xhr.open('POST','/api/files/upload');xhr.responseType='json';xhr.timeout=120000;
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.setRequestHeader('X-Zedair-CSRF',csrf);xhr.setRequestHeader('X-Zedair-Upload',id);
    xhr.setRequestHeader('X-Zedair-Offset',String(offset));
    xhr.onload=()=>{
      const result=xhr.response;
      if(xhr.status<200||xhr.status>=300){reject(Error(result?.error||'Upload failed (HTTP '+xhr.status+')'));return;}
      if(result?.offset!==offset+blob.size){reject(Error('Server acknowledged an unexpected upload offset'));return;}
      resolve(result);
    };
    xhr.onerror=()=>reject(Error('Upload connection lost. Check your connection to ZedAir.'));
    xhr.ontimeout=()=>reject(Error('Upload timed out waiting for ZedAir.'));
    xhr.onabort=()=>reject(Error('Upload cancelled.'));
    xhr.send(blob);
  });
}
let uploadingFiles=false;
$('#uploadFiles').onchange=e=>action(async()=>{
  const input=e.target,files=[...input.files];input.value='';
  if(uploadingFiles)throw Error('Wait for the current uploads to finish.');
  if(files.length>20)throw Error('Upload up to 20 files at a time.');
  if(!files.length)return;
  uploadingFiles=true;input.disabled=true;
  const folder=filePath;
  try{
    $('#uploadProgress').innerHTML=files.map((f,i)=>`<div class="upload-row" id="upload-${i}"><strong>${E(f.name)}</strong><progress max="100" value="0"></progress><span>Waiting</span></div>`).join('');
    for(let i=0;i<files.length;i++){
      const file=files[i],row=$('#upload-'+i),label=$('span',row),bar=$('progress',row);let id;
      try{
        const r=await api('files/upload',{action:'start',folder,name:file.name,size:file.size});id=r.id;
        if(!Number.isInteger(r.chunk_bytes)||r.chunk_bytes<1)throw Error('Refresh the page after updating ZedAir to enable binary uploads.');
        const chunkBytes=Math.min(r.chunk_bytes,4*1024*1024),started=performance.now();
        const update=sent=>{
          bar.value=file.size?100*sent/file.size:0;
          const elapsed=(performance.now()-started)/1000;
          label.textContent=bytes(sent)+' / '+bytes(file.size)+(elapsed>=0.25?' · '+bytes(sent/elapsed)+'/s':'');
        };
        for(let offset=0;offset<file.size;){
          const blob=file.slice(offset,offset+chunkBytes);
          const result=await uploadChunk(id,offset,blob,loaded=>update(offset+loaded));
          offset=result.offset;update(offset);
        }
        label.textContent='Saving to SSD…';
        await api('files/upload',{action:'finish',id});bar.value=100;
        label.textContent='Uploaded'+(file.size?' · '+bytes(file.size/Math.max(.001,(performance.now()-started)/1000))+'/s average':'');
      }catch(error){label.textContent=error.message;if(id)try{await api('files/upload',{action:'cancel',id});}catch{}}
    }
    if(page==='files')await loadFiles();
  }finally{uploadingFiles=false;input.disabled=false;}
});
async function loadProcesses(){
  if(!$('#showProcesses').checked)return;const r=await api('processes');
  $('#processList').innerHTML='<div class="table-scroll"><table><thead><tr><th>Process</th><th>PID</th><th>RAM</th><th>CPU</th><th></th></tr></thead><tbody>'+r.processes.map(p=>`<tr><td>${E(p.name)}</td><td>${p.pid}</td><td>${bytes(p.ram)}</td><td>${p.cpu}%</td><td>${p.worker?`<button class="secondary compact" data-stop-task="${E(p.worker)}" data-kind="${E(p.kind)}">Stop safely</button>`:''}</td></tr>`).join('')+'</tbody></table></div><p class="hint">CPU is sampled since the previous refresh (per core). Only registered Zedair workers can be stopped.</p>';
}
$('#showProcesses').onchange=()=>{$('#processList').hidden=!$('#showProcesses').checked;if($('#showProcesses').checked)action(loadProcesses);};
async function loadActivity(older=false){const r=await api('activity'+(older&&activityBefore?'?before='+activityBefore:''));if(!older)$('#activityList').innerHTML='';$('#activityList').insertAdjacentHTML('beforeend',r.entries.map(e=>`<article class="activity-row"><time>${new Date(e.time*1000).toLocaleString()}</time><strong>${E(e.event)}</strong><p>${E(e.detail)}</p></article>`).join(''));if(r.entries.length)activityBefore=r.entries.at(-1).id;$('#olderActivity').disabled=r.entries.length<200;}
$('#refreshActivity').onclick=()=>action(()=>loadActivity());$('#olderActivity').onclick=()=>action(()=>loadActivity(true));
$('#clearActivity').onclick=()=>action(async()=>{if(!await uiMessage('Clear the activity journal?',{danger:true}))return;await api('activity/clear',{confirm:true});await loadActivity();});
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;action(async()=>{
  if(b.dataset.remoteFolder!==undefined){remotePicker.path=join(remotePicker.path,b.dataset.remoteFolder);await showRemoteFolder();}
  if(b.dataset.resumeRemote){const r=await api('remotes/configure',{action:'resume',name:b.dataset.resumeRemote});remoteConfigName=r.name;$('#remoteConfigFields').innerHTML=questionField(r.option);$('#remoteConfigError').textContent=r.error||'';openDialog('remoteConfigDialog');}
  if(b.dataset.removeRemote){if(!await uiMessage('Remove this remote configuration from Zedair? Files in the cloud will stay untouched.',{danger:true,accept:'Remove remote'}))return;await api('remotes/delete',{remote:b.dataset.removeRemote,confirm:true});await loadCloud();}
  if(b.dataset.browseRemote)await browseRemote(b.dataset.browseRemote,'',()=>{});
  if(b.dataset.stopTask){if(!await uiMessage('Stop this Zedair worker safely? Completed files are kept; a remote stream restarts from zero.'))return;await api('tasks/stop',{id:b.dataset.stopTask,kind:b.dataset.kind||'background'});await pollTasks();}
  if(b.dataset.reviewDuplicates)await reviewDuplicates(b.dataset.reviewDuplicates);
  if(b.dataset.setCover){await api('folders/cover',{path:b.dataset.setCover,stamp:b.dataset.stamp});toast('Folder cover updated.');await loadFiles();}
  if(b.dataset.settingsTab==='storage')await pollTasks();
});});

function appendTorrentTrackers(t,id){
  const trackers=t.trackerStats||[];
  $('#detailBody').insertAdjacentHTML('beforeend',`<hr><h3>Trackers &amp; peers</h3><p class="hint">Connected ${t.peersConnected||0} · sending to us ${t.peersSendingToUs||0} · receiving from us ${t.peersGettingFromUs||0}</p><div class="table-scroll"><table><thead><tr><th>Tracker</th><th>Seeders</th><th>Leechers</th></tr></thead><tbody>${trackers.map(x=>`<tr><td>${E(x.host||x.announce||'Unknown')}<small>${E(x.lastAnnounceResult||'')}</small></td><td>${x.seederCount>=0?x.seederCount:'—'}</td><td>${x.leecherCount>=0?x.leecherCount:'—'}</td></tr>`).join('')}</tbody></table></div><form id="trackerForm"><label>Add trackers<textarea name="trackers" rows="3" placeholder="One tracker URL per line"></textarea></label><div class="dialog-actions"><button class="secondary" type="button" data-torrent="${E(id)}" data-action="announce">Refresh announcements</button><button class="primary" type="submit">Add trackers</button></div></form>`);
  $('#trackerForm').onsubmit=e=>{e.preventDefault();action(async()=>{await api('torrent/action',{id,action:'trackers',trackers:e.target.trackers.value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean)});toast('Trackers updated.');await showTorrentFiles(id);});};
}
