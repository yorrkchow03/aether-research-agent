'use strict';

function curateEthereumNews(rows, now=Date.now()) {
 const day=86400000, byURL=new Map();
 for(const row of rows){
  if(!['page','feed_item'].includes(row.source_kind)||!row.title?.trim()||/\b(?:btc|bitcoin)\b/i.test(row.title))continue;
  const published=Date.parse(row.published_at);
  if(!Number.isFinite(published)||published>now||published<now-30*day)continue;
  let url;
  try{url=new URL(row.url)}catch{continue}
  if(!['https:','http:'].includes(url.protocol)||url.username||url.password)continue;
  const host=url.hostname.toLowerCase().replace(/^www\./,'');
  const originalURL=url.href;
  url.hash='';
  for(const key of [...url.searchParams.keys()]){
   if(/^utm_|^(?:fbclid|gclid|ref)$/i.test(key))url.searchParams.delete(key);
  }
  const identity=host+url.pathname.replace(/\/$/,'')+url.search;
  const official=/(^|\.)(ethereum\.org|sec\.gov|federalreserve\.gov)$/.test(host);
  const catalyst=/\b(?:upgrade|mainnet|testnet|staking|etf|security|governance|regulation|protocol|glamsterdam)\b/i.test(row.title);
  const candidate={...row,url:originalURL,host,published,score:published+day*(Number(official)*.5+Number(catalyst)*.25)};
  const previous=byURL.get(identity);
  if(!previous||(previous.source_kind==='feed_item'&&row.source_kind==='page')||
    (previous.source_kind===row.source_kind&&published>previous.published))byURL.set(identity,candidate);
 }
 const selected=[], titles=new Set();
 for(const row of [...byURL.values()].sort((a,b)=>b.score-a.score)){
  const title=row.title.toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
  if(titles.has(title))continue;
  titles.add(title);
  selected.push(row);
  if(selected.length===6)break;
 }
 return selected;
}

async function loadLatestNews(){
 const list=$('latest-news-list');
 const {items}=await api('/api/news?asset=ETH');
 const rows=curateEthereumNews(items);
 const format=new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeZone:'Asia/Shanghai'});
 list.innerHTML=rows.length?rows.map(row=>{
  const dateLabel=format.format(row.published);
  return `<article class="news-row"><div class="news-icon" aria-hidden="true">◷</div><div><button data-source="${esc(row.record_id)}"><h3>${esc(row.title)}</h3></button><p>${esc(row.host)} · <time datetime="${esc(row.published_at)}">${esc(dateLabel)}</time></p></div></article>`;
 }).join(''):'<p class="desk-empty">No recent Ethereum headlines are saved. Older material is available in the source library.</p>';
}

