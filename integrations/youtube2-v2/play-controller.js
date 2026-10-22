'use strict';
const libQ=require('kew');
const http=require('http');
const parseUrl=require('url').parse;
const yt2=require(yt2PluginLibRoot+'/youtube2');
const VideoHelper=require(yt2PluginLibRoot+'/helper/video');
const SeekEngine=require('./seek-engine').SeekEngine;
const BASE='http://127.0.0.1:18888';
function compatible(promise) {const d=libQ.defer();Promise.resolve(promise).then(v=>d.resolve(v),e=>d.reject(e));return d.promise;}
class PlayController {
    constructor() {
        this.mpdPlugin=yt2.getMpdPlugin();
        const mpd=this.mpdPlugin;
        // Install one adapter only. The original reader remains untouched and reusable.
        if(!mpd.youtubeSeekAdapter) {
            const original=mpd.getState.bind(mpd);
            const parse=mpd.parseTrackInfo.bind(mpd);
            mpd.parseTrackInfo=function(info){const result=parse(info||{})||{};result.trackType=String(result.trackType||'');return result;};
            const adapter={raw:original,owner:null};mpd.youtubeSeekAdapter=adapter;
            const push=mpd.pushState.bind(mpd);
            mpd.pushState=function(state){
                const owner=adapter.owner;
                if(owner && owner.track && owner.io.selected(owner.track)) {
                    if(['resolving','seeking','buffering','paused','error'].indexOf(owner.phase)>=0)return libQ.resolve();
                    if(owner.active && owner.phase==='playing' && state.uri!==owner.active.url && state.status!=='stop')return libQ.resolve();
                }
                return push(state);
            };
            mpd.getState=function(){return compatible(Promise.resolve(adapter.raw()).then(raw=>adapter.owner?adapter.owner.normalize(raw):raw));};
        }
        this.adapter=mpd.youtubeSeekAdapter;
        this.engine=new SeekEngine({
            resolve:id=>this._resolve(id),abortResolve:()=>this._abortResolve(),
            url:(id,ms,session,revision)=>BASE+'/v2/audio/'+session+'/'+revision+'/'+id+'/'+ms+'.wav',
            cancel:session=>this._json('/v2/cancel/'+session,'POST').catch(()=>{}),
            replace:url=>this._replace(url),stop:()=>Promise.resolve(mpd.stop()),
            read:()=>Promise.resolve(this.adapter.raw()),
            selected:track=>this._selected(track),
            metadata:(track,metadata)=>{
                this.lastPlaybackInfo={videoId:metadata.videoId,track:track,
                    position:yt2.getStateMachine().getState().position,
                    relatedVideos:[],upNextVideoId:null};
            },
            publish:(state,track)=>this._publish(state,track),
            status:status=>{
                const machine=yt2.getStateMachine();
                if(['resolving','seeking','buffering'].indexOf(status.phase)>=0) {
                    machine.currentStatus=this.engine.desiredPlaying?'play':'pause';machine.stopPlaybackTimer();
                    if(machine.consumeState)machine.consumeState.status=machine.currentStatus;
                }
                if(status.phase==='error') {
                    machine.currentStatus='pause';machine.currentSeek=status.positionMs;machine.stopPlaybackTimer();
                    if(machine.consumeState)Object.assign(machine.consumeState,{status:'pause',seek:status.positionMs});
                    machine.pushState();
                }
                const router=yt2.getStateMachine().commandRouter;
                if(router && typeof router.broadcastMessage==='function')router.broadcastMessage('pushYoutubeSeek',status);
                const key=status.phase+':'+status.revision;
                if(this.lastLogKey!==key) {
                    this.lastLogKey=key;
                    yt2.getLogger().info('[youtube2-seek] '+JSON.stringify({phase:status.phase,revision:status.revision,positionMs:Math.round(status.positionMs),requestedMs:status.requestedMs}));
                }
            },
            error:message=>yt2.toast('error',message),
            ended:state=>{
                const router=yt2.getStateMachine().commandRouter;
                if(router && typeof router.servicePushState==='function')router.servicePushState(state,'mpd');
            }
        });
    }
    _installClock() {
        const clock=yt2.getStateMachine();
        if(!clock || typeof clock.startPlaybackTimer!=='function')return;
        if(!clock.youtubeSeekClockAdapter) {
            const adapter=this.adapter;
            const start=clock.startPlaybackTimer.bind(clock);
            const increase=typeof clock.increasePlaybackTimer==='function'?clock.increasePlaybackTimer.bind(clock):null;
            const ownsClock=()=>adapter.owner && !adapter.owner.closed && adapter.owner.track && adapter.owner.io.selected(adapter.owner.track) && adapter.owner.phase!=='idle';
            clock.startPlaybackTimer=function(ms){if(ownsClock()){this.runPlaybackTimer=false;return libQ.resolve();}return start(ms);};
            if(increase)clock.increasePlaybackTimer=function(){if(ownsClock()){this.runPlaybackTimer=false;return libQ.resolve();}return increase();};
            clock.youtubeSeekClockAdapter=adapter;
        }
    }
    _selected(track) {
        const machine=yt2.getStateMachine();
        if(machine.isVolatile || machine.isUpnp || (machine.consumeUpdateService && machine.consumeUpdateService!=='mpd'))return false;
        const current=typeof machine.getTrack==='function'?machine.getTrack(machine.currentPosition):null;
        return current?current.uri===track.uri:machine.getState().uri===track.uri;
    }
    _json(path,method,remember) {
        return new Promise((resolve,reject)=>{
            const options=parseUrl(BASE+path);options.method=method||'GET';
            const request=http.request(options,response=>{
                let body='';response.setEncoding('utf8');
                response.on('data',part=>{body+=part;if(body.length>1048576)request.destroy(new Error('Zbyt duża odpowiedź resolvera.'));});
                response.on('end',()=>{try {const value=JSON.parse(body);if(response.statusCode!==200)throw new Error(value.error||'Błąd resolvera.');resolve(value);}catch(error){reject(error);}});
                response.on('error',reject);
            });
            request.setTimeout(85000,()=>{reject(new Error('Przekroczono czas otwierania filmu.'));request.abort();});
            request.on('abort',()=>reject(new Error('Zmieniono nagranie.')));
            request.on('error',reject);request.end();if(remember)this.resolveRequest=request;
        });
    }
    _resolve(id) {if(!/^[A-Za-z0-9_-]{11}$/.test(id))return Promise.reject(new Error('Nieprawidłowy film.'));return this._json('/v2/info/'+id,'GET',true);}
    _abortResolve() {if(this.resolveRequest){this.resolveRequest.abort();this.resolveRequest=null;}}
    _replace(url) {
        this.adapter.owner=this.engine;
        yt2.getStateMachine().setConsumeUpdateService('mpd',true,false);
        // MPD processes the list atomically, so observers cannot see an empty queue.
        return Promise.resolve(this.mpdPlugin.sendMpdCommandArray([
            {command:'stop',parameters:[]},{command:'clear',parameters:[]},
            {command:'addid',parameters:[url]},{command:'play',parameters:[]}
        ]));
    }
    _publish(state,track) {
        if(!track || !this._selected(track))return;
        const machine=yt2.getStateMachine();
        if(!machine.isConsume || machine.consumeUpdateService!=='mpd')machine.setConsumeUpdateService('mpd',true,false);
        machine.consumeState=Object.assign({},machine.consumeState,{
            status:state.status,title:track.title||track.name,artist:track.artist,album:track.album,
            albumart:track.albumart,uri:track.uri,trackType:'wav',seek:state.seek,
            duration:state.duration,stream:false,service:'youtube2',samplerate:'44.1 kHz',bitdepth:'16 bit',channels:2
        });
        machine.currentSeek=state.seek;machine.currentStatus=state.status;
        // YouTube owns its clock. Legacy timers can add a queued tick even after pause.
        machine.stopPlaybackTimer();
        machine.pushState();
    }
    clearAddPlayTrack(track) {
        this._installClock();
        const match=/^youtube2\/video@(?:autoplay=1@)?videoId=([A-Za-z0-9_-]{11})$/.exec(track.uri||'');
        if(!match)return libQ.reject(new Error('Nieprawidłowy film.'));
        this.adapter.owner=this.engine;
        return compatible(this.engine.load(track,match[1]));
    }
    seek(milliseconds) {return compatible(this.engine.seek(Number(milliseconds)));}
    pause() {return compatible(this.engine.pause());}
    resume() {
        if(!this.engine.metadata && this.engine.track)return this.clearAddPlayTrack(this.engine.track);
        return compatible(this.engine.resume());
    }
    stop() {return compatible(this.engine.stop());}

