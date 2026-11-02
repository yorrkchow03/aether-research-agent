'use strict';
// Saved-data product views. These actions never start research implicitly.
const deskState={packet:null,filter:'unseen',visible:8,contexts:new Map(),contextID:0,lastLoad:0,timeline:null};
window.AetherDesk={
  remember(context){const key=String(++deskState.contextID);deskState.contexts.set(key,context);if(deskState.contexts.size>1500)deskState.contexts.delete(deskState.contexts.keys().next().value);return key},
  claimContext(claim,draft,review){
    const lists=[['O',draft.observations],['K',draft.key_changes],['I',draft.interpretations],['VR',draft.advice?.rationale],['VC',draft.advice?.conditions],['VB',draft.advice?.counterpoints],['VI',draft.advice?.invalidation]];
    let claimID='';for(const [prefix,rows] of lists){const n=rows?.indexOf(claim)??-1;if(n>=0){claimID=prefix+(n+1);break}}
    const verdict=review?.verdicts?.find(v=>v.claim_id===claimID);
    return {statement:claim.statement,verdict:verdict||null,limits:draft.limitations||[],counterpoints:draft.advice?.counterpoints||[]};
  },
  answerContext(line,rid,verified){
    const d=verified.draft,a=d?.advice;if(!d)return null;
    const lists=[['A',d.facts],['I',d.interpretations],['VR',a?.rationale],['VC',a?.conditions],['VB',a?.counterpoints],['VI',a?.invalidation]];
    let target=null;
    for(const [prefix,rows] of lists)for(const [index,claim] of (rows||[]).entries())if(claim.evidence_ids.includes(rid)&&line.includes(esc(claim.statement)))target={...claim,claim_id:prefix+(index+1)};
    for(const [index,c] of (a?.historical_cases||[]).entries())if(c.evidence_ids.includes(rid)&&line.includes(esc(c.name)))target={claim_id:'VH'+(index+1),statement:'Historical comparison: '+c.name+'. Case ends '+c.case_end+'. '+c.availability+'. Similarities: '+c.similarities.join('; ')+'. Differences: '+c.differences.join('; ')+'. Transfer limits: '+c.limitations.join('; ')};
    if(!target&&a?.summary&&adviceSourceIDs(a).includes(rid)&&line.includes(esc(a.summary)))target={claim_id:'V0',statement:a.summary};
    if(!target)return null;
    return {statement:target.statement,verdict:verified.review?.verdicts?.find(v=>v.claim_id===target.claim_id)||null,
      limits:a?.missing_information||['This source review is not a truth or investment-performance guarantee.'],counterpoints:a?.counterpoints||[]};
  }
};
const originalAPI=api;
api=async function(...args){try{const r=await originalAPI(...args);$('connection-banner').hidden=true;$('service-state').textContent='Local service connected';$('service-dot').style.background='';return r}catch(e){if(e instanceof TypeError||/Failed to fetch|NetworkError|Load failed/i.test(e.message)){$('connection-banner').hidden=false;$('service-state').textContent='Disconnected · saved view';$('service-dot').style.background='#cda45e'}throw e}};
viewTitles.today=['Home','Market moves, important news, and your latest research.'];
viewTitles.research=['Research','Ask about the market. Follow the evidence.'];
const originalSelectView=selectView;
selectView=function(view){originalSelectView(view);if(state.view==='today')loadDesk().catch(e=>toast(e.message));if(state.view==='news')loadTimeline().catch(e=>toast(e.message));if(state.view==='settings'){loadMemoryValidation().catch(e=>toast(e.message));loadResearchCapabilities().catch(e=>toast(e.message))}};

