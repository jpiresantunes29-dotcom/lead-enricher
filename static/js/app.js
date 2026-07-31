/* ════════════════════════════════════════════════════════════════
   LeadEnricher — app (/app)
   Views roteadas por hash: '' (buscar) · #dashboard · #pipeline ·
   #followups · #history · #settings · #lead-<id>
   ════════════════════════════════════════════════════════════════ */

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
let _profile=null;
let currentLeadId=null;
let _pendingRoute=null;   // rota que o usuário tentou abrir antes de logar

// Rota canônica do produto — precisa estar na allowlist de Redirect URLs do Supabase
const _AUTH_REDIRECT=window.location.origin+'/app';

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
  if(_pendingRoute){const r=_pendingRoute;_pendingRoute=null;location.hash=r;}
  else{applyRoute();focusSearch();}
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
  if(rem>0){
    el.innerHTML='<strong></strong> análises disponíveis neste ciclo.';
    el.querySelector('strong').textContent=`${rem} de ${searches_limit}`;
  }
  else el.innerHTML=`<strong>Cota do ciclo esgotada.</strong> <button onclick="startCheckout('pro')">Fazer upgrade</button>`;
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
    if(el)el.textContent=`${quota} análises`;
  }else{
    preauth.style.display='flex';
    if(postauth)postauth.style.display='none';
  }
}

function openAuthModal(){document.getElementById('auth-modal').classList.add('open');}
function closeAuthModal(){
  document.getElementById('auth-modal').classList.remove('open');
  document.getElementById('auth-error').style.display='none';
  document.getElementById('auth-success').style.display='none';
}

async function sendMagicLink(){
  const email=document.getElementById('auth-email').value.trim();
  const errEl=document.getElementById('auth-error');
  if(!email){errEl.textContent='Informe seu email.';errEl.style.display='block';return;}
  const btn=document.getElementById('magic-btn-text');const sp=document.getElementById('magic-btn-spin');
  btn.textContent='Enviando...';sp.style.display='inline-block';
  errEl.style.display='none';
  const{error}=await _sb.auth.signInWithOtp({email,options:{emailRedirectTo:_AUTH_REDIRECT}});
  btn.textContent='Receber link de acesso';sp.style.display='none';
  if(error){errEl.textContent=error.message;errEl.style.display='block';}
  else{const ok=document.getElementById('auth-success');ok.textContent='Link enviado. Verifique seu e-mail.';ok.style.display='block';}
}

async function signInWithGoogle(){await _sb.auth.signInWithOAuth({provider:'google',options:{redirectTo:_AUTH_REDIRECT}});}
async function signInWithGitHub(){await _sb.auth.signInWithOAuth({provider:'github',options:{redirectTo:_AUTH_REDIRECT}});}
async function signInWithMicrosoft(){await _sb.auth.signInWithOAuth({provider:'azure',options:{redirectTo:_AUTH_REDIRECT}});}
async function signInWithApple(){await _sb.auth.signInWithOAuth({provider:'apple',options:{redirectTo:_AUTH_REDIRECT}});}

