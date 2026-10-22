'use strict';
const assert=require('assert');const StudioSeek=require('../../volumio-ui/studio-seek');
class Element {
 constructor(){this.events={};this.value='0';this.style={setProperty(){}};this.disabled=false;this.textContent='';}
 addEventListener(event,fn){this.events[event]=fn;}setAttribute(){}fire(event){this.events[event]();}
}
let now=0,state={uri:'youtube2/video@videoId=abcdefghijk',service:'youtube2',duration:300,seek:1000,status:'pause'};
const input=new Element(),back=new Element(),forward=new Element(),sent=[];
const widget=new StudioSeek({input:input,back:back,forward:forward,elapsed:new Element(),duration:new Element(),status:new Element(),state:()=>state,receivedAt:()=>0,connected:()=>true,grouped:()=>false,format:s=>String(s),send:value=>{sent.push(value);return true;},now:()=>now});
widget.render();assert.strictEqual(input.disabled,false);
widget.receive({session:'one',revision:1,trackUri:state.uri,phase:'paused',durationMs:300000,positionMs:1000});
input.fire('pointerdown');input.value='200';input.fire('input');state.seek=3000;widget.render();assert.strictEqual(input.value,'200');
input.fire('change');assert.strictEqual(sent[0],200);assert.strictEqual(state.seek,3000,'UI must not invent actual playback progress');
widget.receive({session:'one',revision:2,trackUri:state.uri,phase:'buffering',durationMs:300000,positionMs:1000,requestedMs:200000});
assert.strictEqual(Number(input.value),200);assert(widget.o.status.textContent.includes('Przewijanie'));
widget.receive({session:'one',revision:3,trackUri:state.uri,phase:'paused',durationMs:300000,positionMs:200000});assert.strictEqual(widget.pending,null);
now=10000;widget.render();assert.strictEqual(Number(input.value),200,'paused clock must not advance');
widget.receive({session:'one',revision:2,trackUri:state.uri,phase:'playing',durationMs:100000,positionMs:0});assert.strictEqual(widget.duration(),300,'late status cannot shrink scale');
widget.jump(10);widget.jump(10);assert.strictEqual(sent[sent.length-1],220,'rapid +/- use latest requested position');
widget.commit(300);assert.strictEqual(sent[sent.length-1],299,'right edge stays inside media');
state=Object.assign({},state,{uri:'youtube2/video@videoId=ABCDEFGHIJK',duration:500,seek:0});widget.render();assert.strictEqual(widget.pending,null);assert.strictEqual(widget.remote,null);
console.log('PASS Studio: drag isolation, confirmed position, paused seek, fixed scale, rapid buttons, final sample, track change');