    _handleAutoplay() {
        let self = this;
        let lastPlaybackItemPosition = self._getLastPlaybackItemPosition();

        if (lastPlaybackItemPosition < 0) {
            return;
        }

        let stateMachine = yt2.getStateMachine(),
            state = stateMachine.getState(),
            isLastTrack = stateMachine.getQueue().length - 1 === lastPlaybackItemPosition,
            currentPositionChanged = state.position !== lastPlaybackItemPosition, // true if client clicks on another item in the queue
            autoplayPrefix = 'youtube2/video@autoplay=1@videoId=',
            queueItemRemoved = false;
           
        if (self.lastPlaybackInfo.track.service === 'youtube2' && self.lastPlaybackInfo.track.uri.startsWith(autoplayPrefix)) {
            yt2.getLogger().info(`[youtube2-play] _handleAutoPlay(): Removing autoplayed video from queue (position ${lastPlaybackItemPosition})`);
            stateMachine.removeQueueItem({ value: lastPlaybackItemPosition });
            queueItemRemoved = true;
        }

        let autoplay;
        let noAutoplayConditions = !yt2.getConfigValue('autoplay', false) || currentPositionChanged || !isLastTrack || state.random || state.repeat || state.repeatSingle;
        
        if (noAutoplayConditions) {
            autoplay = libQ.resolve(null);
        }
        else {
            autoplay = self._getAutoplayVideoId();
        }

        autoplay.then( (autoplayVideoId) => {
            if (autoplayVideoId === null) {
                delete self.lastPlaybackInfo.mixPlaylist;
                if (queueItemRemoved && !currentPositionChanged && !isLastTrack) { 
                    // Playback stopped without client request
                    // StateMachine will try to play the next track after this function exits, but if
                    // autoplay item has been removed from queue, StateMachine will somehow obtain 
                    // track info with null fields (even at the same track position as the autoplay item
                    // prior to removal). We force StateMachine to play the next track here.
                    // Note that we don't need to do this if the playback was stopped because
                    // client clicked on another item in the queue, because the StateMachine
                    // would obtain the correct track info before this function is entered (i.e.
                    // before autoplay item is removed).
                    // Hacky, but seems to work.
                    stateMachine.play(state.position);
                }
                if (!noAutoplayConditions) {
                    yt2.getLogger().info('[youtube2-play] _handleAutoplay(): No video available for autoplay');
                }
                return;
            }
            else {
                // Add autoplay video to queue
                let autoplayQueueItem = {
                    service: 'youtube2',
                    uri: 'youtube2/video@autoplay=1@videoId=' + autoplayVideoId
                };
                stateMachine.addQueueItems([autoplayQueueItem]).then( (result) => {
                    yt2.getLogger().info(`[youtube2-play] _handleAutoplay(): Start autoplaying video ${autoplayVideoId}`);

                    // Hacky way to prevent autoplay label from displaying
                    // in the player status (the label contains html tags that
                    // will be shown as escaped string)
                    let queue = stateMachine.getQueue();
                    let addedQueueItem = queue[result.firstItemIndex];
                    let titleWithAutoplayLabel = addedQueueItem.name;
                    addedQueueItem.name = addedQueueItem.title; // 'title' has no autoplay label

                    stateMachine.play(result.firstItemIndex).then( () => {
                        // Reinstate queue item name
                        addedQueueItem.name = titleWithAutoplayLabel;
                    });
                });
            }
        });
    }

