'use strict';
(() => {
  const host = document.getElementById('portfolio-workspace');
  if (!host) return;
  const el = id => host.querySelector('#' + id);
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const known = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const money = value => known(value) ? new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2}).format(Number(value)) : 'Unknown';
  const number = value => known(value) ? new Intl.NumberFormat('en-US', {maximumFractionDigits:8}).format(Number(value)) : 'Unknown';
  const fraction = value => known(value) ? (Number(value) * 100).toFixed(1) + '%' : 'Unknown';
  const when = value => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('en-US', {dateStyle:'medium', timeStyle:'short', timeZone:'UTC'}).format(new Date(value)) + ' UTC' : 'Unknown';
  const localNow = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0,16);
  const state = {mode:'example', portfolio:null, ledgerReady:false, ledgerRevision:0, ledgerMutations:0, transactions:[], loading:false, loadingAsOf:'', pendingLoad:false, pendingRefresh:false, sequence:0, preview:null, previewGeneration:0, previewInFlight:false, asOf:'', loadedAsOf:'', importFile:null};
  /* portfolio example defaults start */
  function exampleDefaults(profile) {
    const baseline={cash:3160,ethQuantity:2.4,ethPrice:2850,ethCost:2600,btcQuantity:0,btcPrice:72000,btcCost:65000,asset:'ETH',notional:500,feeBps:10,cap:75,stress:30,loss:300};
    const supplied=profile&&typeof profile==='object'?profile:{};
    for(const key of Object.keys(baseline)){
      if(key==='asset'){if(['ETH','BTC'].includes(supplied.asset))baseline.asset=supplied.asset;continue;}
      const value=supplied[key];
      if(typeof value!=='number'||!Number.isFinite(value)||value<0)continue;
      if(['ethPrice','btcPrice','notional','cap','stress'].includes(key)&&value<=0)continue;
      if(['cap','stress'].includes(key)&&value>100||key==='feeBps'&&value>500||key==='loss'&&value<=0)continue;
      baseline[key]=value;
    }
    return baseline;
  }
  /* portfolio example defaults end */
  let defaults=exampleDefaults(null);
  let currentProfile='{}',scenarioDirty=false;
  const canVoidTransactions=()=>true;
  const errorText = error => typeof error?.message === 'string' ? error.message : 'This operation could not be completed.';
  const status = (id,text,tone='') => { const node=el(id); if(node){ node.textContent=text; node.className='pw-status '+tone; } };
  const source = (rid,label='Inspect saved source ↗') => {
    if (!rid || !/^[a-zA-Z0-9_-]{1,120}$/.test(rid)) return '';
    if (typeof sourceButton === 'function') return sourceButton(rid,label);
    return '<span class="pw-muted">'+escapeHTML(rid)+'</span>';
  };
  const tile = (label,value,note) => '<div class="stat-card"><div class="stat-label">'+escapeHTML(label)+'</div><div class="stat-number">'+escapeHTML(value)+'</div><div class="stat-foot">'+escapeHTML(note)+'</div></div>';
  const input = (id,label,value,attrs='') => '<label for="'+id+'">'+label+'<input id="'+id+'" value="'+escapeHTML(value)+'" '+attrs+'></label>';
  const numeric = 'type="number" step="any" min="0" required';

  /* portfolio scenario math start */
  function calculateScenario(a) {
    const required=['cash','ethQuantity','ethPrice','ethCost','btcQuantity','btcPrice','btcCost','notional','feeBps','cap','stress'];
    if(required.some(k=>a[k]===null||a[k]===''||!Number.isFinite(Number(a[k])))) throw Error('Complete all scenario values before comparing.');
    const n=Object.fromEntries(required.map(k=>[k,Number(a[k])]));
    if(Object.values(n).some(v=>v<0)||n.ethPrice<=0||n.btcPrice<=0||n.notional<=0||n.feeBps>500||n.cap<=0||n.cap>100||n.stress<=0||n.stress>100) throw Error('Use positive prices and purchase amount, allocation and stress from 0 to 100%, and fees from 0 to 500 bps.');
    if(!['ETH','BTC'].includes(a.asset)) throw Error('Choose ETH or BTC.');
    const loss=a.loss===null||a.loss===''?null:Number(a.loss);
    if(loss!==null&&(!Number.isFinite(loss)||loss<=0)) throw Error('The loss budget must be positive, or leave it blank.');
    const eth=n.ethQuantity*n.ethPrice,btc=n.btcQuantity*n.btcPrice,crypto=eth+btc;
    const equity=n.cash+crypto,basis=n.ethQuantity*n.ethCost+n.btcQuantity*n.btcCost;
    if(![eth,btc,crypto,equity,basis].every(Number.isFinite)) throw Error('The entered amounts are too large to calculate a finite scenario.');
    if(equity<=0) throw Error('The scenario needs positive total equity.');
    const fee=n.notional*n.feeBps/10000,afterEquity=equity-fee,afterCash=n.cash-n.notional-fee;
    const selectedValue=a.asset==='ETH'?eth:btc,selectedPrice=a.asset==='ETH'?n.ethPrice:n.btcPrice;
    const concentration=afterEquity>0?(selectedValue+n.notional)/afterEquity:null;
    const stressLoss=n.notional*n.stress/100+fee;
    const quantity=n.notional/selectedPrice;
    if(![fee,afterEquity,afterCash,stressLoss,quantity,crypto*n.stress/100,(crypto+n.notional)*n.stress/100+fee].every(Number.isFinite)) throw Error('The entered amounts are outside the supported calculation range.');
    const caps=[n.cash/(1+n.feeBps/10000),((n.cap/100)*equity-selectedValue)/(1+(n.cap/100)*(n.feeBps/10000))];
    if(loss!==null)caps.push(loss/(n.stress/100+n.feeBps/10000));
    const maximum=Math.max(0,Math.min(...caps));
    const constraints=[];
    if(afterCash<0)constraints.push('This purchase plus its fee exceeds the entered cash reserve.');
    if(concentration===null)constraints.push('The fee leaves no positive equity for an allocation comparison.');
    else if(concentration>n.cap/100)constraints.push('The selected asset would exceed your entered allocation cap.');
    if(loss!==null&&stressLoss>loss)constraints.push('Loss on the intended purchase in the entered stress exceeds the loss budget.');
    return {equity,basis,unrealized:crypto-basis,eth,btc,cash:n.cash,crypto,fee,afterEquity,afterCash,
      beforeConcentration:selectedValue/equity,concentration,quantity,
      beforeStressLoss:crypto*n.stress/100,afterStressLoss:(crypto+n.notional)*n.stress/100+fee,
      stressLoss,maximum,constraints,asset:a.asset,notional:n.notional,feeBps:n.feeBps,cap:n.cap,stress:n.stress,loss};
  }
  /* portfolio scenario math end */

  function allocation(parts,complete=true) {
    if(!complete)return '<div class="pw-unknown">Allocation unavailable: at least one held asset has no saved mark. Missing values are not zero.</div>';
    const rows=parts.filter(p=>known(p.value)&&Number(p.value)>0),total=rows.reduce((sum,p)=>sum+Number(p.value),0);
    if(!total)return '<p class="pw-muted">No positive balance to chart.</p>';
    return '<div class="pw-allocation" role="img" aria-label="'+escapeHTML(rows.map(p=>p.label+' '+fraction(p.value/total)).join(', '))+'">'+rows.map(p=>'<span class="pw-allocation-'+escapeHTML(p.key)+'" style="width:'+Math.max(0,Number(p.value)/total*100).toFixed(5)+'%"></span>').join('')+'</div><div class="pw-allocation-legend">'+rows.map(p=>'<span><i class="pw-allocation-'+escapeHTML(p.key)+'"></i>'+escapeHTML(p.label)+' <strong>'+fraction(p.value/total)+'</strong><small>'+money(p.value)+'</small></span>').join('')+'</div>';
  }
  function draft(text,origin) {
    if(typeof window.AetherDesk?.draftPrompt==='function') { window.AetherDesk.draftPrompt({text,origin}); return; }
    const field=document.getElementById('question');
    if(field){field.value=text;field.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-view="research"]')?.click();field.focus();}
  }
  function mount() {
    host.innerHTML = [
      '<div class="investment-banner pw-banner"><div><span class="eyebrow">PERSONAL INVESTMENT WORKSPACE</span><h2>Understand the account behind a decision</h2><p>Compare assumptions, inspect saved marks and keep a private transaction record.</p></div><span class="pill neutral" id="pw-mode-badge">Example scenario</span></div>',
      '<div class="pw-tabs" role="tablist" aria-label="Portfolio data mode"><button type="button" id="pw-tab-example" role="tab" aria-selected="true" aria-controls="pw-example">Example scenario</button><button type="button" id="pw-tab-ledger" role="tab" aria-selected="false" aria-controls="pw-ledger">My local ledger</button></div>',
      '<section id="pw-example" class="pw-panel" role="tabpanel" aria-labelledby="pw-tab-example"><p class="pw-intro">Editable hypothetical values. Nothing here is written to your ledger or sent to a market provider.</p><p id="pw-example-profile" class="card-note"></p><div id="pw-example-stats" class="stats-grid"></div><div class="pw-two-column section-gap">',
      '<section class="card"><div class="card-heading"><div><h2>Account assumptions</h2><p>Prices are entered examples, not current quotes.</p></div><button type="button" class="text-button" id="pw-reset">Reset example</button></div><form id="pw-scenario-form"><div class="pw-fields pw-fields-cash">',
      input('pw-cash','Cash reserve · USD',defaults.cash,numeric),'</div><div class="pw-asset-inputs"><h3>Ethereum <span>ETH</span></h3><div class="pw-fields">',
      input('pw-eth-quantity','Quantity',defaults.ethQuantity,numeric),input('pw-eth-price','Assumed price · USD',defaults.ethPrice,numeric),input('pw-eth-cost','Unit cost including fees · USD',defaults.ethCost,numeric),
      '</div></div><div class="pw-asset-inputs"><h3>Bitcoin <span>BTC</span></h3><div class="pw-fields">',
      input('pw-btc-quantity','Quantity',defaults.btcQuantity,numeric),input('pw-btc-price','Assumed price · USD',defaults.btcPrice,numeric),input('pw-btc-cost','Unit cost including fees · USD',defaults.btcCost,numeric),
      '</div></div><p class="card-note">Position value = quantity × assumed price. Cost basis = quantity × entered unit cost. Cash adds to equity. These assumptions are not transaction records.</p></form></section>',
      '<section class="card"><div class="card-heading"><div><h2>Allocation and a possible purchase</h2><p>Update the inputs to see the trade-offs.</p></div></div><div id="pw-example-allocation"></div><form id="pw-scenario-purchase"><div class="pw-fields pw-fields-two"><label for="pw-example-asset">Purchase asset<select id="pw-example-asset"><option>ETH</option><option>BTC</option></select></label>',
      input('pw-example-notional','Purchase notional · USD',defaults.notional,'type="number" min="0.01" step="any" required'),input('pw-example-fee','Entry fee · basis points',defaults.feeBps,'type="number" min="0" max="500" step="any" required'),input('pw-example-cap','Single-asset cap · %',defaults.cap,'type="number" min="0.01" max="100" step="any" required'),input('pw-example-stress','Stress decline · %',defaults.stress,'type="number" min="0.01" max="100" step="any" required'),input('pw-example-loss','Purchase loss budget · USD',defaults.loss,'type="number" min="0.01" step="any" placeholder="Optional"'),
      '</div></form></section></div><section class="card section-gap"><div class="card-heading"><div><h2>Before and after the intended purchase</h2><p>No order, price forecast or fill assumption.</p></div><button type="button" class="button" id="pw-example-ask">Draft an analyst question ↗</button></div><div id="pw-example-comparison" aria-live="polite"></div></section></section>',
      '<section id="pw-ledger" class="pw-panel" role="tabpanel" aria-labelledby="pw-tab-ledger" hidden><div class="pw-ledger-toolbar"><p class="pw-intro">Private manual records in your workspace. Opening this page reads saved market data only.</p><div class="investment-actions"><button type="button" class="button" id="pw-reload">Reload saved records</button><button type="button" class="button" id="pw-refresh-marks">Refresh market marks</button></div></div>',
      '<p id="pw-ledger-status" class="pw-status" role="status">Select this mode to load your saved records.</p><div id="pw-ledger-stats" class="stats-grid"></div><section class="card section-gap"><div class="card-heading"><div><h2>Recorded holdings</h2><p id="pw-ledger-scope">Manual records are not an exchange balance.</p></div><button type="button" class="text-button" id="pw-ledger-ask">Draft an analyst question ↗</button></div><div id="pw-ledger-allocation"></div><div id="pw-ledger-positions"></div><p id="pw-ledger-gaps" class="card-note"></p>',
      '<details class="pw-disclosure"><summary>Snapshot at an earlier economic date</summary><form id="pw-asof-form" class="pw-asof-form"><label for="pw-asof">As of · your local time<input id="pw-asof" type="datetime-local"></label><button class="button" type="submit">Show saved snapshot</button><button class="text-button" type="button" id="pw-current">Return to current records</button></form><p class="card-note">Uses the current corrected ledger and saved marks available by the selected date. This is not a reconstruction of what you knew then. All displayed timestamps are UTC.</p></details></section>',
      '<div class="pw-two-column section-gap"><section class="card"><div class="card-heading"><div><h2>Record a transaction</h2><p>Funding history must precede purchases.</p></div></div><form id="pw-transaction-form"><div class="pw-fields pw-fields-two"><label for="pw-tx-kind">Type<select id="pw-tx-kind"><option value="deposit">Cash deposit</option><option value="withdrawal">Cash withdrawal</option><option value="buy">Buy</option><option value="sell">Sell</option><option value="transfer_in">Transfer in</option><option value="transfer_out">Transfer out</option></select></label><label for="pw-tx-asset">Asset<select id="pw-tx-asset"><option value="USD">USD</option><option value="ETH">ETH</option><option value="BTC">BTC</option></select></label><label for="pw-tx-time">Execution time · your local time<input id="pw-tx-time" type="datetime-local" required></label>',
      input('pw-tx-quantity','Quantity or USD amount','','type="number" min="0.00000001" step="any" required'),
      '<label for="pw-tx-price" id="pw-tx-price-label" hidden>Execution price · USD<input id="pw-tx-price" type="number" min="0.00000001" step="any"></label>',
      input('pw-tx-fee','Fee · USD',0,'type="number" min="0" step="any" required'),
      '<label for="pw-tx-basis" id="pw-tx-basis-label" hidden>Total incoming cost basis · USD<input id="pw-tx-basis" type="number" min="0" step="any" placeholder="Blank means unknown"></label>',
      input('pw-tx-reference','External reference · optional','','type="text" maxlength="120" placeholder="Unique import or trade ID"'),
      '</div><label for="pw-tx-note" class="pw-block-label">Note · optional<input id="pw-tx-note" type="text" maxlength="300" placeholder="For your local record"></label><p class="card-note" id="pw-tx-help">Cash amounts are in USD. Deposits and withdrawals have no execution price.</p><button type="submit" class="button primary">Save actual transaction</button><p id="pw-tx-feedback" class="pw-status" role="status"></p></form></section>',
      '<section class="card"><div class="card-heading"><div><h2>Check an intended purchase</h2><p>Uses saved visible asks and recorded balances.</p></div></div><form id="pw-preview-form"><div class="pw-fields pw-fields-two"><label for="pw-preview-asset">Asset<select id="pw-preview-asset"><option>ETH</option><option>BTC</option></select></label>',
      input('pw-preview-notional','Purchase notional · USD',500,'type="number" min="0.01" max="1000000000" step="any" required'),input('pw-preview-fee','Entry fee · basis points',10,'type="number" min="0" max="500" step="any" required'),input('pw-preview-cap','Single-asset cap · %',50,'type="number" min="0.01" max="100" step="any" required'),input('pw-preview-stress','Stress decline · %',30,'type="number" min="0.01" max="100" step="any" required'),input('pw-preview-loss','Purchase loss budget · USD','','type="number" min="0.01" step="any" placeholder="Optional"'),
      '</div><button type="submit" class="button">Calculate from saved data</button><p class="card-note">No order is placed. A saved order book can be stale and cannot guarantee a fill.</p></form><div id="pw-preview-result" aria-live="polite"></div></section></div>',
      '<section class="card section-gap"><div class="card-heading"><div><h2>Transaction history</h2><p>All saved receipts, including those after an as-of snapshot. Voids preserve the original.</p></div><a class="button" href="/api/portfolio/export" download="aether-private-ledger.csv">Export active ledger</a></div><div id="pw-ledger-history"><p class="pw-muted">Saved transactions appear here.</p></div>',
      '<details class="pw-disclosure"><summary>Import a normalized CSV</summary><p class="card-note">Required columns: external_id, time, kind, asset, quantity. Optional: price_usd, fee_usd, cost_basis_usd, note. Times need a timezone. The server validates the whole batch before saving; matching references are deduplicated and conflicting references are rejected.</p><form id="pw-import-form"><label for="pw-import">Choose a UTF-8 CSV · at most 2 MB<input id="pw-import" type="file" accept=".csv,text/csv"></label><p id="pw-import-info" class="card-note">Choose a file, then explicitly import it into this ledger.</p><div class="investment-actions"><button type="submit" class="button" id="pw-import-submit" disabled>Import chosen CSV</button><button type="button" class="text-button" id="pw-csv-template">Download header template</button></div><p id="pw-import-feedback" class="pw-status" role="status"></p></form></details><details class="pw-disclosure"><summary>Accounting and missing-value rules</summary><p class="card-note">Buy fees are added to cost basis; sell fees reduce proceeds. Incoming transfers without a supplied total basis keep basis and affected P&amp;L unknown. Recorded funding and asset balances cannot go negative. External accounts, staking income, derivatives, dividends and FX are not covered. This is not a tax filing.</p></details></section></section>',
      '<div class="pw-related-links"><p>Check market claims separately from your account assumptions.</p><button class="text-button" type="button" data-view="reports">Browse saved research ↗</button><button class="text-button" type="button" data-view="library">Open source library ↗</button></div>'
    ].join('');
    el('pw-tx-time').value=localNow();
    el('pw-tab-ledger').tabIndex=-1;
    el('pw-tab-example').addEventListener('click',()=>selectMode('example'));
    el('pw-tab-ledger').addEventListener('click',()=>selectMode('ledger'));
    for(const id of ['pw-tab-example','pw-tab-ledger'])el(id).addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const mode=event.key==='Home'?'example':event.key==='End'?'ledger':state.mode==='example'?'ledger':'example';selectMode(mode);el('pw-tab-'+mode).focus();}});
    el('pw-scenario-form').addEventListener('input',renderScenario);
    el('pw-scenario-purchase').addEventListener('input',renderScenario);
    el('pw-scenario-form').addEventListener('input',()=>{scenarioDirty=true;});
    el('pw-scenario-purchase').addEventListener('input',()=>{scenarioDirty=true;});
    el('pw-scenario-form').addEventListener('submit',event=>event.preventDefault());
    el('pw-scenario-purchase').addEventListener('submit',event=>event.preventDefault());
    el('pw-reset').addEventListener('click',resetScenario);
    el('pw-reload').addEventListener('click',()=>loadLedger(false,true));
    el('pw-refresh-marks').addEventListener('click',()=>loadLedger(true));
    el('pw-tx-kind').addEventListener('change',transactionFields);
    el('pw-transaction-form').addEventListener('submit',saveTransaction);
    el('pw-preview-form').addEventListener('submit',preview);
    el('pw-preview-form').addEventListener('input',()=>invalidatePreview('Purchase assumptions changed. Calculate again from saved data.'));
    el('pw-preview-form').addEventListener('change',()=>invalidatePreview('Purchase assumptions changed. Calculate again from saved data.'));
    el('pw-import').addEventListener('change',chooseImport);
    el('pw-import-form').addEventListener('submit',importCSV);
    el('pw-csv-template').addEventListener('click',downloadTemplate);
    el('pw-asof-form').addEventListener('submit',event=>{event.preventDefault();state.asOf=el('pw-asof').value?new Date(el('pw-asof').value).toISOString():'';loadLedger(false);});
    el('pw-asof').addEventListener('input',()=>invalidatePreview('Economic-date input changed. Apply the snapshot or return to current records before calculating.'));
    el('pw-current').addEventListener('click',()=>{state.asOf='';el('pw-asof').value='';loadLedger(false);});
    el('pw-example-ask').addEventListener('click',scenarioPrompt);
    el('pw-ledger-ask').addEventListener('click',ledgerPrompt);
    host.addEventListener('click',event=>{const button=event.target.closest('[data-pw-void]');if(button)voidTransaction(button);});
    transactionFields();el('pw-example-asset').value=defaults.asset;renderScenario();syncPreviewControls();
  }
  function readScenario() {
    const read=id=>el(id).value===''?null:Number(el(id).value);
    return {cash:read('pw-cash'),ethQuantity:read('pw-eth-quantity'),ethPrice:read('pw-eth-price'),ethCost:read('pw-eth-cost'),btcQuantity:read('pw-btc-quantity'),btcPrice:read('pw-btc-price'),btcCost:read('pw-btc-cost'),asset:el('pw-example-asset').value,notional:read('pw-example-notional'),feeBps:read('pw-example-fee'),cap:read('pw-example-cap'),stress:read('pw-example-stress'),loss:read('pw-example-loss')};
  }
  function resetScenario() {
    const ids={cash:'pw-cash',ethQuantity:'pw-eth-quantity',ethPrice:'pw-eth-price',ethCost:'pw-eth-cost',btcQuantity:'pw-btc-quantity',btcPrice:'pw-btc-price',btcCost:'pw-btc-cost',asset:'pw-example-asset',notional:'pw-example-notional',feeBps:'pw-example-fee',cap:'pw-example-cap',stress:'pw-example-stress',loss:'pw-example-loss'};
    Object.entries(ids).forEach(([key,id])=>el(id).value=defaults[key]);scenarioDirty=false;renderScenario();
  }
  function applyExampleProfile(config){
    const profile=config.demo_profile||{},key=JSON.stringify(profile);
    if(key!==currentProfile){defaults=exampleDefaults(profile);currentProfile=key;if(!scenarioDirty)resetScenario();}
    const name=typeof profile.name==='string'?profile.name.trim():'';
    el('pw-example-profile').textContent=name?'Your example preset: '+name+'. Illustrative assumptions only; your private ledger starts separately.':'';
  }
  function renderScenario() {
    try {
      const r=calculateScenario(readScenario());
      el('pw-example-stats').innerHTML=tile('Example equity',money(r.equity),'Entered cash and marked holdings')+tile('Crypto allocation',fraction(r.crypto/r.equity),'ETH and BTC together')+tile('Example unrealized P&L',money(r.unrealized),'Entered unit costs including fees')+tile('Cash reserve',money(r.cash),'Not connected to an account');
      el('pw-example-allocation').innerHTML=allocation([{key:'eth',label:'ETH',value:r.eth},{key:'btc',label:'BTC',value:r.btc},{key:'cash',label:'USD cash',value:r.cash}]);
      const rows=[['Total equity',money(r.equity),money(r.afterEquity)],['Cash',money(r.cash),money(r.afterCash)],[r.asset+' allocation',fraction(r.beforeConcentration),fraction(r.concentration)],['Both crypto assets fall '+r.stress+'% · equity loss',money(r.beforeStressLoss),money(r.afterStressLoss)]];
      el('pw-example-comparison').innerHTML='<div class="table-wrap"><table class="data-table"><thead><tr><th scope="col">Entered scenario</th><th scope="col">Before</th><th scope="col">After intended purchase</th></tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map((x,i)=>'<'+(i?'td':'th scope="row"')+'>'+escapeHTML(x)+'</'+(i?'td':'th')+'>').join('')+'</tr>').join('')+'</tbody></table></div><div class="pw-scenario-summary"><p><strong>'+money(r.notional)+'</strong> buys '+number(r.quantity)+' '+r.asset+' at the entered price. Entry fee: <strong>'+money(r.fee)+'</strong>.</p><p>Stress loss on this purchase alone: <strong>'+money(r.stressLoss)+'</strong>. Maximum purchase notional within your entered cash, concentration and optional loss constraints: <strong>'+money(r.maximum)+'</strong>.</p></div><div class="pw-constraints '+(r.constraints.length?'pw-caution':'')+'">'+(r.constraints.length?r.constraints.map(c=>'<p>'+escapeHTML(c)+'</p>').join(''):'<p>The entered purchase fits these assumptions. This does not establish suitability or future performance.</p>')+'</div><details class="pw-disclosure"><summary>How this scenario is calculated</summary><p class="card-note">Purchase notional excludes the fee. The fee is notional × basis points / 10,000; equity falls by the fee and cash falls by notional plus fee. Post-purchase concentration is the selected asset value plus notional divided by equity less the fee. The stress comparison assumes ETH and BTC both fall by the entered percentage and cash stays unchanged. Its after-purchase loss includes the entry fee. Maximum notional is the smallest nonnegative cash, allocation-cap and optional purchase-loss bound. No order-book depth or slippage is assumed.</p></details>';
      el('pw-example-ask').disabled=false;
    } catch(error) {
      el('pw-example-stats').innerHTML=tile('Example scenario','Incomplete','Complete the entered values to calculate.');
      el('pw-example-allocation').textContent='Enter valid account and purchase assumptions.';
      el('pw-example-comparison').innerHTML='<p class="pw-status pw-caution">'+escapeHTML(errorText(error))+'</p>';
      el('pw-example-ask').disabled=true;
    }
  }
  function selectMode(mode) {
    const changed=mode!==state.mode;
    state.mode=mode;
    if(changed)invalidatePreview('Portfolio mode changed. Calculate again from the selected saved account context.');
    for(const key of ['example','ledger']){el('pw-tab-'+key).setAttribute('aria-selected',String(mode===key));el('pw-tab-'+key).tabIndex=mode===key?0:-1;el('pw-'+key).hidden=mode!==key;}
    el('pw-mode-badge').textContent=mode==='example'?'Example scenario':'Private local ledger';
    if(mode==='ledger')loadLedger(false);
  }
  function setLedgerBusy(busy) {
    state.loading=busy;
    el('pw-reload').disabled=busy;
    el('pw-refresh-marks').disabled=busy||Boolean(state.asOf);
    for(const control of el('pw-asof-form').elements)control.disabled=busy;
    syncPreviewControls();
  }
  async function loadLedger(refresh=false,force=false) {
    invalidatePreview('Saved account data is being reloaded. Calculate again after it finishes.');
    if(state.loading){if(force||refresh||state.asOf!==state.loadingAsOf){state.pendingLoad=true;state.pendingRefresh=state.pendingRefresh||refresh;}return;}
    state.ledgerReady=false;state.ledgerRevision++;
    const request=++state.sequence;
    const requestedRevision=state.ledgerRevision;
    const requestedAsOf=state.asOf;
    state.loadingAsOf=requestedAsOf;
    setLedgerBusy(true);
    status('pw-ledger-status',refresh?'Refreshing market marks from providers…':'Reading saved transactions and market marks…');
    try {
      const params=new URLSearchParams({refresh:String(refresh)});
      if(requestedAsOf)params.set('as_of',requestedAsOf);
      const [p,tx]=await Promise.all([api('/api/portfolio?'+params),api('/api/portfolio/transactions')]);
      if(request!==state.sequence)return;
      if(requestedRevision!==state.ledgerRevision){state.ledgerReady=false;status('pw-ledger-status','Account records changed during this read. Reload saved records to see the updated snapshot.');return;}
      if(!p||!Array.isArray(p.positions)||!Array.isArray(tx.items))throw Error('The saved ledger response could not be read.');
      state.portfolio=p;state.transactions=tx.items;state.loadedAsOf=requestedAsOf;state.ledgerReady=true;renderLedger();
      status('pw-ledger-status',(requestedAsOf?'Economic-date snapshot · '+when(requestedAsOf):'Saved ledger loaded')+(refresh?' · market refresh requested.':' · no market refresh requested.'));
    } catch(error) {
      status('pw-ledger-status','Could not load saved records: '+errorText(error)+(state.portfolio?' Previously displayed values are retained.':''),'pw-caution');
      if(!state.portfolio)el('pw-ledger-positions').innerHTML='<p class="pw-unknown">Records are unavailable. This does not mean your account is empty.</p>';
    } finally {
      if(request===state.sequence)setLedgerBusy(false);
      if(state.pendingLoad){const nextRefresh=state.pendingRefresh;state.pendingLoad=false;state.pendingRefresh=false;await loadLedger(nextRefresh);}
    }
  }
  function renderLedger() {
    const p=state.portfolio;
    el('pw-ledger-stats').innerHTML=tile('Recorded equity',p.empty?'No records':money(p.equity_usd),'Saved marks · incomplete marks stay unknown')+tile('Recorded USD cash',p.empty?'No records':money(p.cash_usd),'Manual funding and transactions only')+tile('Realized P&L',p.empty?'No records':money(p.realized_pnl_usd),'FIFO · net of disposal fees')+tile('Unrealized P&L',p.empty?'No records':money(p.unrealized_pnl_usd),'Unknown incoming basis stays unknown');
    el('pw-ledger-scope').textContent=(p.transaction_count??state.transactions.filter(t=>!t.voided_at).length)+' active records · '+(state.loadedAsOf?'Economic snapshot '+when(state.loadedAsOf):'Current corrected ledger')+' · recorded fees '+money(p.fees_usd);
    const positive=p.positions.filter(row=>known(row.quantity)&&Number(row.quantity)>0);
    el('pw-ledger-allocation').innerHTML=p.empty?'':allocation([...positive.map(row=>({key:row.asset==='ETH'?'eth':'btc',label:row.asset,value:row.market_value_usd})),{key:'cash',label:'USD cash',value:p.cash_usd}],positive.every(row=>known(row.market_value_usd)));
    el('pw-ledger-positions').innerHTML=p.positions.length?'<div class="table-wrap"><table class="data-table"><thead><tr><th scope="col">Asset and quantity</th><th scope="col">Total cost basis</th><th scope="col">Marked value</th><th scope="col">Unrealized P&L</th><th scope="col">Saved mark and source</th></tr></thead><tbody>'+p.positions.map(row=>{
      const mark=row.mark_source;
      const markState=mark?(mark.stale===true||mark.freshness==='stale'?'Saved · stale':mark.freshness==='fresh'?'Saved · within freshness window':'Saved · freshness unclassified'):'No eligible saved mark';
      return '<tr><td><strong>'+escapeHTML(row.asset)+'</strong><small>'+number(row.quantity)+' '+escapeHTML(row.asset)+'</small></td><td>'+(!row.basis_known?'Unknown':money(row.cost_basis_usd))+'</td><td>'+money(row.market_value_usd)+'</td><td>'+money(row.unrealized_pnl_usd)+'</td><td><span class="pw-mark-state">'+markState+'</span>'+(mark?'<small>Observed '+when(mark.observed_at)+'</small><small>Captured '+when(mark.retrieved_at)+'</small>'+source(mark.record_id):'')+'</td></tr>';
    }).join('')+'</tbody></table></div>':'<div class="pw-empty">'+(p.empty?'Your local ledger is empty. Record actual funding and transactions, or import your normalized CSV. Example values are kept separate.':'No ETH or BTC holdings remain in these records. Recorded cash is shown above.')+'</div>';
    el('pw-ledger-gaps').textContent=[...(p.mark_gaps||[]).map(g=>g.asset+': '+g.reason),...(p.warnings||[])].join(' ');
    el('pw-ledger-history').innerHTML=state.transactions.length?'<div class="table-wrap"><table class="data-table"><thead><tr><th scope="col">Economic time · UTC</th><th scope="col">Type</th><th scope="col">Quantity</th><th scope="col">Execution price</th><th scope="col">Fee</th><th scope="col">Reference and note</th><th scope="col">Receipt</th></tr></thead><tbody>'+[...state.transactions].reverse().map(row=>'<tr class="'+(row.voided_at?'pw-voided':'')+'"><td>'+when(row.time)+'</td><td>'+escapeHTML(String(row.kind||'').replaceAll('_',' '))+'</td><td>'+number(row.quantity)+' '+escapeHTML(row.asset)+'</td><td>'+(row.price_usd===null?'Not a trade':money(row.price_usd))+'</td><td>'+money(row.fee_usd)+'</td><td>'+escapeHTML(row.external_id)+'<small>'+escapeHTML(row.note)+'</small></td><td>'+(canVoidTransactions()?'<button type="button" class="text-button" data-pw-void="'+escapeHTML(row.id)+'" data-pw-restore="'+String(Boolean(row.voided_at))+'">'+(row.voided_at?'Restore receipt':'Void receipt')+'</button>':'<span class="pw-muted">Receipt retained</span>')+(row.voided_at?'<small>Voided '+when(row.voided_at)+'</small>':'')+'</td></tr>').join('')+'</tbody></table></div>':'<div class="pw-empty">No saved transactions. Nothing has been added from the example.</div>';
    el('pw-ledger-ask').disabled=p.empty;
    invalidatePreview(state.loadedAsOf?'Return to current records before calculating a purchase preview. The preview uses current corrected ledger balances.':'Calculate again after changes to records or saved marks.');
  }
  function transactionFields() {
    const kind=el('pw-tx-kind').value,cash=['deposit','withdrawal'].includes(kind),trade=['buy','sell'].includes(kind),incoming=kind==='transfer_in';
    if(cash)el('pw-tx-asset').value='USD';else if(el('pw-tx-asset').value==='USD')el('pw-tx-asset').value='ETH';
    el('pw-tx-asset').disabled=cash;
    el('pw-tx-price-label').hidden=!trade;el('pw-tx-price').disabled=!trade;el('pw-tx-price').required=trade;
    el('pw-tx-basis-label').hidden=!incoming;el('pw-tx-basis').disabled=!incoming;
    el('pw-tx-help').textContent=cash?'Cash amounts are in USD. Deposits and withdrawals have no execution price.':incoming?'Supply the total cost basis of the incoming quantity, or leave it blank to keep basis and affected P&L unknown.':trade?'Enter the actual execution price and USD fee. This saves a historical receipt; it does not place an order.':'Transfers remove recorded lots without realizing a sale. Fees are in USD.';
  }
  async function saveTransaction(event) {
    event.preventDefault();const button=event.submitter||el('pw-transaction-form').querySelector('button[type="submit"]');button.disabled=true;status('pw-tx-feedback','Validating and saving the actual receipt…');
    const controls=Array.from(el('pw-transaction-form').elements).map(control=>[control,control.disabled]);
    beginLedgerMutation();
    try {
      const kind=el('pw-tx-kind').value;
      if(!el('pw-tx-reference').value.trim())el('pw-tx-reference').value=crypto.randomUUID?crypto.randomUUID():'local-'+Date.now()+'-'+Math.random().toString(16).slice(2);
      const body={external_id:el('pw-tx-reference').value.trim(),time:new Date(el('pw-tx-time').value).toISOString(),kind,asset:el('pw-tx-asset').value,quantity:el('pw-tx-quantity').value,price_usd:['buy','sell'].includes(kind)?el('pw-tx-price').value:null,fee_usd:el('pw-tx-fee').value,cost_basis_usd:kind==='transfer_in'&&el('pw-tx-basis').value!==''?el('pw-tx-basis').value:null,note:el('pw-tx-note').value};
      controls.forEach(([control])=>control.disabled=true);
      const result=await api('/api/portfolio/transactions','POST',body);
      status('pw-tx-feedback',result.duplicates?'Matching reference already exists; no duplicate receipt added.':'Saved to your private local ledger.');
      el('pw-tx-reference').value='';el('pw-tx-quantity').value='';el('pw-tx-note').value='';
      await loadLedger(false,true);
    } catch(error){status('pw-tx-feedback',errorText(error),'pw-caution');}
    finally{controls.forEach(([control,disabled])=>control.disabled=disabled);button.disabled=false;transactionFields();endLedgerMutation();}
  }
  async function voidTransaction(button) {
    if(!canVoidTransactions()){status('pw-ledger-status','Receipt deletion, voiding and restoration are disabled for this account.','pw-caution');return;}
    button.disabled=true;
    beginLedgerMutation();
    try{await api('/api/portfolio/transactions/'+encodeURIComponent(button.dataset.pwVoid)+'/void','POST',{void:button.dataset.pwRestore!=='true'});await loadLedger(false,true);}
    catch(error){status('pw-ledger-status',errorText(error),'pw-caution');button.disabled=false;}
    finally{endLedgerMutation();}
  }
  function chooseImport() {
    const file=el('pw-import').files?.[0];state.importFile=null;el('pw-import-submit').disabled=true;
    if(!file){el('pw-import-info').textContent='Choose a file, then explicitly import it into this ledger.';return;}
    if(file.size>2_000_000){el('pw-import-info').textContent='This file exceeds the 2 MB import limit.';return;}
    state.importFile=file;el('pw-import-info').textContent=file.name+' · '+number(file.size)+' bytes. Import writes validated rows to your actual local ledger.';el('pw-import-submit').disabled=false;
  }
  async function importCSV(event) {
    event.preventDefault();const button=event.submitter||el('pw-import-submit'),file=state.importFile;if(!file)return;button.disabled=true;
    beginLedgerMutation();
    status('pw-import-feedback','Validating the CSV batch…');
    try{const result=await api('/api/portfolio/import','POST',{text:await file.text()});status('pw-import-feedback',result.added+' receipts imported · '+result.duplicates+' matching duplicates skipped.');el('pw-import').value='';state.importFile=null;el('pw-import-info').textContent='Import complete. Original receipts remain available in transaction history.';await loadLedger(false,true);}
    catch(error){status('pw-import-feedback',errorText(error),'pw-caution');button.disabled=false;}
    finally{endLedgerMutation();}
  }
  function downloadTemplate() {
    const blob=new Blob(['external_id,time,kind,asset,quantity,price_usd,fee_usd,cost_basis_usd,note\r\n'],{type:'text/csv;charset=utf-8'});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='aether-ledger-template.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function previewBody() {
    return {asset:el('pw-preview-asset').value,notional:Number(el('pw-preview-notional').value),fee_bps:Number(el('pw-preview-fee').value),max_asset_fraction:Number(el('pw-preview-cap').value)/100,stress_decline_fraction:Number(el('pw-preview-stress').value)/100,max_loss_usd:el('pw-preview-loss').value===''?null:Number(el('pw-preview-loss').value),refresh:false};
  }
  function previewAvailable() {
    return state.mode==='ledger'&&state.ledgerReady&&Boolean(state.portfolio)&&!state.loading&&!state.ledgerMutations&&!state.loadedAsOf&&!state.asOf&&!el('pw-asof').value;
  }
  function syncPreviewControls() {
    const form=el('pw-preview-form');
    if(!form)return;
    const locked=!previewAvailable();
    for(const control of form.elements)control.disabled=locked;
    form.querySelector('button[type="submit"]').disabled=locked||state.previewInFlight;
  }
  function invalidatePreview(message) {
    state.previewGeneration++;state.previewInFlight=false;state.preview=null;
    const result=el('pw-preview-result');if(result)result.textContent=message;
    syncPreviewControls();
  }
  function beginLedgerMutation() {
    state.ledgerMutations++;state.ledgerRevision++;state.ledgerReady=false;
    invalidatePreview('Account records are changing. Reload saved records before calculating another preview.');
  }
  function endLedgerMutation() {
    state.ledgerMutations=Math.max(0,state.ledgerMutations-1);syncPreviewControls();
  }
  function previewContextMatches(context) {
    return context.generation===state.previewGeneration&&previewAvailable()&&context.portfolio===state.portfolio&&context.ledgerRevision===state.ledgerRevision&&context.loadedAsOf===state.loadedAsOf&&context.requestedAsOf===state.asOf&&context.asOfInput===el('pw-asof').value&&context.bodySignature===JSON.stringify(previewBody());
  }
  async function preview(event) {
    event.preventDefault();
    if(!previewAvailable()){invalidatePreview('Load current saved records before calculating a purchase preview. Historical snapshots cannot use this current-account preview.');return;}
    if(state.previewInFlight)return;
    invalidatePreview('Calculating from saved data…');
    const body=previewBody(),context={generation:state.previewGeneration,bodySignature:JSON.stringify(body),portfolio:state.portfolio,ledgerRevision:state.ledgerRevision,loadedAsOf:state.loadedAsOf,requestedAsOf:state.asOf,asOfInput:el('pw-asof').value};
    state.previewInFlight=true;syncPreviewControls();
    try{
      const r=await api('/api/portfolio/preview','POST',body);
      if(!previewContextMatches(context)){if(context.generation===state.previewGeneration)invalidatePreview('Inputs or account context changed. Calculate again from current saved records.');return;}
      const book=r.orderbook_source||{},estimate=r.estimate||{};
      state.preview={body,result:r};
      const rows=[['Estimated VWAP',money(estimate.estimated_vwap_usd)],['Entry fee',money(r.estimated_entry_fee_usd)],['Required recorded cash',money(r.total_recorded_cash_needed_usd)],['Post-purchase asset allocation',fraction(r.post_purchase_asset_fraction)],['Intended purchase stress loss',money(r.hypothetical_stress_loss_usd)],['Maximum within entered bounds',money(r.maximum_notional_under_entered_constraints_usd)]];
      el('pw-preview-result').innerHTML='<div class="pw-preview-metrics">'+rows.map(([label,value])=>'<div><span>'+escapeHTML(label)+'</span><strong>'+escapeHTML(value)+'</strong></div>').join('')+'</div><div class="pw-constraints '+((r.constraints||[]).length?'pw-caution':'')+'">'+((r.constraints||[]).length?r.constraints.map(c=>'<p>'+escapeHTML(c)+'</p>').join(''):'<p>Within these entered constraints. Actual execution and future losses remain uncertain.</p>')+'</div>'+source(estimate.source_record_id,'Inspect saved order book ↗')+(book.observed_at?'<p class="card-note">Observed '+when(book.observed_at)+' · captured '+when(book.retrieved_at)+(book.stale?' · stale saved snapshot':'')+'</p>':'')+'<p class="card-note">'+escapeHTML(r.scope||'A conditional research preview, not an instruction or order.')+'</p>';
    }catch(error){if(previewContextMatches(context))el('pw-preview-result').innerHTML='<p class="pw-status pw-caution">'+escapeHTML(errorText(error))+'</p>';else if(context.generation===state.previewGeneration)invalidatePreview('Inputs or account context changed. Calculate again from current saved records.');}
    finally{if(context.generation===state.previewGeneration)state.previewInFlight=false;syncPreviewControls();}
  }
  function scenarioPrompt() {
    try{
      const a=readScenario(),r=calculateScenario(a);
      draft('Review this synthetic portfolio scenario; it is not my actual account. Entered cash '+money(r.cash)+', '+a.ethQuantity+' ETH at assumed price '+money(a.ethPrice)+' and '+a.btcQuantity+' BTC at assumed price '+money(a.btcPrice)+'. Consider an intended '+money(r.notional)+' '+r.asset+' purchase, '+r.feeBps+' bps entry fee, '+r.cap+'% single-asset cap, '+r.stress+'% stress decline and '+(r.loss===null?'no supplied purchase loss budget':money(r.loss)+' purchase loss budget')+'. Explain the allocation, cash and stress trade-offs. Separate these assumptions from dated saved market evidence; do not place an order or treat the scenario as actual holdings.','portfolio_example');
    }catch(error){el('pw-example-comparison').textContent=errorText(error);}
  }
  function ledgerPrompt() {
    const p=state.portfolio;if(!p||p.empty)return;
    draft('Help me understand my manually recorded local portfolio. First read my local portfolio_snapshot as private account context; do not treat it as public source evidence or send its personal contents to a public-source classification or reader tool. '+(state.loadedAsOf?'The screen shows an economic-date snapshot at '+when(state.loadedAsOf)+'. Do not substitute current holdings for this earlier date; explain any unavailable historical snapshot. ':'')+'Explain concentration, missing cost basis, stale marks and data gaps. Keep unknown values unknown, distinguish current corrected records from historical knowledge, and do not invent external balances or place trades.','portfolio_ledger');
  }
  mount();
  applyExampleProfile({});
  document.addEventListener('aether:view',event=>{if(event.detail?.view==='portfolio'&&state.mode==='ledger')loadLedger(false);});
  if(!document.getElementById('view-portfolio')?.hidden&&state.mode==='ledger')loadLedger(false);
  window.AetherPortfolio={reloadSaved:()=>state.mode==='ledger'?loadLedger(false,true):renderScenario()};
})();
