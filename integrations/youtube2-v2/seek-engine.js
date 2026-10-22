'use strict';
// Node 8 compatible. All player mutations are serialized; snapshots are immutable.
const crypto=require('crypto');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
class SeekEngine {
    constructor(io,options) {
        this.io=io;this.options=Object.assign({debounceMs:140,pollMs:1000,readyMs:25000},options);
        this.epoch=0;this.revision=0;this.phase='idle';this.desiredPlaying=false;
        this.metadata=null;this.track=null;this.active=null;this.confirmed=null;
        this.chain=Promise.resolve();this.closed=false;
        this.timer=setInterval(()=>this.poll(),this.options.pollMs);
        if(this.timer.unref)this.timer.unref();
    }
    status() {
        return {session:this.session||'',revision:this.revision,trackUri:this.track&&this.track.uri,
            phase:this.phase,durationMs:this.metadata?this.metadata.durationMs:0,
            positionMs:this.confirmed?this.confirmed.seek:0,requestedMs:this.targetMs||0,lastSeekMs:this.metadata?Math.max(0,this.metadata.durationMs-1000):0,
            error:this.error||null};
    }
    emit() {if(this.io.status)this.io.status(this.status());}
    valid(epoch,revision) {return this.epoch===epoch && this.revision===revision && !this.closed;}
    enqueue(fn) {const result=this.chain.then(fn);this.chain=result.catch(()=>{});return result;}
    async load(track,videoId) {
        const old=this.session;
        const epoch=++this.epoch;this.revision++;this.session=crypto.randomBytes(12).toString('hex');
        this.track=track;this.metadata=null;this.active=null;this.confirmed=null;
        this.targetMs=0;this.desiredPlaying=true;this.phase='resolving';this.error=null;this.emit();
        if(this.io.abortResolve)this.io.abortResolve();
        if(old)this.io.cancel(old).catch(()=>{});
        try {
            const metadata=await this.io.resolve(videoId);
            if(this.epoch!==epoch)return;
            if(this.io.selected && !this.io.selected(track)){this.release();return;}
            if(metadata.videoId!==videoId || !Number.isFinite(metadata.durationMs) || metadata.durationMs<=0 || !metadata.seekable)
                throw new Error('To nagranie nie udostępnia przewijalnego audio.');
            this.metadata=Object.freeze(metadata);
            if(this.io.metadata)this.io.metadata(track,metadata);
            return this.schedule(false);
        } catch(error) {
            if(this.epoch!==epoch)return;
            if(this.phase!=='error')this.fail(error);throw error;
        }
    }
    seek(ms) {
        if(!this.metadata)return Promise.reject(new Error('Poczekaj na otwarcie nagrania.'));
        if(!Number.isFinite(ms))return Promise.reject(new Error('Nieprawidłowa pozycja.'));
        // Legacy MPD rounds duration to whole seconds; retain a decodable final second.
        this.targetMs=Math.round(Math.max(0,Math.min(ms,this.metadata.durationMs-1000)));
        return this.schedule(true);
    }
    schedule(debounce) {
        const epoch=this.epoch,revision=++this.revision;
        this.phase=this.desiredPlaying?'seeking':'paused';this.error=null;this.emit();
        return this.enqueue(async()=>{
            if(!this.valid(epoch,revision))return;
            if(this.io.selected && !this.io.selected(this.track)){this.release();return;}
            if(debounce)await sleep(this.options.debounceMs);
            if(!this.valid(epoch,revision))return;
            const descriptor=Object.freeze({epoch:epoch,revision:revision,session:this.session,
                videoId:this.metadata.videoId,trackUri:this.track.uri,offsetMs:this.targetMs,
                durationMs:this.metadata.durationMs,
                url:this.io.url(this.metadata.videoId,this.targetMs,this.session,revision)});
            this.active=descriptor;
            if(!this.desiredPlaying) {
                await this.io.stop();
                if(!this.valid(epoch,revision))return;
                this.confirmed=this.staged(descriptor);this.phase='paused';this.publish(this.confirmed);return;
            }
            this.phase='buffering';this.emit();
            try {
                await this.io.replace(descriptor.url);
                if(!this.valid(epoch,revision))return;
                const deadline=Date.now()+this.options.readyMs;
                while(this.valid(epoch,revision)) {
                    const raw=await this.io.read();
                    if(!this.valid(epoch,revision))return;
                    // A play ACK or the core's optimistic timer does not confirm audio.
                    if(raw.uri===descriptor.url && raw.status==='play' && Number(raw.duration)>0 && Number(raw.seek)>0) {
                        this.phase='playing';this.confirmed=this.normalize(raw);this.targetMs=this.confirmed.seek;
                        this.publish(this.confirmed);return;
                    }
                    if(raw.error)throw new Error('MPD: '+raw.error);
                    if(Date.now()>deadline)throw new Error('Audio nie rozpoczęło odtwarzania. Spróbuj ponownie.');
                    await sleep(180);
                }
            } catch(error) {if(this.valid(epoch,revision)){this.fail(error);throw error;}}
        });
    }
    staged(descriptor) {
        return {uri:descriptor.url,status:'pause',seek:descriptor.offsetMs,
            duration:descriptor.durationMs/1000,isStreaming:false,trackType:'wav'};
    }
    normalize(raw) {
        const active=this.active;
        if(!raw)return raw;
        if(this.io.selected && this.track && !this.io.selected(this.track))return raw;
        if(['resolving','seeking','buffering'].indexOf(this.phase)>=0) {
            return Object.assign({},raw,{status:'pause',seek:this.confirmed?this.confirmed.seek:0,
                duration:this.metadata?this.metadata.durationMs/1000:raw.duration,isStreaming:false});
        }
        if(!active)return raw;
        const owned=typeof raw.uri==='string' && raw.uri.indexOf('/v2/audio/'+active.session+'/')>=0;
        if(raw.uri!==active.url && !owned)return raw;
        if(this.phase==='paused' && !this.desiredPlaying)return this.staged(active);
        if(raw.uri!==active.url)return Object.assign({},this.confirmed||this.staged(active),{status:'pause'});
        const ready=Number(raw.duration)>0;
        return Object.assign({},raw,{duration:active.durationMs/1000,
            seek:Math.min(active.durationMs,active.offsetMs+(Number(raw.seek)||0)),
            isStreaming:false,status:ready?raw.status:'pause'});
    }
    publish(state) {if(this.io.publish)this.io.publish(state,this.track);this.emit();}
    fail(error) {this.error=error.message||String(error);this.phase='error';this.emit();if(this.io.error)this.io.error(this.error);}
    async poll() {
        if(this.phase==='paused'){this.emit();return;}
        if(this.polling || !this.active || this.phase!=='playing')return;
        if(this.io.selected && !this.io.selected(this.track)){this.release();return;}
        const epoch=this.epoch,revision=this.revision,active=this.active;this.polling=true;
        try {
            const raw=await this.io.read();
            if(!this.valid(epoch,revision) || this.active!==active)return;
            if(raw.uri!==active.url && raw.status==='play') {
                if(raw.uri && raw.uri.indexOf('/v2/audio/'+active.session+'/')<0)this.release();
                return;
            }
            if(raw.status==='play' && Number(raw.duration)>0) {
                // Ignore stale elapsed samples and update only from the current source.
                this.confirmed=this.normalize(raw);this.targetMs=this.confirmed.seek;this.publish(this.confirmed);
            } else if(raw.status==='stop') {
                this.phase='stopped';this.confirmed=Object.assign({},this.confirmed,{status:'stop'});
                this.emit();
                if(this.io.ended)this.io.ended(this.normalize(raw));
            }
        } catch(error) {if(this.valid(epoch,revision))this.fail(error);}
        finally {this.polling=false;}
    }
    pause() {
        if(!this.metadata){this.desiredPlaying=false;this.emit();return Promise.resolve();}
        if(this.phase==='playing' && this.confirmed)this.targetMs=this.confirmed.seek;
        this.desiredPlaying=false;return this.schedule(false);
    }
    resume() {if(!this.metadata)return Promise.reject(new Error('Wybierz nagranie.'));this.desiredPlaying=true;return this.schedule(false);}
    stop() {
        const session=this.session;++this.epoch;++this.revision;this.desiredPlaying=false;
        this.phase='stopped';this.active=null;this.confirmed=null;this.emit();
        if(this.io.abortResolve)this.io.abortResolve();
        if(session)this.io.cancel(session).catch(()=>{});
        return this.enqueue(()=>this.io.stop());
    }
    release() {++this.epoch;++this.revision;const session=this.session;this.active=null;this.metadata=null;this.phase='idle';if(session)this.io.cancel(session).catch(()=>{});this.emit();}
    close() {this.closed=true;clearInterval(this.timer);this.release();}
}
module.exports={SeekEngine:SeekEngine};
