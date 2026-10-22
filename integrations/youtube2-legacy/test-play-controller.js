'use strict';
Promise.prototype.fail=Promise.prototype.catch;
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const commands=[],flags=[];let rawState={};
const mpd={getState(){return Promise.resolve(rawState);},sendMpdCommand(command,args){commands.push(command);return Promise.resolve({Id:1});}};
const yt2={getMpdPlugin(){return mpd;},getStateMachine(){return {getState(){return {duration:300};},setConsumeUpdateService(){flags.push(Array.from(arguments));}};}};
const module_={exports:{}};
vm.runInNewContext(fs.readFileSync(process.argv[2]||__dirname+'/play-controller.js','utf8'),{
 setInterval(){return {unref(){}};},clearInterval(){},module:module_,yt2PluginLibRoot:'/plugin',require(name){
  if(name==='kew')return {};
  if(name==='/plugin/youtube2')return yt2;
  if(name==='/plugin/helper/video')return {};
  throw new Error(name);
 }
});
const player=new module_.exports();
player._doPlay('https://example.com/audio.m4a',{}).then(()=>{
 assert.deepStrictEqual(commands,['stop','clear','addid "https://example.com/audio.m4a"','play']);
 assert.strictEqual(flags[0][0],'mpd');assert.strictEqual(flags[0][1],true);
 return player.seek(120000).then(()=>{assert.strictEqual(commands[commands.length-1],'seekcur 120');});
}).then(()=>{
 player.streamBase='http://127.0.0.1:18888/audio/abcdefghijk.wav';player.lastPlaybackInfo={track:{uri:'youtube2/video@videoId=abcdefghijk'}};
 player._doPlay=(url,track,seeking)=>{assert.strictEqual(url,'http://127.0.0.1:18888/audio/abcdefghijk/120.wav');assert.strictEqual(seeking,true);return Promise.resolve();};
 return player.seek(120000).then(()=>{assert.strictEqual(player.playbackOffset,120);assert.strictEqual(player.fullDuration,300);});
}).then(()=>{
 player.currentStreamUrl='http://127.0.0.1:18888/audio/abcdefghijk/120.wav';
 const normalized=player._normalizeMpdState({uri:player.currentStreamUrl,seek:3000,duration:180,status:'play'});
 assert.strictEqual(normalized.seek,123000);assert.strictEqual(normalized.duration,300);assert.strictEqual(normalized.isStreaming,false);
 const other={uri:'http://example.com/radio',seek:3000,duration:0};assert.strictEqual(player._normalizeMpdState(other),other);
 rawState={uri:player.currentStreamUrl,seek:3000,duration:180,status:'play'};
 return mpd.getState().then(actual=>{assert.strictEqual(actual.duration,300);assert.strictEqual(actual.seek,123000);assert.strictEqual(rawState.duration,180);});
}).then(()=>{
 console.log('PASS: direct AAC is added as audio, metadata retained, no playlist parsing');
}).catch(error=>{console.error(error);process.exit(1);});