async function loadResearchCapabilities(){
  let box=$('research-capabilities');if(!box){box=document.createElement('section');box.id='research-capabilities';box.className='card section-gap';$('view-settings').append(box)}
  const info=await api('/api/research-capabilities'),lib=info.library,mem=info.memory,checks=info.semantic_checks;
  const window=lib.available?lib.window_start.slice(0,10)+' → '+lib.window_end.slice(0,10)+' UTC':'Not installed';
  const reviewer={jev:'Jev',doubao:'Doubao',lexical:'Local relevance',disabled:'Disabled'}[checks.provider]||checks.provider;
  box.innerHTML='<div class="card-heading"><h2>Research memory & sources</h2></div>'
    +metricRow('Historical coverage',window,lib.available?lib.complete_datasets+' / '+lib.datasets+' dataset contracts complete':'Build or select a research library')
    +metricRow('Task memory',mem.enabled?'Enabled':'Disabled','Saved plan, tool artifacts and open questions')
    +metricRow('Additional source checks',checks.configured?reviewer:'Not configured',checks.mode==='shadow'?'Observe only · existing publication review remains active':checks.mode==='assist'?'Assist evidence selection · sources remain inspectable':'Disabled')
    +'<p class="card-note">Original evidence and versions remain available. Coverage gaps, model uncertainty, and source disagreements stay explicit. Stored capabilities do not certify report correctness.</p>';
}
// Keep the existing conversation, source links and job controls inside Research.
$('research-chat-slot').append($('analyst'));
$('open-research').onclick=showAnalyst;$('mobile-analyst').onclick=showAnalyst;
$('open-research').textContent='✦ Ask a question';
const originalRenderData=renderData;
renderData=function(){originalRenderData();renderTodayMarket()};

