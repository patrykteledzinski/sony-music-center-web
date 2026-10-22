'use strict';
// Destructive only to the explicitly supplied, separate MPD test instance.
const net=require('net'),http=require('http'),assert=require('assert');
const SeekEngine=require('./seek-engine').SeekEngine;
const port=Number(process.argv[2]||16600),base=process.argv[3]||'http://127.0.0.1:18889',video=process.argv[4]||'p1MQRmyZyH0';
if(port===6600)throw Error('Use a separate silent MPD, never the production port');
function mpd(commands){return new Promise((resolve,reject)=>{
 const socket=net.connect(port,'127.0.0.1');let buffer='',greeted=false,done=false;
 const finish=(err,result)=>{if(done)return;done=true;socket.destroy();err?reject(err):resolve(result);};
 socket.setTimeout(10000,()=>finish(Error('MPD timeout')));socket.on('error',e=>finish(e));
 socket.on('data',part=>{buffer+=part.toString();if(!greeted){const at=buffer.indexOf('\n');if(at<0)return;buffer=buffer.slice(at+1);greeted=true;socket.write('command_list_begin\n'+commands.join('\n')+'\ncommand_list_end\n');}
 if(buffer.endsWith('OK\n')){const result={};buffer.split('\n').forEach(line=>{const at=line.indexOf(': ');if(at>0)result[line.slice(0,at)]=line.slice(at+2);});finish(null,result);}
 else if(buffer.indexOf('ACK [')>=0)finish(Error(buffer));});
});}
function json(path,method){return new Promise((resolve,reject)=>{const options=require('url').parse(base+path);options.method=method||'GET';const req=http.request(options,res=>{let body='';res.on('data',part=>body+=part);res.on('end',()=>{try{const d=JSON.parse(body);if(res.statusCode!==200)throw Error(d.error);resolve(d);}catch(e){reject(e);}});});req.setTimeout(85000,()=>req.destroy(Error('resolver timeout')));req.on('error',reject);req.end();});}
let replacements=0,selected=true;
const engine=new SeekEngine({resolve:id=>json('/v2/info/'+id),url:(id,ms,s,r)=>base+'/v2/audio/'+s+'/'+r+'/'+id+'/'+ms+'.wav',cancel:s=>json('/v2/cancel/'+s,'POST'),
 replace:async url=>{replacements++;await mpd(['stop','clear','addid "'+url+'"','play']);},stop:()=>mpd(['stop']),read:async()=>{const s=await mpd(['status','currentsong']);return {uri:s.file,status:s.state,seek:Number(s.elapsed||0)*1000,duration:Number(s.duration||0)};},selected:()=>selected,
 status:s=>{if(s.phase!=='playing')console.log('PHASE',s.phase,s.revision,s.requestedMs);}
},{readyMs:30000,pollMs:1000});
(async()=>{try{
 await engine.load({uri:'youtube2/video@videoId='+video},video);const duration=engine.metadata.durationMs;assert.strictEqual(engine.phase,'playing');
 for(const target of [duration-10000,duration-1000,0]){await engine.seek(target);assert.strictEqual(engine.phase,'playing');assert(Math.abs(engine.confirmed.seek-target)<3000);assert.strictEqual(engine.confirmed.duration*1000,duration);console.log('REAL MPD SEEK',target,engine.confirmed.seek);}
 const count=replacements;await Promise.all([engine.seek(60000),engine.seek(120000),engine.seek(duration-15000)]);assert(replacements-count<=2);assert(engine.active.url.endsWith('/'+(duration-15000)+'.wav'));
 await engine.pause();const pausedCount=replacements;await engine.seek(30000);assert.strictEqual(replacements,pausedCount);assert.strictEqual((await mpd(['status'])).state,'stop');assert.strictEqual(engine.phase,'paused');
 await engine.resume();assert.strictEqual(engine.phase,'playing');assert(engine.active.url.endsWith('/30000.wav'));
 console.log('PASS real MPD: beginning/end, rapid seeks, fixed duration, paused seek, resume; replaces='+replacements);
 }finally{await engine.stop();engine.close();await mpd(['clear']);}
})().catch(error=>{console.error(error);process.exitCode=1;});
