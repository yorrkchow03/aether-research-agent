'use strict';
const $=id=>document.getElementById(id);
function workspaceStorageGet(key){try{return localStorage.getItem(key)}catch{return null}}
function workspaceStorageSet(key,value){try{localStorage.setItem(key,String(value))}catch{}}
function workspaceStorageRemove(key){try{localStorage.removeItem(key)}catch{}}
const welcomeHTML=$('chat').innerHTML;
const state={sid:null,sessions:[],dashboard:{items:{},indicators:{}},reports:[],news:[],period:30,line:true,busy:false,modelConfigured:false,jobKey:'',view:'overview',jevAssistance:readJevPreference(),reportReviewProvider:readReportReviewPreference()};
function readJevPreference(){
 return workspaceStorageGet('aether-jev-assistance')==='true';
}
function configureJevControl(){
 const control=$('jev-assistance');
 const review=$('report-review-provider');
 if(control){
  control.checked=state.jevAssistance;
  control.disabled=state.busy||!state.modelConfigured;
 }
 if(review){
  review.value=state.reportReviewProvider;
  review.disabled=state.busy||!state.modelConfigured;
 }
}
function saveJevPreference(enabled){
 state.jevAssistance=Boolean(enabled);
 workspaceStorageSet('aether-jev-assistance',String(state.jevAssistance));
 configureJevControl();
}
function jevOptionLabel(requested){
 return typeof requested==='boolean'?`Jev sources: ${requested?'On':'Off'}`:'';
}
function readReportReviewPreference(){
 return workspaceStorageGet('aether-report-review-provider')==='jev'?'jev':'generation_model';
}
function saveReportReviewPreference(provider){
 state.reportReviewProvider=provider==='jev'?'jev':'generation_model';
 workspaceStorageSet('aether-report-review-provider',state.reportReviewProvider);
 configureJevControl();
}
function reportReviewLabel(provider,model){
 if(provider==='jev'||/^jev-/.test(model||''))return 'Review: '+(model||'Jev');
 if(model)return 'Review: '+model;
 return provider==='generation_model'?'Review: same model':'';
}
function reportDeliveryBadge(kind,status){
 if(kind==='evidence_gap')return {label:'Evidence gap',tone:'warn'};
 if(kind==='minimal')return {label:'Limited report',tone:'neutral'};
 return status==='reviewed'?{label:'Sources checked',tone:''}:{label:'Archived report',tone:'neutral'};
}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const usd=(v,d=2)=>finite(v)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:d,maximumFractionDigits:d}).format(Number(v)):'—';
const compact=v=>finite(v)?'$'+new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(Number(v)):'—';
const pct=v=>finite(v)?(Number(v)>0?'+':'')+(Number(v)*100).toFixed(2)+'%':'—';
const color=v=>finite(v)?Number(v)>=0?'positive':'negative':'';
const date=v=>v&&Number.isFinite(Date.parse(v))?new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Shanghai'}).format(new Date(v)):'Unknown';
const age=v=>{if(!v||!Number.isFinite(Date.parse(v)))return 'Unknown';const m=Math.max(0,(Date.now()-Date.parse(v))/60000);return m<1?'Just now':m<60?Math.floor(m)+'m ago':m<1440?Math.floor(m/60)+'h ago':Math.floor(m/1440)+'d ago'};
const safeURL=v=>{try{const u=new URL(v,location.origin);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:''}catch{return ''}};
function toast(text){$('toast').textContent=text;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').textContent='',5500)}
async function api(path,method='GET',body){const response=await fetch(path,{method,cache:'no-store',headers:{'X-Local-Token':window.AETHER_TOKEN,...(body!==undefined?{'Content-Type':'application/json'}:{})},body:body===undefined?undefined:JSON.stringify(body)});const text=await response.text();let data;try{data=JSON.parse(text)}catch{data={error:text.slice(0,200)}}if(!response.ok)throw Error(data.error||data.detail?.map?.(item=>item.msg).join('; ')||'Request failed ('+response.status+')');return data}
function markdown(text,verified=null){
 const inline=line=>line.replace(/\[([^\]]+)\]\(([^)]+)\)/g,(_,label,url)=>{
  const safe=safeURL(url);if(!safe)return label;let attrs='';const parsed=new URL(safe),match=parsed.pathname.match(/^\/api\/sessions\/([a-f0-9]{32})\/evidence\/([a-z0-9-]+)$/);
  if(verified&&parsed.origin===location.origin&&match&&window.AetherDesk){const context=window.AetherDesk.answerContext(line,match[2],verified);if(context)attrs=`data-source="${esc(match[2])}" data-source-session="${esc(match[1])}" data-evidence-context="${window.AetherDesk.remember(context)}"`}
  return `<a href="${esc(safe)}" ${url.includes('/evidence/')?'data-source-link':''} ${/^\d+$/.test(label)?'class="inline-citation"':''} ${attrs} target="_blank" rel="noopener">${label}</a>`;
 }).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');
 let output='',paragraph=[],list='',code=null;
 const flush=()=>{if(paragraph.length){output+='<p>'+paragraph.join(' ')+'</p>';paragraph=[]}if(list){output+='</'+list+'>';list=''}};
 const lines=esc(text).split('\n');
 const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());
 for(let index=0;index<lines.length;index++){
  const raw=lines[index];
  if(raw.startsWith('```')){flush();if(code!==null){output+='<pre><code>'+code.join('\n')+'</code></pre>';code=null}else code=[];continue}
  if(code!==null){code.push(raw);continue}
  if(raw.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[index+1]||'')&&cells(lines[index+1]).every(cell=>/^:?-{3,}:?$/.test(cell))){
   flush();
   output+='<div class="chat-table-wrap"><table><thead><tr>'+cells(raw).map(cell=>'<th>'+inline(cell)+'</th>').join('')+'</tr></thead><tbody>';
   index++;
   while(lines[index+1]?.trim()&&lines[index+1].includes('|')){
    index++;
    output+='<tr>'+cells(lines[index]).map(cell=>'<td>'+inline(cell)+'</td>').join('')+'</tr>';
   }
   output+='</tbody></table></div>';
   continue;
  }
  const line=inline(raw),heading=line.match(/^(#{1,4})\s+(.+)$/),bullet=line.match(/^\s*[-*]\s+(.+)$/),numbered=line.match(/^\s*\d+\.\s+(.+)$/);
  if(!line.trim()){flush();continue}
  if(heading){flush();const level=Math.min(heading[1].length+1,4);output+='<h'+level+'>'+heading[2]+'</h'+level+'>';continue}
  if(/^&gt;\s?/.test(line)){flush();output+='<blockquote>'+line.replace(/^&gt;\s?/,'')+'</blockquote>';continue}
  if(bullet||numbered){const type=bullet?'ul':'ol';if(list!==type){flush();output+='<'+type+'>';list=type}output+='<li>'+(bullet||numbered)[1]+'</li>';continue}
  if(list)flush();paragraph.push(line);
 }
 flush();if(code!==null)output+='<pre><code>'+code.join('\n')+'</code></pre>';return output;
}
function sourceButton(rid,text='View source ↗',sourceSession=state.sid,context=null){const key=context&&window.AetherDesk?window.AetherDesk.remember(context):'';return rid?`<button class="text-button" data-source="${esc(rid)}" data-source-session="${esc(sourceSession||'')}" data-evidence-context="${key}">${esc(text)}</button>`:''}
const item=key=>state.dashboard.items[key]||{};
const indicator=key=>state.dashboard.indicators?.[key]||{};
const val=(key,metric)=>item(key).record?.values?.[metric];
const iv=(key,metric)=>indicator(key).record?.values?.[metric];
function metricRow(label,value,note,rid,cls=''){return `<div class="metric-row"><div>${esc(label)}<small>${esc(note)}</small></div><div class="metric-value"><strong class="${cls}">${esc(value)}</strong><small>${sourceButton(rid,'Source ↗')}</small></div></div>`}
function renderData(){
 const market=state.dashboard;
 const quote=item('eth_price').record, hourly=item('eth_hourly').record, daily=item('eth_candles').record;
 const rate24=indicator('eth_24h').record, rate7=indicator('eth_7d').record, risk=indicator('eth_30d').record;
 const close=quote?.values?.price_usd??daily?.values?.close_usd;
 const priceSource=quote?.values?.price_usd!=null?quote:daily;
 const volumeRows=hourly?.series?.slice(-24)||[];
 const volume=volumeRows.length===24&&volumeRows.every((row,n)=>finite(row.volume_asset)&&
  (n===0||Date.parse(row.period_end)-Date.parse(volumeRows[n-1].period_end)===3600000))?
  volumeRows.reduce((sum,row)=>sum+Number(row.volume_asset),0):null;
 const rows=[
  ['ETH price',usd(close),(priceSource===daily?'Last completed candle':'Coinbase spot')+' · '+age(priceSource?.observed_at),priceSource?.record_id,''],
  ['24h change',pct(rate24?.values?.return_fraction),'Completed hourly prices',rate24?.record_id,color(rate24?.values?.return_fraction)],
  ['7d change',pct(rate7?.values?.return_fraction),'Completed daily prices',rate7?.record_id,color(rate7?.values?.return_fraction)],
  ['24h trading volume',finite(volume)?new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:2}).format(volume)+' ETH':'—','Coinbase · completed hours',hourly?.record_id,''],
 ];
 $('today-market').innerHTML=rows.map(([label,value,note,rid,cls])=>
  `<div class="stat-card"><div class="stat-label"><span>${esc(label)}</span>${sourceButton(rid,'↗')}</div><div class="stat-number ${cls}">${esc(value)}</div><div class="stat-foot">${esc(note)}</div></div>`).join('');
 $('chart-price').textContent=usd(close);
 const range=iv(state.period===1?'eth_24h':'eth_'+state.period+'d','return_fraction');
 $('chart-performance').textContent=finite(range)?pct(range)+' for this period':'';
 $('chart-performance').className=color(range);

 const volatility=iv('eth_30d','annualized_volatility_fraction'), spread=iv('execution','spread_bps'), funding=val('funding','funding_rate_fraction');
 const advanced=[
  metricRow('30d annualized volatility',finite(volatility)?(Number(volatility)*100).toFixed(2)+'%':'—','Completed daily prices',risk?.record_id),
  metricRow('30d maximum drawdown',pct(iv('eth_30d','max_drawdown_fraction')),'Completed daily prices',risk?.record_id),
  metricRow('Bid–ask spread',finite(spread)?Number(spread).toFixed(2)+' bps':'—','Coinbase snapshot',indicator('execution').record?.record_id),
  metricRow('Perpetual funding rate',finite(funding)?(Number(funding)*100).toFixed(4)+'%':'—','OKX · per settlement',item('funding').record?.record_id),
  metricRow('Perpetual open interest',compact(val('open_interest','open_interest_usd')),'OKX · USD exposure',item('open_interest').record?.record_id),
  metricRow('Ethereum TVL',compact(val('tvl','tvl_usd')),'Saved chain observation',item('tvl').record?.record_id),
  metricRow('Ethereum DEX volume',compact(val('dex_volume','volume_24h_usd')),'Covered protocols · rolling 24h',item('dex_volume').record?.record_id),
 ];
 $('risk-metrics').innerHTML=advanced.join('');
 $('coverage').textContent='Times and sources are available with each metric.';
 $('freshness').textContent=market.refreshing?'Updating prices…':market.updated_at?'Data updated '+age(market.updated_at):'Waiting for data';
 const connectionLabels={eth_price:'ETH spot price',eth_candles:'ETH daily history',eth_hourly:'ETH hourly history',orderbook:'Order book',funding:'Funding rate',open_interest:'Open interest',tvl:'Ethereum TVL',dex_volume:'Ethereum DEX volume'};
 $('data-connections').innerHTML=Object.entries(connectionLabels).map(([key,label])=>{
  const source=item(key);
  return metricRow(label,source.status==='ok'?'Available':'Unavailable',source.record?'Observed '+date(source.record.observed_at):'No saved observation',source.record?.record_id);
 }).join('');
 renderPriceChart();
}
async function loadData(refresh=false){
 if(refresh)await api('/api/dashboard/refresh','POST',{});
 state.dashboard=await api('/api/dashboard');
 renderData();
}
function reportRow(r){const badge=reportDeliveryBadge(r.delivery_kind,r.review_status);return `<article class="report-row"><div class="report-symbol">▤</div><div><button data-report="${esc(r.report_id)}"><h3>${esc(r.title)}</h3></button><p>${esc(date(r.created_at))} · <span class="pill ${badge.tone}">${badge.label}</span></p></div></article>`}
async function loadReports(){
 const query=$('report-query').value.trim();
 const data=await api('/api/reports?query='+encodeURIComponent(query));
 if(query!==$('report-query').value.trim())return;
 state.reports=data.items;
 $('reports-list').innerHTML=data.items.length?data.items.map(reportRow).join(''):
  `<div class="card empty">${query?'No reports match this search.':'Your research reports will appear here.'}</div>`;
}
function reportToolbar(id){
 return `<div class="report-reader-toolbar"><button class="text-button" data-report-back>← All reports</button><a class="button primary" href="/api/reports/${encodeURIComponent(id)}/pdf" download="aether-${esc(id)}.pdf">Export PDF ↓</a></div>`;
}

