import {test} from 'node:test';
import assert from 'node:assert/strict';
import {postUrl,profile,limit,collect,dateBounds} from './threads.js';
test('rejects credentials, other domains, ports, invalid limits and usernames',()=>{
 for(const u of ['http://www.threads.com/@nasa/post/ABC','https://evil.com/@nasa/post/ABC','https://x:y@www.threads.com/@nasa/post/ABC','https://www.threads.com:444/@nasa/post/ABC','https://www.threads.com/messages'])assert.throws(()=>postUrl(u));
 for(const n of [0,11,2.5,'bad'])assert.throws(()=>limit(n));assert.throws(()=>profile('../messages'));assert.equal(postUrl('https://threads.net/@nasa/post/ABC'),'https://www.threads.com/@nasa/post/ABC');
});
test('never treats login or unrelated page as content',async()=>{
 for(const payload of [{status:'auth',rows:[],finalUrl:'https://www.instagram.com/accounts/login/'},{status:'ok',rows:[{text:'private feed',url:'x'}],finalUrl:'https://www.threads.com/'}])await assert.rejects(()=>collect({goto:async()=>{},evaluate:async()=>payload},'https://www.threads.com/@nasa',1));
});
test('deduplicates rows and honors relevance and requested bound',async()=>{
 const row={url:'https://www.threads.com/@nasa/post/ABC',text:'沈伯洋 example'};
 const page={goto:async()=>{},evaluate:async()=>({status:'ok',finalUrl:'https://www.threads.com/search',query:'沈伯洋',rows:[row,row,{url:'other',text:'unrelated'}]})};
 assert.equal((await collect(page,'https://www.threads.com/search?q=沈伯洋',1,'沈伯洋')).length,1);
});
test('rejects changed query; retries only a transient navigation rejection once',async()=>{
 await assert.rejects(()=>collect({goto:async()=>{},evaluate:async()=>({status:'ok',finalUrl:'https://www.threads.com/search',query:'another',rows:[]})},'https://www.threads.com/search?q=x',1,'x'));
 let navigations=0;const page={goto:async()=>{if(++navigations===1)throw Error('Navigation rejected');},evaluate:async()=>({status:'ok',finalUrl:'https://www.threads.com/@nasa',rows:[{url:'https://www.threads.com/@nasa/post/ABC',text:'visible'}]})};
 assert.equal((await collect(page,'https://www.threads.com/@nasa',1)).length,1);assert.equal(navigations,2);
});
test('Taipei date range includes full end day and rejects invalid ranges',async()=>{
 const [start,end]=dateBounds('2026-10-13','2026-10-13');
 assert.equal(start,Date.parse('2026-10-12T16:00:00Z'));assert.equal(end,Date.parse('2026-10-13T16:00:00Z'));
 assert.throws(()=>dateBounds('2026-02-31'));assert.throws(()=>dateBounds('2026-10-13','2026-10-01'));
 const page={goto:async()=>{},evaluate:async()=>({status:'ok',finalUrl:'https://www.threads.com/search',query:'test',rows:[{url:'old',text:'test',published_at:'2026-10-12T15:00:00Z'},{url:'unknown',text:'test',published_at:null},{url:'valid',text:'test',published_at:'2026-10-13T01:00:00Z'}]})};
 assert.equal((await collect(page,'https://www.threads.com/search?q=test',1,'test',{since:'2026-10-13',until:'2026-10-13'}))[0].url,'valid');
});