function renderPriceChart(){
 const box=$('market-chart'), hourly=state.period===1, unit=hourly?'hourly':'daily';
 const record=item(hourly?'eth_hourly':'eth_candles').record;
 const fields=state.line?['close_usd']:['open_usd','high_usd','low_usd','close_usd'];
 const rows=(record?.series||[]).slice(-(hourly?25:state.period+1))
  .map(row=>({...row,closedAt:Date.parse(row.period_end||row.observed_at)}))
  .filter(row=>Number.isFinite(row.closedAt)&&fields.every(key=>finite(row[key])));
 $('chart-style').textContent=state.line?'Line':'Candles';
 $('chart-style').setAttribute('aria-label',state.line?'Show candlesticks':'Show price line');
 for(const button of document.querySelectorAll('[data-period]'))button.setAttribute('aria-pressed',String(Number(button.dataset.period)===state.period));
 if(rows.length<2){
  box.innerHTML='<div class="empty">No price history is available for this period yet.</div>';
  $('chart-scope').textContent='Coinbase';
  $('chart-date').textContent='—';
  return;
 }
 const width=Math.max(248,box.clientWidth),height=Math.max(215,box.clientHeight);
 const compactChart=width<480,left=8,right=width-(compactChart?57:73),top=14,bottom=height-30;
 const high=Math.max(...rows.map(r=>Number(state.line?r.close_usd:r.high_usd)));
 const low=Math.min(...rows.map(r=>Number(state.line?r.close_usd:r.low_usd)));
 const pad=(high-low)*.09||Math.max(Math.abs(high)*.01,1),upper=high+pad,lower=low-pad;
 const first=rows[0].closedAt,last=rows.at(-1).closedAt,span=last-first;
 const x=(r,i)=>span>0?left+(r.closedAt-first)/span*(right-left):left+i/(rows.length-1)*(right-left);
 const y=value=>top+(upper-Number(value))/(upper-lower)*(bottom-top);
 let svg='';
 for(let tick=0;tick<5;tick++){
  const price=upper-(upper-lower)*tick/4,yy=y(price);
  svg+=`<path d="M${left} ${yy}H${right}" stroke="#eef0f6" stroke-dasharray="4 4"/><text x="${right+8}" y="${yy+4}" fill="#687389" font-size="11">${usd(price,0)}</text>`;
 }
 if(state.line){
  const segments=[], step=hourly?3600000:86400000;
  rows.forEach((row,i)=>{
   if(i===0||row.closedAt-rows[i-1].closedAt>step*1.5)segments.push([]);
   segments.at(-1).push([x(row,i),y(row.close_usd)]);
  });
  svg+='<defs><linearGradient id="eth-price-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#9386dc" stop-opacity=".25"/><stop offset="100%" stop-color="#9386dc" stop-opacity=".01"/></linearGradient></defs>';
  for(const points of segments){
   if(points.length===1){svg+=`<circle cx="${points[0][0]}" cy="${points[0][1]}" r="3" fill="#635bce"/>`;continue}
   const line=points.map(point=>point.join(' ')).join(' L');
   svg+=`<path d="M${line} L${points.at(-1)[0]} ${bottom} L${points[0][0]} ${bottom}Z" fill="url(#eth-price-fill)"/><path d="M${line}" fill="none" stroke="#635bce" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
 }else{
  const candleWidth=Math.max(2,Math.min(13,(right-left)/rows.length*.58));
  rows.forEach((row,i)=>{
   const xx=x(row,i),open=y(row.open_usd),close=y(row.close_usd);
   const tone=Number(row.close_usd)>=Number(row.open_usd)?'#5ca292':'#cf8692';
   svg+=`<path d="M${xx} ${y(row.high_usd)}V${y(row.low_usd)}" stroke="${tone}"/><rect x="${xx-candleWidth/2}" y="${Math.min(open,close)}" width="${candleWidth}" height="${Math.max(1,Math.abs(open-close))}" rx="1" fill="${tone}"/>`;
  });
 }
 const ticks=Math.min(compactChart?3:5,rows.length);
 const format=new Intl.DateTimeFormat('en-US',hourly?{hour:'numeric',minute:'2-digit',timeZone:'Asia/Shanghai'}:{month:'short',day:'numeric',timeZone:'Asia/Shanghai'});
 for(let tick=0;tick<ticks;tick++){
  const index=Math.round(tick*(rows.length-1)/(ticks-1)),row=rows[index];
  const anchor=tick===0?'start':tick===ticks-1?'end':'middle';
  svg+=`<text x="${x(row,index)}" y="${height-8}" text-anchor="${anchor}" fill="#687389" font-size="11">${esc(format.format(row.closedAt))}</text>`;
 }
 box.innerHTML=`<svg id="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Ethereum ${unit} ${state.line?'closing price in US dollars':'price candlesticks'}">${svg}<line id="crosshair" y1="${top}" y2="${bottom}" stroke="#9487d0" stroke-dasharray="4 4" visibility="hidden"/><circle id="price-dot" r="4" fill="#635bce" stroke="#fff" stroke-width="2" visibility="hidden"/></svg><div class="chart-tooltip" id="chart-tooltip" hidden></div>`;
 $('chart-scope').innerHTML=`Coinbase · ${rows.length} ${unit} closes ${sourceButton(record.record_id,'Source ↗')}`;
 $('chart-date').textContent='Through '+date(new Date(last).toISOString());
 const chart=$('chart-svg'),crosshair=$('crosshair'),dot=$('price-dot'),tooltip=$('chart-tooltip');
 chart.addEventListener('pointermove',event=>{
  const rect=chart.getBoundingClientRect(),px=(event.clientX-rect.left)*width/rect.width;
  let index=0;
  for(let i=1;i<rows.length;i++)if(Math.abs(x(rows[i],i)-px)<Math.abs(x(rows[index],index)-px))index=i;
  const row=rows[index],xx=x(row,index);
  for(const attribute of ['x1','x2'])crosshair.setAttribute(attribute,xx);
  crosshair.setAttribute('visibility','visible');
  dot.setAttribute('cx',xx);dot.setAttribute('cy',y(row.close_usd));dot.setAttribute('visibility','visible');
  tooltip.hidden=false;
  const when=date(new Date(row.closedAt).toISOString());
  tooltip.textContent=state.line?`${when} · ${usd(row.close_usd)}`:`${when} · Open ${usd(row.open_usd)} · High ${usd(row.high_usd)} · Low ${usd(row.low_usd)} · Close ${usd(row.close_usd)}`;
 });
 chart.addEventListener('pointerleave',()=>{
  crosshair.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');tooltip.hidden=true;
 });
}
