/* One UI controller for mouse, touch, keyboard and ±10s. No optimistic playback clock. */
(function(root){
'use strict';
class StudioSeek {
    constructor(options) {
        this.o=options;this.input=options.input;this.pending=null;this.dragging=false;this.draft=null;
        this.remote=null;this.remoteAt=0;this.now=options.now||Date.now;
        this.input.step='0.1';
        this.input.addEventListener('pointerdown',()=>{if(!this.input.disabled)this.dragging=true;});
        this.input.addEventListener('pointerup',()=>setTimeout(()=>{this.dragging=false;this.render();},0));
        this.input.addEventListener('pointercancel',()=>{this.dragging=false;this.draft=null;this.render();});
        this.input.addEventListener('blur',()=>{this.dragging=false;this.draft=null;this.render();});
        this.input.addEventListener('input',()=>{this.draft=Number(this.input.value);this.paint(this.draft,this.duration());});
        this.input.addEventListener('change',()=>{this.commit(Number(this.input.value));this.dragging=false;this.draft=null;this.render();});
        options.back.onclick=()=>this.jump(-10);options.forward.onclick=()=>this.jump(10);
    }
    matches(value) {return value && value.trackUri===this.o.state().uri;}
    duration() {return this.matches(this.remote)&&this.remote.durationMs>0?this.remote.durationMs/1000:Number(this.o.state().duration)||0;}
    position() {
        const state=this.o.state();
        if(this.matches(this.remote))return this.remote.positionMs/1000+(this.remote.phase==='playing'?(this.now()-this.remoteAt)/1000:0);
        return (Number(state.seek)||0)/1000+(state.status==='play'?(this.now()-this.o.receivedAt())/1000:0);
    }
    receive(value) {
        if(!value || !Number.isFinite(value.revision))return;
        if(this.remote && value.session===this.remote.session && value.revision<this.remote.revision)return;
        if(!this.matches(value))return;
        this.remote=value;this.remoteAt=this.now();
        if(this.pending && this.pending.uri===value.trackUri) {
            if(value.phase==='error')this.pending=null;
            else if(['playing','paused'].includes(value.phase) && Math.abs(value.positionMs/1000-this.pending.target)<5)this.pending=null;
        }
        this.render();
    }
    commit(value) {
        const duration=this.duration();
        if(this.input.disabled || !Number.isFinite(value) || duration<=0)return false;
        const target=Math.max(0,Math.min(value,Math.max(0,duration-1)));
        const previous=this.pending;
        this.pending={target:target,uri:this.o.state().uri,at:this.now()};
        if(!this.o.send(target)){this.pending=previous;return false;}
        return true;
    }
    jump(amount) {this.commit((this.pending?this.pending.target:this.position())+amount);this.render();}
    paint(value,duration) {
        value=Math.max(0,Math.min(value,duration||value));
        this.input.value=String(value);this.input.style.setProperty('--seek-fill',(duration?value/duration*100:0)+'%');
        this.o.elapsed.textContent=this.o.format(value);this.o.duration.textContent=duration?this.o.format(duration):'—';
        this.input.setAttribute('aria-valuetext',this.o.format(value)+(duration?' z '+this.o.format(duration):''));
    }
    render() {
        const state=this.o.state(),duration=this.duration();
        if(this.pending && this.pending.uri!==state.uri)this.pending=null;
        if(this.remote && !this.matches(this.remote))this.remote=null;
        const youtube=state.service==='youtube2'||String(state.uri||'').startsWith('youtube2/');
        const enabled=this.o.connected() && !this.o.grouped() && duration>0 && (!state.stream || youtube);
        this.input.disabled=this.o.back.disabled=this.o.forward.disabled=!enabled;
        this.input.max=String(Math.max(0,duration-1));
        let message='';
        if(this.pending) {
            if(this.now()-this.pending.at>30000){this.pending=null;message='Nie potwierdzono przewinięcia. Spróbuj ponownie.';}
            else message='Przewijanie do '+this.o.format(this.pending.target)+'…';
        }
        if(!message && this.matches(this.remote)) {
            const phase=this.remote.phase;
            if(['resolving','buffering','seeking'].includes(phase))message=phase==='resolving'?'Otwieranie nagrania…':'Przewijanie do '+this.o.format(this.remote.requestedMs/1000)+'…';
            if(phase==='error')message=this.remote.error||'Nie udało się przewinąć.';
        }
        this.o.status.textContent=message;
        if(this.dragging || this.draft!==null)return;
        this.paint(this.pending?this.pending.target:this.position(),duration);
    }
}
root.StudioSeek=StudioSeek;
if(typeof module!=='undefined')module.exports=StudioSeek;
})(typeof window!=='undefined'?window:global);
