import {cli,Strategy} from '@jackwener/opencli/registry';
import {ArgumentError,AuthRequiredError,CommandExecutionError,EmptyResultError} from '@jackwener/opencli/errors';
export function limit(value=10){const n=Number(value);if(!Number.isInteger(n)||n<1||n>10)throw new ArgumentError('limit must be 1..10');return n;}
export function profile(value){const name=String(value??'').replace(/^@/,'');if(!/^[a-zA-Z0-9._]{1,60}$/.test(name))throw new ArgumentError('Invalid public username');return name;}
export function postUrl(value){let u;try{u=new URL(value);}catch{throw new ArgumentError('Invalid post URL');}if(u.protocol!=='https:'||!['www.threads.com','threads.com','www.threads.net','threads.net'].includes(u.hostname)||u.username||u.password||u.port||!/^\/@[a-zA-Z0-9._]+\/post\/[a-zA-Z0-9_-]+\/?$/.test(u.pathname))throw new ArgumentError('Use a public Threads post URL');return 'https://www.threads.com'+u.pathname.replace(/\/$/,'');}
export function extract(){
  const finalUrl=location.origin+location.pathname;
  if(location.hostname!=='www.threads.com'||/\/login|\/accounts|\/oauth/.test(location.pathname))return {status:'auth',rows:[],finalUrl};
  const rows=[],seen=new Set();
  for(const a of document.querySelectorAll('a[href]')){
    if(a.hostname!==location.hostname||!/^\/@[^/]+\/post\/[^/]+\/?$/.test(a.pathname)||seen.has(a.pathname))continue;
    let best=a;
    for(let e=a.parentElement;e;e=e.parentElement){const paths=new Set([...e.querySelectorAll('a[href]')].filter(x=>/^\/@[^/]+\/post\/[^/]+\/?$/.test(x.pathname)).map(x=>x.pathname));if(paths.size>1)break;best=e;}
    const author=a.pathname.split('/')[1].slice(1);let text=best.innerText;
    const start=text.indexOf(author+'\n');if(start>=0)text=text.slice(start);
    const time=a.querySelector('time')||best.querySelector('time');
    if(text.length<author.length+8)continue;
    seen.add(a.pathname);rows.push({author,url:a.origin+a.pathname,published_at:time?.dateTime||null,text:Array.from(text).slice(0,1200).join(''),truncated:Array.from(text).length>1200});
  }
  return {status:rows.length?'ok':'empty',finalUrl,query:new URL(location.href).searchParams.get('q'),title:document.title,rows};
}
const unwrap=r=>r&&typeof r==='object'&&'data'in r?r.data:r;
export function dateBounds(since,until){
  const parse=(s,end)=>{if(!s)return end?Infinity:-Infinity;if(!/^\d{4}-\d{2}-\d{2}$/.test(s)||new Date(s+'T00:00:00Z').toISOString().slice(0,10)!==s)throw new ArgumentError('Use valid YYYY-MM-DD dates');return Date.parse(s+'T00:00:00+08:00')+(end?86400000:0);};
  const bounds=[parse(since,false),parse(until,true)];if(bounds[0]>=bounds[1])throw new ArgumentError('since must not follow until');return bounds;
}
export async function collect(page,url,max,query=null,dates={}){
  const [start,end]=dateBounds(dates.since,dates.until),filterDates=!!(dates.since||dates.until);
  try{await page.goto(url,{settleMs:3000});}catch(error){
    if(!/Navigation rejected|stale page identity|Page not found/i.test(String(error?.message)))throw new CommandExecutionError('Could not navigate to public Threads target');
    await new Promise(r=>setTimeout(r,750));
    try{await page.goto(url,{settleMs:3000});}catch{throw new CommandExecutionError('Public Threads navigation rejected after one retry');}
  }
  const found=new Map(),targetMatches=row=>!query||row.text.toLowerCase().includes(query.toLowerCase());
  let unchanged=0,lastSize=-1;
  for(let i=0;i<25;i++){
    const data=unwrap(await page.evaluate(`(${extract.toString()})()`));
    if(process.env.OPENCLI_THREADS_DEBUG)console.error(JSON.stringify({round:i,rows:data.rows?.length,found:found.size}));
    if(!data||!Array.isArray(data.rows))throw new CommandExecutionError('Malformed browser response');
    if(data.status==='auth')throw new AuthRequiredError('www.threads.com','Finish login manually in the connected Chrome profile.');
    const actual=new URL(data.finalUrl),expected=new URL(url);
    if(actual.pathname!==expected.pathname)throw new CommandExecutionError('Target changed; refusing to collect another page');
    if(query&&data.query!==query)throw new CommandExecutionError('Search query changed; refusing unrelated results');
    for(const row of data.rows){if(!targetMatches(row))continue;const timestamp=Date.parse(row.published_at);if(filterDates&&(!Number.isFinite(timestamp)||timestamp<start||timestamp>=end))continue;found.set(row.url,row);if(found.size===max)break;}
    unchanged=found.size===lastSize?unchanged+1:0;lastSize=found.size;
    if(found.size>=max||i===24)break;
    if(unchanged===5){await page.goto(url,{settleMs:5000});unchanged=0;continue;}
    await page.evaluate(`(()=>{const links=[...document.querySelectorAll('a[href]')].filter(a=>/^\\/@[^/]+\\/post\\/[^/]+\\/?$/.test(a.pathname));links.at(-1)?.scrollIntoView({block:'end',behavior:'instant'});window.scrollBy(0,innerHeight*.7);})()`);await new Promise(r=>setTimeout(r,1800));
  }
  if(!found.size)throw new EmptyResultError('threads','No public post bodies visible; login, content or markup may need checking');
  return [...found.values()].slice(0,max).map((r,i)=>({index:i+1,...r}));
}
const common={site:'threads',access:'read',domain:'www.threads.com',strategy:Strategy.COOKIE,browser:true,siteSession:'persistent',defaultWindowMode:'foreground',navigateBefore:false,columns:['index','author','published_at','text','url','truncated']};
cli({...common,name:'search',description:'Search public Threads posts; Taipei dates inclusive; relevance order',args:[{name:'query',required:true,positional:true},{name:'limit',type:'int',default:10},{name:'since',help:'Inclusive Taipei date YYYY-MM-DD'},{name:'until',help:'Inclusive Taipei date YYYY-MM-DD'}],func:async(page,k)=>{const q=String(k.query??'').trim();if(!q||q.length>100)throw new ArgumentError('Query must be 1..100 characters');return collect(page,'https://www.threads.com/search?q='+encodeURIComponent(q)+'&serp_type=default',limit(k.limit),q,k);}});
cli({...common,name:'profile',description:'Read public profile posts',args:[{name:'username',required:true,positional:true},{name:'limit',type:'int',default:3}],func:async(page,k)=>collect(page,'https://www.threads.com/@'+profile(k.username),limit(k.limit))});
cli({...common,name:'read',description:'Read one public post, excluding other posts',args:[{name:'url',required:true,positional:true}],func:async(page,k)=>{const url=postUrl(k.url),rows=await collect(page,url,10);const row=rows.find(r=>r.url===url);if(!row)throw new EmptyResultError('threads read','Target post body not visible');return [{...row,index:1}];}});