function beginReport(id){
 selectView('reports');
 $('report-list-view').hidden=true;
 const box=$('report-detail');
 box.dataset.reportId=id;
 box.hidden=false;
 box.innerHTML=reportToolbar(id)+'<p class="empty" role="status">Opening report…</p>';
 window.scrollTo({top:0,behavior:'instant'});
 return box;
}

function reportSummaryHTML(summary){
 const text=String(summary||'');
 const sentences=text.match(/[\s\S]+?(?:[.!?](?=\s|$)|[。！？]|$)/g)||[text];
 let lead=sentences[0]||text;
 if(sentences[1]&&(lead+sentences[1]).length<=280)lead+=sentences[1];
 const remaining=text.slice(lead.length).trim();
 return `<p class="report-summary">${esc(lead.trim())}</p>${remaining?`<details class="report-summary-more"><summary>Read the rest of the summary</summary><p>${esc(remaining)}</p></details>`:''}`;
}
function adviceSourceIDs(a){if(!a)return [];return [...new Set([...(a.context_evidence_ids||[]),...['rationale','conditions','counterpoints','invalidation'].flatMap(k=>(a[k]||[]).flatMap(c=>c.evidence_ids)),...(a.historical_cases||[]).flatMap(c=>c.evidence_ids)])]}
function adviceHTML(a,claims,sid){if(!a)return '';let body=`<section class="advice-section"><h2>Conditional research suggestion</h2><span class="pill neutral">${a.scope==='user_context'?'Supplied personal context':'General market research'}</span><p class="body-copy">${esc(a.summary)} ${adviceSourceIDs(a).map(rid=>sourceButton(rid,'View evidence ↗',sid)).join(' ')}</p>`;if(a.horizon_days)body+=`<p>Research horizon: ${esc(a.horizon_days)} days</p>`;for(const [label,key] of [['Evidence & reasoning','rationale'],['Conditions to investigate','conditions'],['Counterevidence','counterpoints'],['Revisit or invalidate when','invalidation']])if(a[key]?.length)body+=`<h3>${label}</h3>`+claims(a[key]);for(const c of a.historical_cases||[]){body+=`<h3>Historical comparison · ${esc(c.name)}</h3><p>${esc(date(c.case_end))} · ${c.evidence_ids.map(rid=>sourceButton(rid,'View case evidence ↗',sid)).join(' ')}</p><p class="card-note">${c.availability==='point_in_time'?'Saved availability receipts checked against case end.':'Retrospective observed history; historical public availability is not established.'}</p>`;for(const [label,key] of [['Similarities','similarities'],['Material differences','differences'],['Limits of transfer','limitations']])body+=`<strong>${label}</strong><ul>${(c[key]||[]).map(t=>'<li>'+esc(t)+'</li>').join('')}</ul>`}for(const [label,key] of [['Scenario assumptions','assumptions'],['Missing information','missing_information'],['What would prompt another review','review_triggers']])if(a[key]?.length)body+=`<h3>${label}</h3><ul>${a[key].map(t=>'<li>'+esc(t)+'</li>').join('')}</ul>`;return body+'</section>'}

