'use strict';
Promise.prototype.fail=Promise.prototype.catch;
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const SeekEngine=require('./seek-engine').SeekEngine;
let raw={uri:null,status:'stop',duration:0,seek:0};const batches=[],pushed=[],events=[];
const track={uri:'youtube2/video@videoId=abcdefghijk',title:'Test'};let selectedTrack=track;
const machine={currentPosition:0,currentStatus:'play',consumeState:{},currentSeek:0,
 getTrack(){return selectedTrack;},getState(){return {uri:track.uri,position:0};},
 setConsumeUpdateService(name){this.consumeUpdateService=name;this.isConsume=true;},
 startPlaybackTimer(ms){this.timerFrom=ms;},increasePlaybackTimer(){this.currentSeek+=250;},stopPlaybackTimer(){this.timerFrom=null;},pushState(){pushed.push(this.currentSeek);},
 commandRouter:{broadcastMessage(event,status){events.push({event:event,status:status});},servicePushState(){}}
};
const mpd={getState(){return Promise.resolve(Object.assign({},raw));},parseTrackInfo(){return {};},pushState(state){pushed.push(state);return Promise.resolve();},
 stop(){raw.status='stop';return Promise.resolve();},
 sendMpdCommandArray(commands){batches.push(commands);const url=commands[2].parameters[0];const offset=Number(/\/(\d+)\.wav$/.exec(url)[1]);raw={uri:url,status:'play',duration:(300000-offset)/1000,seek:25};return Promise.resolve();}
};
let machineReady=false;
const yt2={getMpdPlugin:()=>mpd,getStateMachine:()=>machineReady?machine:undefined,getLogger:()=>({info(){},error(){}}),toast(){},getConfigValue:()=>false};
const q={resolve:Promise.resolve.bind(Promise),reject:Promise.reject.bind(Promise),defer(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise:promise,resolve:resolve,reject:reject};}};
const mod={exports:{}};
vm.runInNewContext(fs.readFileSync(__dirname+'/play-controller.js','utf8'),{module:mod,yt2PluginLibRoot:'/plugin',Promise:Promise,require(name){if(name==='kew')return q;if(name==='http')return require('http');if(name==='url')return require('url');if(name==='/plugin/youtube2')return yt2;if(name==='/plugin/helper/video')return {};if(name==='./seek-engine')return {SeekEngine:SeekEngine};throw Error(name);}});
(async()=>{
 const controller=new mod.exports();machineReady=true;controller._resolve=async id=>({videoId:id,durationMs:300000,seekable:true});controller._json=async()=>({ok:true});
 try {
  await controller.clearAddPlayTrack(track);assert.strictEqual(batches.length,1);assert.strictEqual(batches[0].map(c=>c.command).join(','),'stop,clear,addid,play');
  await controller.seek(200000);const normalized=await mpd.getState();assert.strictEqual(normalized.duration,300);assert.strictEqual(normalized.seek,200025);assert.strictEqual(machine.currentSeek,200025);machine.startPlaybackTimer(200025);assert.strictEqual(machine.runPlaybackTimer,false,'legacy clock must be disabled only for owned playback');
  await controller.pause();await controller.seek(120000);assert.strictEqual(batches.length,2);const paused=await mpd.getState();assert.strictEqual(paused.status,'pause');assert.strictEqual(paused.seek,120000);
  await controller.resume();assert.strictEqual(batches.length,3);assert.strictEqual(machine.consumeState.duration,300);const fixed=machine.currentSeek;machine.increasePlaybackTimer();assert.strictEqual(machine.currentSeek,fixed,'queued legacy tick must not advance YouTube time');
  selectedTrack={uri:'music-library/song.flac'};machine.startPlaybackTimer(123);assert.strictEqual(machine.timerFrom,123,'unrelated source keeps original clock');machine.increasePlaybackTimer();assert.strictEqual(machine.currentSeek,fixed+250);selectedTrack=track;
  controller.engine.phase='buffering';const count=pushed.length;await mpd.pushState({status:'stop'});assert.strictEqual(pushed.length,count,'transient stop must not advance queue');
  assert.strictEqual(mpd.parseTrackInfo({}).trackType,'');assert(events.some(e=>e.event==='pushYoutubeSeek'));
  const reader=mpd.getState;const second=new mod.exports();assert.strictEqual(mpd.getState,reader,'do not stack adapters');second.engine.close();
  console.log('PASS Volumio adapter: atomic replace, one timeline, paused seek, stop filtering, empty metadata, one adapter');
 } finally {controller.engine.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
