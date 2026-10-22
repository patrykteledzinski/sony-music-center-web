'use strict';
// Preserve the original module's stream / format helpers. Replace only metadata
// extraction with the local server's modern resolver; Node 8 stays unchanged.
const original=require('./index.legacy.js');
const http=require('http');
const endpoint='http://192.168.8.50:8088/api/youtube/info/';
function resolve(input) {
    let id;
    try {id=/^[A-Za-z0-9_-]{11}$/.test(input)?input:original.getVideoID(input);}
    catch(error){return Promise.reject(error);}
    if(!/^[A-Za-z0-9_-]{11}$/.test(id))return Promise.reject(new Error('Invalid YouTube video ID'));
    return new Promise((resolve,reject)=>{
        const req=http.get(endpoint+encodeURIComponent(id),response=>{
            let body='';response.setEncoding('utf8');
            response.on('data',chunk=>{body+=chunk;if(body.length>2*1024*1024){req.destroy();reject(new Error('Resolver response too large'));}});
            response.on('end',()=>{
                try{const data=JSON.parse(body);if(response.statusCode!==200||data.error)throw new Error(data.error||'YouTube resolver failed');resolve(data);}
                catch(error){reject(error);}
            });
            response.on('error',reject);
        });
        req.setTimeout(60000,()=>{req.destroy();reject(new Error('YouTube resolver timeout'));});
        req.on('error',reject);
    });
}
original.getInfo=resolve;
original.getBasicInfo=resolve;
module.exports=original;