function renderTodayMarket(){
  const price=item('eth_price'),daily=indicator('eth_7d'),risk=indicator('eth_30d');
  const rows=[['Ethereum',usd(price.record?.values?.price_usd),(price.status==='ok'?'Saved ':'Last saved · source unavailable · ')+age(price.record?.observed_at),price.record?.record_id,''],['7D return',pct(daily.record?.values?.return_fraction),'Completed UTC daily closes',daily.record?.record_id,color(daily.record?.values?.return_fraction)],['30D max drawdown',pct(risk.record?.values?.max_drawdown_fraction),'Saved price-path decline',risk.record?.record_id,''],['Bitcoin',usd(val('btc_price','price_usd')),'Market reference · saved '+age(item('btc_price').record?.retrieved_at),item('btc_price').record?.record_id,'']];
  $('today-market').innerHTML=rows.map(([label,value,note,rid,cls])=>`<div class="stat-card"><div class="stat-label"><span>${label}</span>${sourceButton(rid,'↗')}</div><div class="stat-number ${cls}">${esc(value)}</div><div class="stat-foot">${esc(note)}</div></div>`).join('');
}
async function loadDesk(){
  const packet=await api('/api/desk');deskState.packet=packet;deskState.lastLoad=Date.now();
  $('inbox-count').textContent=packet.unseen_count;$('desk-date').innerHTML=esc(new Intl.DateTimeFormat('en-US',{weekday:'long',month:'long',day:'numeric'}).format(new Date()))+'<br>Personal research workspace';
  $('baseline-time').textContent=packet.baseline?'Saved '+date(packet.baseline.saved_at):'Save a point to compare future observations';
  $('today-changes').innerHTML=packet.market_changes.length?packet.market_changes.map(r=>metricRow(r.asset+' price change',pct(r.change_fraction),date(r.from_time)+' → '+date(r.to_time),r.record_id,color(r.change_fraction))).join(''):'<p class="desk-empty">No comparable newer quote is available yet. A saved comparison point keeps the interval explicit.</p>';
  const latest=packet.latest_report,d=latest?.report;
  $('today-judgment').innerHTML=d?`<span class="pill neutral">${esc(({informational:'Research brief',consider:'Conditions to investigate',wait:'Wait and monitor',avoid:'Caution',insufficient_evidence:'Evidence incomplete'})[d.judgment]||'Saved research')}</span><h3>${esc(d.advice?.summary||d.title)}</h3><p>${esc(d.advice?.rationale?.[0]?.statement||d.judgment_reason||d.summary)}</p><p class="card-note">Saved ${esc(date(latest.created_at))} · ${latest.review_status==='reviewed'?'Source-reviewed AI assessment':'Archived judgment'}${d.advice?.scope==='user_context'?' · Supplied personal context':' · Check applicability to your situation'}</p><button class="text-button" data-report="${esc(latest.report_id)}">Read brief & evidence ↗</button>`:'<p class="desk-empty">No saved research judgment yet. Ask the analyst a question or create a brief from the asset workspace.</p><button class="text-button" data-view="research">Prepare research context ↗</button>';
  const scheduler=packet.scheduler;
  $('today-monitoring').innerHTML=metricRow('Active schedules',scheduler.enabled+' / '+scheduler.schedules,'Runs while this computer and service are on')+scheduler.items.slice(0,3).map(s=>metricRow(s.name,s.enabled?'On':'Off',s.last_status||'No saved check')).join('')+(scheduler.enabled?'':'<p class="desk-empty">Automatic research is currently off. Enable a schedule when you want recurring checks.</p>');
  renderInbox();renderTodayMarket();
  await loadHoldingsContext();
}
async function loadHoldingsContext(){
  try{const p=await api('/api/portfolio'),positions=(p.positions||[]).filter(r=>finite(r.quantity)&&Number(r.quantity)>0);$('today-holdings').innerHTML=positions.length?positions.map(r=>metricRow(r.asset+' recorded position',Number(r.quantity).toLocaleString('en-US',{maximumFractionDigits:8}),r.market_value_usd===null?'Valuation unavailable; no invented mark':usd(r.market_value_usd)+' saved valuation',r.mark_source?.record_id)).join('')+'<p class="card-note">Research relevance follows your manual records. It does not prove these are all your holdings.</p>':'<p class="desk-empty">No asset position is recorded. Research remains general until you supply context or add your actual transactions.</p><button class="text-button" data-view="research">Set research preferences ↗</button>'}catch(e){$('today-holdings').innerHTML='<p class="desk-empty">Recorded holdings unavailable: '+esc(e.message)+'</p>'}
}
function renderInbox(){
  const rows=(deskState.packet?.inbox||[]).filter(r=>deskState.filter==='all'||(deskState.filter==='unseen'?!r.seen:r.kind===deskState.filter));
  const symbols={event:'◷',report:'▤',source:'↗',scheduled_run:'◉'};
  $('research-inbox').innerHTML=rows.length?rows.slice(0,deskState.visible).map(r=>`<article class="inbox-row ${r.seen?'seen':''}"><span class="activity-icon">${symbols[r.kind]||'·'}</span><div><h3>${esc(r.title)} ${r.seen?'':'<span class="pill neutral">Unseen</span>'}</h3><p>${esc(r.kind==="scheduled_run"?"Scheduled research "+r.status+". Saved work and diagnostics are retained.":r.detail)}</p><p class="why">${esc(r.why)}</p><div class="activity-actions">${r.report_id?`<button class="text-button" data-report="${esc(r.report_id)}">Read research ↗</button>`:''}${(r.evidence_ids||[]).map(rid=>sourceButton(rid,'Read evidence ↗')).join(' ')}${r.session_id&&!r.report_id?`<button class="text-button" data-session="${esc(r.session_id)}">Open saved research ↗</button>`:''}${r.seen?'':`<button class="text-button" data-seen="${esc(r.id)}">Mark seen</button>`}</div></div><time>${esc(age(r.time))}</time></article>`).join('')+(rows.length>deskState.visible?'<button class="text-button" id="more-inbox">Show more saved activity ↧</button>':''):'<p class="desk-empty">Nothing in this view. New saved activity will appear here as research and monitoring run.</p>';
}
$('save-baseline').onclick=async()=>{try{await api('/api/desk/seen','POST',{capture_baseline:true});await loadDesk();toast('Comparison point saved from available quotes')}catch(e){toast(e.message)}};
$('mark-inbox-seen').onclick=async()=>{try{await api('/api/desk/seen','POST',{ids:(deskState.packet?.inbox||[]).filter(r=>!r.seen).map(r=>r.id)});await loadDesk()}catch(e){toast(e.message)}};
$('retry-connection').onclick=async()=>{try{await api('/api/health');await Promise.all([loadData(),loadDesk(),refreshSession()]);toast('Workspace reconnected')}catch(e){toast(e.message)}};
document.addEventListener('click',async event=>{const b=event.target.closest('button');if(!b)return;try{if(b.id==='more-inbox'){deskState.visible+=10;renderInbox()}if(b.dataset.inboxFilter){deskState.visible=8;deskState.filter=b.dataset.inboxFilter;document.querySelectorAll('[data-inbox-filter]').forEach(n=>n.classList.toggle('selected',n===b));renderInbox()}if(b.dataset.seen){await api('/api/desk/seen','POST',{ids:[b.dataset.seen]});await loadDesk()}}catch(e){toast(e.message)}});

