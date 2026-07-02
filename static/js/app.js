/* ══════ ROLE ADAPTIVE ══════ */
const ROLES={
  sdr:  'SDR — pare de gastar 20 min por prospect. Ficha completa em segundos.',
  manager:'Gestor — chegue em cada reunião sabendo tudo sobre quem está do outro lado.',
  founder:'Founder — ache os decisores certos nas empresas certas. Agora.',
  mkt:  'Marketing — conheça o stack do prospect antes de criar sua próxima campanha.',
};
function selectRole(btn){
  document.querySelectorAll('.role-pill').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  const el=document.getElementById('search-eyebrow-text');
  if(!el||_profile)return;  // não sobrescreve quando logado (mostra plano)
  el.style.opacity='0';
  setTimeout(()=>{el.textContent=ROLES[btn.dataset.role];el.style.opacity='1';},180);
}

/* ══════ CELEBRATION ══════ */
function celebrate(){
  const canvas=document.getElementById('celebrate-canvas');
  canvas.width=window.innerWidth;canvas.height=window.innerHeight;
  const ctx=canvas.getContext('2d');
  canvas.classList.add('active');
  const colors=['#F04E00','#D94500','#FF8040','#1A7A4A','#0A66C2'];
  const particles=Array.from({length:55},()=>({
    x:Math.random()*canvas.width,y:-10,
    vx:(Math.random()-.5)*4,vy:Math.random()*4+2,
    r:Math.random()*4+2,c:colors[Math.floor(Math.random()*colors.length)],
    life:1,rot:Math.random()*360,rv:(Math.random()-.5)*5,
  }));
  let frame=0;
  function tick(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    let alive=false;
    for(const p of particles){
      p.x+=p.vx;p.y+=p.vy;p.vy+=.08;p.life-=.012;p.rot+=p.rv;
      if(p.life<=0)continue;alive=true;
      ctx.save();ctx.globalAlpha=p.life;ctx.fillStyle=p.c;
      ctx.translate(p.x,p.y);ctx.rotate(p.rot*Math.PI/180);
      ctx.fillRect(-p.r,-p.r/2,p.r*2,p.r);ctx.restore();
    }
    if(alive&&frame++<200)requestAnimationFrame(tick);
    else{ctx.clearRect(0,0,canvas.width,canvas.height);canvas.classList.remove('active');}
  }
  requestAnimationFrame(tick);
}

/* ══════ LOADING MESSAGES ══════ */
const LOAD_MSGS=['Acessando o site...','Consultando LinkedIn...','Verificando DNS/MX...','Mapeando emails...','Identificando decisores...','Consolidando ficha...'];
let loadInt=null;
function startLoad(){
  const s=document.getElementById('loading-status'),m=document.getElementById('loading-msg');
  s.classList.add('visible');let i=0;m.textContent=LOAD_MSGS[0];
  loadInt=setInterval(()=>{i=(i+1)%LOAD_MSGS.length;m.style.opacity='0';setTimeout(()=>{m.textContent=LOAD_MSGS[i];m.style.opacity='1';},150);},1800);
}
function stopLoad(){clearInterval(loadInt);document.getElementById('loading-status').classList.remove('visible');}

/* ══════ SUPABASE AUTH ══════ */
const _sb = supabase.createClient(
  'https://unpujwtgnldkrqisoytf.supabase.co',
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVucHVqd3Rnbmxka3JxaXNveXRmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5MTAwNzEsImV4cCI6MjA5MzQ4NjA3MX0.MWn_SZdK619vsAdziVbGAz7_fScmKlDx3I1yZUDLd8Q'
);
let _profile = null;
let currentLeadId = null;

// Rota canônica do produto — precisa estar na allowlist de Redirect URLs do Supabase
const _AUTH_REDIRECT = window.location.origin + '/app';

// Demo mode: token local que o backend reconhece (DEMO_MODE=1)
const _DEMO_KEY='le_demo_token';
function _getDemoToken(){return localStorage.getItem(_DEMO_KEY);}
function _setDemoToken(){
  const t='demo-session-'+Math.random().toString(36).slice(2,12);
  localStorage.setItem(_DEMO_KEY,t);return t;
}
function _clearDemoToken(){localStorage.removeItem(_DEMO_KEY);}
function isDemoSession(){return !!_getDemoToken();}

async function getToken(){
  const demo=_getDemoToken();
  if(demo)return demo;
  const {data}=await _sb.auth.getSession();
  return data.session?.access_token||null;
}

async function signInAsDemo(){
  _setDemoToken();
  closeAuthModal();
  await loadProfile();
  loadIntegrations();
  openDashboard();
}

