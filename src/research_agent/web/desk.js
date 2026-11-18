'use strict';
// Saved-data product views. These actions never start research implicitly.
const deskState={contexts:new Map(),contextID:0};
window.AetherDesk={
  draftPrompt({text,origin}={}){
    if(typeof text!=='string'||!text.trim())return;
    selectView('research');
    $('question').value=text.trim();
    $('question').dispatchEvent(new Event('input',{bubbles:true}));
    $('question').focus();
    toast((origin?origin+' · ':'')+'Question drafted. Review it and press Send when ready.');
  },
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
$('research-chat-slot').append($('analyst'));
const expandedChat=$('expanded-chat');
const chatSize=$('chat-size');
function moveChat(panel,expanded){
 const chat=$('chat');
 const scrollTop=chat.scrollTop;
 const atEnd=chat.scrollHeight-chat.clientHeight-scrollTop<8;
 panel.append($('analyst'));
 chatSize.setAttribute('aria-expanded',String(expanded));
 chatSize.setAttribute('aria-label',expanded?'Collapse chat':'Expand chat');
 chatSize.querySelector('span').textContent=expanded?'Collapse':'Expand';
 requestAnimationFrame(()=>chat.scrollTo({top:atEnd?chat.scrollHeight:scrollTop,behavior:'instant'}));
}
chatSize.onclick=()=>{
 if(expandedChat.open){expandedChat.close();return}
 moveChat(expandedChat,true);
 expandedChat.showModal();
 chatSize.focus({preventScroll:true});
};
expandedChat.addEventListener('close',()=>{
 moveChat($('research-chat-slot'),false);
 if(state.view==='research')chatSize.focus({preventScroll:true});
});
$('retry-connection').onclick=async()=>{
 try{
  await api('/api/health');
  await Promise.allSettled([loadData(),refreshSession()]);
  toast('Workspace reconnected.');
 }catch{toast('Could not reconnect to the workspace.')}
};
const originalShowReport=showReport;
showReport=async function(id){
 const packet=await originalShowReport(id);
 const detail=$('report-detail'),meta=state.reports.find(report=>report.report_id===id);
 if(!packet||!meta||detail.dataset.reportId!==id)return;
 await mountReportFeedback(id,meta,packet);
 if(detail.dataset.reportId!==id)return;
 const older=state.reports.filter(report=>Date.parse(report.created_at)<Date.parse(meta.created_at));
 if(!older.length)return;
 const box=document.createElement('details');
 box.className='report-comparison report-disclosure';
 box.innerHTML='<summary>Compare with an earlier report</summary><select aria-label="Earlier report">'+older.map(report=>`<option value="${esc(report.report_id)}">${esc(date(report.created_at)+' · '+report.title)}</option>`).join('')+'</select><div class="comparison-body"></div>';
 detail.append(box);
 let loaded=false;
 const compare=async()=>{
  try{
   const result=await api('/api/reports/compare?older='+encodeURIComponent(box.querySelector('select').value)+'&newer='+encodeURIComponent(id));
   box.querySelector('.comparison-body').innerHTML=`<p>Judgment <strong>${esc(result.judgment.before.replaceAll('_',' '))}</strong> → <strong>${esc(result.judgment.after.replaceAll('_',' '))}</strong></p><p>Summary ${result.summary_changed?'changed':'unchanged'} · suggestion ${result.advice_changed?'changed':'unchanged'} · ${result.added_evidence_ids.length} added sources</p><div class="change-columns"><div><h3>Added or revised statements</h3>${result.added_claims.map(claim=>'<p>'+esc(claim.statement)+' '+claim.evidence_ids.map(rid=>sourceButton(rid,'Source ↗',meta.session_id)).join(' ')+'</p>').join('')||'<p>No statement changes.</p>'}</div><div><h3>Earlier statements no longer present</h3>${result.removed_claims.map(claim=>'<p>'+esc(claim.statement)+'</p>').join('')||'<p>None.</p>'}</div></div><p class="card-note">${esc(result.scope)}</p>`;
   loaded=true;
  }catch(error){box.querySelector('.comparison-body').textContent=error.message}
 };
 box.querySelector('select').onchange=compare;
 box.addEventListener('toggle',()=>{if(box.open&&!loaded)compare()});
};

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
async function mountReportFeedback(id,meta,reportPacket){
  const section=document.createElement('section');section.className='report-feedback section-gap';
  section.innerHTML='<div class="feedback-notices" aria-live="polite"></div><details><summary>Flag an unclear or outdated statement</summary><p class="card-note">Tell us what concerns you. You do not need to know the technical cause. A flag is saved for review and does not alter the original report.</p><form><label>Where in the report?<select name="claim" aria-label="Report statement"><option value="S1">Summary / whole report</option></select></label><label>Your concern<textarea name="note" rows="3" maxlength="4000" required placeholder="For example: this date may have changed, or I cannot understand this conclusion."></textarea></label><label>Type<select name="category"><option value="unclear">Hard to understand</option><option value="outdated">May be outdated</option><option value="source">Source may not support it</option><option value="calculation">Number or calculation</option><option value="other">Other</option></select></label><button class="button" type="submit">Save for review</button><p class="feedback-result card-note" role="status"></p></form></details>';
  $('report-detail').append(section);
  const form=section.querySelector('form'),result=section.querySelector('.feedback-result');
  try{
    const packet=reportPacket;
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