const reportClock = new Intl.DateTimeFormat('en-GB', {
 day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit',
 second:'2-digit', timeZone:'UTC', hourCycle:'h23',
});
const reportDay = new Intl.DateTimeFormat('en-GB', {day:'numeric', month:'short', timeZone:'UTC'});
const reportHour = new Intl.DateTimeFormat('en-GB', {
 hour:'2-digit', minute:'2-digit', timeZone:'UTC', hourCycle:'h23',
});
const reportDocumentRoles = {
 primary:'Main in-window evidence', background:'Earlier background', correction:'Correction',
 counterevidence:'Counterevidence', later_update:'Update after this window',
};

function reportTime(value){
 const instant=Date.parse(value);
 return Number.isFinite(instant)
  ? `<time datetime="${esc(value)}" title="${esc(value)}">${esc(reportClock.format(instant))} UTC</time>`
  : 'Unknown time';
}

function reportOverviewHTML(presentation,sid){
 const overview=presentation.overview;
 return `<section class="report-overview" aria-label="Report overview">
  <div class="report-lead"><h2>Summary</h2>${reportSummaryHTML(overview.summary)}</div>
  <details class="report-scope-details"><summary>Question, time window and evidence cutoff</summary>
   <div class="report-question"><h3>Original question</h3><p>${esc(overview.user_prompt)}</p></div>
   <dl class="report-metadata">
    <div><dt>Research window</dt><dd>${reportTime(overview.start)} to ${reportTime(overview.end)} <small>(end exclusive)</small></dd></div>
    <div><dt>Evidence cutoff</dt><dd>${reportTime(overview.evidence_as_of)}</dd></div>
    <div><dt>Generated</dt><dd>${reportTime(overview.created_at)}</dd></div>
   </dl>
  </details>
 </section>`;
}

