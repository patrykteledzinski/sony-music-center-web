'use strict';
// Regression checks runnable on the Pi's Node 8, without contacting YouTube.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const path=process.argv[2] || __dirname+'/scraper.js';
const source=fs.readFileSync(path,'utf8');
function loader(html) {
    const urls=[];
    function request(url,callback) {urls.push(url);setImmediate(()=>callback(null,{statusCode:200},html));}
    request.defaults=options=>{assert.strictEqual(options.timeout,15000);return request;};
    const module={exports:{}};
    vm.runInNewContext(source,{module:module,console:{error(){},log(){}},require(name){
        if(name==='request')return request;
        if(name==='url')return require('url');
        if(name==='./package.json')return {version:'test'};
        throw new Error('Unexpected dependency: '+name);
    }});
    return {scraper:module.exports,urls:urls};
}
const video={videoId:'test123',title:{runs:[{text:'Test track'}]},
    navigationEndpoint:{commandMetadata:{webCommandMetadata:{url:'/watch?v=test123'}}},
    thumbnail:{thumbnails:[{url:'https://example.com/art.jpg'}]},
    ownerText:{runs:[{text:'Artist',navigationEndpoint:{commandMetadata:{webCommandMetadata:{url:'/@artist'}}}}]},
    lengthText:{simpleText:'3:00'}};
const data={contents:{twoColumnSearchResultsRenderer:{primaryContents:{sectionListRenderer:{
    contents:[{itemSectionRenderer:{contents:[{videoRenderer:video}]}}]
}}}}};
const valid=loader('<script>var ytInitialData = '+JSON.stringify(data)+';</script>');
const timer=setTimeout(()=>{console.error('Parser did not settle');process.exit(1);},2000);
valid.scraper.youtube({query:'Test',type:'video'}).then(result=>{
    assert.strictEqual(result.results.length,1);
    assert.strictEqual(result.results[0].video.title,'Test track');
    assert.strictEqual(valid.urls.length,1,'Do not request removed dynamic filter groups');
    assert(valid.urls[0].includes('sp='));
    const invalid=loader('<html>Changed response without ytInitialData</html>');
    return invalid.scraper.youtube({query:'Test',type:'video'}).then(()=>{
        throw new Error('Malformed HTML must reject');
    },error=>{assert(error.message.includes('YouTube changed'));});
}).then(()=>{clearTimeout(timer);console.log('PASS: removed filters, missing API key, video results, malformed response rejection');})
.catch(error=>{clearTimeout(timer);console.error(error);process.exit(1);});