    _getLastPlaybackItemPosition() {
        if (!this.lastPlaybackInfo) {
            return -1;
        }

        let queue = yt2.getStateMachine().getQueue(),
            uri = this.lastPlaybackInfo.track.uri,
            upTo = this.lastPlaybackInfo.position;

        for (let i = upTo; i >= 0; i--) {
            let queueItem = queue[i];
            if (queueItem && queueItem.uri === uri) {
                return i;
            }
        }

        return -1;
    }

    _getAutoplayVideoId() {
        let self = this;
        let defer = libQ.defer();
        
        // Return the video Id for autoplay in the following order of preference:
        // 1. If Mix Playlist available, the Id of the next video in the list
        // 2. If related videos available, the Id of a random video in the list
        // 3. The Id of the 'Up Next' video (last resort - can easily encounter a loop)
        let getMixPlaylistInfo;
        let lastMixPlaylist = self.lastPlaybackInfo.mixPlaylist;
        if (lastMixPlaylist && lastMixPlaylist.items.length > lastMixPlaylist.currentIndex + 1) {
            getMixPlaylistInfo = libQ.resolve(lastMixPlaylist);
        }
        else if (lastMixPlaylist) {
            getMixPlaylistInfo = VideoHelper.refreshMixPlaylist(lastMixPlaylist, self.lastPlaybackInfo.videoId);
        }
        else {
            getMixPlaylistInfo = VideoHelper.getMixPlaylist(self.lastPlaybackInfo.videoId);
        }
        getMixPlaylistInfo.then( (mixPlaylist) => {
            if (mixPlaylist && mixPlaylist.items.length > mixPlaylist.currentIndex + 1) {
                mixPlaylist.currentIndex++;
                let nextVideoId = mixPlaylist.items[mixPlaylist.currentIndex].id;
                self.lastPlaybackInfo.mixPlaylist = mixPlaylist;
                yt2.getLogger().info(`[youtube2-play] _getAutoplayVideoId(): resolving to next video in Mix Playlist (video Id: ${nextVideoId} ... Mix playlist Id: ${mixPlaylist.id} ... index: ${mixPlaylist.currentIndex} / ${mixPlaylist.items.length - 1})`);
                defer.resolve(nextVideoId);
            }
            else if (self.lastPlaybackInfo.relatedVideos && self.lastPlaybackInfo.relatedVideos.length > 0) {
                delete self.lastPlaybackInfo.mixPlaylist;
                let rndIndex = self._getRandomInt(0, self.lastPlaybackInfo.relatedVideos.length);
                let nextVideoId = self.lastPlaybackInfo.relatedVideos[rndIndex].id;
                yt2.getLogger().info(`[youtube2-play] _getAutoplayVideoId(): resolving to related video (Id: ${nextVideoId})`);
                defer.resolve(nextVideoId);
            }
            else {
                delete self.lastPlaybackInfo.mixPlaylist;
                yt2.getLogger().info(`[youtube2-play] _getAutoplayVideoId(): resolving to 'Up Next' video (Id: ${self.lastPlaybackInfo.upNextVideoId})`);
                defer.resolve(self.lastPlaybackInfo.upNextVideoId);
            }
        });

        return defer.promise;
    }

    // https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Math/random
    _getRandomInt(min, max) {
        min = Math.ceil(min);
        max = Math.floor(max);
        return Math.floor(Math.random() * (max - min) + min); //The maximum is exclusive and the minimum is inclusive
    }

}

module.exports = PlayController;