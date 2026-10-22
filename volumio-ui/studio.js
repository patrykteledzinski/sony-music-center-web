'use strict';
try { localStorage.setItem('studioMode','studio'); } catch(error) {}
const $ = id => document.getElementById(id);
let state = {}, queue = [], stateReceivedAt = 0, coverUrl = '', seekActive = false;
let currentView = 'now', browseGeneration = 0, browseHistory = [], currentBrowse = {uri:'',title:'Źródła muzyki'};
let sonyBase = 'http://192.168.8.50:8088';
try { sonyBase=localStorage.getItem('studioSonyAddress')||sonyBase; } catch(error) {}
let sonyBusy = false, toastTimer;
let groupState={enabled:false}, groupBusy=false;
async function groupRequest(path,body) {
    const response=await fetch(sonyBase+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok||data.ok===false)throw new Error(data.error||'Błąd wspólnego grania');return data;
}
async function refreshGroup() {
    if(groupBusy)return;
    try {
        groupState=await getJSON(sonyBase+'/api/group');
        $('group-enabled').checked=groupState.enabled;
        $('group-message').textContent=groupState.error||groupState.message+(groupState.drift_seconds!==null?' · różnica pozycji '+Number(groupState.drift_seconds).toFixed(1)+' s':'');
    } catch(error) {
        // Do not silently switch to independent playback on a network failure.
        $('group-message').textContent='Brak połączenia ze wspólnym sterowaniem';
    }
}
$('group-enabled').onchange=async event=>{
    groupBusy=true;event.target.disabled=true;
    try{groupState=await groupRequest('/api/group',{enabled:event.target.checked});}
    catch(error){notify(error.message);}
    finally{groupBusy=false;event.target.disabled=false;refreshGroup();}
};
const socket = typeof io === 'function' ? io({reconnection:true,timeout:6000}) : null;
function notify(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$('toast').hidden=true;},4500); }
function fmt(seconds) { seconds=Math.max(0,Math.floor(Number(seconds)||0)); return (seconds>=3600?Math.floor(seconds/3600)+':':'')+String(Math.floor(seconds/60)%60).padStart(seconds>=3600?2:1,'0')+':'+String(seconds%60).padStart(2,'0'); }
function connected() { return Boolean(socket && socket.connected); }
function command(event,data) {
    if(groupState.enabled && !['volume','mute'].includes(event)) {
        if(['play','pause','stop','prev','next','seek'].includes(event) && !(event==='play' && data?.value!==undefined)) {
            let payload={};
            if(event==='seek') {notify('W trybie Graj razem przewijaj utwór w panelu Sony.');return false;}
            groupRequest('/api/group/control/'+event,payload).catch(error=>notify(error.message));
            return true;
        }
        notify('Graj razem: wybieraj utwory i kolejkę w Sony Music Center. Głośność pozostaje niezależna.');
        return false;
    }
    if (!connected()) { notify('Brak połączenia z Volumio. Spróbuj po jego odzyskaniu.'); return false; }
    socket.emit(event,data); return true;
}
function setConnection(online) {
    $('connection-dot').classList.toggle('online',online);
    $('connection-label').textContent=online?'Volumio połączone':'Volumio offline';
    document.querySelectorAll('[data-control]').forEach(el=>{el.disabled=!online;});
    seekControl.render();
    if(online && state.disableVolumeControl) { $('volume').disabled=true; $('mute').disabled=true; }
}
function showView(view) {
    currentView=view;
    document.querySelectorAll('.view').forEach(el=>{el.hidden=el.id!=='view-'+view;});
    document.querySelectorAll('[data-view]').forEach(el=>{el.classList.toggle('active',el.dataset.view===view);});
    if(view==='library' && !$('library-items').children.length) browse('', 'Źródła muzyki',false);
    if(view==='queue' && connected()) socket.emit('getQueue');
}
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>showView(el.dataset.view)));
$('open-queue').onclick=$('all-queue').onclick=()=>showView('queue');
$('play').onclick=()=>command(state.status==='play'?'pause':'play');
$('prev').onclick=()=>command('prev'); $('next').onclick=()=>command('next');
$('shuffle').onclick=()=>command('setRandom',{value:!state.random});
$('repeat').onclick=()=>command('setRepeat',{value:!state.repeat});
$('mute').onclick=()=>command('mute');
$('volume').oninput=event=>{$('volume-value').textContent=event.target.value;};
$('volume').onchange=event=>command('volume',Number(event.target.value));
const seekControl=new StudioSeek({
    input:$('seek'),back:$('seek-back'),forward:$('seek-forward'),elapsed:$('elapsed'),duration:$('duration'),status:$('seek-status'),
    state:()=>state,receivedAt:()=>stateReceivedAt,connected:connected,grouped:()=>groupState.enabled,
    send:target=>command('seek',target),format:fmt
});
function artwork(url) {
    if(!url) return '';
    try { const parsed=new URL(url,location.origin); return ['http:','https:'].includes(parsed.protocol)?parsed.href:''; } catch(e) { return ''; }
}
$('cover').onerror=()=>{$('cover').hidden=true;};
$('sony-cover').onerror=()=>{$('sony-cover').hidden=true;$('sony-art-fallback').hidden=false;};
function updateState(data) {
    if(!data || typeof data!=='object')return;
    state=data;stateReceivedAt=Date.now();
    const item=queue[Number(state.position)] || {};
    const generic=/^\d+\.[a-z0-9]+$/i.test(state.title||'');
    $('track-title').textContent=(generic?item.title||item.name:state.title)||'Wybierz swój dźwięk';
    $('track-artist').textContent=[state.artist||item.artist,state.album||item.album].filter(Boolean).join(' · ')||'Twoja przestrzeń. Twoja muzyka.';
    $('source-label').textContent=state.trackType==='webradio'?'RADIO INTERNETOWE':state.service==='upnp_browser'?'BIBLIOTEKA NA SERWERZE':'TERAZ W VOLUMIO';
    const rawArt=artwork(state.albumart);
    const url=rawArt && new URL(rawArt).searchParams.get('web')==='//extralarge'?'':rawArt;
    if(url!==coverUrl) {coverUrl=url;$('cover').hidden=!url;if(url)$('cover').src=url;}
    $('play').textContent=state.status==='play'?'Ⅱ':'▶';
    $('play').setAttribute('aria-label',state.status==='play'?'Wstrzymaj':'Odtwórz');
    $('shuffle').setAttribute('aria-pressed',String(Boolean(state.random)));
    $('repeat').setAttribute('aria-pressed',String(Boolean(state.repeat)));
    if(document.activeElement!==$('volume')) { $('volume').value=Number(state.volume)||0;$('volume-value').textContent=state.mute?'0':String(state.volume??'—'); }
    $('mute').setAttribute('aria-pressed',String(Boolean(state.mute)));
    const format=[state.trackType,state.samplerate,state.bitdepth?state.bitdepth+' BIT':'',state.bitrate].filter(Boolean).join(' · ');
    $('audio-format').textContent=(format||'GOTOWY DO ODSŁUCHU').toUpperCase();
    setConnection(connected());updateProgress();renderQueue();
}
function updateProgress() {seekControl.render();}
function playQueue(index) { command('play',{value:index}); }
function renderQueue() {
    $('queue-count').textContent=String(queue.length);$('queue-total').textContent=queue.length+' UTWORÓW';
    const rows=$('queue-items');rows.replaceChildren();
    if(!queue.length) {const p=document.createElement('p');p.className='secondary';p.textContent='Kolejka jest pusta. Wybierz muzykę w bibliotece.';rows.append(p);}
    queue.forEach((item,index)=>{
        const row=document.createElement('button');row.className='queue-track'+(Number(state.position)===index?' current':'');
        row.disabled=!connected(); row.onclick=()=>playQueue(index);
        const number=document.createElement('span');number.textContent=Number(state.position)===index && state.status==='play'?'♫':String(index+1).padStart(2,'0');
        const detail=document.createElement('span'),title=document.createElement('strong'),artist=document.createElement('small');
        title.textContent=item.title||item.name||'Utwór';artist.textContent=item.artist||item.album||'Biblioteka muzyki';detail.append(title,artist);row.append(number,detail);rows.append(row);
    });
    const next=$('up-next');next.replaceChildren();
    const start=state.status?Math.max(0,Number(state.position)+1):0;
    let upcoming=queue.map((item,index)=>({item,index})).slice(start,start+3);
    if(!upcoming.length && state.repeat) upcoming=queue.slice(0,3).map((item,index)=>({item,index}));
    if(!upcoming.length) {const empty=document.createElement('span');empty.className='empty-small';empty.textContent=state.random?'Losowa kolejność jest włączona. Następny utwór wybierze Volumio.':'To koniec kolejki. Jest miejsce na coś dobrego.';next.append(empty);}
    if(state.random && upcoming.length) { const hint=document.createElement('p');hint.className='empty-small';hint.textContent='Losowo — poniżej kolejność na liście.';next.append(hint); }
    upcoming.forEach(({item,index})=>{
        const row=document.createElement('button');row.className='mini-track';row.disabled=!connected();row.onclick=()=>playQueue(index);
        const art=document.createElement('span');art.className='mini-art';art.textContent='♫';
        const info=document.createElement('span');info.className='mini-info';const title=document.createElement('strong'),artist=document.createElement('small');
        title.textContent=item.title||item.name||'Utwór';artist.textContent=item.artist||'Biblioteka muzyki';info.append(title,artist);
        const number=document.createElement('span');number.className='mini-index';number.textContent=String(index+1).padStart(2,'0');row.append(art,info,number);next.append(row);
    });
}
async function getJSON(url,timeout=8000) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
    try { const response=await fetch(url,{signal:controller.signal,cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);return await response.json(); }finally{clearTimeout(timer);}
}
async function browse(uri,title,remember=true) {
    const generation=++browseGeneration;
    if(remember)browseHistory.push({...currentBrowse});
    currentBrowse={uri,title};$('browse-location').textContent=title;$('browse-back').disabled=!browseHistory.length;
    $('library-items').textContent='Wczytywanie muzyki…';
    try {
        const response=await getJSON('/api/v1/browse'+(uri?'?uri='+encodeURIComponent(uri):''),20000);
        if(generation!==browseGeneration)return;
        const lists=response.navigation?.lists||[];
        const items=lists.flatMap(list=>list.items||(!list.items && list.uri?[list]:[]));
        const container=$('library-items');container.replaceChildren();
        if(!items.length)container.textContent='Brak elementów w tym miejscu.';
        items.forEach(item=>{
            const row=document.createElement('button');row.className='library-item';
            const song=['song','webradio'].includes(item.type), symbol=document.createElement('span'),info=document.createElement('span');
            symbol.className='library-symbol';symbol.textContent=song?'♫':'▤';info.className='item-name';info.textContent=item.title||item.name||'Źródło';
            const label=document.createElement('small');label.textContent=song?(item.artist||'Odtwórz utwór'):'Przeglądaj';info.append(label);row.append(symbol,info);
            row.onclick=()=>{if(song){if(command('replaceAndPlay',item)){notify('Wysłano utwór do Volumio');showView('now');}}else if(item.uri){browse(item.uri,item.title||item.name||'Biblioteka');}};
            container.append(row);
        });
    } catch(error) { if(generation===browseGeneration){$('library-items').textContent='Nie udało się wczytać biblioteki. Wybierz Źródła, aby spróbować ponownie.';} }
}
$('browse-back').onclick=()=>{const previous=browseHistory.pop();if(previous)browse(previous.uri,previous.title,false);};
$('browse-home').onclick=()=>{browseHistory=[];browse('','Źródła muzyki',false);};
async function refreshSony() {
    if(sonyBusy)return;sonyBusy=true;
    try {
        const data=await getJSON(sonyBase+'/api/studio/status',6500);
        $('sony-dot').style.background=data.online?'var(--mint)':'#7d7869';
        $('sony-title').textContent=data.online?(data.title||'Sony gotowe do grania'):'Sony offline';
        $('sony-artist').textContent=data.artist||data.album||'Sony Music Center';
        $('sony-state').textContent=data.online?({PLAYING:'Odtwarzanie',PAUSED_PLAYBACK:'Pauza',STOPPED:'Zatrzymane'}[data.state]||data.state):'Brak połączenia z zestawem';
        $('sony-volume').textContent=data.online?'Głośność '+data.volume:'—';
        $('sony-progress-fill').style.width=(data.online && data.duration_seconds?Math.min(100,Math.max(0,data.position/data.duration_seconds*100)):0)+'%';
        $('sony-equalizer').classList.toggle('playing',Boolean(data.online && data.state==='PLAYING'));
        const source=data.online && data.browser_source?sonyBase+'/cover?path='+encodeURIComponent(data.browser_source):'';
        if(source && $('sony-cover').getAttribute('src')!==source){$('sony-cover').src=source;$('sony-cover').hidden=false;$('sony-art-fallback').hidden=true;}
        if(!source){$('sony-cover').hidden=true;$('sony-art-fallback').hidden=false;}
    }catch(error){$('sony-title').textContent='Panel Sony niedostępny';$('sony-artist').textContent='Sprawdź połączenie z serwerem.';$('sony-state').textContent='Ponowne sprawdzenie za chwilę';$('sony-dot').style.background='#7d7869';$('sony-equalizer').classList.remove('playing');$('sony-progress-fill').style.width='0%';$('sony-volume').textContent='—';$('sony-cover').hidden=true;$('sony-art-fallback').hidden=false;}
    finally{sonyBusy=false;}
}
$('sony-link').href=sonyBase+'/';
$('settings-button').onclick=()=>{$('sony-address').value=sonyBase;$('settings-dialog').showModal();};
$('cancel-settings').onclick=()=>$('settings-dialog').close();
$('settings-form').onsubmit=event=>{
    event.preventDefault();
    try{const url=new URL($('sony-address').value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw new Error();sonyBase=url.origin;localStorage.setItem('studioSonyAddress',sonyBase);$('sony-link').href=sonyBase+'/';$('settings-dialog').close();refreshSony();}
    catch(error){notify('Podaj poprawny adres HTTP lub HTTPS panelu Sony.');}
};
if(socket) {
    socket.on('connect',()=>{setConnection(true);socket.emit('getState');socket.emit('getQueue');});
    socket.on('disconnect',()=>{setConnection(false);renderQueue();});
    socket.on('connect_error',()=>setConnection(false));
    socket.on('pushState',updateState);
    socket.on('pushYoutubeSeek',value=>seekControl.receive(value));
    socket.on('pushQueue',data=>{queue=Array.isArray(data)?data:Array.isArray(data?.queue)?data.queue:[];renderQueue();if(state.status){const receivedAt=stateReceivedAt;updateState(state);stateReceivedAt=receivedAt;}});
    socket.on('pushToastMessage',data=>{if(data?.type==='error')notify(data.message||'Volumio zgłosiło błąd.');});
} else {notify('Nie udało się wczytać połączenia z Volumio. Odśwież stronę.');}
setConnection(connected());renderQueue();refreshSony();refreshGroup();
setInterval(refreshGroup,3000);
setInterval(updateProgress,1000);
setInterval(()=>{if(!document.hidden){refreshSony();if(connected())socket.emit('getState');}},5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshSony();if(connected()){socket.emit('getState');socket.emit('getQueue');}}});
