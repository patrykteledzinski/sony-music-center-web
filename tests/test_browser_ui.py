"""Optional JavaScript behavior checks: run with QuickJS installed."""
import unittest
import app
try:
    import quickjs
except ImportError:
    quickjs = None


@unittest.skipIf(quickjs is None, 'optional QuickJS is not installed')
class BrowserUITests(unittest.TestCase):
    def setUp(self):
        self.ctx = quickjs.Context()
        self.ctx.eval('''
const elements = {};
const document = {
 getElementById(id) {
  if (!elements[id]) elements[id] = {value:0, style:{}, options:[], disabled:false,
   classList:{add(){},remove(){},toggle(){}}, listeners:{},
   addEventListener(event, fn){this.listeners[event]=fn;},
   matches(){return false;}, focus(){}, textContent:"", innerHTML:"",
   readyState:0, paused:true, duration:300, currentTime:0,
   pause(){this.paused=true;}, load(){this.readyState=0;},
   removeAttribute(name){delete this[name];},
   play(){this.paused=false;return Promise.resolve();}};
  return elements[id];
 },
 querySelectorAll(){return [];},
 createElement(){return {textContent:"",innerHTML:""};}
};
function setTimeout(){return 1;}
function clearTimeout(){}
function setInterval(){}
function fetch(){return new Promise(()=>{});}
function check(value, message){if(!value) throw Error(message);}
''')
        self.ctx.eval(app.INDEX_HTML.split('<script>')[1].split('</script>')[0])
        self.ctx.eval('''
currentStatus = {online:true, browser_source:"one.mp3",uri:"sony-uri",
 position:42,duration_seconds:300,state:"PLAYING"};
browserStatusAt=Date.now();
let requests=[];
api = async (url, options) => {
 requests.push(url);
 return {source:currentStatus.browser_source,url:"/media/one.mp3"};
};
''')

    def drain(self):
        for _ in range(100):
            if not self.ctx.execute_pending_job():
                return
        self.fail('JavaScript promise loop did not settle')

    def enable(self):
        self.ctx.eval('startBrowserListening();')
        self.drain()
        self.ctx.eval('browserAudio.readyState=1; browserAudio.listeners.loadedmetadata();')
        self.drain()

    def test_follow_position_pause_stop_and_independent_volume(self):
        self.enable()
        self.ctx.eval('''
check(browserEnabled && !browserAudio.paused, "play on opt in");
check(Math.abs(browserAudio.currentTime-42)<1,"initial position");
currentStatus={...currentStatus,state:"PAUSED_PLAYBACK",position:80};
syncBrowserAudio(currentStatus);
check(browserAudio.paused && browserAudio.currentTime===80,"pause and seek");
document.getElementById("browserVolume").listeners.input({target:{value:"25"}});
check(browserAudio.volume===0.25,"local volume");
check(requests.every(url=>url==="/api/browser/audio"),"must not control Sony");
stopBrowserListening();
check(!browserEnabled && browserAudio.paused && !browserAudio.src,"disabled");
''')

    def test_autoplay_block_can_be_resumed_by_user(self):
        self.ctx.eval('''
browserAudio.play = () => Promise.reject(Object.assign(new Error("blocked"),{name:"NotAllowedError"}));
''')
        self.enable()
        self.ctx.eval('''
check(browserBlocked,"autoplay blocked state");
check(!document.getElementById("browserListen").disabled,"resume button available");
browserAudio.play = () => {browserAudio.paused=false;return Promise.resolve();};
startBrowserListening();
''')
        self.drain()
        self.ctx.eval('check(!browserBlocked && !browserAudio.paused,"resume from click");')

    def test_stale_preparation_and_disabled_audio_never_start(self):
        self.ctx.eval('''
let pending=[];
api=(url,options)=>new Promise(resolve=>pending.push(resolve));
startBrowserListening();
currentStatus={...currentStatus,browser_source:"two.mp3",uri:"second-uri"};
syncBrowserAudio(currentStatus);
pending[0]({source:"one.mp3",url:"/media/one.mp3"});
''')
        self.drain()
        self.ctx.eval('''
check(!browserAudio.src,"old source discarded");
stopBrowserListening();
pending[1]({source:"two.mp3",url:"/media/two.mp3"});
''')
        self.drain()
        self.ctx.eval('check(!browserEnabled && !browserAudio.src && browserAudio.paused,"disable wins");')
