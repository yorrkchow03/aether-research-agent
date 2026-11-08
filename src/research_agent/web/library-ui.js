'use strict';
const librarySearch={tab:'sources',query:'',nextOffset:null,request:0,detailRequest:0,seen:new Set(),series:null};
const libraryDate=new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeZone:'UTC'});
const libraryKinds={article:'Article',page:'Article',forum_post:'Discussion',feed_item:'Publisher update',pdf_text:'Document'};
function librarySourceURL(value){try{return safeURL(new URL(value).href)}catch{return ''}}
function libraryTimestamp(value){return value&&Number.isFinite(Date.parse(value))?new Date(value).toISOString().replace('T',' ').replace(/\.000Z$/,' UTC'):'Unknown'}
function libraryScopeParams(){
 const p=new URLSearchParams(),asset=$('library-asset').value;
 if(asset!=='ALL')p.set('asset',asset);
 for(const [id,key] of [['library-start','start'],['library-end','end']])if($(id).value)p.set(key,$(id).value+'T00:00:00Z');
 if(p.has('start')&&p.has('end')&&p.get('start')>=p.get('end'))throw Error('Start must be before the exclusive end date.');
 p.set('mode',$('library-mode').value);
 if(p.get('mode')==='strict'){const value=$('library-known-by').value;if(!value)throw Error('Enter the UTC capture cutoff.');p.set('known_by',value+(value.length===16?':00Z':'Z'))}
 return p;
}
function libraryRow(row){
 const url=librarySourceURL(row.url),title=esc(row.title||'Untitled source');
 const publisher=row.source_id||row.publisher||(url?new URL(url).hostname:'Saved source');
 const pub=Number.isFinite(Date.parse(row.published_at))?libraryDate.format(Date.parse(row.published_at)):'Unknown';
 const saved=/^(?:page|web|live)-[a-f0-9]{24}$/.test(row.record_id);
 return '<article class="news-row"><div class="news-icon" aria-hidden="true">▤</div><div><h3>'+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+title+'</a>':title)+'</h3><p class="library-source-meta">'+esc(publisher)+' · '+esc(libraryKinds[row.source_kind]||row.source_kind||'Document')+' · '+esc((row.assets||[]).join(' / '))+'<br>Published '+esc(pub)+' UTC · Captured '+esc(libraryTimestamp(row.known_at||row.retrieved_at||row.acquired_at))+'</p>'+((row.excerpt||'').trim()?'<p class="excerpt">'+esc(row.excerpt.trim().slice(0,340))+(row.excerpt.trim().length>340?'…':'')+'</p>':'')+'<div class="library-source-actions">'+(saved?sourceButton(row.record_id,'Read saved copy ↗'):'')+'</div></div></article>';
}
function librarySetTab(tab){
 if(!['sources','data','research'].includes(tab))return;
 librarySearch.tab=tab;librarySearch.series=null;
 document.querySelectorAll('[data-library-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.libraryTab===tab)));
 $('library-results').hidden=tab!=='sources';$('library-data-panel').hidden=tab!=='data';$('library-research-panel').hidden=tab!=='research';
 document.querySelectorAll('[data-source-filter]').forEach(n=>n.hidden=tab!=='sources');
 $('library-scope-filters').hidden=tab==='research';
 $('library-time-filters').hidden=tab==='research';
 $('library-detail').hidden=true;$('library-query').placeholder=tab==='sources'?'Search saved sources':tab==='data'?'Find dataset ID, unit or scope':'Search earlier saved analysis';
 void loadLibrary();
}
async function loadLibrary(append=false){
 const status=$('library-status'),more=$('library-more'),tab=librarySearch.tab,request=++librarySearch.request;
 const query=append?librarySearch.query:$('library-query').value.trim(),offset=append?librarySearch.nextOffset:0;if(offset===null)return;
 if(!append){librarySearch.detailRequest++;librarySearch.query=query;librarySearch.seen.clear();librarySearch.nextOffset=null;more.hidden=true;$('library-detail').hidden=true}
 more.disabled=true;status.textContent='Reading saved material…';
 try{
  const params=tab==='research'?new URLSearchParams():libraryScopeParams();params.set('query',query);params.set('offset',String(offset));params.set('limit','20');
  if(tab==='sources'){
   if(!append)$('library-results').replaceChildren();
   if($('library-source').value.trim())params.set('source',$('library-source').value.trim());
   if($('library-kind').value)params.set('kind',$('library-kind').value);
   let packet;
   try{packet=await api('/api/library/documents?'+params)}
   catch(error){
    if(params.get('mode')==='strict'||['start','end','source','kind'].some(k=>params.has(k)))throw error;
    packet={results:(await api('/api/news?'+new URLSearchParams({asset:$('library-asset').value,query}))).items,next_offset:null,fallback:true};
   }
   if(request!==librarySearch.request)return;
   librarySearch.nextOffset=packet.next_offset??null;
   for(const row of packet.results||[]){const key=row.record_id||librarySourceURL(row.url);if(!key||librarySearch.seen.has(key))continue;librarySearch.seen.add(key);$('library-results').insertAdjacentHTML('beforeend',libraryRow(row))}
   status.textContent=(packet.fallback?'Curated saved-news fallback · ':'')+librarySearch.seen.size+' shown'+(finite(packet.total)?' of '+packet.total:'')+' · '+(params.get('mode')==='strict'?'Only versions captured by the entered cutoff.':'Retrospective saved versions; publication is not authenticated historical availability.');
  }else if(tab==='data'){
   const packet=await api('/api/library/datasets');if(request!==librarySearch.request)return;
   const terms=query.toLowerCase().split(/\s+/).filter(Boolean),rows=(packet.datasets||[]).filter(d=>(!params.get('asset')||d.asset===params.get('asset'))&&terms.every(t=>JSON.stringify(d).toLowerCase().includes(t)));
   $('library-data-panel').innerHTML=rows.map(d=>'<article class="card library-dataset"><div class="card-heading"><h2>'+esc(d.dataset_id)+'</h2><span class="pill neutral">'+esc(d.asset)+' · '+esc(d.frequency||d.cadence||'Cadence unspecified')+'</span></div><p>'+esc(d.scope||'Saved dataset contract')+'</p><p class="card-note">Provider '+esc(d.provider||'Unknown')+' · Units '+esc(Object.entries(d.units||{}).map(([k,v])=>k+': '+v).join(' · '))+'</p><button class="button" data-library-dataset="'+esc(d.dataset_id)+'">Preview saved observations ↗</button></article>').join('')||'<div class="empty">No dataset matches these filters.</div>';
   librarySearch.nextOffset=null;status.textContent=rows.length+' saved dataset contracts · observation previews use the selected dates and capture mode.';
  }else{
   const p=new URLSearchParams({query,limit:'20',offset:String(offset)}),packet=await api('/api/library/research?'+p);
   if(request!==librarySearch.request)return;if(!append)$('library-research-panel').replaceChildren();librarySearch.nextOffset=packet.next_offset??null;
   for(const row of packet.results||[]){if(librarySearch.seen.has(row.output_id))continue;librarySearch.seen.add(row.output_id);
    $('library-research-panel').insertAdjacentHTML('beforeend','<article class="card library-output"><div class="card-heading"><h2>'+esc(row.kind==='report'?'Earlier saved report':'Earlier saved answer')+'</h2><span class="pill neutral">Historical analysis</span></div><p class="card-note">'+esc(libraryTimestamp(row.created_at))+' · '+esc(row.output_id)+'</p><p>'+esc(row.summary||'No summary saved')+'</p><button class="button" data-library-output="'+esc(row.output_id)+'">Inspect saved analysis and sources ↗</button></article>');
   }
   status.textContent=librarySearch.seen.size+' of '+packet.total+' earlier outputs · keyword search within original creation window. Asset/source/capture filters apply to Sources and Data. Earlier analysis is not a new factual source.';
  }
  more.hidden=librarySearch.nextOffset===null;
 }catch(e){if(request===librarySearch.request)status.textContent='Saved material could not be read: '+e.message}
 finally{if(request===librarySearch.request)more.disabled=false}
}
async function librarySeries(id,offset=0){
 const ticket=++librarySearch.detailRequest;
 const target=$('library-detail');target.hidden=false;target.innerHTML='<div class="empty">Reading saved observations…</div>';
 try{
  const p=libraryScopeParams();p.delete('asset');p.set('dataset_id',id);p.set('limit','20');p.set('offset',String(offset));
  const d=await api('/api/library/series?'+p);if(librarySearch.tab!=='data'||ticket!==librarySearch.detailRequest)return;librarySearch.series={id,next:d.next_offset};
  const keys=[...new Set(d.records.flatMap(r=>Object.keys(r.values||{})))].slice(0,5),v=x=>finite(x)?Number(x).toLocaleString('en-US',{maximumFractionDigits:8}):x===null||x===undefined?'Unknown':String(x);
  target.innerHTML='<div class="card-heading"><h2>'+esc(id)+'</h2><button class="text-button" data-library-close>Close preview</button></div><p class="card-note">'+esc(d.scope)+' · '+d.total+' observations. Showing '+(d.records.length?offset+1:0)+'–'+(offset+d.records.length)+'.</p><p class="card-note">UTC ['+esc(d.start)+', '+esc(d.end)+')'+(d.known_by?' · known by '+esc(d.known_by):'')+'</p><div class="library-series-table"><table class="data-table"><thead><tr><th>Observation</th>'+keys.map(k=>'<th>'+esc(k)+'<small>'+esc(d.dataset.units?.[k]||'Unit unspecified')+'</small></th>').join('')+'<th>Evidence</th></tr></thead><tbody>'+d.records.map(r=>'<tr><td>'+esc(libraryTimestamp(r.observed_at))+'<small>Captured '+esc(libraryTimestamp(r.known_at||r.retrieved_at||r.acquired_at))+'</small></td>'+keys.map(k=>'<td>'+esc(v(r.values?.[k]))+'</td>').join('')+'<td>'+(/^(?:live|page|web)-[a-f0-9]{24}$/.test(r.record_id)?sourceButton(r.record_id,'Saved source ↗'):esc(r.record_id||'Unavailable'))+'</td></tr>').join('')+'</tbody></table></div>'+(!d.records.length?'<p class="empty">No observations satisfy these dates and availability limits.</p>':'')+'<div class="investment-actions section-gap">'+(offset?'<button class="button" data-series-offset="'+Math.max(0,offset-20)+'">Previous 20</button>':'')+(d.next_offset!==null?'<button class="button" data-series-offset="'+d.next_offset+'">Next 20</button>':'')+'</div><details class="section-gap"><summary>Exact saved records and contract</summary><pre class="library-raw">'+esc(JSON.stringify(d,null,2))+'</pre></details>';
  target.scrollIntoView({behavior:'smooth',block:'start'});
 }catch(e){if(ticket===librarySearch.detailRequest)target.textContent='Saved observations unavailable: '+e.message}
}
async function libraryOutput(id){
 const ticket=++librarySearch.detailRequest;
 const target=$('library-detail');target.hidden=false;target.innerHTML='<div class="empty">Reading earlier saved research…</div>';
 try{
  const d=await api('/api/library/research/'+encodeURIComponent(id));if(librarySearch.tab!=='research'||ticket!==librarySearch.detailRequest)return;
  const b=d.body?.report||d.body?.draft||{};
  const links=ids=>(ids||[]).filter(r=>/^(live|page|web)-[a-f0-9]{24}$/.test(r)).slice(0,40).map(r=>sourceButton(r,'Source ↗')).join(' ');
  target.innerHTML='<div class="card-heading"><h2>'+esc(b.title||d.output_id)+'</h2><button class="text-button" data-library-close>Close analysis</button></div><p class="card-note">Saved '+esc(libraryTimestamp(d.created_at))+' · historical analysis, not new evidence or current account context.</p><p>'+esc(b.summary||d.body?.rendered||'Read the preserved statements and sources below.')+'</p>'+(d.claims||[]).slice(0,30).map(c=>'<p>'+esc(c.claim?.statement||c.claim_path)+' '+links(c.claim?.evidence_ids)+'</p>').join('')+'<h3>Underlying source evidence</h3><div class="library-source-actions">'+links(d.source_evidence_ids)+'</div><details class="section-gap"><summary>Full preserved analysis and citation versions</summary><pre class="library-raw">'+esc(JSON.stringify(d,null,2))+'</pre></details>';
  target.scrollIntoView({behavior:'smooth',block:'start'});
 }catch(e){if(ticket===librarySearch.detailRequest)target.textContent='Earlier analysis unavailable: '+e.message}
}
$('library-search').addEventListener('submit',e=>{e.preventDefault();void loadLibrary()});
$('library-more').addEventListener('click',()=>void loadLibrary(true));
document.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.libraryTab)librarySetTab(b.dataset.libraryTab);
 if(b.dataset.libraryDataset)void librarySeries(b.dataset.libraryDataset);
 if(b.dataset.seriesOffset!==undefined&&librarySearch.series)void librarySeries(librarySearch.series.id,Number(b.dataset.seriesOffset));
 if(b.dataset.libraryOutput)void libraryOutput(b.dataset.libraryOutput);
 if(b.hasAttribute('data-library-close'))$('library-detail').hidden=true;
});