async function authFetch(url,opts={}){
  const token=await getToken();
  if(!token){openAuthModal();throw new Error('not_authenticated');}
  return fetch(url,{...opts,headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`,...(opts.headers||{})}});
}

async function loadProfile(){
  try{
    const resp=await authFetch('/api/me');
    if(resp.ok){_profile=await resp.json();updateQuotaUI();updateNavUser();loadTodayFollowupsCount();}
  }catch(_){}
}

async function loadTodayFollowupsCount(){
  try{
    const resp=await authFetch('/api/followups/today');
    if(resp.ok){
      const fus=await resp.json();
      const badge=document.getElementById('nav-today-count');
      if(badge){
        if(fus.length>0){badge.textContent=fus.length;badge.style.display='inline-block';}
        else badge.style.display='none';
      }
    }
  }catch(_){}
}

function updateQuotaUI(){
  const el=document.getElementById('trial-counter');if(!el||!_profile)return;
  const{searches_used,searches_limit,plan}=_profile;
  if(plan==='enterprise'){el.style.display='none';return;}
  const rem=searches_limit-searches_used;
  el.style.display='block';
  if(rem>0){const strong=el.querySelector('strong');if(strong)strong.textContent=rem;}
  else el.innerHTML=`<strong>Cota esgotada.</strong> <button onclick="startCheckout('pro')" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:inherit;padding:0;text-decoration:underline">Faça upgrade</button>.`;
}

// Gate any action behind auth — opens modal if not logged in
function requireAuth(fn){
  if(_profile)fn();
  else openAuthModal();
}

function updateNavUser(){
  const preauth=document.getElementById('header-preauth');
  const postauth=document.getElementById('header-postauth');
  if(!preauth)return;
  document.body.classList.toggle('is-demo',isDemoSession()&&!!_profile);
  if(_profile){
    preauth.style.display='none';
    if(postauth)postauth.style.display='flex';
    const{searches_used,searches_limit,plan}=_profile;
    const quota=plan==='enterprise'?'∞':`${searches_used}/${searches_limit}`;
    const el=document.getElementById('nav-quota');
    if(el)el.textContent=`${quota} buscas`;
  }else{
    preauth.style.display='flex';
    if(postauth)postauth.style.display='none';
  }
}

function openAuthModal(){document.getElementById('auth-modal').classList.add('open');}
function closeAuthModal(){document.getElementById('auth-modal').classList.remove('open');document.getElementById('auth-error').style.display='none';document.getElementById('auth-success').style.display='none';}

async function sendMagicLink(){
  const email=document.getElementById('auth-email').value.trim();
  if(!email){document.getElementById('auth-error').textContent='Informe seu email.';document.getElementById('auth-error').style.display='block';return;}
  const btn=document.getElementById('magic-btn-text');const sp=document.getElementById('magic-btn-spin');
  btn.textContent='Enviando...';sp.style.display='inline-block';
  document.getElementById('auth-error').style.display='none';
  const{error}=await _sb.auth.signInWithOtp({email,options:{emailRedirectTo:_AUTH_REDIRECT}});
  btn.textContent='Entrar com link mágico';sp.style.display='none';
  if(error){document.getElementById('auth-error').textContent=error.message;document.getElementById('auth-error').style.display='block';}
  else{document.getElementById('auth-success').textContent='Link enviado! Verifique seu email.';document.getElementById('auth-success').style.display='block';}
}

async function signInWithGoogle(){
  await _sb.auth.signInWithOAuth({provider:'google',options:{redirectTo:_AUTH_REDIRECT}});
}
async function signInWithGitHub(){
  await _sb.auth.signInWithOAuth({provider:'github',options:{redirectTo:_AUTH_REDIRECT}});
}
async function signInWithMicrosoft(){
  await _sb.auth.signInWithOAuth({provider:'azure',options:{redirectTo:_AUTH_REDIRECT}});
}
async function signInWithApple(){
  await _sb.auth.signInWithOAuth({provider:'apple',options:{redirectTo:_AUTH_REDIRECT}});
}

async function signOut(){
  _clearDemoToken();
  await _sb.auth.signOut();_profile=null;updateNavUser();
  const tc=document.getElementById('trial-counter');if(tc)tc.style.display='none';
  // Fecha qualquer modal aberto e volta pro estado limpo
  ['dashboard-modal','pipeline-modal','followups-modal','history-modal'].forEach(id=>{
    document.getElementById(id)?.classList.remove('open');
  });
}

async function startCheckout(plan){
  const token=await getToken();
  if(!token){openAuthModal();return;}
  try{
    const resp=await authFetch('/api/billing/checkout',{method:'POST',body:JSON.stringify({plan})});
    const json=await resp.json();
    if(json.url)window.location.href=json.url;
  }catch(e){if(e.message!=='not_authenticated')alert('Erro ao iniciar checkout.');}
}

function openPaywall(){document.getElementById('paywall-modal').classList.add('open');}
function closePaywall(){document.getElementById('paywall-modal').classList.remove('open');}

/* ══════ ENRICH ══════ */
async function enrich(domainOverride){
  const input=document.getElementById('domain-input');
  let domain=(domainOverride||input.value).trim();
  if(!domain){showError('Digite o domínio da empresa (ex: nubank.com.br).');return;}
  const token=await getToken();
  if(!token){openAuthModal();return;}
  if(_profile&&_profile.searches_limit>0&&_profile.searches_used>=_profile.searches_limit){openPaywall();return;}
  setLoading(true);hideError();hideResults();startLoad();
  try{
    const resp=await authFetch('/api/enrich',{method:'POST',body:JSON.stringify({domain})});
    const json=await resp.json();
    if(!resp.ok){
      if(resp.status===402){openPaywall();return;}
      showError(json.detail||'Erro ao enriquecer este domínio.');return;
    }
    if(!json.success||!json.data){showError(json.message||'Não foi possível coletar dados.');return;}
    if(_profile)_profile.searches_used++;
    updateQuotaUI();updateNavUser();
    renderResult(json.data);celebrate();
  }catch(e){
    if(e.message!=='not_authenticated')showError('Erro de conexão com o servidor.');
  }finally{setLoading(false);stopLoad();}
}

/* ══════ SCORE PILL + BREAKDOWN ══════ */
let currentLeadData=null;

function renderScorePill(data){
  if(data.score==null)return'';
  const p=data.priority||'baixa';
  const labels={alta:'ALTA',media:'MÉDIA',baixa:'BAIXA'};
  const rows=(data.score_breakdown||[]).map(it=>
    `<div class="sp-row"><span class="sp-crit">${esc(it.criterion)}</span><span class="sp-evi">${esc(it.evidence||'')}</span><span class="sp-pts">+${it.points}</span></div>`
  ).join('')||'<div class="sp-row"><span class="sp-crit">Nenhum sinal pontuado ainda — busque decisores para subir o score.</span></div>';
  return `<span class="score-wrap"><button class="score-pill prio-${p}" onclick="toggleScorePop(event)" title="Ver composição do score">⚡ ${data.score} · ${labels[p]||p}</button><div class="score-pop" id="score-pop" onclick="event.stopPropagation()"><div class="sp-title">Por que ${data.score} pontos?</div>${rows}<div class="sp-foot">Modelo ${esc(data.score_version||'v1')} · recalculado ao encontrar decisores</div></div></span>`;
}
function toggleScorePop(e){e.stopPropagation();const el=document.getElementById('score-pop');if(el)el.classList.toggle('open');}
document.addEventListener('click',()=>{const el=document.getElementById('score-pop');if(el)el.classList.remove('open');});

async function refreshLeadScore(){
  if(!currentLeadId)return;
  try{
    const resp=await authFetch(`/api/leads/${currentLeadId}`);
    if(!resp.ok)return;
    currentLeadData=await resp.json();
    const slot=document.getElementById('score-pill-slot');
    if(slot)slot.innerHTML=renderScorePill(currentLeadData);
  }catch(_){}
}

/* ══════ REGISTRO DE LIGAÇÕES ══════ */
function toggleMeetRow(){
  const el=document.getElementById('meet-row');
  if(el)el.style.display=el.style.display==='none'?'flex':'none';
}

async function logCall(outcome){
  if(!currentLeadId)return;
  const body={type:'call',outcome};
  if(outcome==='meeting_scheduled'){
    const w=document.getElementById('meet-when').value;
    if(!w){document.getElementById('meet-when').focus();return;}
    body.meeting_at=new Date(w).toISOString();
  }
  const fb=document.getElementById('call-feedback');
  try{
    const resp=await authFetch(`/api/leads/${currentLeadId}/activities`,{method:'POST',body:JSON.stringify(body)});
    const json=await resp.json();
    if(!resp.ok){fb.textContent=json.detail||'Erro ao registrar.';fb.classList.add('show');return;}
    const meeting=(json.derived||[]).find(d=>d.type==='meeting');
    const ics=meeting?` <a href="#" onclick="downloadIcs(${meeting.id});return false">Baixar convite .ics</a>`:'';
    fb.innerHTML=`${esc(json.message)}${ics}`;
    fb.classList.add('show');
    if(outcome==='meeting_scheduled')document.getElementById('meet-row').style.display='none';
  }catch(e){if(e.message!=='not_authenticated'){fb.textContent='Erro de conexão.';fb.classList.add('show');}}
}

async function downloadIcs(activityId){
  const token=await getToken();if(!token)return;
  const resp=await fetch(`/api/activities/${activityId}/ics`,{headers:{Authorization:`Bearer ${token}`}});
  if(!resp.ok){alert('Erro ao gerar convite.');return;}
  const blob=await resp.blob();
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=`leadenricher_${activityId}.ics`;
  document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(url);a.remove();},1000);
}