async function signOut(){
  _clearDemoToken();
  await _sb.auth.signOut();
  _profile=null;_integr=null;currentLeadId=null;
  hideResults();hideError();
  const tc=document.getElementById('trial-counter');if(tc)tc.style.display='none';
  updateNavUser();
  if(location.hash)history.replaceState(null,'',location.pathname);
  showView('search');
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
function closeIfBackdrop(e,id){if(e.target===document.getElementById(id))document.getElementById(id).classList.remove('open');}

/* ══════ ROUTER (views por hash) ══════ */
const ROUTES=['','dashboard','pipeline','followups','history','settings'];

function nav(route){
  if(route&&!_profile){_pendingRoute=route;openAuthModal();return;}
  if(location.hash.slice(1)===route)applyRoute();   // re-clique recarrega a view
  else location.hash=route;
}

function showView(v){
  document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active',s.id==='view-'+v));
  const route=v==='search'?'':v;
  document.querySelectorAll('[data-route]').forEach(b=>b.classList.toggle('active',b.dataset.route===route));
  window.scrollTo({top:0});
}

function applyRoute(){
  let h=location.hash.slice(1);
  if(h.startsWith('lead-')){
    const id=parseInt(h.slice(5),10);
    if(!_profile){_pendingRoute=h;showView('search');openAuthModal();return;}
    showView('search');
    if(id)openLead(id);
    return;
  }
  if(!ROUTES.includes(h))h='';
  if(h&&!_profile){
    _pendingRoute=h;
    history.replaceState(null,'',location.pathname);
    showView('search');openAuthModal();
    return;
  }
  showView(h||'search');
  if(h==='dashboard')loadDashboard();
  else if(h==='pipeline')loadPipeline();
  else if(h==='followups')loadFollowups();
  else if(h==='history')loadHistory();
  else if(h==='settings')loadSettings();
}
window.addEventListener('hashchange',applyRoute);

function focusSearch(){
  if(matchMedia('(pointer: coarse)').matches)return; // não abre teclado no mobile
  document.getElementById('domain-input')?.focus();
}

/* ══════ ENRICH ══════ */
async function enrich(){
  const input=document.getElementById('domain-input');
  const domain=input.value.trim();
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
    loadProfile(); // recarrega cota do servidor — cache hit não consome busca
    renderResult(json.data);
    if(json.data.id)history.replaceState(null,'','#lead-'+json.data.id);
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
  return `<span class="score-wrap"><button class="score-pill prio-${p}" onclick="toggleScorePop(event)" title="Ver composição do score">${data.score} · ${labels[p]||p}</button><div class="score-pop" id="score-pop" onclick="event.stopPropagation()"><div class="sp-title">Por que ${data.score} pontos?</div>${rows}<div class="sp-foot">Modelo ${esc(data.score_version||'v1')} · recalculado ao encontrar decisores</div></div></span>`;
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
    loadTimeline();loadTodayFollowupsCount();
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

/* ══════ VIEW: FOLLOW-UPS ══════ */
async function loadFollowups(){
  const body=document.getElementById('followups-body');
  body.innerHTML='<div class="muted-box">Carregando…</div>';
  try{
    const resp=await authFetch('/api/activities/pending');
    const list=await resp.json();
    if(!resp.ok){body.innerHTML='<div class="muted-box">Erro ao carregar.</div>';return;}
    if(!list.length){
      body.innerHTML=`<div class="muted-box">Tudo em dia — nenhum follow-up pendente.<br/><a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Analisar um domínio</a></div>`;
      return;
    }
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
          <span class="fu-when${late?' late':''}">${late?'atrasado · ':''}${when}</span>
        </div>
        <div class="fu-actions">
          <button class="fu-btn" onclick="loadLeadIntoView(${a.lead_id})">Abrir lead</button>
          ${ics}
          <button class="fu-btn done" onclick="completeActivity(${a.id})">Concluir</button>
        </div>
      </div>`;
    }).join('');
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div class="muted-box">Erro de conexão.</div>';}
}

async function completeActivity(id){
  try{
    await authFetch(`/api/activities/${id}`,{method:'PATCH',body:JSON.stringify({completed:true})});
    loadFollowups();loadTodayFollowupsCount();
  }catch(_){}
}

/* ══════ VIEW: DASHBOARD ══════ */
const STAGE_LABELS={novo:'Novo',contatado:'Contatado',reuniao_agendada:'Reunião agendada',oportunidade:'Oportunidade',ganho:'Ganho',perdido:'Perdido'};
const STAGE_ORDER=['novo','contatado','reuniao_agendada','oportunidade','ganho','perdido'];
const PRIO_LABELS={alta:'Alta',media:'Média',baixa:'Baixa'};

async function loadDashboard(){
  const body=document.getElementById('dashboard-body');
  body.innerHTML='<div class="panel"><div class="muted-box">Carregando…</div></div>';
  try{
    const resp=await authFetch('/api/dashboard/metrics?days=30');
    const m=await resp.json();
    if(!resp.ok){body.innerHTML='<div class="panel"><div class="muted-box">Erro ao carregar.</div></div>';return;}
    document.getElementById('dash-period').textContent=`Últimos ${m.period_days} dias`;
    if(!m.leads_pesquisados){
      body.innerHTML=`<div class="panel"><div class="muted-box">
        Seu dashboard ganha vida com a primeira busca.<br/>
        <a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Analisar meu primeiro domínio</a>
      </div></div>`;
      return;
    }
    const pct=v=>Math.round(v*100)+'%';
    const kpi=(val,lbl,warn)=>`<div class="kpi${warn?' warn':''}"><span class="kpi-val">${val}</span><span class="kpi-lbl">${lbl}</span></div>`;
    const funilMax=Math.max(1,...STAGE_ORDER.map(s=>m.funil_por_estagio[s]||0));
    const funil=STAGE_ORDER.map(s=>{
      const v=m.funil_por_estagio[s]||0;
      return `<div class="fn-row"><span class="fn-lbl">${STAGE_LABELS[s]}</span><div class="fn-track"><div class="fn-bar" style="width:${Math.max(2,(v/funilMax)*100)}%"></div></div><span class="fn-val">${v}</span></div>`;
    }).join('');
    const prio=['alta','media','baixa'].map(p=>{
      const v=m.leads_por_prioridade[p]||0;
      return `<span class="score-pill static prio-${p}">${PRIO_LABELS[p]}: ${v}</span>`;
    }).join('');
    body.innerHTML=`
      <div class="kpi-grid">
        ${kpi(m.leads_pesquisados,'leads pesquisados')}
        ${kpi(m.ligacoes_realizadas,'ligações realizadas')}
        ${kpi(pct(m.taxa_contato),'taxa de contato')}
        ${kpi(pct(m.taxa_reuniao),'taxa de reunião')}
        ${kpi(pct(m.conversao_oportunidade),'conversão p/ oportunidade')}
        ${kpi(m.followups_pendentes+(m.followups_atrasados?` <small>(${m.followups_atrasados} atrasados)</small>`:''),'follow-ups pendentes',m.followups_atrasados>0)}
      </div>
      <div class="panel panel-pad">
        <div class="dash-sec-title" style="margin-top:0">Funil por estágio</div>
        <div class="funnel">${funil}</div>
        <div class="dash-sec-title">Leads por prioridade</div>
        <div class="prio-row">${prio}</div>
      </div>`;
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div class="panel"><div class="muted-box">Erro de conexão.</div></div>';}
}

/* ══════ VIEW: PIPELINE (KANBAN) ══════ */
async function loadPipeline(){
  const body=document.getElementById('pipeline-body');
  body.innerHTML='<div class="panel"><div class="muted-box">Carregando…</div></div>';
  try{
    const resp=await authFetch('/api/leads?per_page=100');
    const leads=await resp.json();
    if(!resp.ok){body.innerHTML='<div class="panel"><div class="muted-box">Erro ao carregar.</div></div>';return;}
    if(!leads.length){
      body.innerHTML=`<div class="panel"><div class="muted-box">Nenhum lead ainda.<br/><a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Analisar um domínio</a></div></div>`;
      return;
    }
    const byStage={};STAGE_ORDER.forEach(s=>byStage[s]=[]);
    leads.forEach(l=>{(byStage[l.stage||'novo']||byStage.novo).push(l)});
    body.innerHTML=`<div class="kanban-wrap"><div class="kanban">${STAGE_ORDER.map(stage=>{
      const cards=byStage[stage].map(l=>{
        const i=STAGE_ORDER.indexOf(stage);
        const left=i>0?`<button class="kb-move" title="Voltar" onclick="moveLead(${l.id},'${STAGE_ORDER[i-1]}')">‹</button>`:'<span></span>';
        const right=i<STAGE_ORDER.length-1?`<button class="kb-move" title="Avançar" onclick="moveLead(${l.id},'${STAGE_ORDER[i+1]}')">›</button>`:'<span></span>';
        const prio=l.priority?`<span class="kb-prio prio-${l.priority}">${l.score??''}</span>`:'';
        return `<div class="kb-card" draggable="true" data-lead-id="${l.id}" ondragstart="dragStart(event)">
          <div class="kb-card-top"><button class="kb-name" onclick="loadLeadIntoView(${l.id})">${esc(l.company_name||l.domain||'—')}</button>${prio}</div>
          <div class="kb-domain">${esc(l.domain||'')}</div>
          <div class="kb-card-actions">${left}${right}</div>
        </div>`;
      }).join('')||'<div class="kb-empty">—</div>';
      return `<div class="kb-col" data-stage="${stage}" ondrop="dragDropCol(event)" ondragover="dragOverCol(event)" ondragleave="dragLeaveCol(event)"><div class="kb-col-hdr">${STAGE_LABELS[stage]} <span class="kb-count">${byStage[stage].length}</span></div>${cards}</div>`;
    }).join('')}</div></div>`;
    document.querySelectorAll('.kb-card').forEach(c=>{
      c.addEventListener('dragend',()=>{c.classList.remove('dragging');document.querySelectorAll('.kb-col.drag-over').forEach(k=>k.classList.remove('drag-over'));});
    });
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div class="panel"><div class="muted-box">Erro de conexão.</div></div>';}
}

let draggedCard=null;
function dragStart(e){draggedCard=e.target.closest('.kb-card');draggedCard?.classList.add('dragging');}
function dragOverCol(e){e.preventDefault();e.currentTarget.classList.add('drag-over');}
function dragLeaveCol(e){if(!e.currentTarget.contains(e.relatedTarget))e.currentTarget.classList.remove('drag-over');}
function dragDropCol(e){
  e.preventDefault();
  const col=e.currentTarget;col.classList.remove('drag-over');
  if(!draggedCard)return;
  const leadId=parseInt(draggedCard.dataset.leadId,10);
  draggedCard=null;
  moveLead(leadId,col.dataset.stage);
}

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

const IC_SPARK='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.9 5.7L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.3z"/></svg>';
const IC_PUSH='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/><polyline points="7 8 12 3 17 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
const IC_DL='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

async function genAiSummary(force){
  if(!currentLeadId)return;
  const box=document.getElementById('ai-box');
  box.style.display='block';
  box.innerHTML='<div class="ai-loading">Gerando resumo com IA…</div>';
  try{
    const resp=await authFetch(`/api/leads/${currentLeadId}/ai-summary${force?'?force=true':''}`,{method:'POST'});
    const json=await resp.json();
    if(resp.status===402){box.style.display='none';openPaywall();return;}
    if(!resp.ok){box.innerHTML=`<div class="ai-loading">${esc(json.detail||'Erro ao gerar resumo.')}</div>`;return;}
    box.innerHTML=`<div class="ai-title">${IC_SPARK} Resumo executivo ${json.cached?'<small>(cacheado)</small>':''} <a href="#" onclick="genAiSummary(true);return false">regenerar</a></div><div class="ai-text">${esc(json.summary).replace(/\n/g,'<br/>')}</div>`;
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
    if(resp.ok)loadTimeline();
  }catch(e){if(e.message!=='not_authenticated')fb.textContent='Erro de conexão.';}
}

/* ══════ TIMELINE ══════ */
const ACT_LABELS={call:'Ligação',voicemail:'Caixa postal',no_answer:'Sem resposta',meeting:'Reunião',note:'Nota',followup:'Follow-up'};
const OUT_LABELS={no_answer:'não atendeu',busy:'ocupado',voicemail:'caixa postal',talked:'conversou',meeting_scheduled:'reunião agendada'};

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
      const cls=a.type==='meeting'?' ok':(a.outcome==='no_answer'||a.outcome==='busy'?' warn':'');
      const when=a.completed_at?new Date(a.completed_at).toLocaleDateString('pt-BR'):(a.due_at?new Date(a.due_at).toLocaleDateString('pt-BR'):'');
      const label=ACT_LABELS[a.type]||a.type;
      const out=a.outcome?(OUT_LABELS[a.outcome]||a.outcome):'';
      return `<div class="tl-item${cls}"><span class="tl-dot"></span><span class="tl-content"><span><strong>${esc(label)}</strong>${out?' · '+esc(out):''}${when?' · '+when:''}</span>${a.notes?`<small>${esc(a.notes)}</small>`:''}</span></div>`;
    }).join('');
    box.innerHTML=`<div class="tl-title">Timeline</div><div class="timeline-rail">${tl}</div>`;
  }catch(_){}
}

/* ══════ RENDER RESULT ══════ */
async function openLead(leadId){
  try{
    const resp=await authFetch(`/api/leads/${leadId}`);
    if(!resp.ok){showError('Lead não encontrado.');return;}
    const lead=await resp.json();
    hideError();
    renderResult(lead);
  }catch(_){}
}

function loadLeadIntoView(leadId){
  const h='lead-'+leadId;
  if(location.hash.slice(1)===h){showView('search');openLead(leadId);}
  else location.hash=h;
}

function renderResult(data){
  currentLeadId=data.id;
  currentLeadData=data;
  setTimeout(loadTimeline,300);
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
  if(data.employee_count){if(typeof data.employee_count==='object'){const e=data.employee_count;if(e.exact)emp=e.exact.toLocaleString('pt-BR');else if(e.min&&e.max)emp=`${e.min.toLocaleString('pt-BR')}–${e.max.toLocaleString('pt-BR')} (faixa)`;else if(e.min)emp=`${e.min.toLocaleString('pt-BR')}+ (faixa)`;else if(e.band)emp=e.band;else emp=e.raw||'';}else emp=data.employee_count;}
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
    <div class="lead-actions" id="lead-actions"></div>
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
    <div class="ai-box" id="ai-box" style="display:none"></div>
    <div id="timeline-box" style="display:none"></div>
  </div>`;
  // Barra de ações: exportação sempre; IA e CRM conforme configuração
  const integ=_integr||{};
  document.getElementById('lead-actions').innerHTML=[
    `<span class="la-lbl">Ações</span>`,
    `<button class="la-btn" onclick="exportLead(${data.id},'csv')">${IC_DL} CSV</button>`,
    `<button class="la-btn" onclick="exportLead(${data.id},'xlsx')">${IC_DL} Excel</button>`,
    integ.ai?`<button class="la-btn" onclick="genAiSummary()">${IC_SPARK} Resumo IA</button>`:'',
    integ.crm_webhook?`<button class="la-btn accent" onclick="pushToCrm()">${IC_PUSH} Enviar ao CRM</button>`:'',
  ].filter(Boolean).join('');
  if(data.ai_summary){
    const box=document.getElementById('ai-box');
    box.style.display='block';
    box.innerHTML=`<div class="ai-title">${IC_SPARK} Resumo executivo <small>(cacheado)</small> <a href="#" onclick="genAiSummary(true);return false">regenerar</a></div><div class="ai-text">${esc(data.ai_summary).replace(/\n/g,'<br/>')}</div>`;
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
  list.innerHTML='<div class="muted-box">Procurando no LinkedIn… (até 15s)</div>';
  try{
    const resp=await authFetch('/api/decisores',{method:'POST',body:JSON.stringify({lead_id:currentLeadId,roles:[role]})});
    const json=await resp.json();
    if(!resp.ok||!json.success){list.innerHTML=`<div class="muted-box">${esc(json.detail||json.message||'Erro.')}</div>`;return;}
    renderDecisores(json.decisores);
    refreshLeadScore(); // sinais de decisor mudam o score
  }catch(e){list.innerHTML='<div class="muted-box">Erro de conexão.</div>';}
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
  return `<details class="dns-report"><summary><span class="dns-tog">⌄</span>Relatório DNS completo</summary><div class="dns-body"><div class="dns-sec"><h4>MX</h4><table class="dns-tbl"><thead><tr><th>Prio</th><th>Host</th><th>IP</th><th>ASN</th><th>País</th></tr></thead><tbody>${mx||'<tr><td colspan="5">sem registros</td></tr>'}</tbody></table></div><div class="dns-grid"><div class="dns-sec"><h4>A</h4><ul class="dns-ul">${(dns.a||[]).map(x=>`<li>${e(x)}</li>`).join('')||'<li>—</li>'}</ul></div><div class="dns-sec"><h4>NS</h4><ul class="dns-ul">${(dns.ns||[]).map(x=>`<li>${e(x)}</li>`).join('')||'<li>—</li>'}</ul></div></div>${dns.spf?`<div class="dns-sec"><h4>SPF</h4><div class="dns-rec">${e(dns.spf)}</div></div>`:''}</div></details>`;
}

/* ══════ VIEW: HISTÓRICO ══════ */
let _histLeads=[];

async function loadHistory(){
  const body=document.getElementById('history-body');
  body.innerHTML='<div class="muted-box">Carregando…</div>';
  document.getElementById('history-filter').value='';
  try{
    const resp=await authFetch('/api/leads?per_page=100');
    if(!resp.ok){body.innerHTML='<div class="muted-box">Erro ao carregar histórico.</div>';return;}
    _histLeads=await resp.json();
    renderHistory('');
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div class="muted-box">Erro de conexão.</div>';}
}

function filterHistory(q){renderHistory(q);}

/* Ordenação da tabela de histórico */
let _histSort={key:'created_at',dir:-1};

function _histEmpMin(l){
  const e=l.employee_count;
  if(!e||typeof e!=='object')return -1;
  return e.exact||e.min||-1;
}
function _histSortVal(l,key){
  switch(key){
    case 'company':return (l.company_name||l.domain||'').toLowerCase();
    case 'domain':return (l.domain||'').toLowerCase();
    case 'score':return l.score??-1;
    case 'stage':return STAGE_ORDER.indexOf(l.stage||'novo');
    case 'employees':return _histEmpMin(l);
    default:return l.created_at||'';
  }
}
function sortHistory(key){
  if(_histSort.key===key)_histSort.dir*=-1;
  else _histSort={key,dir:(key==='created_at'||key==='score'||key==='employees')?-1:1};
  renderHistory(document.getElementById('history-filter').value);
}

function renderHistory(q){
  const body=document.getElementById('history-body');
  const count=document.getElementById('history-count');
  const norm=(q||'').trim().toLowerCase();
  const leads=(norm?_histLeads.filter(l=>
    (l.company_name||'').toLowerCase().includes(norm)||(l.domain||'').toLowerCase().includes(norm)
  ):[..._histLeads]).sort((a,b)=>{
    const va=_histSortVal(a,_histSort.key),vb=_histSortVal(b,_histSort.key);
    return (va<vb?-1:va>vb?1:0)*_histSort.dir;
  });
  count.textContent=_histLeads.length?`${leads.length} de ${_histLeads.length} lead${_histLeads.length!==1?'s':''}`:'';
  if(!_histLeads.length){
    body.innerHTML=`<div class="muted-box">Nenhum lead ainda.<br/><a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Fazer minha primeira busca</a></div>`;
    return;
  }
  if(!leads.length){body.innerHTML='<div class="muted-box">Nenhum lead corresponde ao filtro.</div>';return;}

  const th=(label,key)=>{
    if(!key)return `<th>${label}</th>`;
    const on=_histSort.key===key;
    const arrow=on?(_histSort.dir>0?' ↑':' ↓'):'';
    return `<th><button class="th-sort${on?' on':''}" onclick="sortHistory('${key}')">${label}${arrow}</button></th>`;
  };
  const rows=leads.map(l=>{
    const date=new Date(l.created_at).toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit',year:'2-digit'});
    const e=l.employee_count;
    const emp=e?(typeof e==='object'?(e.exact?e.exact.toLocaleString('pt-BR'):(e.band||e.raw||'—')):e):'—';
    const score=l.score!=null?`<span class="kb-prio prio-${l.priority||'baixa'}" title="prioridade ${l.priority||''}">${l.score}</span>`:'—';
    return `<tr id="row-${l.id}">
      <td class="td-co"><button class="td-name" onclick="loadLeadIntoView(${l.id})">${esc(l.company_name||l.domain||'—')}</button></td>
      <td class="td-mono">${esc(l.domain||'—')}</td>
      <td>${score}</td>
      <td><span class="stage-chip">${STAGE_LABELS[l.stage||'novo']||esc(l.stage||'')}</span></td>
      <td class="td-mono">${esc(String(emp))}</td>
      <td class="td-mono td-date">${date}</td>
      <td class="td-actions">
        <button class="hist-btn primary" onclick="loadLeadIntoView(${l.id})">Ver</button>
        <button class="hist-btn" onclick="exportLead(${l.id},'csv')" title="Exportar CSV">CSV</button>
        <button class="hist-btn danger" onclick="deleteLead(${l.id})" title="Remover">✕</button>
      </td>
    </tr>`;
  }).join('');
  body.innerHTML=`<div class="tbl-scroll"><table class="lead-tbl">
    <thead><tr>
      ${th('Empresa','company')}${th('Domínio','domain')}${th('Score','score')}${th('Estágio','stage')}${th('Funcionários','employees')}${th('Data','created_at')}${th('Ações',null)}
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

async function deleteLead(leadId){
  if(!confirm('Remover este lead do histórico?'))return;
  try{
    const resp=await authFetch(`/api/leads/${leadId}`,{method:'DELETE'});
    if(resp.ok||resp.status===204){
      _histLeads=_histLeads.filter(l=>l.id!==leadId);
      renderHistory(document.getElementById('history-filter').value);
    }else alert('Erro ao remover lead.');
  }catch(e){
    if(e.message!=='not_authenticated')alert('Erro de conexão.');
  }
}

async function exportLead(leadId,fmt){
  const token=await getToken();if(!token)return;
  const resp=await fetch(`/api/export/${leadId}?format=${fmt}`,{headers:{Authorization:`Bearer ${token}`}});
  if(!resp.ok){alert('Erro ao exportar.');return;}
  const blob=await resp.blob();
  const url=URL.createObjectURL(blob);
  const cd=resp.headers.get('content-disposition')||'';
  const match=cd.match(/filename="([^"]+)"/);
  const a=document.createElement('a');
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

/* ══════ VIEW: CONFIGURAÇÕES ══════ */
async function loadSettings(){
  const body=document.getElementById('settings-body');
  body.innerHTML='<div class="panel"><div class="muted-box">Carregando…</div></div>';
  try{
    const[meR,connR]=await Promise.all([
      authFetch('/api/me'),
      authFetch('/api/crm/connections'),
    ]);
    const me=meR.ok?await meR.json():_profile;
    const conns=connR.ok?await connR.json():[];
    if(me)_profile=me;
    updateNavUser();updateQuotaUI();
    renderSettings(me,conns);
  }catch(e){
    if(e.message!=='not_authenticated')body.innerHTML='<div class="panel"><div class="muted-box">Erro de conexão.</div></div>';
  }
}

function renderSettings(me,conns){
  const body=document.getElementById('settings-body');
  const demo=isDemoSession();
  const plan=me?.plan||'free';
  const unlimited=plan==='enterprise'||(me?.searches_limit??0)<0;
  const used=me?.searches_used??0,limit=me?.searches_limit??0;
  const pctUsed=unlimited?0:Math.min(100,Math.round((used/Math.max(1,limit))*100));
  const reset=me?.quota_reset_at?new Date(me.quota_reset_at).toLocaleDateString('pt-BR'):null;

  const planActions=demo
    ?`<p class="set-desc" style="margin:14px 0 0">Você está em <strong>modo demonstração</strong> — os dados desta sessão ficam restritos a este navegador. Crie uma conta para manter seus leads.</p>
      <div class="set-actions"><button class="set-btn primary" onclick="signOut().then(()=>openAuthModal())">Criar conta gratuita</button></div>`
    :plan==='free'
    ?`<div class="set-actions"><button class="set-btn primary" onclick="startCheckout('pro')">Fazer upgrade — Pro R$ 97/mês</button></div>`
    :`<div class="set-actions"><button class="set-btn ghost" onclick="openBillingPortal()">Gerenciar assinatura</button></div>`;

  const webhook=(conns||[]).find(c=>c.provider==='webhook');
  const connCard=webhook
    ?`<div class="crm-conn">
        <div class="crm-conn-info">
          <span class="crm-conn-name">Webhook</span>
          <span class="crm-conn-meta">${webhook.webhook_configured?'URL configurada':'sem URL'}${webhook.updated_at?' · atualizado em '+new Date(webhook.updated_at).toLocaleDateString('pt-BR'):''}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="crm-conn-state ${webhook.is_active?'on':'off'}">${webhook.is_active?'ativo':'inativo'}</span>
          <button class="set-btn ghost" onclick="toggleCrmConn('webhook')">${webhook.is_active?'Desativar':'Ativar'}</button>
          <button class="set-btn danger" onclick="deleteCrmConn('webhook')">Remover</button>
        </div>
      </div>`
    :'';

  body.innerHTML=`
    <div class="panel panel-pad">
      <div class="set-title">Conta</div>
      <div class="set-row"><span class="set-lbl">Email</span><span class="set-val">${esc(me?.email||(demo?'sessão demo':'—'))}</span></div>
      <div class="set-row"><span class="set-lbl">Plano</span><span class="set-val"><span class="plan-badge">${esc(plan)}</span></span></div>
      <div class="set-row" style="display:block">
        <span class="set-lbl">Uso do ciclo</span>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:6px">
          <span class="set-val" style="text-align:left">${unlimited?'Análises ilimitadas':`${used} de ${limit} análises`}</span>
          ${reset&&!unlimited?`<span class="set-lbl">renova em ${reset}</span>`:''}
        </div>
        ${unlimited?'':`<div class="usage-track"><div class="usage-fill${pctUsed>=100?' full':''}" style="width:${pctUsed}%"></div></div>`}
      </div>
      ${planActions}
    </div>

    <div class="panel panel-pad">
      <div class="set-title">Integração CRM — webhook</div>
      <p class="set-desc">
        Enviamos cada lead (com decisores e atividades) via <strong>POST assinado com HMAC-SHA256</strong>
        para o endpoint que você configurar — funciona com Zapier, Make, Power Automate ou seu próprio sistema.
        O botão "Enviar ao CRM" aparece no card do lead quando há um webhook ativo.
      </p>
      ${connCard}
      <div class="set-form">
        <input id="crm-url" class="set-input" placeholder="https://hooks.zapier.com/…" autocomplete="off" spellcheck="false"/>
        <input id="crm-secret" class="set-input" placeholder="Segredo HMAC (opcional, recomendado)" autocomplete="off" spellcheck="false"/>
        <div class="set-actions">
          <button class="set-btn primary" onclick="saveCrmWebhook()">${webhook?'Atualizar webhook':'Salvar webhook'}</button>
        </div>
        <p id="crm-feedback" class="set-feedback"></p>
      </div>
    </div>

    <div class="panel panel-pad">
      <div class="set-title">Sessão</div>
      <p class="set-desc">Encerra sua sessão neste navegador.</p>
      <div class="set-actions"><button class="set-btn danger" onclick="signOut()">Sair da conta</button></div>
    </div>`;
}

function _crmFeedback(msg,ok){
  const el=document.getElementById('crm-feedback');
  if(!el)return;
  el.textContent=msg;
  el.className='set-feedback '+(ok?'ok':'err');
}

async function saveCrmWebhook(){
  const url=(document.getElementById('crm-url')?.value||'').trim();
  const secret=(document.getElementById('crm-secret')?.value||'').trim();
  if(!url||!/^https?:\/\//.test(url)){_crmFeedback('Informe uma URL válida (https://…).',false);return;}
  try{
    const resp=await authFetch('/api/crm/connections',{method:'POST',body:JSON.stringify({provider:'webhook',webhook_url:url,webhook_secret:secret||null})});
    const json=await resp.json();
    if(!resp.ok){_crmFeedback(json.detail||'Erro ao salvar.',false);return;}
    _crmFeedback('Webhook salvo e ativado.',true);
    loadIntegrations();
    loadSettings();
  }catch(e){if(e.message!=='not_authenticated')_crmFeedback('Erro de conexão.',false);}
}

async function toggleCrmConn(provider){
  try{
    await authFetch(`/api/crm/connections/${provider}/toggle`,{method:'PATCH'});
    loadIntegrations();loadSettings();
  }catch(_){}
}

async function deleteCrmConn(provider){
  if(!confirm('Remover esta conexão CRM?'))return;
  try{
    await authFetch(`/api/crm/connections/${provider}`,{method:'DELETE'});
    loadIntegrations();loadSettings();
  }catch(_){}
}

async function openBillingPortal(){
  try{
    const resp=await authFetch('/api/billing/portal',{method:'POST'});
    const json=await resp.json();
    if(resp.ok&&json.url)window.location.href=json.url;
    else alert(json.detail||'Portal de assinatura indisponível no momento.');
  }catch(e){if(e.message!=='not_authenticated')alert('Erro de conexão.');}
}

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
function fillExample(v){const el=document.getElementById('domain-input');el.value=v;el.focus();}
function esc(s){if(s==null)return'';return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

/* ══════ ATALHOS DE TECLADO ══════ */
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='k'){
    e.preventDefault();
    // troca a view de forma síncrona para o foco funcionar (hashchange é assíncrono)
    if(location.hash)history.replaceState(null,'',location.pathname);
    showView('search');
    document.getElementById('domain-input')?.focus();
  }
  if(e.key==='Escape'){
    document.querySelectorAll('.modal-overlay.open').forEach(m=>m.classList.remove('open'));
  }
});

/* ══════ INIT ══════ */
document.addEventListener('DOMContentLoaded',async()=>{
  document.getElementById('domain-input').addEventListener('keydown',e=>{if(e.key==='Enter')enrich();});

  // domínio vindo da landing (/app?domain=…) — pré-preenche e foca
  const _qDomain=new URLSearchParams(window.location.search).get('domain');
  if(_qDomain){
    const inp=document.getElementById('domain-input');
    inp.value=_qDomain;inp.focus();
    window.history.replaceState({},'',window.location.pathname+window.location.hash);
  }

  // sessão existente (Supabase ou demo)
  const{data:{session}}=await _sb.auth.getSession();
  if(session||isDemoSession()){
    await loadProfile();
    loadIntegrations();
    applyRoute();
    if(!location.hash&&!_qDomain)focusSearch();
  }else{
    updateNavUser();
    applyRoute();   // rota protegida sem sessão → volta pra busca e abre login
    if(!_qDomain)focusSearch();
  }

  // mudanças de auth (callback do magic link / OAuth)
  _sb.auth.onAuthStateChange(async(event,session)=>{
    if(session){
      closeAuthModal();
      const wasLoggedIn=!!_profile;
      await loadProfile();
      loadIntegrations();
      if(!wasLoggedIn){
        if(_pendingRoute){const r=_pendingRoute;_pendingRoute=null;location.hash=r;}
        else{applyRoute();focusSearch();}
      }
    }else if(!isDemoSession()){
      _profile=null;
      updateNavUser();
    }
  });

  // retorno do Stripe
  if(new URLSearchParams(window.location.search).get('upgraded')==='1'){
    await loadProfile();
    window.history.replaceState({},'',window.location.pathname+window.location.hash);
  }
});