function reportChartSVG(chart,index,points){
 const start=Date.parse(chart.start),end=Date.parse(chart.end);
 if(points.length<2||!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return '';
 const left=59,right=349,top=11,bottom=143;
 const values=points.map(point=>point.number);
 const maximum=Math.max(...values),minimum=Math.min(...values);
 const span=maximum-minimum,padding=span*.07||Math.max(Math.abs(maximum)*.025,.000001);
 const high=maximum+padding,low=minimum-padding;
 if(!Number.isFinite(high-low)||high<=low)return '';
 const x=instant=>left+(instant-start)/(end-start)*(right-left);
 const y=value=>bottom-(value-low)/(high-low)*(bottom-top);
 const compactAxis=Math.max(Math.abs(high),Math.abs(low))>=100000;
 const divisor=compactAxis?1000**Math.floor(Math.log10(Math.max(Math.abs(high),Math.abs(low)))/3):1;
 const digits=Math.max(0,Math.min(12,Math.ceil(-Math.log10((high-low)/(4*divisor)))+1));
 const axisNumber=new Intl.NumberFormat('en-US',{notation:compactAxis?'compact':'standard',maximumFractionDigits:digits});
 const grid=[];
 for(let tick=0;tick<=4;tick++){
  const value=low+(high-low)*tick/4,yy=y(value);
  grid.push(`<path class="report-chart-gridline" d="M${left} ${yy}H${right}"/>
   <text class="report-chart-axis" x="${left-6}" y="${yy+3}" text-anchor="end">${esc(axisNumber.format(value))}</text>`);
 }
 for(const fraction of [0,.5,1]){
  const instant=start+(end-start)*fraction;
  const anchor=fraction===0?'start':fraction===1?'end':'middle';
  grid.push(`<path class="report-chart-gridline report-chart-time-grid" d="M${x(instant)} ${top}V${bottom}"/>
   <text class="report-chart-axis" x="${x(instant)}" y="158" text-anchor="${anchor}">
    <tspan x="${x(instant)}">${esc(reportDay.format(instant))}</tspan><tspan x="${x(instant)}" dy="12">${esc(reportHour.format(instant))}</tspan>
   </text>`);
 }
 const segments=[];
 for(const point of points){
  if(!segments.length||point.gap_before)segments.push([]);
  segments.at(-1).push([x(point.instant),y(point.number)]);
 }
 const curves=segments.map(segment=>{
  if(segment.length===1)return `<circle class="report-chart-dot" cx="${segment[0][0]}" cy="${segment[0][1]}" r="2.2"/>`;
  const coordinates=segment.map(([xx,yy])=>`${xx.toFixed(3)},${yy.toFixed(3)}`).join(' ');
  return `<polyline class="report-chart-line" points="${coordinates}"/>`;
 });
 const name=`${chart.asset} ${chart.title} (${chart.unit})`;
 const breaks=segments.length-1;
 return `<svg class="report-chart-svg${chart.metric==='volume_asset'?' report-chart-volume':''}" viewBox="0 0 360 178"
  role="img" aria-labelledby="report-chart-title-${index} report-chart-desc-${index}">
  <title id="report-chart-title-${index}">${esc(name)}</title>
  <desc id="report-chart-desc-${index}">${points.length} observations on a linear time axis in UTC and a linear value axis in ${esc(chart.unit)}.${breaks?` The line breaks across ${breaks} gaps.`:''}</desc>
  ${grid.join('')}${curves.join('')}
 </svg>`;
}

function reportChartHTML(chart,index,sid){
 const points=(chart.points||[]).map(point=>({...point,instant:Date.parse(point.at),number:Number(point.value)}))
  .filter(point=>Number.isFinite(point.instant)&&finite(point.value));
 const svg=reportChartSVG(chart,index,points);
 const coverage=chart.coverage||{};
 const gap=coverage.internal_gap_count>0||coverage.starts_after_window||coverage.ends_before_window;
 const sample=points.length?[points[0],points.at(-1)]:[];
 const links=(chart.sources||[]).map(source=>sharedSourceLink(source.url,source.publisher+' ↗')).filter(Boolean);
 return `<figure class="report-chart-card">
  <figcaption><h3>${esc(chart.asset)} ${esc(chart.title)}</h3>
   <p class="report-chart-meta">${esc(reportPublisher(chart.provider))} · ${esc(chart.frequency)} · ${esc(chart.unit)} · ${points.length} saved observations</p></figcaption>
  ${svg||'<p class="body-copy">Chart preview unavailable. Open the saved evidence for the original values.</p>'}
  ${gap?'<p class="report-chart-gap">Coverage is incomplete within the research window.</p>':''}
  <div class="report-evidence-links">${sourceButton(chart.evidence_id,'Inspect saved series ↗',sid)}</div>
  <details class="report-chart-details"><summary>Exact endpoint values and sources</summary>
   ${sample.length?`<dl>${sample.map((point,n)=>`<div><dt>${n===0?'First point':'Last point'}</dt>
    <dd>${esc(point.value)} ${esc(chart.unit)} · ${reportTime(point.at)} ${sourceButton(point.evidence_id,'Evidence ↗',sid)}</dd></div>`).join('')}</dl>`:''}
   ${links.length?`<p class="report-evidence-links">${links.join(' ')}</p>`:''}
  </details>
 </figure>`;
}

function reportPublisher(value){
 const names={ethereum:'Ethereum Foundation',bitstamp:'Bitstamp',coinbase:'Coinbase',lido:'Lido Research',
  aave:'Aave Governance',federal_reserve:'Federal Reserve',sec:'SEC',okx:'OKX'};
 return Object.hasOwn(names,value)?names[value]:value;
}

function reportDocumentsHTML(documents,sid,review){
 if(!documents.length)return '';
 const verdicts=new Map((review?.verdicts||[]).map(verdict=>[verdict.claim_id,verdict.status]));
 return `<section class="report-featured-documents" aria-label="Important documents"><h2>Important documents</h2>
  ${documents.map((doc,index)=>{
   const reviewed=['supported','reasonable_inference'].includes(verdicts.get('DOC'+(index+1)));
   return `<article class="report-document-card">
    <span class="pill neutral">${esc(reportDocumentRoles[doc.role]||'Saved original')}</span>
    <h3>${esc(doc.title)}</h3><p class="report-document-publisher">${esc(reportPublisher(doc.publisher))}</p>
    <p class="report-document-relevance">${esc(doc.relevance)}</p>
    <div class="report-evidence-links">${sourceButton(doc.evidence_id,'Read saved original ↗',sid)} ${sharedSourceLink(doc.url,'Captured publication ↗')}</div>
    <details class="report-source-excerpt"><summary>Quoted passage and dates${reviewed?' · source checked':''}</summary>
     <blockquote>${esc(doc.quote)}</blockquote>
     <dl class="report-metadata"><div><dt>Published</dt><dd>${reportTime(doc.published_at)}</dd></div>
      <div><dt>Captured</dt><dd>${reportTime(doc.retrieved_at)}</dd></div></dl>
    </details>
   </article>`;
  }).join('')}
 </section>`;
}

function reportMetricCards(draft,numbers,sid,review){
 const numeric=(draft.observations||[]).filter(claim=>claim.metric&&finite(claim.value));
 const selected=[];
 for(const pattern of [/^(close_usd|price_usd)$/, /return|change/, /volatility/]){
  const claim=numeric.find(item=>pattern.test(item.metric)&&!selected.includes(item));
  if(claim)selected.push(claim);
 }
 for(const claim of numeric)if(selected.length<3&&!selected.includes(claim))selected.push(claim);
 if(!selected.length)return '';
 const labels=[[/return|change/,'Period change'],[/volatility/,'Annualized volatility'],[/close|price/,'Closing price'],
  [/high/,'Period high'],[/low/,'Period low'],[/volume/,'Trading volume'],[/tvl/,'DeFi TVL'],[/stablecoin/,'Stablecoin supply']];
 return '<div class="report-key-metrics">'+selected.map(claim=>{
  const label=labels.find(([pattern])=>pattern.test(claim.metric))?.[1]||'Key measure';
  let value=new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(Number(claim.value))+' '+claim.unit;
  if(claim.unit==='USD')value=usd(claim.value);
  if(claim.unit==='fraction')value=/return|change/.test(claim.metric)?pct(claim.value):(Number(claim.value)*100).toFixed(2)+'%';
  return `<div class="report-key-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><details><summary>Measurement details</summary><p>${esc(claim.statement)}</p>${claim.evidence_ids.map(rid=>sourceButton(rid,'['+numbers[rid]+']',sid,window.AetherDesk?.claimContext(claim,draft,review))).join(' ')}</details></div>`;
 }).join('')+'</div>';
}

async function showReport(id){
 let meta=state.reports.find(report=>report.report_id===id);
 if(!meta){$('report-query').value='';await loadReports();meta=state.reports.find(report=>report.report_id===id)}
 if(!meta){toast('This report is unavailable.');return}
 const box=beginReport(id);
 let packet;
 try{packet=await api(`/api/sessions/${meta.session_id}/artifact?path=${encodeURIComponent('/reports/'+id+'.json')}`)}
 catch(error){if(box.dataset.reportId===id)box.innerHTML=reportToolbar(id)+'<p class="empty">'+esc(error.message)+'</p>';return}
 if(box.dataset.reportId!==id)return;
 const draft=packet.report,presentation=packet.presentation?.version==='report-presentation-v1'?packet.presentation:null;
 const charts=presentation?.charts||[],documents=presentation?.documents||[];
 const all=[...(draft.observations||[]),...(draft.key_changes||[]),...(draft.interpretations||[])];
 const extra=(draft.forecasts||[]).flatMap(forecast=>[...forecast.supporting_evidence_ids,...forecast.counter_evidence_ids]);
 const purchase=draft.purchase_decision?[...draft.purchase_decision.conditions_to_consider,...draft.purchase_decision.reasons_to_wait].flatMap(condition=>condition.evidence_ids):[];
 const ids=[...new Set([...all.flatMap(claim=>claim.evidence_ids),...extra,...purchase,...adviceSourceIDs(draft.advice),...[...charts,...documents].map(item=>item.evidence_id)])];
 const numbers=Object.fromEntries(ids.map((value,index)=>[value,index+1]));
 const claims=list=>(list||[]).map(claim=>`<div class="statement">${esc(claim.statement)} ${claim.evidence_ids.map(rid=>sourceButton(rid,'['+(numbers[rid]||'Source')+']',meta.session_id,window.AetherDesk?.claimContext(claim,draft,packet.review))).join(' ')}${claim.metric?`<details class="numeric-detail"><summary>Exact measurement</summary><small>${esc(claim.metric)} = ${esc(claim.value)} ${esc(claim.unit)}</small></details>`:''}</div>`).join('');
 const scope=presentation?.overview||draft.scope;
 const day=new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
 const windowLabel=scope?.start&&scope?.end?day.format(new Date(scope.start))+' – '+day.format(new Date(Date.parse(scope.end)-1))+' · UTC':'';
 const badge=reportDeliveryBadge(draft.delivery_kind,packet.review?.status);
 const reviewLabel=draft.delivery_kind==='evidence_gap'?'':reportReviewLabel(packet.run_metadata?.report_review_provider,packet.review?.reviewer_model);
 let body=reportToolbar(id)+`<header class="report-title"><div class="report-title-meta"><span class="report-kicker">Ethereum research</span><span class="pill ${badge.tone}">${badge.label}</span></div><h1>${esc(draft.title)}</h1><p>${esc(windowLabel||date(packet.created_at))}${windowLabel?' · Published '+esc(date(packet.created_at)):''}${jevOptionLabel(packet.run_metadata?.jev_assistance_requested)?' · '+jevOptionLabel(packet.run_metadata.jev_assistance_requested):''}${reviewLabel?' · '+esc(reviewLabel):''}</p></header>`;
 if(draft.delivery_kind==='minimal')body+='<p class="card-note">This limited report keeps the findings that passed final source review. Unresolved claims and dependent conclusions were removed; the remaining gaps are disclosed below.</p>';
 if(draft.delivery_kind==='evidence_gap')body+='<p class="card-note">This is an evidence availability notice. No source-reviewed market conclusions were published.</p>';
 body+=presentation?reportOverviewHTML(presentation,meta.session_id):'<section class="report-lead"><h2>Summary</h2>'+reportSummaryHTML(draft.summary)+'</section>';
 body+=reportMetricCards(draft,numbers,meta.session_id,packet.review);
 if(charts.length)body+=`<section class="report-chart-section" aria-label="Key data charts"><h2>Key data</h2>${charts.map((chart,index)=>reportChartHTML(chart,index,meta.session_id)).join('')}</section>`;
 const highlights=(draft.key_changes?.length?draft.key_changes:draft.observations||[]).slice(0,3);
 if(highlights.length)body+='<section class="report-takeaways"><h2>What matters</h2>'+claims(highlights)+'</section>';
 body+=reportDocumentsHTML(documents,meta.session_id,packet.review);
 if(draft.questions_to_watch?.length)body+='<section class="report-watch"><h2>What to watch next</h2><ul>'+draft.questions_to_watch.map(question=>'<li>'+esc(question)+'</li>').join('')+'</ul></section>';
 body+='<section class="report-limits"><h2>Limits to keep in mind</h2><ul>'+draft.limitations.map(limit=>'<li>'+esc(limit)+'</li>').join('')+'</ul></section>';
 body+='<details class="report-disclosure"><summary>Full analysis and supporting facts</summary><h2>Facts</h2>'+claims(draft.observations);
 if(draft.key_changes?.length)body+='<h2>Changes</h2>'+claims(draft.key_changes);
 if(draft.interpretations?.length)body+='<h2>Interpretation</h2>'+claims(draft.interpretations);
 if(draft.judgment_reason)body+='<h2>Research judgment</h2><p>'+esc(draft.judgment_reason)+'</p>';
 if(draft.forecasts?.length)body+='<h2>Conditional outlook</h2>'+draft.forecasts.map(forecast=>`<p>${esc(forecast.statement)} ${[...forecast.supporting_evidence_ids,...forecast.counter_evidence_ids].map(rid=>sourceButton(rid,'View source ↗',meta.session_id)).join(' ')}</p><small>${esc(forecast.uncertainty)}</small>`).join('');
 if(draft.purchase_decision){
  const purchase=draft.purchase_decision;body+='<h2>Decision context</h2>';
  for(const [label,rows] of [['Conditions to investigate',purchase.conditions_to_consider],['Reasons to wait',purchase.reasons_to_wait]])body+=`<h3>${label}</h3>`+rows.map(row=>`<p>${esc(row.condition)} ${row.evidence_ids.map(rid=>sourceButton(rid,'View source ↗',meta.session_id)).join(' ')}</p>`).join('');
  for(const [label,rows] of [['Invalidation conditions',purchase.invalidation_conditions],['Missing information',purchase.missing_information],['What to monitor',purchase.monitor],['Costs and execution assumptions',purchase.cost_and_execution_notes]])body+=`<h3>${label}</h3><ul>${rows.map(row=>'<li>'+esc(row)+'</li>').join('')}</ul>`;
 }
 body+=adviceHTML(draft.advice,claims,meta.session_id)+'</details>';
 body+='<details class="report-disclosure"><summary>Sources, review and downloads</summary><div class="report-evidence-links">'+ids.map(rid=>sourceButton(rid,'['+numbers[rid]+'] '+rid,meta.session_id)).join(' ')+'</div><h3>Claim-level review</h3>';
 for(const verdict of packet.review?.verdicts||[])body+=`<details class="review-item"><summary><span class="pill ${['contradicted','insufficient'].includes(verdict.status)?'warn':'neutral'}">${esc(verdict.status.replaceAll('_',' '))}</span> ${esc(verdict.claim_id)} · ${esc(verdict.reason)}</summary>${verdict.quotes.map(quote=>`<blockquote>${esc(quote.text)}</blockquote>${sourceButton(quote.record_id,'View source ↗',meta.session_id)}`).join('')}</details>`;
 body+=`<div class="report-actions"><a class="button" href="/api/sessions/${meta.session_id}/artifact?path=${encodeURIComponent('/reports/'+id+'.md')}" target="_blank">Original Markdown ↗</a></div><p class="card-note">Source checks assess support in the saved material. They do not guarantee publisher accuracy or future performance.</p></details>`;
 box.innerHTML=body;
 api('/api/sessions/'+meta.session_id+'/packages').then(packages=>{
  if(box.dataset.reportId!==id)return;
  const pack=packages.find(item=>item.package_id.startsWith(id+'-'));
  if(pack)box.querySelector('.report-actions').insertAdjacentHTML('beforeend',`<a class="button" href="/api/audits/${pack.package_id}/download">Evidence ZIP ↓</a><button class="button" data-audit="${pack.package_id}" data-kind="verify">Check integrity</button><button class="button" data-audit="${pack.package_id}" data-kind="replay">Replay calculations</button>`);
 }).catch(()=>{});
 return packet;
}
async function openSource(rid,sourceSession){const shared=/^(live|page|web)-/.test(rid);if(!shared&&!sourceSession&&!state.sid)await createSession();sourceSession=sourceSession||state.sid;const dialog=$('source-dialog');$('source-detail').innerHTML='<div class="empty">Loading saved evidence…</div>';if(!dialog.open)dialog.showModal();try{const d=await api(shared?`/api/evidence/${encodeURIComponent(rid)}`:`/api/sessions/${sourceSession}/evidence/${encodeURIComponent(rid)}`),r=d.record,src=r.source||{},kind=r.record_id.startsWith('page-')?'Original page':(r.recipe_sha256||r.quality_flags?.includes('derived'))?'Derived calculation':r.source_kind==='feed_item'?'Publisher feed excerpt':r.record_id.startsWith('web-')?'Search excerpt':'Saved data';$('source-detail').innerHTML=`<span class="pill neutral">${kind}</span><h3>${esc(r.title||src.name||r.dataset_id)}</h3><div class="metadata"><div><small>Observed / effective time</small>${esc(date(r.observed_at))}</div><div><small>Retrieved</small>${esc(date(r.retrieved_at))}</div><div><small>Published</small>${esc(date(r.published_at))}</div><div><small>Snapshot integrity</small>${d.raw_verified?'Raw response verified':r.recipe_sha256?'Saved calculation recipe':'Structured source record'}</div></div>${src.url?`<a href="${esc(safeURL(src.url))}" target="_blank" rel="noopener">Open publisher / API ↗</a>`:''}${r.scope?'<p class="body-copy">'+esc(r.scope)+'</p>':''}${r.text?'<h3>Saved source text</h3><pre>'+esc(r.text)+'</pre>':''}${Object.keys(r.values||{}).length?'<h3>Original numeric fields</h3><pre>'+esc(JSON.stringify({values:r.values,units:r.units},null,2))+'</pre>':''}${r.method?'<h3>Calculation method</h3><pre>'+esc(r.method)+'</pre>':''}${r.input_record_ids?'<h3>Inputs</h3>'+r.input_record_ids.map(i=>sourceButton(i)).join(' · '):''}<details class="section-gap"><summary>Provenance details</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details><p class="card-note">Publication metadata may be unverified. A valid source pointer and matching snapshot do not guarantee the publisher’s claims.</p>`}catch(e){$('source-detail').innerHTML='<p class="body-copy">'+esc(e.message)+'</p>'}}
const viewTitles={
 today:['Home','Ethereum price, performance, and key investment data.'],
 news:['Home','The latest Ethereum developments worth following.'],
 research:['Analyst','Ask a question about Ethereum and explore the evidence.'],
 reports:['Analyst','Browse your previous Ethereum research reports.'],
 library:['Library','Explore saved sources, typed data series and earlier research.'],
 portfolio:['Portfolio','Explore an editable scenario or review your private local ledger.'],
 strategies:['Strategy Lab','Inspect dated predictions, holdout results and frozen research experiments.'],
 resources:['Resources','Browse your research tools and saved activity.'],
 help:['How to use Aether','Follow a source from research to a report, scenario or saved experiment.'],
 settings:['Settings','Manage your workspace and see which data sources are available.'],
};
const workspaceRoutes={
 home:'today',overview:'today',today:'today','home/news':'news',news:'news',
 research:'research',analyst:'research','analyst/chat':'research',
 'research/reports':'reports','analyst/reports':'reports',reports:'reports',
 'research/updates':'library','analyst/library':'library',library:'library',
 portfolio:'portfolio','advanced/strategy-lab':'strategies',strategies:'strategies',settings:'settings',
 resources:'resources','advanced/resources':'resources',help:'help','resources/help':'help',
};
const workspacePaths={today:'home',news:'home/news',research:'analyst/chat',reports:'analyst/reports',
 library:'analyst/library',portfolio:'portfolio',strategies:'advanced/strategy-lab',settings:'settings',
 resources:'resources',help:'help'};
function selectView(requested){
 let view=Object.hasOwn(workspaceRoutes,requested)?workspaceRoutes[requested]:'today';
 if(view!=='research'&&$('expanded-chat').open)$('expanded-chat').close();
 const group=['today','news'].includes(view)?'home':['research','reports','library'].includes(view)?'research':view;
 window.scrollTo(0,0);state.view=view;document.body.dataset.workspace=group;document.body.dataset.view=view;
 $('report-list-view').hidden=false;
 $('report-detail').hidden=true;
 $('report-detail').dataset.reportId='';
 $('job-progress-link').hidden=!state.researchActive||view==='research';
 document.querySelectorAll('.view').forEach(n=>n.hidden=n.id!=='view-'+view);
 document.querySelectorAll('.nav-item').forEach(n=>{
  const active=n.closest('[aria-label="Primary navigation"]')?(n.dataset.view==='today'?'home':n.dataset.view)===group:n.dataset.view===view;
  n.classList.toggle('active',active);
  if(active)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');
 });
 $('home-navigation').hidden=group!=='home';
 $('research-navigation').hidden=group!=='research';
 document.querySelectorAll('.section-nav button').forEach(n=>{
  const active=n.dataset.view===view;
  n.classList.toggle('selected',active);
  if(active)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');
 });
 const [title,subtitle]=viewTitles[view];
 $('page-title').textContent=title;
 $('page-subtitle').textContent=subtitle;
 $('page-eyebrow').textContent=group==='home'?'ETHEREUM MARKET':group==='research'?'RESEARCH WORKSPACE':'AETHER WORKSPACE';
 $('breadcrumb').textContent={today:'Home / Overview',news:'Home / Latest News',research:'Analyst / Chat',
  reports:'Analyst / Reports',library:'Analyst / Library',portfolio:'Portfolio',strategies:'More / Strategy Lab',
  resources:'More / Resources',help:'More / How to use',settings:'Settings'}[view];
 $('refresh-data').hidden=!['today','news'].includes(view);
 $('freshness').hidden=!['today','news'].includes(view);
 $('open-research').hidden=view==='research';
 $('market-review-status').hidden=!['today','news'].includes(view)||!$('market-review-status').textContent;
 $('more-menu').open=false;
 if(view==='research')requestAnimationFrame(()=>{$('chat').scrollTop=$('chat').scrollHeight});
 if(view==='today')renderPriceChart();
 if(view==='news')loadLatestNews().catch(()=>toast('Latest news is temporarily unavailable.'));
 if(view==='reports')loadReports().catch(()=>toast('Reports are temporarily unavailable.'));
 if(view==='library')loadLibrary().catch(()=>toast('The library is temporarily unavailable.'));
 history.replaceState(null,'','#'+workspacePaths[view]);
 document.dispatchEvent(new CustomEvent('aether:view',{detail:{view}}));
}
const marketReviewTitle='ETH market refresh';
let marketReviewSession=null, startingMarketReview=false;
async function sessions(){
 const rows=await api('/api/sessions');
 marketReviewSession=rows.find(s=>s.title===marketReviewTitle)?.session_id||null;
 state.sessions=rows.filter(s=>s.title!==marketReviewTitle);
 if(!state.sessions.some(s=>s.session_id===state.sid)){
  const saved=workspaceStorageGet('aether-session');
  state.sid=state.sessions.some(s=>s.session_id===saved)?saved:state.sessions[0]?.session_id||null;
 }
 const picker=$('session-picker');
 picker.innerHTML=state.sessions.length?state.sessions.map(s=>`<option value="${esc(s.session_id)}" ${s.session_id===state.sid?'selected':''}>${esc(s.title)}</option>`).join(''):'<option>No conversation yet</option>';
}
async function createSession(){const s=await api('/api/sessions','POST',{title:'ETH research · '+new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric'}).format(new Date()),research_mode:'current'});state.sid=s.session_id;state.jobKey='';renderResearchProgress(null);workspaceStorageSet('aether-session',state.sid);await sessions();await refreshSession();return s}
function showAnalyst(){selectView('research');$('question').focus()}
async function ask(prompt,deliverable='answer'){
 if(!state.modelConfigured){toast('The assistant is unavailable. Check Settings.');return}
 if(state.busy&&deliverable==='report'){toast('This session is already researching.');return}
 if(!state.sid)await createSession();
 await api('/api/sessions/'+state.sid+'/messages','POST',{prompt,deliverable,jev_assistance:state.jevAssistance,report_review_provider:state.reportReviewProvider});
 $('question').value='';$('question').style.height='';showAnalyst();
 await refreshSession();
 $('chat').scrollTop=$('chat').scrollHeight;
}
function chatReportLinks(job){
 const kind=job.report_delivery_kind;
 const title=kind==='evidence_gap'?'Evidence gap saved':kind==='minimal'?'Your limited report is ready':'Your report is ready';
 const reviewer=reportReviewLabel(job.report_review_provider,job.report_review_model);
 return (job.published_reports||[]).flatMap(path=>{
  const match=/^\/reports\/(research-[a-f0-9]{12})\.md$/.exec(path);
  return match?[`<button class="chat-report-card" data-report="${match[1]}"><span class="chat-report-icon" aria-hidden="true">▤</span><span><strong>${title}</strong><small>Open the saved ${kind==='evidence_gap'?'notice':'report'}${jevOptionLabel(job.jev_assistance_requested)?' · '+jevOptionLabel(job.jev_assistance_requested):''}${reviewer&&kind!=='evidence_gap'?' · '+esc(reviewer):''}</small></span><span aria-hidden="true">↗</span></button>`]:[];
 }).join('');
}
function chatTurn(job){
 const continued=job.continued_from_job_id||job.prompt==='Continue the last unfinished research from saved sources and work. Reuse verified evidence and complete the requested answer or report.';
 const prompt=continued?'<div class="chat-resume-note">Continuing the previous request</div>':`<div class="chat-user"><div class="chat-message user">${esc(job.prompt)}</div></div>`;
 const reportLinks=chatReportLinks(job);
 let reply='';
 if(job.answer||reportLinks){
  const time=job.finished_at?new Intl.DateTimeFormat('en',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}).format(new Date(job.finished_at)):'';
  reply=`<article class="chat-assistant"><div class="chat-assistant-heading"><span class="chat-avatar" aria-hidden="true">✦</span><span>Aether</span><time>${esc(time)}</time></div><div class="chat-message">${job.answer?markdown(job.answer,job.verified_answer):'<p>Your report has been saved.</p>'}</div>${job.verified_answer?'<div class="chat-response-meta">Sources checked</div>':''}${reportLinks}</article>`;
 }else if(['failed','cancelled','interrupted'].includes(job.state)){
  reply='<div class="chat-turn-note">'+({failed:'This request could not finish.',cancelled:'You stopped this request.',interrupted:'This request was interrupted.'})[job.state]+'</div>';
 }
 return `<section class="chat-turn">${prompt}${reply}</section>`;
}
const researchStages={
 responding:'Preparing a reply',
 starting:'Starting research', working:'Working on your request', planning:'Planning the research',
 library:'Checking saved data and research', web:'Checking fresh data and news',
 evidence:'Reading the sources', analysis:'Analyzing the data', verification:'Checking sources and claims',
 revision:'Revising the findings', finalizing:'Preparing your result',
};
function researchElapsed(job){
 const start=Date.parse(job.created_at), end=job.finished_at?Date.parse(job.finished_at):Date.now();
 if(!Number.isFinite(start)||!Number.isFinite(end))return '';
 const seconds=Math.max(0,Math.floor((end-start)/1000));
 const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds/60)%60;
 return hours?`${hours}h ${minutes}m`:minutes?`${minutes}m ${seconds%60}s`:`${seconds}s`;
}
function renderResearchProgress(job,progress=null,active=false,stopping=false){
 const panel=$('job-status'),link=$('job-progress-link');
 state.researchActive=active;
 panel.hidden=!job||!active;
 link.hidden=!active||state.view==='research';
 if(!job)return;
 const stage=progress?.job_id===job.job_id?progress.stage:null;
 const phase=researchStages[stage]||researchStages.working;
 const outcomes={completed:job.published_reports?.length?(job.report_delivery_kind==='evidence_gap'?'Evidence gap saved':job.report_delivery_kind==='minimal'?'Limited report ready':'Report ready'):'Answer ready',
  failed:'Research could not finish',cancelled:'Research stopped',interrupted:'Research interrupted',
  waiting_for_conversation:'Waiting for a response'};
 const reviewer=reportReviewLabel(progress?.reviewer_provider||job.report_review_provider,progress?.reviewer_model||job.report_review_model);
 const label=stopping?'Stopping research…':outcomes[job.state]||phase+(stage==='verification'&&job.deliverable==='report'&&reviewer?' · '+reviewer:'');
 panel.dataset.state=job.state;
 if($('job-description').textContent!==label)$('job-description').textContent=label;
 const earlier=progress?.job_id===job.job_id?progress.previous||[]:[];
 const trail=(active||job.state==='completed'?earlier:[...earlier,stage]).map(step=>researchStages[step]).filter(Boolean).slice(-2);
 $('job-previous').hidden=true;
 $('job-previous').textContent=trail.length?trail.join(' · '):'';
 $('job-elapsed').textContent=researchElapsed(job);
 $('stop-job').hidden=!active;
 $('stop-job').disabled=stopping;
 $('job-progress-label').textContent=label;
 link.setAttribute('aria-label',label+' — open chat');
}
async function refreshSession(){
 if(!state.sid)return;
 const requestedSID=state.sid,snap=await api('/api/sessions/'+requestedSID);
 if(requestedSID!==state.sid)return;
 const jobs=snap.jobs,latest=jobs.at(-1);
 const job=jobs.find(x=>x.job_id===snap.research_progress?.job_id)||jobs.findLast(x=>!x.chat_reply);
 state.busy=Boolean(snap.active_job_id);
 const active=job?.job_id===snap.active_job_id&&['running','waiting_for_conversation'].includes(job?.state);
 renderResearchProgress(active||!latest?.chat_reply?job:null,snap.research_progress,active,active&&snap.stopping);
 $('send-question').disabled=!state.modelConfigured;
 $('create-chat-report').disabled=state.busy||!state.modelConfigured;
 configureJevControl();
 const canContinue=job&&['failed','cancelled','interrupted'].includes(job.state);
 $('job-error').hidden=!canContinue||Boolean(latest?.chat_reply);
 $('job-error').textContent=canContinue?'Your progress is saved.':'';
 $('continue-job').hidden=!canContinue||state.busy;
 const key=requestedSID+':'+jobs.map(x=>x.job_id+':'+x.state+':'+(x.finished_at||'')).join('|');
 if(key!==state.jobKey){
  const chat=$('chat'),follow=state.jobKey.split(':')[0]!==requestedSID||chat.scrollHeight-chat.clientHeight-chat.scrollTop<100;
  const scroll=chat.scrollTop;
  state.jobKey=key;
  chat.innerHTML=jobs.length?jobs.map(chatTurn).join(''):welcomeHTML;
  chat.scrollTop=follow?chat.scrollHeight:scroll;
  if(latest?.state==='completed'&&!latest.chat_reply){loadReports().catch(()=>{});loadLatestNews().catch(()=>{})}
 }
 state.lastJob=job;
}
document.addEventListener('click',async event=>{
 const control=event.target.closest('button,a');
 if(!control)return;
 try{
  if(control.hasAttribute('data-report-back')){selectView('reports');return}
  if(control.dataset.view){event.preventDefault();selectView(control.dataset.view)}
  if(control.dataset.source){event.preventDefault();await openSource(control.dataset.source,control.dataset.sourceSession)}
  if(control.hasAttribute('data-source-link')){event.preventDefault();await openSource(new URL(control.href).pathname.split('/').at(-1))}
  if(control.dataset.report)await showReport(control.dataset.report);
  if(control.dataset.prompt)await ask(control.dataset.prompt);
  if(control.dataset.close)$(control.dataset.close).close();
  if(control.dataset.audit){
   control.disabled=true;
   const result=await api('/api/audits/'+control.dataset.audit+'/'+control.dataset.kind,'POST',{});
   toast(result.status==='passed'?'Evidence check passed.':'Evidence check '+result.status+'.');
   control.disabled=false;
  }
 }catch(error){control.disabled=false;toast(error.message)}
});
function showMarketReviewStatus(text, busy=false){
 const status=$('market-review-status'), button=$('refresh-data');
 status.textContent=text;
 status.hidden=!text;
 button.disabled=busy||!state.modelConfigured;
 button.textContent=busy?'↻ Reviewing…':'↻ Refresh';
}
async function checkMarketReview(){
 if(!marketReviewSession||startingMarketReview)return;
 const snapshot=await api('/api/sessions/'+marketReviewSession);
 const pending=workspaceStorageGet('aether-market-review')===marketReviewSession;
 if(snapshot.active_job_id){
  workspaceStorageSet('aether-market-review',marketReviewSession);
  showMarketReviewStatus('Reviewing ETH data and looking for new sources…',true);
  return;
 }
 if(!pending)return;
 workspaceStorageRemove('aether-market-review');
 const job=snapshot.jobs.at(-1);
 if(job?.state==='completed'){
  showMarketReviewStatus('Market review complete. Open Latest News or Library to see saved sources.');
  await Promise.allSettled([loadData(),loadLatestNews(),...(state.view==='library'?[loadLibrary()]:[])]);
 }else{
  showMarketReviewStatus('The market review did not finish. Any saved sources are still available.');
 }
}
async function requestMarketReview(){
 if(!state.modelConfigured||startingMarketReview)return;
 startingMarketReview=true;
 showMarketReviewStatus('Starting an ETH market review…',true);
 try{
  if(!marketReviewSession){
   const session=await api('/api/sessions','POST',{title:marketReviewTitle,research_mode:'current'});
   marketReviewSession=session.session_id;
  }
  const path='/api/sessions/'+marketReviewSession;
  const snapshot=await api(path);
  if(!snapshot.active_job_id){
   await api(path+'/messages','POST',{
    deliverable:'answer',
    purpose:'market_refresh',
    prompt:'Refresh the Ethereum research library. Check the latest available ETH market data and source timestamps. Search for material Ethereum news and official announcements from the past seven days, compare with saved sources, and open the original public pages for relevant new material so the existing evidence tools save them in the searchable library. Skip duplicate material, distinguish confirmed events from plans, and cite saved evidence in a short status update. Report missing or inaccessible sources accurately. Focus on ETH. Do not publish a report or use personal portfolio details.',
   });
   api('/api/dashboard/refresh','POST',{}).catch(()=>{});
  }
  workspaceStorageSet('aether-market-review',marketReviewSession);
  showMarketReviewStatus('Reviewing ETH data and looking for new sources…',true);
 }catch{
  showMarketReviewStatus('The review could not start. Please try again.');
 }finally{
  startingMarketReview=false;
 }
}
$('refresh-data').onclick=()=>requestMarketReview();
$('open-research').onclick=showAnalyst;
$('mobile-analyst').onclick=showAnalyst;
$('close-analyst').onclick=()=>selectView('today');
$('new-session').onclick=()=>createSession().catch(()=>toast('Could not start a new conversation.'));
$('session-picker').onchange=async()=>{
 state.sid=$('session-picker').value;
 state.jobKey='';
 renderResearchProgress(null);
 workspaceStorageSet('aether-session',state.sid);
 await refreshSession();
};
$('question-form').onsubmit=event=>{
 event.preventDefault();
 const question=$('question').value.trim();
 if(question)ask(question).catch(error=>toast(error.message));
};
$('jev-assistance').onchange=event=>saveJevPreference(event.target.checked);
$('report-review-provider').onchange=event=>saveReportReviewPreference(event.target.value);
configureJevControl();
$('create-chat-report').onclick=()=>{
 const scope=$('question').value.trim();
 if(!scope){$('question').focus();toast('Enter what the report should cover.');return}
 ask(scope,'report').catch(error=>toast(error.message));
};
$('question').addEventListener('keydown',event=>{
 if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$('question-form').requestSubmit()}
});
$('question').addEventListener('input',()=>{
 $('question').style.height='auto';
 $('question').style.height=Math.min($('question').scrollHeight,160)+'px';
});
$('stop-job').onclick=async()=>{
 const sid=state.sid,job=state.lastJob;
 if(!sid||!job)return;
 $('stop-job').disabled=true;
 try{
  const result=await api('/api/sessions/'+sid+'/stop?job_id='+encodeURIComponent(job.job_id),'POST',{});
  if(sid!==state.sid)return;
  if(result.status==='stopping')renderResearchProgress(job,null,true,true);
  await refreshSession();
 }catch{
  if(sid===state.sid){$('stop-job').disabled=false;toast('Could not stop research.');}
 }
};
$('continue-job').onclick=async()=>{
 const sid=state.sid,job=state.lastJob;
 if(!sid||!job||state.busy)return;
 $('continue-job').disabled=true;
 try{
  await api('/api/sessions/'+sid+'/jobs/'+job.job_id+'/continue','POST',{});
  if(sid===state.sid){showAnalyst();await refreshSession();}
 }catch(error){toast(error.message)}
 finally{$('continue-job').disabled=false}
};
document.querySelectorAll('[data-period]').forEach(button=>button.onclick=()=>{
 state.period=Number(button.dataset.period);
 document.querySelectorAll('[data-period]').forEach(other=>{
  other.classList.toggle('selected',other===button);
  other.setAttribute('aria-pressed',String(other===button));
 });
 renderData();
});
$('chart-style').onclick=()=>{
 state.line=!state.line;
 $('chart-style').textContent=state.line?'Line':'Candles';
 $('chart-style').setAttribute('aria-pressed',String(state.line));
 renderPriceChart();
};
$('new-report').onclick=()=>{
 showAnalyst();
 $('question').placeholder='What should this report cover?';
};
$('report-query').addEventListener('input',()=>{
 clearTimeout(loadReports.timer);
 loadReports.timer=setTimeout(()=>loadReports().catch(()=>toast('Reports are temporarily unavailable.')),350);
});
new ResizeObserver(()=>{if(state.view==='today')renderPriceChart()}).observe($('market-chart'));
{
 let polling=false;
 setInterval(async()=>{
  if(polling||document.hidden)return;
  polling=true;
  try{
   await Promise.allSettled([refreshSession(),checkMarketReview(),...(state.view==='today'?[loadData()]:[])]);
  }finally{polling=false}
 },4000);
 setInterval(()=>{if(!document.hidden&&state.view==='today')loadData(true).catch(()=>{})},60000);
}
document.addEventListener('DOMContentLoaded',async()=>{
 try{
  selectView(location.hash.slice(1)||'home');
  const config=await api('/api/config');
  state.modelConfigured=config.configured;
  $('send-question').disabled=!config.configured;
  $('create-chat-report').disabled=!config.configured;
  $('refresh-data').disabled=!config.configured;
  $('new-report').disabled=!config.configured;
  configureJevControl();
  if(!config.configured)$('question').placeholder='The assistant is unavailable. Check Settings.';
  $('service-state').textContent='Local service connected';
  $('engine-status').textContent=config.configured?'Connected':'Unavailable';
  $('engine-details').innerHTML=metricRow('Assistant',config.configured?'Doubao':'Unavailable','Used for questions and reports')
   +metricRow('Web research',config.search_enabled?'Available':'Unavailable','Finds current public information');
  await Promise.allSettled([loadData(),sessions().then(()=>Promise.allSettled([refreshSession(),checkMarketReview()]))]);
  if(state.view==='today'&&(!state.dashboard.updated_at||Date.now()-Date.parse(state.dashboard.updated_at)>60000))await loadData(true);
 }catch{
  $('service-state').textContent='Connection needs attention';
  $('connection-banner').hidden=false;
 }
});