/* ══════ FILA DE FOLLOW-UPS ══════ */
function openFollowups(){
  document.getElementById('followups-modal').classList.add('open');
  loadFollowups();
}
function closeFollowups(){document.getElementById('followups-modal').classList.remove('open');}
function closeFollowupsOutside(e){if(e.target===document.getElementById('followups-modal'))closeFollowups();}

async function loadFollowups(){
  const body=document.getElementById('followups-body');
  body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Carregando...</div>';
  try{
    const resp=await authFetch('/api/activities/pending');
    const list=await resp.json();
    if(!resp.ok){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro ao carregar.</div>';return;}
    if(!list.length){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Nenhum follow-up pendente. 🎉</div>';return;}
    const now=Date.now();
    body.innerHTML=list.map(a=>{
      const due=a.due_at?new Date(a.due_at):null;
      const late=due&&due.getTime()<now;
      const when=due?due.toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
      const kind=a.type==='meeting'?'Reunião':'Follow-up';
      const ics=a.type==='meeting'?`<button class="fu-btn" onclick="downloadIcs(${a.id})" title="Convite .ics">.ics</button>`:'';
      return `<div class="fu-row${late?' late':''}">
        <div class="fu-info">
          <span class="fu-kind">${kind}</span>
          <span class="fu-notes">${esc(a.notes||'')}</span>
          <span class="fu-when${late?' late':''}">${late?'⚠ atrasado · ':''}${when}</span>
        </div>
        <div class="fu-actions">
          <button class="fu-btn" onclick="loadLeadIntoView(${a.lead_id});closeFollowups()">Abrir lead</button>
          ${ics}
          <button class="fu-btn done" onclick="completeActivity(${a.id})">Concluir</button>
        </div>
      </div>`;
    }).join('');
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro de conexão.</div>';}
}

async function completeActivity(id){
  try{
    await authFetch(`/api/activities/${id}`,{method:'PATCH',body:JSON.stringify({completed:true})});
    loadFollowups();
  }catch(_){}
}

/* ══════ DASHBOARD ══════ */
function openDashboard(){document.getElementById('dashboard-modal').classList.add('open');loadDashboard();}
function closeDashboard(){document.getElementById('dashboard-modal').classList.remove('open');}
function closeDashboardOutside(e){if(e.target===document.getElementById('dashboard-modal'))closeDashboard();}

const STAGE_LABELS={novo:'Novo',contatado:'Contatado',reuniao_agendada:'Reunião agendada',oportunidade:'Oportunidade',ganho:'Ganho',perdido:'Perdido'};
const STAGE_ORDER=['novo','contatado','reuniao_agendada','oportunidade','ganho','perdido'];

async function loadDashboard(){
  const body=document.getElementById('dashboard-body');
  body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Carregando...</div>';
  try{
    const resp=await authFetch('/api/dashboard/metrics?days=30');
    const m=await resp.json();
    if(!resp.ok){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro ao carregar.</div>';return;}
    document.getElementById('dash-period').textContent=`· últimos ${m.period_days} dias`;
    const pct=v=>Math.round(v*100)+'%';
    const kpi=(val,lbl,warn)=>`<div class="kpi${warn?' warn':''}"><span class="kpi-val">${val}</span><span class="kpi-lbl">${lbl}</span></div>`;
    const funilMax=Math.max(1,...STAGE_ORDER.map(s=>m.funil_por_estagio[s]||0));
    const funil=STAGE_ORDER.map(s=>{
      const v=m.funil_por_estagio[s]||0;
      return `<div class="fn-row"><span class="fn-lbl">${STAGE_LABELS[s]}</span><div class="fn-track"><div class="fn-bar" style="width:${Math.max(2,(v/funilMax)*100)}%"></div></div><span class="fn-val">${v}</span></div>`;
    }).join('');
    const prio=['alta','media','baixa'].map(p=>{
      const v=m.leads_por_prioridade[p]||0;
      return `<span class="score-pill prio-${p}" style="cursor:default">${p==='alta'?'🔴':p==='media'?'🟡':'🔵'} ${p}: ${v}</span>`;
    }).join(' ');
    body.innerHTML=`
      <div class="kpi-grid">
        ${kpi(m.leads_pesquisados,'leads pesquisados')}
        ${kpi(m.ligacoes_realizadas,'ligações realizadas')}
        ${kpi(pct(m.taxa_contato),'taxa de contato')}
        ${kpi(pct(m.taxa_reuniao),'taxa de reunião')}
        ${kpi(pct(m.conversao_oportunidade),'conversão p/ oportunidade')}
        ${kpi(m.followups_pendentes+(m.followups_atrasados?` <small>(${m.followups_atrasados} atrasados)</small>`:''),'follow-ups pendentes',m.followups_atrasados>0)}
      </div>
      <div class="dash-sec-title">Funil por estágio</div>
      <div class="funnel">${funil}</div>
      <div class="dash-sec-title">Leads por prioridade</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">${prio}</div>`;
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro de conexão.</div>';}
}

/* ══════ PIPELINE (KANBAN) ══════ */
function openPipeline(){document.getElementById('pipeline-modal').classList.add('open');loadPipeline();}
function closePipeline(){document.getElementById('pipeline-modal').classList.remove('open');}
function closePipelineOutside(e){if(e.target===document.getElementById('pipeline-modal'))closePipeline();}

async function loadPipeline(){
  const body=document.getElementById('pipeline-body');
  body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Carregando...</div>';
  try{
    const resp=await authFetch('/api/leads?per_page=100');
    const leads=await resp.json();
    if(!resp.ok){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro ao carregar.</div>';return;}
    if(!leads.length){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Nenhum lead ainda — analise um domínio para começar.</div>';return;}
    const byStage={};STAGE_ORDER.forEach(s=>byStage[s]=[]);
    leads.forEach(l=>{(byStage[l.stage||'novo']||byStage.novo).push(l)});
    body.innerHTML=`<div class="kanban">${STAGE_ORDER.map(stage=>{
      const cards=byStage[stage].map(l=>{
        const i=STAGE_ORDER.indexOf(stage);
        const left=i>0?`<button class="kb-move" title="Voltar" onclick="moveLead(${l.id},'${STAGE_ORDER[i-1]}')">‹</button>`:'<span></span>';
        const right=i<STAGE_ORDER.length-1?`<button class="kb-move" title="Avançar" onclick="moveLead(${l.id},'${STAGE_ORDER[i+1]}')">›</button>`:'<span></span>';
        const prio=l.priority?`<span class="kb-prio prio-${l.priority}">${l.score??''}</span>`:'';
        return `<div class="kb-card" draggable="true" data-lead-id="${l.id}" data-stage="${stage}" ondragstart="dragStart(event)" ondrop="dragDrop(event)" ondragover="dragOver(event)" ondragleave="dragLeave(event)">
          <div class="kb-card-top"><button class="kb-name" onclick="loadLeadIntoView(${l.id});closePipeline()">${esc(l.company_name||l.domain||'—')}</button>${prio}</div>
          <div class="kb-domain">${esc(l.domain||'')}</div>
          <div class="kb-card-actions">${left}${right}</div>
        </div>`;
      }).join('')||'<div class="kb-empty">—</div>';
      return `<div class="kb-col" data-stage="${stage}" ondrop="dragDropCol(event)" ondragover="dragOverCol(event)"><div class="kb-col-hdr">${STAGE_LABELS[stage]} <span class="kb-count">${byStage[stage].length}</span></div>${cards}</div>`;
    }).join('')}</div>`;
    initDragDrop();
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro de conexão.</div>';}
}

let draggedCard=null;
function dragStart(e){draggedCard=e.target.closest('.kb-card');draggedCard?.classList.add('dragging');}
function dragOver(e){e.preventDefault();e.target.closest('.kb-card')?.classList.add('drag-over');}
function dragLeave(e){e.target.closest('.kb-card')?.classList.remove('drag-over');}
function dragOverCol(e){e.preventDefault();}
function dragDrop(e){e.preventDefault();e.stopPropagation();if(!draggedCard)return;const target=e.target.closest('.kb-card');if(target&&target!==draggedCard){const col=draggedCard.parentElement;const targetCol=target.parentElement;if(col===targetCol){const rect=target.getBoundingClientRect();if(e.clientY<rect.top+rect.height/2)target.parentNode.insertBefore(draggedCard,target);else target.parentNode.insertBefore(draggedCard,target.nextSibling);}}}
function dragDropCol(e){e.preventDefault();if(!draggedCard)return;const col=e.target.closest('.kb-col');if(col){const stage=col.dataset.stage;const leadId=parseInt(draggedCard.dataset.leadId);moveLead(leadId,stage).then(()=>{draggedCard.classList.remove('dragging');draggedCard=null;loadPipeline();})}}
function initDragDrop(){document.querySelectorAll('.kb-card').forEach(c=>{c.addEventListener('dragend',()=>{c.classList.remove('dragging','drag-over');})})}

async function moveLead(id,stage){
  try{
    await authFetch(`/api/leads/${id}/stage`,{method:'PATCH',body:JSON.stringify({stage})});
    loadPipeline();
  }catch(_){}
}

/* ══════ INTEGRAÇÕES (IA + CRM) ══════ */
let _integr=null;
async function loadIntegrations(){
  try{
    const resp=await authFetch('/api/integrations/status');
    if(resp.ok)_integr=await resp.json();
  }catch(_){_integr=null;}
}

async function genAiSummary(force){
  if(!currentLeadId)return;
  const box=document.getElementById('ai-box');
  box.style.display='block';
  box.innerHTML='<div class="ai-loading">✨ Gerando resumo com IA...</div>';
  try{
    const resp=await authFetch(`/api/leads/${currentLeadId}/ai-summary${force?'?force=true':''}`,{method:'POST'});
    const json=await resp.json();
    if(resp.status===402){box.style.display='none';openPaywall();return;}
    if(!resp.ok){box.innerHTML=`<div class="ai-loading">${esc(json.detail||'Erro ao gerar resumo.')}</div>`;return;}
    box.innerHTML=`<div class="ai-title">✨ Resumo executivo ${json.cached?'<small>(cacheado)</small>':''} <a href="#" onclick="genAiSummary(true);return false" style="font-size:11px">regenerar</a></div><div class="ai-text">${esc(json.summary).replace(/\n/g,'<br/>')}</div>`;
  }catch(e){if(e.message!=='not_authenticated')box.innerHTML='<div class="ai-loading">Erro de conexão.</div>';}
}

async function pushToCrm(){
  if(!currentLeadId)return;
  const fb=document.getElementById('call-feedback');
  fb.textContent='Enviando ao CRM...';fb.classList.add('show');
  try{
    const resp=await authFetch(`/api/leads/${currentLeadId}/push`,{method:'POST'});
    const json=await resp.json();
    fb.textContent=resp.ok?'✓ Lead enviado ao CRM com sucesso.':(json.detail||'Falha no envio ao CRM.');
  }catch(e){if(e.message!=='not_authenticated')fb.textContent='Erro de conexão.';}
}

async function loadTimeline(){
  if(!currentLeadId)return;
  const box=document.getElementById('timeline-box');
  if(!box)return;
  try{
    const resp=await authFetch(`/api/leads/${currentLeadId}/activities`);
    const acts=await resp.json();
    if(!acts.length){box.style.display='none';return;}
    box.style.display='block';
    const tl=acts.map(a=>{
      const icon={call:'☎',voicemail:'📱',no_answer:'⏱',meeting:'✓',note:'📝'}[a.type]||'●';
      const when=a.completed_at?new Date(a.completed_at).toLocaleDateString('pt-BR'):(a.due_at?new Date(a.due_at).toLocaleDateString('pt-BR'):'');
      return `<div class="tl-item"><span class="tl-icon">${icon}</span><span class="tl-content"><strong>${a.type}</strong>${a.outcome?' · '+a.outcome:''}${when?' · '+when:''}<br/><small>${esc(a.notes||'')}</small></span></div>`;
    }).join('');
    box.innerHTML=`<div class="tl-title">⏱ Timeline</div><div class="timeline-rail">${tl}</div>`;
  }catch(_){}
}

/* ══════ RENDER RESULT ══════ */
function renderResult(data){
  currentLeadId=data.id;
  currentLeadData=data;
  setTimeout(loadTimeline, 300);
  const root=document.getElementById('results-section');root.innerHTML='';
  const smap={enriched:['Enriquecido','enriched'],partial:['Parcial','partial'],failed:['Falhou','failed']};
  const[sl,sc]=smap[data.status]||['—','partial'];
  const init=(data.company_name||data.domain||'?').trim()[0].toUpperCase();
  const fav=data.domain?`https://www.google.com/s2/favicons?domain=${data.domain}&sz=64`:'';
  const IC={
    globe:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`,
    li:`<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.45 20.45h-3.55v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.13 1.45-2.13 2.94v5.67h-3.55V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.45v6.29zM5.34 7.43A2.06 2.06 0 1 1 5.34 3.3a2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45z"/></svg>`,
    mail:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>`,
    users:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
    pin:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
    phone:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`,
    tag:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>`,
  };
  const cb=(conf)=>{if(!conf||conf==='none')return'';const m={verified:['OK','verified'],probable:['~','probable'],unverified:['?','unverified'],high:['OK','verified'],medium:['~','probable'],low:['?','unverified']};const[l,c]=m[conf]||[conf,'probable'];return ` <span class="conf-badge ${c}">${l}</span>`;};
  let emp='';
  if(data.employee_count){if(typeof data.employee_count==='object'){const e=data.employee_count;if(e.exact)emp=`${e.exact.toLocaleString('pt-BR')} (${e.band})`;else if(e.min&&e.max)emp=`${e.min.toLocaleString('pt-BR')}–${e.max.toLocaleString('pt-BR')}`;else if(e.min)emp=`${e.min.toLocaleString('pt-BR')}+`;else if(e.band)emp=e.band;else emp=e.raw||'';}else emp=data.employee_count;}
  const cell=(lbl,val,opts={})=>{const d=opts.delay||0;if(!val)return `<div class="data-cell" style="animation-delay:${d}ms"><span class="data-lbl">${opts.ic||''}${lbl}</span><span class="data-val muted">—</span></div>`;if(opts.isLink)return `<div class="data-cell" style="animation-delay:${d}ms"><span class="data-lbl">${opts.ic||''}${lbl}</span><a class="data-val link" href="${val}" target="_blank" rel="noopener">${esc(opts.disp||val)}</a></div>`;return `<div class="data-cell" style="animation-delay:${d}ms"><span class="data-lbl">${opts.ic||''}${lbl}</span><span class="data-val">${esc(val)}</span></div>`;};
  const ws=data.website?data.website.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,''):'';
  const li=data.linkedin_url?data.linkedin_url.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,''):'';
  let d=0;
  const cards=[
    cell('Site',ws,{ic:IC.globe,isLink:true,disp:ws,delay:d+=50}),
    `<div class="data-cell" style="animation-delay:${d+=50}ms"><span class="data-lbl">${IC.li}LinkedIn</span>${data.linkedin_url?`<a class="data-val link" href="${data.linkedin_url}" target="_blank" rel="noopener">${esc(li)}</a>${cb(data.linkedin_confidence)}`:'<span class="data-val muted">—</span>'}</div>`,
    `<div class="data-cell" style="animation-delay:${d+=50}ms"><span class="data-lbl">${IC.mail}Provedor MX</span>${data.mx_provider?`<span class="mx-tag">${esc(data.mx_provider)}</span>${cb(data.mx_provider_confidence)}`:'<span class="data-val muted">—</span>'}</div>`,
    cell('Funcionários',emp,{ic:IC.users,delay:d+=50}),
    cell('Localização',data.location,{ic:IC.pin,delay:d+=50}),
    cell('Setor',data.sector,{ic:IC.tag,delay:d+=50}),
    cell('Email Corporativo',data.corporate_email,{ic:IC.mail,delay:d+=50}),
    cell('Telefone',data.phone,{ic:IC.phone,delay:d+=50}),
  ];
  if(data.hosting_provider)cards.push(cell('Hosting',data.hosting_provider,{ic:IC.globe,delay:d+=50}));
  const desc=data.description?`<div class="result-desc">${esc(data.description)}</div>`:'';
  const dns=renderDNS(data.dns_report);
  root.innerHTML=`<div class="result-card">
    <div class="result-hdr">
      <div class="result-co">
        <div class="result-fav">${fav?`<img src="${fav}" onerror="this.style.display='none'" alt=""/>`:''}${init}</div>
        <div><div class="result-name">${esc(data.company_name||data.domain||'Empresa')}</div><div class="result-domain">${esc(data.domain||'')}</div></div>
      </div>
      <div class="result-hdr-right"><span id="score-pill-slot">${renderScorePill(data)}</span><span class="status-pill ${sc}">${sl}</span></div>
    </div>
    <div class="result-grid">${cards.join('')}</div>
    ${desc}${dns}
    <div class="dec-section">
      <div class="dec-title"><span class="dec-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11l-3-3m0 0l-3 3m3-3v12"/></svg></span>Mapear Decisores</div>
      <div class="dec-sub">Informe o cargo e receba perfis com LinkedIn e emails.</div>
      <div class="role-irow">
        <input id="role-input" class="role-inp" placeholder="Ex: Coordenador de TI, CFO, Diretor Comercial..." />
        <button class="role-srch-btn" id="role-btn" onclick="searchDecisores()">
          <span id="role-btn-text">Buscar</span>
          <span id="role-btn-spinner" class="spinner" style="display:none"></span>
        </button>
      </div>
      <div class="role-chips">
        <button class="role-chip" onclick="setRole('Coordenador de TI')">Coordenador de TI</button>
        <button class="role-chip" onclick="setRole('Diretor de TI')">Diretor de TI</button>
        <button class="role-chip" onclick="setRole('CTO')">CTO</button>
        <button class="role-chip" onclick="setRole('Gerente Comercial')">Gerente Comercial</button>
        <button class="role-chip" onclick="setRole('CFO')">CFO</button>
      </div>
      <div id="decisores-list" class="dec-list">
        <div class="empty-state-box">
          <div class="empty-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg></div>
          <div class="empty-title">Pronto para mapear decisores</div>
          <div class="empty-sub">Informe o cargo acima e clique em Buscar.</div>
        </div>
      </div>
    </div>
    <div class="call-section">
      <div class="dec-title"><span class="dec-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg></span>Registrar ligação</div>
      <div class="dec-sub">O resultado vira follow-up ou reunião automaticamente.</div>
      <div class="call-row">
        <button class="call-btn" onclick="logCall('no_answer')">Não atendeu</button>
        <button class="call-btn" onclick="logCall('busy')">Ocupado</button>
        <button class="call-btn" onclick="logCall('voicemail')">Caixa postal</button>
        <button class="call-btn" onclick="logCall('talked')">Conversou</button>
        <button class="call-btn meet" onclick="toggleMeetRow()">Reunião agendada</button>
      </div>
      <div class="meet-row" id="meet-row" style="display:none">
        <input type="datetime-local" id="meet-when" class="role-inp" style="max-width:230px;flex:none"/>
        <button class="role-srch-btn" onclick="logCall('meeting_scheduled')">Confirmar reunião</button>
      </div>
      <div id="call-feedback" class="call-feedback"></div>
    </div>
    <div class="integr-row" id="integr-row"></div>
    <div class="ai-box" id="ai-box" style="display:none"></div>
    <div id="timeline-box" style="display:none"></div>
  </div>`;
  // Ações de integração (só aparecem se configuradas no servidor)
  const integ=_integr||{};
  const integBtns=[
    integ.ai?`<button class="call-btn" onclick="genAiSummary()">✨ Resumo IA</button>`:'',
    integ.crm_webhook?`<button class="call-btn" onclick="pushToCrm()">⇪ Enviar ao CRM</button>`:'',
  ].filter(Boolean).join('');
  if(integBtns)document.getElementById('integr-row').innerHTML=integBtns;
  if(data.ai_summary){
    const box=document.getElementById('ai-box');
    box.style.display='block';
    box.innerHTML=`<div class="ai-title">✨ Resumo executivo <small>(cacheado)</small> <a href="#" onclick="genAiSummary(true);return false" style="font-size:11px">regenerar</a></div><div class="ai-text">${esc(data.ai_summary).replace(/\n/g,'<br/>')}</div>`;
  }
  document.getElementById('role-input').addEventListener('keydown',e=>{if(e.key==='Enter')searchDecisores()});
  const top=root.closest('.results-wrap').getBoundingClientRect().top+window.scrollY-100;
  window.scrollTo({top,behavior:'smooth'});
}

function setRole(v){const el=document.getElementById('role-input');if(el)el.value=v;}

async function searchDecisores(){
  if(!currentLeadId)return;
  const input=document.getElementById('role-input');
  const role=(input.value||'').trim();if(!role){input.focus();return;}
  const list=document.getElementById('decisores-list');
  const btn=document.getElementById('role-btn');
  const bText=document.getElementById('role-btn-text');
  const bSpin=document.getElementById('role-btn-spinner');
  btn.disabled=true;bText.textContent='Buscando...';bSpin.style.display='inline-block';
  list.innerHTML='<div style="padding:24px;text-align:center;color:var(--text-3);font-size:13.5px">Procurando no LinkedIn... (até 15s)</div>';
  try{
    const resp=await authFetch('/api/decisores',{method:'POST',body:JSON.stringify({lead_id:currentLeadId,roles:[role]})});
    const json=await resp.json();
    if(!resp.ok||!json.success){list.innerHTML=`<div style="padding:24px;text-align:center;color:var(--text-3);font-size:13.5px">${esc(json.detail||json.message||'Erro.')}</div>`;return;}
    renderDecisores(json.decisores);
    refreshLeadScore(); // sinais de decisor mudam o score
  }catch(e){list.innerHTML=`<div style="padding:24px;text-align:center;color:var(--text-3);font-size:13.5px">Erro de conexão.</div>`;}
  finally{btn.disabled=false;bText.textContent='Buscar';bSpin.style.display='none';}
}

function renderDecisores(list){
  const root=document.getElementById('decisores-list');
  if(!list||!list.length){root.innerHTML=`<div class="empty-state-box"><div class="empty-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/></svg></div><div class="empty-title">Nenhum resultado</div><div class="empty-sub">Tente variar o cargo — "Diretor TI" em vez de "Diretor de TI".</div></div>`;return;}
  const mB=(c)=>{const m={high:['✓','verified'],medium:['~','probable'],low:['?','unverified']};const[l,cl]=m[c]||['?','unverified'];return `<span class="conf-badge ${cl}">${l}</span>`;};
  const eC=(e)=>{if(typeof e==='string')return `<span class="meta-chip email">${esc(e)}</span>`;const m={valid:['✓','email-valid'],catch_all:['~','email-catchall'],invalid:['✗','email-invalid'],unknown:['?','']};const[ic,cl]=m[e.status]||m.unknown;return `<span class="meta-chip email ${cl}">${ic} ${esc(e.email)}</span>`;};
  root.innerHTML=list.map((p,i)=>{
    const init=(p.name||'?').trim()[0].toUpperCase();
    const emails=(p.probable_emails||[]).slice(0,4).map(eC).join('');
    const li=p.linkedin_url?`<a class="meta-chip linkedin" href="${p.linkedin_url}" target="_blank" rel="noopener">LinkedIn</a>`:'';
    return `<div class="dec-card" style="animation-delay:${i*60}ms"><div class="dec-ava">${init}</div><div class="dec-info"><div class="dec-name">${esc(p.name||'—')} ${mB(p.match_confidence)}</div><div class="dec-role-txt">${esc(p.title_searched||'')}</div>${p.snippet?`<div class="dec-snippet">${esc(p.snippet.slice(0,200))}</div>`:''}<div class="dec-meta">${li}${emails}</div></div></div>`;
  }).join('');
}

function renderDNS(dns){
  if(!dns||(!dns.mx&&!dns.a))return'';
  const e=(s)=>esc(String(s||''));
  const mx=(dns.mx||[]).map(m=>`<tr><td class="dns-prio">${m.priority}</td><td>${e(m.host)}</td><td>${e(m.ip||'—')}</td><td>${e(m.asn_org||'—')}</td><td>${e(m.country||'—')}</td></tr>`).join('');
  const txt=(dns.txt||[]).slice(0,8).map(t=>`<li>${e(t)}</li>`).join('');
  return `<details class="dns-report"><summary><span class="dns-tog">⌄</span>Relatório DNS completo</summary><div class="dns-body"><div class="dns-sec"><h4>MX</h4><table class="dns-tbl"><thead><tr><th>Prio</th><th>Host</th><th>IP</th><th>ASN</th><th>País</th></tr></thead><tbody>${mx||'<tr><td colspan="5" style="color:var(--text-3)">sem registros</td></tr>'}</tbody></table></div><div class="dns-grid"><div class="dns-sec"><h4>A</h4><ul class="dns-ul">${(dns.a||[]).map(x=>`<li>${e(x)}</li>`).join('')||'<li style="color:var(--text-3)">—</li>'}</ul></div><div class="dns-sec"><h4>NS</h4><ul class="dns-ul">${(dns.ns||[]).map(x=>`<li>${e(x)}</li>`).join('')||'<li style="color:var(--text-3)">—</li>'}</ul></div></div>${dns.spf?`<div class="dns-sec"><h4>SPF</h4><div class="dns-rec">${e(dns.spf)}</div></div>`:''}</div></details>`;
}

/* ══════ FEATURES PROGRESSIVE ══════ */
function showAllFeatures(){
  document.querySelectorAll('.feat-card.hidden').forEach(c=>{c.classList.remove('hidden');setTimeout(()=>c.classList.add('in'),50);});
  document.getElementById('show-more-btn').style.display='none';
}

/* ══════ COMMAND PALETTE ══════ */
function openCmd(){document.getElementById('cmd-overlay').classList.add('open');setTimeout(()=>document.getElementById('cmd-input').focus(),50);}
function closeCmd(){document.getElementById('cmd-overlay').classList.remove('open');document.getElementById('cmd-input').value='';}
function closeCmdOutside(e){if(e.target===document.getElementById('cmd-overlay'))closeCmd();}
function cmdSearch(){const v=document.getElementById('cmd-input').value.trim();closeCmd();if(v){document.getElementById('domain-input').value=v;enrich();}else document.getElementById('domain-input').focus();}
function cmdGoto(id){closeCmd();const el=document.getElementById(id);if(el)window.scrollTo({top:el.getBoundingClientRect().top+window.scrollY-80,behavior:'smooth'});}
function cmdFill(d){closeCmd();document.getElementById('domain-input').value=d;enrich();}
function filterCmd(val){
  document.querySelectorAll('.cmd-row').forEach(r=>{
    const lbl=r.querySelector('.cmd-row-lbl')?.textContent||'';
    r.style.display=val&&!lbl.toLowerCase().includes(val.toLowerCase())?'none':'flex';
  });
}
function cmdKeydown(e){
  if(e.key==='Escape'){closeCmd();return;}
  if(e.key==='Enter'){const s=document.querySelector('.cmd-row.sel:not([style*="display: none"])');if(s)s.click();else cmdSearch();}
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    const items=[...document.querySelectorAll('.cmd-row:not([style*="display: none"])')];
    const idx=items.findIndex(i=>i.classList.contains('sel'));
    items.forEach(i=>i.classList.remove('sel'));
    const next=e.key==='ArrowDown'?(idx+1)%items.length:(idx-1+items.length)%items.length;
    items[next]?.classList.add('sel');e.preventDefault();
  }
}
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='k'){e.preventDefault();openCmd();}
  if(e.key==='Escape')closeCmd();
});

/* ══════ HELPERS ══════ */
function setLoading(s){
  const btn=document.getElementById('search-btn');
  const txt=document.getElementById('search-btn-text');
  const sp=document.getElementById('search-btn-spinner');
  btn.disabled=s;txt.textContent=s?'Analisando...':'Analisar';sp.style.display=s?'inline-block':'none';
}
function showError(m){const el=document.getElementById('error-banner');el.textContent=m;el.style.display='block';}
function hideError(){document.getElementById('error-banner').style.display='none';}
function hideResults(){document.getElementById('results-section').innerHTML='';}
function fillExample(v){document.getElementById('domain-input').value=v;document.getElementById('domain-input').focus();}
function esc(s){if(s==null)return'';return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function toggleFAQ(item){document.querySelectorAll('.faq-item.open').forEach(el=>{if(el!==item)el.classList.remove('open')});item.classList.toggle('open');}

async function deleteLead(leadId){
  if(!confirm('Remover este lead do histórico?'))return;
  try{
    const resp=await authFetch(`/api/leads/${leadId}`,{method:'DELETE'});
    if(resp.ok||resp.status===204){
      const row=document.getElementById(`row-${leadId}`);
      if(row){row.style.opacity='0';row.style.transition='opacity .25s';setTimeout(()=>row.remove(),250);}
    }else{
      alert('Erro ao remover lead.');
    }
  }catch(e){
    if(e.message!=='not_authenticated')alert('Erro de conexão.');
  }
}

/* ══════ HISTÓRICO DE LEADS ══════ */
function openHistory(){document.getElementById('history-modal').classList.add('open');loadHistory();}
function closeHistory(){document.getElementById('history-modal').classList.remove('open');}
function closeHistoryOutside(e){if(e.target===document.getElementById('history-modal'))closeHistory();}

async function loadHistory(){
  const body=document.getElementById('history-body');
  body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Carregando...</div>';
  try{
    const resp=await authFetch('/api/leads?per_page=50');
    if(!resp.ok){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro ao carregar histórico.</div>';return;}
    const leads=await resp.json();
    if(!leads.length){body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Nenhum lead encontrado. Faça sua primeira busca!</div>';return;}
    body.innerHTML=leads.map(l=>{
      const date=new Date(l.created_at).toLocaleDateString('pt-BR',{day:'2-digit',month:'short',year:'numeric'});
      const emp=l.employee_count?(typeof l.employee_count==='object'?(l.employee_count.band||l.employee_count.raw||''):l.employee_count):'';
      return `<div id="row-${l.id}" style="display:flex;align-items:center;gap:12px;padding:11px 22px;border-bottom:1px solid var(--border);transition:background .15s" onmouseenter="this.style.background='var(--surface)'" onmouseleave="this.style.background=''">
        <div style="width:36px;height:36px;border-radius:8px;background:var(--surface);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;color:var(--accent);flex-shrink:0">${esc((l.company_name||l.domain||'?')[0].toUpperCase())}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(l.company_name||l.domain)}</div>
          <div style="font-size:11px;color:var(--text-3);margin-top:2px">${esc(l.domain||'')}${l.mx_provider?` · ${esc(l.mx_provider)}`:''}${emp?` · ${esc(emp)} func.`:''}</div>
        </div>
        <div style="font-size:11px;color:var(--text-3);white-space:nowrap">${date}</div>
        <div style="display:flex;gap:6px">
          <button onclick="loadLeadIntoView(${l.id})" title="Ver resultado" style="background:var(--accent);color:#fff;border:none;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:11px;font-weight:600">Ver</button>
          <button onclick="exportLead(${l.id},'csv')" title="Exportar CSV" style="background:none;border:1px solid var(--border);color:var(--text-3);padding:4px 8px;border-radius:6px;cursor:pointer;font-size:11px">CSV</button>
          <button onclick="deleteLead(${l.id})" title="Remover" style="background:none;border:1px solid var(--border);color:#f87171;padding:4px 8px;border-radius:6px;cursor:pointer;font-size:11px">✕</button>
        </div>
      </div>`;
    }).join('');
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-3);font-size:13px">Erro de conexão.</div>';}
}

async function loadLeadIntoView(leadId){
  try{
    const resp=await authFetch(`/api/leads/${leadId}`);
    if(!resp.ok)return;
    const lead=await resp.json();
    closeHistory();
    renderResult(lead);
  }catch(_){}
}

async function exportLead(leadId,fmt){
  const token=await getToken();if(!token)return;
  const a=document.createElement('a');
  a.href=`/api/export/${leadId}?format=${fmt}`;
  a.setAttribute('download','');
  // inclui token via fetch pois link simples não tem auth header
  const resp=await fetch(a.href,{headers:{Authorization:`Bearer ${token}`}});
  if(!resp.ok){alert('Erro ao exportar.');return;}
  const blob=await resp.blob();
  const url=URL.createObjectURL(blob);
  const cd=resp.headers.get('content-disposition')||'';
  const match=cd.match(/filename="([^"]+)"/);
  a.href=url;a.download=match?match[1]:`lead_${leadId}.${fmt}`;
  document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(url);a.remove();},1000);
}

async function exportAll(fmt){
  const token=await getToken();if(!token)return;
  const resp=await fetch(`/api/export?format=${fmt}`,{headers:{Authorization:`Bearer ${token}`}});
  if(!resp.ok){alert('Nenhum lead para exportar.');return;}
  const blob=await resp.blob();
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=`leads_enriquecidos.${fmt}`;
  document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(url);a.remove();},1000);
}

/* ══════ INTERSECTION OBSERVER (fade-up) ══════ */
const io=new IntersectionObserver(entries=>{
  for(const e of entries){if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);}}
},{threshold:.1});
document.querySelectorAll('.fade-up').forEach(el=>io.observe(el));

/* ══════ INIT ══════ */
document.addEventListener('DOMContentLoaded',async()=>{
  document.getElementById('domain-input').addEventListener('keydown',e=>{if(e.key==='Enter')enrich();});
  // domínio vindo da landing (/app?domain=…) — pré-preenche e foca
  const _qDomain=new URLSearchParams(window.location.search).get('domain');
  if(_qDomain){
    const inp=document.getElementById('domain-input');
    inp.value=_qDomain;inp.focus();
    window.history.replaceState({},'',window.location.pathname);
  }

  // handle magic link redirect (token in URL hash)
  const{data:{session}}=await _sb.auth.getSession();
  if(session||isDemoSession()){
    await loadProfile();
    loadIntegrations();
    openDashboard();
  }else{
    updateNavUser();
  }

  // listen for auth state changes (magic link callback)
  _sb.auth.onAuthStateChange(async(event,session)=>{
    if(session){
      closeAuthModal();
      const wasLoggedIn=!!_profile;
      await loadProfile();
      loadIntegrations();
      // On first login this session, open Dashboard so the user sees the product
      if(!wasLoggedIn)openDashboard();
    }else{
      _profile=null;
      updateNavUser();
    }
  });

  // check if returning from Stripe
  if(new URLSearchParams(window.location.search).get('upgraded')==='1'){
    await loadProfile();
    window.history.replaceState({},'',window.location.pathname);
  }
});