async function loadTimeline(){
  const packet=await api('/api/news/events/history');deskState.timeline=packet;
  let container=$('event-history');if(!container){container=document.createElement('section');container.id='event-history';container.className='card timeline';$('view-news').append(container)}
  container.innerHTML='<div class="card-heading"><div><h2>How the view evolved</h2><p>Saved classifications retain their earlier versions and source links.</p></div></div>'+(packet.items.length?packet.items.slice(0,25).map(e=>`<article class="timeline-item"><span class="pill neutral">${esc(e.status)} · v${e.version}</span><h3>${esc(e.title)}</h3><p>${esc(e.summary)}</p><p>Saved ${esc(date(e.available_at))} · ${e.evidence_ids.map(rid=>sourceButton(rid,'Source ↗')).join(' ')}</p></article>`).join(''):'<p class="desk-empty">No source-reviewed events have been recorded yet.</p>')+`<p class="card-note">${packet.possibly_truncated?'Showing the latest saved revisions; older history may be omitted. ':''}These are analyst classifications. A later saved version does not independently prove an outcome.</p>`;
}
const originalShowReport=showReport;
showReport=async function(id){await originalShowReport(id);const meta=state.reports.find(r=>r.report_id===id);if(!meta)return;await mountReportFeedback(id,meta);const older=state.reports.filter(r=>Date.parse(r.created_at)<Date.parse(meta.created_at));if(!older.length)return;const box=document.createElement('section');box.className='report-comparison';box.innerHTML='<h3>Compare with an earlier report</h3><select aria-label="Earlier report">'+older.map(r=>`<option value="${esc(r.report_id)}">${esc(date(r.created_at)+' · '+r.title)}</option>`).join('')+'</select><div class="comparison-body"></div>';$('report-detail').append(box);const compare=async()=>{try{const d=await api('/api/reports/compare?older='+encodeURIComponent(box.querySelector('select').value)+'&newer='+encodeURIComponent(id));box.querySelector('.comparison-body').innerHTML=`<p>Judgment: <strong>${esc(d.judgment.before.replaceAll('_',' '))}</strong> → <strong>${esc(d.judgment.after.replaceAll('_',' '))}</strong></p><p>Summary ${d.summary_changed?'changed':'unchanged'} · suggestion ${d.advice_changed?'changed':'unchanged'} · ${d.added_evidence_ids.length} added sources</p><div class="change-columns"><div><h3>Added or revised statements</h3>${d.added_claims.map(c=>'<p>'+esc(c.statement)+' '+c.evidence_ids.map(rid=>sourceButton(rid,'Source ↗',meta.session_id)).join(' ')+'</p>').join('')||'<p>No statement changes.</p>'}</div><div><h3>Earlier statements no longer present</h3>${d.removed_claims.map(c=>'<p>'+esc(c.statement)+'</p>').join('')||'<p>None.</p>'}</div></div><p class="card-note">${esc(d.scope)}</p>`}catch(e){box.querySelector('.comparison-body').textContent=e.message}};box.querySelector('select').onchange=compare;await compare()};

