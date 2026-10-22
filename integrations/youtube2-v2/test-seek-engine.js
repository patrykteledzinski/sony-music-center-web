'use strict';
const assert=require('assert');
const SeekEngine=require('./seek-engine').SeekEngine;
const tests=[];const test=(name,fn)=>tests.push({name:name,fn:fn});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise:promise,resolve:resolve,reject:reject};};
function fixture(extra,options) {
 const f={replaces:[],published:[],statuses:[],stops:0,selected:true,raw:{status:'stop',uri:null,seek:0,duration:0}};
 f.io=Object.assign({resolve:async id=>({videoId:id,durationMs:300000,seekable:true}),
  url:(id,ms,s,r)=>'http://local/v2/audio/'+s+'/'+r+'/'+id+'/'+ms+'.wav',
  cancel:async()=>{},selected:()=>f.selected,
  replace:async url=>{f.replaces.push(url);const offset=Number(/\/(\d+)\.wav$/.exec(url)[1]);f.raw={uri:url,status:'play',seek:25,duration:(300000-offset)/1000};},
  stop:async()=>{f.stops++;f.raw.status='stop';},read:async()=>Object.assign({},f.raw),
  publish:s=>f.published.push(s),status:s=>f.statuses.push(s)},extra);
 f.engine=new SeekEngine(f.io,Object.assign({pollMs:60000,debounceMs:5,readyMs:5},options));
 f.track={uri:'youtube2/video@videoId=abcdefghijk'};return f;
}
async function use(f,fn){try{await fn(f);}finally{f.engine.close();}}
test('full duration and actual confirmed position survive distant seeks',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');await f.engine.seek(298000);
 assert.strictEqual(f.engine.phase,'playing');assert.strictEqual(f.engine.confirmed.duration,300);assert.strictEqual(f.engine.confirmed.seek,298025);
 await f.engine.seek(0);assert.strictEqual(f.engine.confirmed.seek,25);assert.strictEqual(f.engine.confirmed.duration,300);
}));
test('rapid seeks coalesce to last request rather than replaying every intermediate seek',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');await Promise.all([f.engine.seek(10000),f.engine.seek(100000),f.engine.seek(250000)]);
 assert.strictEqual(f.replaces.length,2);assert(f.replaces[1].endsWith('/250000.wav'));
}));
test('seeking while paused never starts audio and resume opens staged target',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');await f.engine.pause();const count=f.replaces.length;
 await f.engine.seek(200000);assert.strictEqual(f.replaces.length,count);assert.strictEqual(f.engine.phase,'paused');assert.strictEqual(f.engine.confirmed.seek,200000);
 await f.engine.resume();assert(f.replaces[f.replaces.length-1].endsWith('/200000.wav'));assert.strictEqual(f.engine.phase,'playing');
}));
test('late metadata from previous film cannot start playback',async()=>{
 const a=deferred(),b=deferred();const f=fixture({resolve:id=>id==='abcdefghijk'?a.promise:b.promise});
 await use(f,async()=>{const first=f.engine.load(f.track,'abcdefghijk');const track={uri:'youtube2/video@videoId=ABCDEFGHIJK'};
 const second=f.engine.load(track,'ABCDEFGHIJK');b.resolve({videoId:'ABCDEFGHIJK',durationMs:300000,seekable:true});await second;
 a.resolve({videoId:'abcdefghijk',durationMs:300000,seekable:true});await first;assert.strictEqual(f.replaces.length,1);assert(f.replaces[0].includes('ABCDEFGHIJK'));
 });
});
test('stop during metadata resolution cannot be undone by late response',async()=>{
 const d=deferred(),f=fixture({resolve:()=>d.promise});await use(f,async()=>{const loading=f.engine.load(f.track,'abcdefghijk');await f.engine.stop();d.resolve({videoId:'abcdefghijk',durationMs:300000,seekable:true});await loading;assert.strictEqual(f.replaces.length,0);assert.strictEqual(f.engine.phase,'stopped');});
});
test('switch to unrelated player cancels a pending YouTube load',async()=>{
 const d=deferred(),f=fixture({resolve:()=>d.promise});await use(f,async()=>{const loading=f.engine.load(f.track,'abcdefghijk');f.selected=false;d.resolve({videoId:'abcdefghijk',durationMs:300000,seekable:true});await loading;assert.strictEqual(f.replaces.length,0);});
});
test('live streams and invalid offsets fail explicitly',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');await assertRejects(f.engine.seek(NaN));await f.engine.seek(999999);assert(f.replaces[f.replaces.length-1].endsWith('/299000.wav'));
}));
test('a play ACK without audio does not count as seek success',async()=>use(fixture({replace:async()=>{}},{readyMs:1}),async f=>{
 await assertRejects(f.engine.load(f.track,'abcdefghijk'));assert.strictEqual(f.engine.phase,'error');assert.strictEqual(f.engine.confirmed,null);
}));
test('remaining segment duration is normalized only for owned source',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');await f.engine.seek(200000);
 const raw={uri:f.engine.active.url,status:'play',seek:1000,duration:100};const normalized=f.engine.normalize(raw);
 assert.strictEqual(normalized.duration,300);assert.strictEqual(normalized.seek,201000);assert.strictEqual(raw.duration,100);
 const other={uri:'file:///music.flac',seek:123,duration:50};assert.strictEqual(f.engine.normalize(other),other);
}));
test('transient stopped state during replacement is not published as end of track',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');const gate=deferred();f.io.replace=async url=>{f.raw={uri:url,status:'stop',seek:0,duration:0};await gate.promise;f.raw={uri:url,status:'play',seek:25,duration:100};};
 const seeking=f.engine.seek(200000);await new Promise(r=>setTimeout(r,10));assert.strictEqual(f.engine.normalize(f.raw).status,'pause');gate.resolve();await seeking;assert.strictEqual(f.engine.confirmed.duration,300);
}));
test('late poll cannot overwrite the result of a newer seek',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');const old=Object.assign({},f.raw),gate=deferred();let first=true;
 f.io.read=async()=>{if(first){first=false;return gate.promise;}return Object.assign({},f.raw);};
 const polling=f.engine.poll();await f.engine.seek(200000);gate.resolve(old);await polling;assert.strictEqual(f.engine.confirmed.seek,200025);
}));
test('pause before metadata completes does not start the recording later',async()=>{
 const d=deferred(),f=fixture({resolve:()=>d.promise});await use(f,async()=>{
  const loading=f.engine.load(f.track,'abcdefghijk');await f.engine.pause();d.resolve({videoId:'abcdefghijk',durationMs:300000,seekable:true});await loading;
  assert.strictEqual(f.engine.phase,'paused');assert.strictEqual(f.replaces.length,0);
 });
});
test('pause during an in-flight seek cancels its eventual playing confirmation',async()=>use(fixture(),async f=>{
 await f.engine.load(f.track,'abcdefghijk');const gate=deferred();f.io.replace=async url=>{f.raw={uri:url,status:'play',seek:25,duration:100};await gate.promise;};
 const seeking=f.engine.seek(200000);await new Promise(r=>setTimeout(r,10));const pausing=f.engine.pause();gate.resolve();await Promise.all([seeking,pausing]);
 assert.strictEqual(f.engine.phase,'paused');assert.strictEqual(f.engine.confirmed.seek,200000);assert.strictEqual(f.raw.status,'stop');
}));
test('real EOF without current song is delivered once',async()=>{
 let ended=0;await use(fixture({ended:()=>{ended++;}}),async f=>{await f.engine.load(f.track,'abcdefghijk');f.raw={status:'stop',uri:null,seek:0,duration:0};await f.engine.poll();await f.engine.poll();assert.strictEqual(ended,1);assert.strictEqual(f.engine.phase,'stopped');});
});
async function assertRejects(promise){let failed=false;try{await promise;}catch(e){failed=true;}assert(failed,'expected rejection');}
(async()=>{for(const item of tests){await item.fn();console.log('PASS '+item.name);}console.log(tests.length+' seek engine scenarios passed');})().catch(error=>{console.error(error);process.exitCode=1;});