async function loadMemoryValidation(){
  let box=$('memory-validation');if(!box){box=document.createElement('section');box.id='memory-validation';box.className='card section-gap';$('view-settings').append(box)}
  const packet=await api('/api/evaluations/memory/latest');
  if(!packet.summary){box.innerHTML='<h2>Evidence memory evaluation</h2><p class="desk-empty">No frozen memory evaluation has been saved.</p>';return}
  const s=packet.summary,rows=s.retrieval.filter(r=>r.split==='held_out');
  box.innerHTML=`<div class="card-heading"><h2>Evidence memory evaluation</h2><span class="pill neutral">${esc(packet.version)} · AI-authored labels</span></div><p class="body-copy">${s.retrieval_questions} frozen questions/probes across ${s.topic_groups} topic groups; ${s.offline_checks} offline checks in ${s.offline_families} contract families, ${s.failures} failures. This measures source retrieval and contracts.</p><div class="table-wrap"><table class="data-table"><thead><tr><th>Selector</th><th>Required passage recall</th><th>Cutoff probes blocked</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.variant.replaceAll('_',' '))}</td><td>${finite(r.required_passage_recall)?(r.required_passage_recall*100).toFixed(1)+'%':'Unknown'} · ${r.topic_groups} groups</td><td>${r.target_capture_blocked} / ${r.temporal_probes}</td></tr>`).join('')}</tbody></table></div><p class="card-note">This small single-publisher corpus has already been inspected. It is not untouched confirmation for later tuning. Full event revision histories and human review remain incomplete. LLM correctness, appropriate abstention and investment performance were not measured by this retrieval run. The weaker coverage result is preserved.</p>`;
}

openSource=async function(rid,sourceSession,contextKey){
  const shared=/^(live|page|web)-/.test(rid);if(!shared&&!state.sid&&!sourceSession)await createSession();sourceSession=sourceSession||state.sid;
  const dialog=$('source-dialog'),context=deskState.contexts.get(contextKey);$('source-detail').innerHTML='<p class="desk-empty">Loading saved evidence…</p>';if(!dialog.open)dialog.showModal();
  try{const packet=await api(shared?'/api/evidence/'+encodeURIComponent(rid):`/api/sessions/${sourceSession}/evidence/${encodeURIComponent(rid)}`),r=packet.record,src=r.source||{},url=safeURL(r.url||src.url),verdict=context?.verdict,quotes=(verdict?.quotes||[]).filter(q=>q.record_id===rid),kind=r.record_id.startsWith('page-')?'Original page':r.recipe_sha256?'Derived calculation':r.source_kind==='feed_item'?'Publisher feed':r.record_id.startsWith('web-')?'Search excerpt':'Saved data';
    const claim=context?`<aside class="claim-pane"><h3>Research claim</h3><p>${esc(context.statement)}</p><span class="pill ${['insufficient','contradicted'].includes(verdict?.status)?'warn':'neutral'}">${esc(verdict?.status?.replaceAll('_',' ')||'No claim-specific review available')}</span>${verdict?'<p class="support-reason">'+esc(verdict.reason)+'</p>':''}${context.counterpoints?.length?'<h3 class="section-gap">Counterpoints in this report</h3>'+context.counterpoints.map(c=>'<p>'+esc(c.statement)+' '+c.evidence_ids.map(id=>sourceButton(id,'Evidence ↗',sourceSession)).join(' ')+'</p>').join(''):''}<h3 class="section-gap">Limits of this research</h3>${context.limits.slice(0,4).map(t=>'<p class="support-reason">'+esc(t)+'</p>').join('')}<p class="card-note">Support is an AI assessment of saved material, not a guarantee of truth or future performance.</p></aside>`:'';
    const values=Object.keys(r.values||{}).length?'<h3>Saved numeric fields</h3><div class="table-wrap"><table class="data-table"><tbody>'+Object.entries(r.values).map(([key,value])=>'<tr><td>'+esc(key)+'</td><td>'+esc(value)+' '+esc(r.units?.[key]||'')+'</td></tr>').join('')+'</tbody></table></div>':'';
    const allInputs=r.input_record_ids||[],shownInputs=allInputs.length>40?[...allInputs.slice(0,20),...allInputs.slice(-20)]:allInputs;
    const sourceURL=shared?'/api/evidence/'+encodeURIComponent(rid):`/api/sessions/${sourceSession}/evidence/${encodeURIComponent(rid)}`;
    const provenance={...r};
    if(allInputs.length>40){provenance.input_record_ids=shownInputs;provenance.input_preview_note='First and last20 IDs only; download the complete evidence record for all '+allInputs.length+' inputs.'}
    if(r.records?.length>20){provenance.records=[...r.records.slice(0,3),...r.records.slice(-3)];provenance.observation_preview_note='First and last3 saved observations only; the original contains '+r.records.length+' observations.'}
    const inputHTML=allInputs.length?'<h3>Underlying evidence</h3>'+(allInputs.length>40?'<p class="card-note">Showing the first and last20 of '+allInputs.length+' inputs. The complete record retains every observation and source.</p>':'')+shownInputs.map(id=>sourceButton(id,'Read input ↗',sourceSession)).join(' · '):'';
    const download=`<p><a href="${esc(sourceURL)}" download="${esc(rid)}.json">Download complete evidence JSON ↗</a></p>`;
    $('source-detail').innerHTML=`<span class="pill neutral">${kind}</span><h3>${esc(r.title||src.name||r.dataset_id||'Saved source')}</h3><div class="metadata"><div><small>Published</small>${esc(date(r.published_at))}</div><div><small>Captured</small>${esc(date(r.retrieved_at))}</div><div><small>Observed / effective</small>${esc(date(r.observed_at))}</div></div>${url?`<a href="${esc(url)}" target="_blank" rel="noopener">Open original publisher / API ↗</a>`:''}<div class="${context?'source-reader':''}">${claim}<div><h3>${quotes.length?'Passages used in the claim review':'Saved source material'}</h3>${quotes.map(q=>'<blockquote class="source-passage">'+esc(q.text)+'</blockquote>').join('')}${context&&!quotes.length?'<p class="card-note">No saved review passage for this particular source. Read the material and support limits directly.</p>':''}${values}${r.text?`<${quotes.length?'details':'div'} class="source-full">${quotes.length?'<summary>Read the full saved text</summary>':''}<div class="source-body">${esc(r.text)}</div></${quotes.length?'details':'div'}>`:''}${r.method?'<h3 class="section-gap">Calculation method</h3><p class="source-body">'+esc(r.method)+'</p>':''}${inputHTML}</div></div>${download}<details class="section-gap"><summary>Technical provenance & snapshot details</summary><pre>${esc(JSON.stringify(provenance,null,2))}</pre></details><p class="card-note">${esc(r.scope||packet.scope)} Publication metadata may be unverified. A matching raw snapshot establishes integrity, not publisher truth.</p>`;
  }catch(e){$('source-detail').innerHTML='<p class="desk-empty">'+esc(e.message)+'</p>'}
};
document.addEventListener('click',event=>{const b=event.target.closest('[data-source][data-evidence-context]');if(!b?.dataset.evidenceContext)return;event.preventDefault();event.stopImmediatePropagation();openSource(b.dataset.source,b.dataset.sourceSession,b.dataset.evidenceContext).catch(e=>toast(e.message))},true);
setInterval(()=>{if(!document.hidden&&state.view==='today'&&Date.now()-deskState.lastLoad>15000)loadDesk().catch(()=>{})},5000);
// Existing deep links resolve through workspaceRoutes; new workspaces open Home.
if(!location.hash){history.replaceState(null,'','#home')}


async function mountReportFeedback(id,meta){
  const section=document.createElement('section');section.className='report-feedback section-gap';
  section.innerHTML='<div class="feedback-notices" aria-live="polite"></div><details><summary>Flag an unclear or outdated statement</summary><p class="card-note">Tell us what concerns you. You do not need to know the technical cause. A flag is saved for review and does not alter the original report.</p><form><label>Where in the report?<select name="claim" aria-label="Report statement"><option value="S1">Summary / whole report</option></select></label><label>Your concern<textarea name="note" rows="3" maxlength="4000" required placeholder="For example: this date may have changed, or I cannot understand this conclusion."></textarea></label><label>Type<select name="category"><option value="unclear">Hard to understand</option><option value="outdated">May be outdated</option><option value="source">Source may not support it</option><option value="calculation">Number or calculation</option><option value="other">Other</option></select></label><button class="button" type="submit">Save for review</button><p class="feedback-result card-note" role="status"></p></form></details>';
  $('report-detail').append(section);
  const form=section.querySelector('form'),result=section.querySelector('.feedback-result');
  try{
    const packet=await api('/api/sessions/'+meta.session_id+'/artifact?path='+encodeURIComponent('/reports/'+id+'.json'));
    const draft=packet.report,targets=[...(draft.observations||[]).map((c,n)=>['O'+(n+1),c.statement]),...(draft.key_changes||[]).map((c,n)=>['K'+(n+1),c.statement]),...(draft.interpretations||[]).map((c,n)=>['I'+(n+1),c.statement])];
    form.elements.claim.insertAdjacentHTML('beforeend',targets.map(([key,text])=>'<option value="'+esc(key)+'">'+esc(key+' · '+text.slice(0,120))+'</option>').join(''));
  }catch(e){result.textContent='Statement list unavailable; you can still flag the summary.'}
  const refresh=async()=>{const packet=await api('/api/reports/'+encodeURIComponent(id)+'/feedback');section.querySelector('.feedback-notices').innerHTML=packet.items.map(item=>{
    const review=item.latest_review,resolved=item.resolution;
    const label=resolved?'Updated report available':review?(review.assessment==='needs_correction'?'Correction requested':review.assessment==='not_reproduced'?'Concern reviewed · not reproduced':'More evidence needed'):'Concern saved · awaiting review';
    return '<article class="feedback-notice"><span class="pill '+(resolved?'neutral':'warn')+'">'+esc(label)+'</span><p>'+esc(item.note)+'</p>'+(review?.correction?'<p>'+esc(review.correction)+'</p>':'')+(review?'<p class="card-note">'+(review.reviewer_kind==='independent_model'?'Independent AI review; not human verification.':'Attributed '+esc(review.reviewer_kind)+' review.')+' Original report retained.</p>':'')+(review?.evidence_ids||[]).map(rid=>sourceButton(rid,'Review source ↗',meta.session_id)).join(' ')+(resolved?'<p><button class="text-button" data-report="'+esc(resolved.replacement_report_id)+'">Read the source-reviewed update ↗</button></p>':'')+'</article>'}).join('')};
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;try{await api('/api/reports/'+encodeURIComponent(id)+'/feedback','POST',{note:form.elements.note.value,claim_id:form.elements.claim.value,category:form.elements.category.value});form.elements.note.value='';result.textContent='Concern saved for review. The original report is unchanged.';await refresh()}catch(e){result.textContent=e.message}finally{button.disabled=false}};
  try{await refresh()}catch(e){section.querySelector('.feedback-notices').textContent='Saved review notices could not be loaded: '+e.message}
}
