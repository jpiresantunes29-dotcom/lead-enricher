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
let currentLeadData=null;   // ficha aberta — usada pela prévia do relatório DNS
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
    if(resp.ok){
      _profile=await resp.json();
      updateQuotaUI();updateNavUser();loadTodayFollowupsCount();loadRecent();
    }
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
    el.innerHTML='Restam <strong></strong> análises neste ciclo. Reabrir uma empresa dos últimos 7 dias não consome cota.';
    el.querySelector('strong').textContent=`${rem} de ${searches_limit}`;
  }
  else el.innerHTML=`<strong>Sua cota deste ciclo acabou.</strong> <button onclick="startCheckout('pro')">Ver o plano Pro</button>`;
}

/* Rodapé da sidebar: quem está logado, em que plano e quanto da cota já foi. */
function updateNavUser(){
  const preauth=document.getElementById('header-preauth');
  const postauth=document.getElementById('header-postauth');
  if(!preauth)return;
  const demo=isDemoSession();
  document.body.classList.toggle('is-demo',demo&&!!_profile);
  if(!_profile){
    preauth.style.display='flex';
    if(postauth)postauth.style.display='none';
    return;
  }
  preauth.style.display='none';
  if(postauth)postauth.style.display='flex';

  const{searches_used=0,searches_limit=0,plan='free',email,quota_reset_at}=_profile;
  const unlimited=plan==='enterprise'||searches_limit<0;
  const mail=email||(demo?'Sessão de demonstração':'—');
  const set=(id,txt)=>{const el=document.getElementById(id);if(el)el.textContent=txt;};
  set('sb-avatar',(mail[0]||'•').toUpperCase());
  set('sb-user-mail',mail);
  set('sb-user-plan',`Plano ${plan}`);
  set('nav-quota',unlimited
    ? 'Análises ilimitadas'
    : `${searches_used} de ${searches_limit} análises`);
  const reset=quota_reset_at?new Date(quota_reset_at):null;
  set('sb-quota-reset',reset&&!unlimited
    ? 'renova '+reset.toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'})
    : '');
  const fill=document.getElementById('sb-quota-fill');
  if(fill){
    const pct=unlimited?0:Math.min(100,Math.round((searches_used/Math.max(1,searches_limit))*100));
    fill.style.width=pct+'%';
    fill.classList.toggle('full',pct>=100);
  }
  const quotaBox=document.getElementById('sb-quota');
  if(quotaBox)quotaBox.style.display=unlimited?'none':'block';
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
  const rg=document.getElementById('recent-grid');if(rg)rg.innerHTML='';
  const rb=document.getElementById('recent-block');if(rb)rb.style.display='none';
  updateNavUser();
  if(location.hash)history.replaceState(null,'',location.pathname);
  showView('search');
}

async function startCheckout(plan){
  const token=await getToken();
  if(!token){openAuthModal();return;}
  // Sessão demo mora no localStorage deste navegador: assinar por ela
  // significaria pagar por uma conta que some ao limpar o histórico.
  if(isDemoSession()){closePaywall();openAuthModal();return;}
  try{
    const resp=await authFetch('/api/billing/checkout',{method:'POST',body:JSON.stringify({plan})});
    const json=await resp.json();
    if(resp.ok&&json.url){window.location.href=json.url;return;}
    alert(json.detail||'Não foi possível iniciar a assinatura agora.');
  }catch(e){if(e.message!=='not_authenticated')alert('Erro ao iniciar checkout.');}
}

function openPaywall(){
  // Em demo o caminho não é o cartão: é criar a conta para não perder o que
  // já foi pesquisado. O texto do modal muda junto com a ação.
  const demo=isDemoSession();
  const cta=document.getElementById('paywall-cta');
  const text=document.getElementById('paywall-text');
  if(cta){
    cta.textContent=demo?'Criar conta gratuita →':'Assinar Pro — R$ 97/mês →';
    cta.onclick=demo?function(){closePaywall();openAuthModal();}
                    :function(){closePaywall();startCheckout('pro');};
  }
  if(text&&demo)text.textContent='Você está na demonstração — os dados ficam só neste navegador. Crie uma conta gratuita para guardar seus leads e liberar uma nova cota.';
  document.getElementById('paywall-modal').classList.add('open');
}
function closePaywall(){document.getElementById('paywall-modal').classList.remove('open');}
function closeIfBackdrop(e,id){if(e.target===document.getElementById(id))document.getElementById(id).classList.remove('open');}

/* ══════ ROUTER (views por hash) ══════ */
const ROUTES=['','lote','import','sheet','dashboard','pipeline','followups','history','settings'];

function nav(route){
  if(route&&!_profile){_pendingRoute=route;openAuthModal();return;}
  if(location.hash.slice(1)===route)applyRoute();   // re-clique recarrega a view
  else location.hash=route;
}

/* Cada view se apresenta na topbar: o que é a tela e para que serve. */
const VIEW_META={
  search:{
    title:'Nova análise',
    sub:'Digite o domínio da empresa e receba a ficha completa em segundos.',
  },
  lote:{
    title:'Análise em lote',
    sub:'Cole uma lista de domínios ou solte um CSV. Processamos em fila, sem você ficar esperando cada empresa.',
  },
  import:{
    title:'Importar planilha',
    sub:'Suba o .xlsx ou .csv que você já usa. Reconhecemos as colunas e criamos as linhas como leads.',
  },
  sheet:{
    title:'Planilha',
    sub:'Suas linhas como no arquivo original, mais as colunas que descobrimos. Clique numa célula para editar.',
  },
  pipeline:{
    title:'Pipeline de prospecção',
    sub:'Em que estágio está cada lead. Arraste o card ou use os botões para avançar.',
  },
  followups:{
    title:'Follow-ups',
    sub:'Tarefas criadas a partir das suas ligações. Comece pelas atrasadas.',
  },
  dashboard:{
    title:'Dashboard comercial',
    sub:'Esforço e conversão do período: quanto ligou, quanto virou contato e quanto virou reunião.',
  },
  history:{
    title:'Histórico de leads',
    sub:'Todas as empresas já analisadas. Filtre, reabra a ficha ou exporte para Excel.',
  },
  settings:{
    title:'Configurações',
    sub:'Plano e uso do ciclo, integração com CRM, extensão do navegador e sessão.',
  },
};

function showView(v){
  document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active',s.id==='view-'+v));
  const route=v==='search'?'':v;
  document.querySelectorAll('[data-route]').forEach(b=>b.classList.toggle('active',b.dataset.route===route));
  const meta=VIEW_META[v]||VIEW_META.search;
  const t=document.getElementById('tb-title'),s=document.getElementById('tb-sub');
  if(t)t.textContent=meta.title;
  if(s)s.textContent=meta.sub;
  // Só as ações da tela atual ficam visíveis — nada de botão sem contexto.
  document.querySelectorAll('.tb-slot').forEach(el=>el.classList.toggle('active',el.dataset.slot===v));
  document.getElementById('tb-new-btn')?.classList.toggle('hidden',v==='search');
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
  if(h==='lote')loadLote();
  else if(h==='import')loadImport();
  // A grade vive em sheet.js: se o arquivo não carregou, a view não trava a navegação.
  else if(h==='sheet'&&typeof loadSheet==='function')loadSheet();
  else if(h==='dashboard')loadDashboard();
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

/* Fecha a ficha aberta e devolve a tela de busca ao estado inicial. */
function clearResult(){
  currentLeadId=null;currentLeadData=null;
  hideResults();hideError();
  if(location.hash.startsWith('#lead-'))history.replaceState(null,'',location.pathname);
  const input=document.getElementById('domain-input');
  if(input)input.value='';
  focusSearch();
  window.scrollTo({top:0,behavior:'smooth'});
}

/* Exemplo clicável: preenche e já analisa — o usuário vê o resultado antes de
   ter que pensar num domínio. */
function useExample(domain){
  const input=document.getElementById('domain-input');
  if(!input)return;
  input.value=domain;
  enrich();
}

/* ══════ ÚLTIMAS ANÁLISES (preenche a tela antes da primeira busca) ══════ */
async function loadRecent(){
  const block=document.getElementById('recent-block');
  const grid=document.getElementById('recent-grid');
  if(!block||!grid||!_profile)return;
  try{
    const resp=await authFetch('/api/leads?per_page=6');
    if(!resp.ok)return;
    const leads=await resp.json();
    if(!leads.length){block.style.display='none';return;}
    grid.innerHTML=leads.slice(0,6).map(l=>{
      const name=l.company_name||l.domain||'—';
      const when=l.created_at?new Date(l.created_at).toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'}):'';
      const stage=STAGE_LABELS[l.stage||'novo']||'';
      return `<button class="recent-card" onclick="loadLeadIntoView(${l.id})" title="Abrir a ficha de ${esc(name)}">
        <span class="recent-ava">${esc((name.trim()[0]||'?').toUpperCase())}</span>
        <span class="recent-txt">
          <span class="recent-name">${esc(name)}</span>
          <span class="recent-meta">${esc(stage)}${when?' · '+when:''}</span>
        </span>
      </button>`;
    }).join('');
    // Só aparece quando não há uma ficha aberta ocupando a tela
    const hasResult=document.getElementById('view-search')?.classList.contains('has-result');
    block.style.display=hasResult?'none':'block';
  }catch(_){}
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
  const summary=document.getElementById('fu-summary');
  body.innerHTML='<div class="panel"><div class="muted-box">Carregando…</div></div>';
  try{
    const resp=await authFetch('/api/activities/pending');
    const list=await resp.json();
    if(!resp.ok){body.innerHTML='<div class="panel"><div class="muted-box">Erro ao carregar.</div></div>';return;}
    if(!list.length){
      if(summary)summary.textContent='';
      body.innerHTML=`<div class="panel"><div class="muted-box">
        Tudo em dia — nenhum follow-up pendente.<br/>
        Quando você registrar uma ligação sem resposta, a tarefa de retorno aparece aqui.
        <a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Analisar um domínio</a>
      </div></div>`;
      return;
    }
    const now=Date.now();
    const endOfDay=new Date();endOfDay.setHours(23,59,59,999);
    let late=0,today=0;
    const rows=list.map(a=>{
      const due=a.due_at?new Date(a.due_at):null;
      const isLate=due&&due.getTime()<now;
      const isToday=due&&!isLate&&due.getTime()<=endOfDay.getTime();
      if(isLate)late++;else if(isToday)today++;
      const when=due?due.toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'sem data';
      const kind=a.type==='meeting'?'Reunião':'Retornar contato';
      const ics=a.type==='meeting'
        ? `<button class="fu-btn" onclick="downloadIcs(${a.id})" title="Baixar convite de calendário (.ics)">Convite .ics</button>`
        : '';
      return `<div class="fu-row${isLate?' late':''}">
        <div class="fu-info">
          <span class="fu-kind">${kind}</span>
          <span class="fu-notes">${esc(a.notes||'')}</span>
          <span class="fu-when${isLate?' late':''}">${isLate?'atrasado · ':(isToday?'hoje · ':'')}${when}</span>
        </div>
        <div class="fu-actions">
          <button class="fu-btn" onclick="loadLeadIntoView(${a.lead_id})" title="Abrir a ficha da empresa">Abrir lead</button>
          ${ics}
          <button class="fu-btn done" onclick="completeActivity(${a.id})" title="Marcar como resolvido e tirar da fila">Concluir</button>
        </div>
      </div>`;
    }).join('');
    const tags=[
      late?`<span class="fu-tag late">${late} atrasado${late>1?'s':''}</span>`:'',
      today?`<span class="fu-tag today">${today} para hoje</span>`:'',
      `<span class="fu-tag">${list.length} no total</span>`,
    ].filter(Boolean).join('');
    if(summary)summary.textContent=late?`${late} tarefa(s) em atraso`:'Fila em dia';
    body.innerHTML=`<div class="panel">
      <div class="fu-head">${tags}<span>Concluir tira a tarefa da fila; abrir o lead leva à ficha completa.</span></div>
      ${rows}
    </div>`;
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div class="panel"><div class="muted-box">Erro de conexão.</div></div>';}
}

async function completeActivity(id){
  try{
    await authFetch(`/api/activities/${id}`,{method:'PATCH',body:JSON.stringify({completed:true})});
    loadFollowups();loadTodayFollowupsCount();
  }catch(_){}
}

/* ══════ VIEW: ANÁLISE EM LOTE ══════
   O lote não roda sozinho no servidor: cada chamada de /run processa uma
   rodada curta e devolve quantos faltam. Quem pede a próxima rodada é esta
   tela — assim nenhuma requisição chega perto do limite de tempo da função, e
   o progresso aparece de verdade em vez de um spinner de dois minutos. */
let _loteId=null;      // lote em andamento
let _loteRodando=false;

function loadLote(){
  atualizarContagemLote();
  carregarLotesRecentes();
}

/* Conta quantos domínios plausíveis há no texto — feedback antes de enviar. */
function contarDominios(texto){
  const vistos=new Set();
  (texto||'').split(/[\n,;\t|]+/).forEach(p=>{
    const t=(p||'').trim().replace(/^https?:\/\//,'').replace(/^www\./,'').split('/')[0].toLowerCase();
    if(t.includes('.')&&!t.includes(' '))vistos.add(t.includes('@')?t.split('@')[1]:t);
  });
  return vistos.size;
}

function atualizarContagemLote(){
  const el=document.getElementById('lote-count');
  const input=document.getElementById('lote-input');
  if(!el||!input)return;
  const n=contarDominios(input.value);
  el.textContent=n?`${n} domínio(s) reconhecido(s)`:'';
}

async function iniciarLote(){
  const input=document.getElementById('lote-input');
  const fb=document.getElementById('lote-feedback');
  const texto=(input.value||'').trim();
  if(!texto){fb.className='set-feedback err';fb.textContent='Cole a lista ou escolha um arquivo.';return;}

  const btn=document.getElementById('lote-start');
  btn.disabled=true;fb.className='set-feedback';fb.textContent='Enfileirando…';
  try{
    const resp=await authFetch('/api/batches',{method:'POST',body:JSON.stringify({text:texto})});
    const json=await resp.json();
    if(!resp.ok){fb.className='set-feedback err';fb.textContent=json.detail||'Não foi possível criar o lote.';return;}

    _loteId=json.batch_id;
    fb.className='set-feedback ok';
    fb.textContent=json.message+(json.cabe_na_quota?'':` Atenção: sua cota cobre ${json.quota_restante} análise(s) neste ciclo; o restante fica na fila até a renovação.`);
    document.getElementById('lote-progress-panel').style.display='block';
    renderProgressoLote(json.progresso||{total:json.total,concluidos:0,na_fila:json.total,rodando:0,com_erro:0,finalizado:false,itens:[]});
    processarLote();
  }catch(e){
    if(e.message!=='not_authenticated'){fb.className='set-feedback err';fb.textContent='Erro de conexão.';}
  }finally{btn.disabled=false;}
}

/* Pede rodadas em sequência até a fila esvaziar (ou o usuário pausar). */
async function processarLote(){
  if(!_loteId||_loteRodando)return;
  _loteRodando=true;
  document.getElementById('lote-stop').textContent='Pausar';
  try{
    while(_loteRodando&&_loteId){
      const resp=await authFetch(`/api/batches/${_loteId}/run`,{method:'POST'});
      if(!resp.ok)break;
      const json=await resp.json();
      renderProgressoLote(json.progresso);
      loadProfile();                       // a cota muda a cada rodada
      if(json.quota_reached){
        const fb=document.getElementById('lote-feedback');
        fb.className='set-feedback err';
        fb.textContent='Sua cota deste ciclo acabou. Os domínios restantes continuam na fila e retomam quando a cota renovar.';
        break;
      }
      if(json.progresso.finalizado||json.remaining===0)break;
    }
  }catch(_){ }
  finally{
    _loteRodando=false;
    document.getElementById('lote-stop').textContent='Retomar';
    carregarLotesRecentes();
  }
}

function pararLote(){
  if(_loteRodando){_loteRodando=false;return;}
  processarLote();
}

const LOTE_RESULT_LABEL={
  enriched:['Enriquecido','ok'],partial:['Parcial','warn'],cached:['Já tínhamos','ok'],
  failed:['Sem dados','warn'],error:['Erro','err'],quota:['Aguardando cota','warn'],
};

function renderProgressoLote(p){
  if(!p)return;
  const pct=p.total?Math.round((p.concluidos/p.total)*100):0;
  document.getElementById('lote-bar').style.width=pct+'%';
  document.getElementById('lote-progress-title').textContent=
    p.finalizado?'Lote concluído':`Processando — ${p.concluidos} de ${p.total}`;
  document.getElementById('lote-progress-sub').textContent=
    `${p.na_fila} na fila · ${p.com_erro} com erro`+(p.finalizado?' · nada mais pendente':'');

  const itens=document.getElementById('lote-items');
  itens.innerHTML=(p.itens||[]).map(item=>{
    const [rotulo,classe]=LOTE_RESULT_LABEL[item.result]||
      (item.status==='running'?['Analisando…','']:['Na fila','']);
    const link=item.lead_id?`<button class="btn-link" onclick="loadLeadIntoView(${item.lead_id})">ver ficha →</button>`:'';
    return `<div class="lote-item">
      <span class="lote-item-dom">${esc(item.domain||'')}</span>
      <span class="lote-item-st ${classe}">${rotulo}</span>
      ${link}
    </div>`;
  }).join('');
}

async function carregarLotesRecentes(){
  const painel=document.getElementById('lote-recent-panel');
  const alvo=document.getElementById('lote-recent');
  if(!alvo)return;
  try{
    const resp=await authFetch('/api/batches');
    if(!resp.ok)return;
    const lotes=await resp.json();
    if(!lotes.length){painel.style.display='none';return;}
    painel.style.display='block';
    alvo.innerHTML=lotes.map(l=>{
      const retomar=l.finalizado?'':`<button class="btn-link" onclick="retomarLote('${l.batch_id}')">retomar →</button>`;
      return `<div class="set-row">
        <span class="set-lbl">${l.total} domínio(s) · ${l.concluidos} concluído(s)${l.com_erro?` · ${l.com_erro} com erro`:''}</span>
        <span class="set-val">${l.finalizado?'finalizado':`${l.na_fila} na fila`} ${retomar}</span>
      </div>`;
    }).join('');
  }catch(_){ }
}

async function retomarLote(batchId){
  _loteId=batchId;
  document.getElementById('lote-progress-panel').style.display='block';
  try{
    const resp=await authFetch(`/api/batches/${batchId}`);
    if(resp.ok)renderProgressoLote(await resp.json());
  }catch(_){ }
  processarLote();
}

/* ══════ VIEW: DASHBOARD ══════ */
const STAGE_LABELS={novo:'Novo',contatado:'Contatado',reuniao_agendada:'Reunião agendada',oportunidade:'Oportunidade',ganho:'Ganho',perdido:'Perdido'};
const STAGE_ORDER=['novo','contatado','reuniao_agendada','oportunidade','ganho','perdido'];

let _dashDays=30;

function setDashPeriod(days){
  _dashDays=days;
  document.querySelectorAll('.seg-btn[data-days]').forEach(b=>b.classList.toggle('active',+b.dataset.days===days));
  loadDashboard();
}

async function loadDashboard(){
  const body=document.getElementById('dashboard-body');
  body.innerHTML='<div class="panel"><div class="muted-box">Carregando…</div></div>';
  try{
    const resp=await authFetch(`/api/dashboard/metrics?days=${_dashDays}`);
    const m=await resp.json();
    if(!resp.ok){body.innerHTML='<div class="panel"><div class="muted-box">Erro ao carregar.</div></div>';return;}
    document.getElementById('dash-period').textContent=
      `Números dos últimos ${m.period_days} dias. O funil considera todos os leads da conta.`;
    if(!m.leads_pesquisados){
      body.innerHTML=`<div class="panel"><div class="muted-box">
        Ainda não há dados neste período.<br/>
        Analise uma empresa e registre a ligação: as taxas aparecem aqui.<br/>
        <a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Analisar meu primeiro domínio</a>
      </div></div>`;
      return;
    }
    const pct=v=>Math.round(v*100)+'%';
    // Cada número explica o que mede — o vendedor não precisa adivinhar a conta.
    const kpi=(val,lbl,desc,warn)=>`<div class="kpi${warn?' warn':''}">
      <span class="kpi-val">${val}</span>
      <span class="kpi-lbl">${lbl}</span>
      <span class="kpi-desc">${desc}</span>
    </div>`;
    const funilMax=Math.max(1,...STAGE_ORDER.map(s=>m.funil_por_estagio[s]||0));
    const funil=STAGE_ORDER.map(s=>{
      const v=m.funil_por_estagio[s]||0;
      return `<div class="fn-row"><span class="fn-lbl">${STAGE_LABELS[s]}</span><div class="fn-track"><div class="fn-bar" style="width:${Math.max(2,(v/funilMax)*100)}%"></div></div><span class="fn-val">${v}</span></div>`;
    }).join('');

    const dicas=[];
    if(m.followups_atrasados)dicas.push(`<b>${m.followups_atrasados} follow-up(s) atrasado(s).</b> Comece por eles — <a class="btn-link" href="#followups">abrir a fila</a>.`);
    if(!m.ligacoes_realizadas)dicas.push('Nenhuma ligação registrada no período. Registre o resultado na ficha do lead para as taxas passarem a fazer sentido.');
    if(m.ligacoes_realizadas&&m.taxa_contato<0.2)dicas.push('Taxa de contato abaixo de 20%: vale testar outro horário de ligação ou buscar um cargo diferente na empresa.');
    if((m.funil_por_estagio.novo||0)>5)dicas.push(`<b>${m.funil_por_estagio.novo} leads parados em "Novo".</b> Eles ainda não receberam nenhuma tentativa de contato.`);
    if(!dicas.length)dicas.push('Nada travado por aqui: follow-ups em dia e leads circulando no funil.');

    body.innerHTML=`
      <div class="kpi-grid">
        ${kpi(m.leads_pesquisados,'Leads pesquisados','Empresas analisadas no período')}
        ${kpi(m.ligacoes_realizadas,'Ligações registradas','Tentativas anotadas na ficha do lead')}
        ${kpi(pct(m.taxa_contato),'Taxa de contato','Ligações em que você falou com alguém')}
        ${kpi(pct(m.taxa_reuniao),'Taxa de reunião','Ligações que terminaram em reunião marcada')}
        ${kpi(pct(m.conversao_oportunidade),'Conversão em oportunidade','Leads que chegaram a oportunidade ou ganho')}
        ${kpi(m.followups_pendentes+(m.followups_atrasados?` <small>(${m.followups_atrasados} atrasados)</small>`:''),'Follow-ups pendentes','Tarefas em aberto na sua fila',m.followups_atrasados>0)}
      </div>
      <div class="dash-cols">
        <div class="panel panel-pad">
          <div class="dash-sec-title">Funil por estágio</div>
          <div class="dash-sec-sub">Quantos leads estão parados em cada etapa da negociação.</div>
          <div class="funnel">${funil}</div>
        </div>
        <div class="panel panel-pad">
          <div class="dash-sec-title">O que fazer agora</div>
          <div class="dash-sec-sub">Leitura automática dos números acima.</div>
          <div class="next-list">
            ${dicas.map(d=>`<div class="next-item"><span class="next-dot"></span><span>${d}</span></div>`).join('')}
          </div>
        </div>
      </div>`;
  }catch(e){if(e.message!=='not_authenticated')body.innerHTML='<div class="panel"><div class="muted-box">Erro de conexão.</div></div>';}
}

/* ══════ VIEW: PIPELINE (KANBAN) ══════ */
/* O que cada coluna significa — o vendedor não deveria ter que deduzir. */
const STAGE_DESC={
  novo:'Analisado, ainda sem contato',
  contatado:'Já houve tentativa de contato',
  reuniao_agendada:'Reunião marcada com data',
  oportunidade:'Proposta ou negociação em andamento',
  ganho:'Fechou negócio',
  perdido:'Sem interesse ou fora do perfil',
};

async function loadPipeline(){
  const body=document.getElementById('pipeline-body');
  body.innerHTML='<div class="panel"><div class="muted-box">Carregando…</div></div>';
  try{
    const resp=await authFetch('/api/leads?per_page=100');
    const leads=await resp.json();
    if(!resp.ok){body.innerHTML='<div class="panel"><div class="muted-box">Erro ao carregar.</div></div>';return;}
    if(!leads.length){
      body.innerHTML=`<div class="panel"><div class="muted-box">
        Nenhum lead no pipeline ainda.<br/>
        Toda empresa analisada entra automaticamente na coluna "Novo".
        <a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Analisar um domínio</a>
      </div></div>`;
      return;
    }
    const byStage={};STAGE_ORDER.forEach(s=>byStage[s]=[]);
    leads.forEach(l=>{(byStage[l.stage||'novo']||byStage.novo).push(l)});
    body.innerHTML=`<div class="kanban-wrap"><div class="kanban">${STAGE_ORDER.map(stage=>{
      const i=STAGE_ORDER.indexOf(stage);
      const cards=byStage[stage].map(l=>{
        const nome=esc(l.company_name||l.domain||'—');
        const left=i>0
          ? `<button class="kb-move" title="Mover para ${STAGE_LABELS[STAGE_ORDER[i-1]]}" onclick="moveLead(${l.id},'${STAGE_ORDER[i-1]}')">◀ Voltar</button>`
          : '<span class="kb-move ghost">◀ Voltar</span>';
        const right=i<STAGE_ORDER.length-1
          ? `<button class="kb-move" title="Mover para ${STAGE_LABELS[STAGE_ORDER[i+1]]}" onclick="moveLead(${l.id},'${STAGE_ORDER[i+1]}')">Avançar ▶</button>`
          : '<span class="kb-move ghost">Avançar ▶</span>';
        return `<div class="kb-card" draggable="true" data-lead-id="${l.id}" ondragstart="dragStart(event)" title="Arraste para outra coluna para mudar o estágio">
          <div class="kb-card-top"><button class="kb-name" onclick="loadLeadIntoView(${l.id})" title="Abrir a ficha de ${nome}">${nome}</button></div>
          <div class="kb-domain">${esc(l.domain||'')}</div>
          <div class="kb-card-actions">${left}${right}</div>
        </div>`;
      }).join('')||'<div class="kb-empty">Nenhum lead aqui</div>';
      return `<div class="kb-col" data-stage="${stage}" ondrop="dragDropCol(event)" ondragover="dragOverCol(event)" ondragleave="dragLeaveCol(event)">
        <div class="kb-col-hdr">
          <div class="kb-col-name">${STAGE_LABELS[stage]} <span class="kb-count">${byStage[stage].length}</span></div>
          <div class="kb-col-desc">${STAGE_DESC[stage]||''}</div>
        </div>${cards}</div>`;
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
const IC_DOWN='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

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
  // Com uma ficha na tela, o formulário encolhe e o painel de apoio sai
  document.getElementById('view-search')?.classList.add('has-result');
  const recent=document.getElementById('recent-block');
  if(recent)recent.style.display='none';
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
  const _mxCell=primaryMx(data);
  let d=0;
  const cards=[
    cell('Site',ws,{ic:IC.globe,isLink:true,disp:ws,delay:d+=50}),
    `<div class="data-cell" style="animation-delay:${d+=50}ms"><span class="data-lbl">${IC.li}LinkedIn</span>${data.linkedin_url?`<a class="data-val link" href="${data.linkedin_url}" target="_blank" rel="noopener">${esc(li)}</a>${cb(data.linkedin_confidence)}`:`<a class="data-val link" href="https://www.google.com/search?q=${encodeURIComponent('site:linkedin.com/company "'+(data.company_name||data.domain||'')+'"')}" target="_blank" rel="noopener">Buscar no Google →</a>`}</div>`,
    // A ficha mostra o servidor MX como ele é publicado no DNS. O nome
    // comercial do provedor e o resto da infraestrutura ficam no relatório
    // completo, logo abaixo.
    `<div class="data-cell" style="animation-delay:${d+=50}ms"><span class="data-lbl">${IC.mail}Domínio MX</span>${_mxCell?`<span class="data-val mono">${esc(_mxCell.host)}</span>${_mxCell.count>1?`<span class="data-sub">+${_mxCell.count-1} servidor(es) de reserva</span>`:''}`:'<span class="data-val muted">—</span>'}</div>`,
    cell('Funcionários',emp,{ic:IC.users,delay:d+=50}),
    cell('Localização',data.location,{ic:IC.pin,delay:d+=50}),
    cell('Setor',data.sector,{ic:IC.tag,delay:d+=50}),
  ];
  if(data.hosting_provider)cards.push(cell('Hosting',data.hosting_provider,{ic:IC.globe,delay:d+=50}));
  const dns=renderInfra(data);
  root.innerHTML=`<div class="result-card">
    <div class="result-hdr">
      <div class="result-co">
        <div class="result-fav">${fav?`<img src="${fav}" onerror="this.style.display='none'" alt=""/>`:''}${init}</div>
        <div><div class="result-name">${esc(data.company_name||data.domain||'Empresa')}</div><div class="result-domain">${esc(data.domain||'')}</div></div>
      </div>
      <div class="result-hdr-right"><span class="status-pill ${sc}">${sl}</span></div>
    </div>
    <div class="lead-actions" id="lead-actions"></div>
    <div class="sec-head"><span class="sec-num">1</span><h4>Ficha da empresa</h4><span>Dados públicos coletados a partir do domínio</span></div>
    <div class="result-grid">${cards.join('')}</div>
    ${dns}
    <div class="dec-section">
      <div class="dec-title"><span class="dec-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 11l-3-3m0 0l-3 3m3-3v12"/></svg></span>Passo 2 · Encontrar decisores</div>
      <div class="dec-sub">Diga o cargo que você quer alcançar. Buscamos perfis públicos nessa empresa e devolvemos LinkedIn e e-mail corporativo provável, com a confiança de cada endereço. Leva até 15 segundos.</div>
      <div class="role-irow">
        <input id="role-input" class="role-inp" placeholder="Ex: Coordenador de TI, CFO, Diretor Comercial..." />
        <button class="role-srch-btn" id="role-btn" onclick="searchDecisores()" title="Buscar pessoas com esse cargo na empresa">
          <span id="role-btn-text">Buscar decisores</span>
          <span id="role-btn-spinner" class="spinner" style="display:none"></span>
        </button>
      </div>
      <div class="role-chips">
        <span class="role-chips-lbl">Cargos comuns:</span>
        <button class="role-chip" onclick="setRole('Coordenador de TI')">Coordenador de TI</button>
        <button class="role-chip" onclick="setRole('Diretor de TI')">Diretor de TI</button>
        <button class="role-chip" onclick="setRole('CTO')">CTO</button>
        <button class="role-chip" onclick="setRole('Gerente Comercial')">Gerente Comercial</button>
        <button class="role-chip" onclick="setRole('CFO')">CFO</button>
      </div>
      <div id="decisores-list" class="dec-list">
        <div class="empty-state-box">
          <div class="empty-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg></div>
          <div class="empty-title">Nenhum cargo buscado ainda</div>
          <div class="empty-sub">Escolha um cargo acima (ou digite o seu) e clique em <strong>Buscar decisores</strong>.</div>
        </div>
      </div>
    </div>
    <div class="call-section">
      <div class="dec-title"><span class="dec-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg></span>Passo 3 · Registrar o resultado da ligação</div>
      <div class="dec-sub">Clique no que aconteceu. <strong>Não atendeu</strong>, <strong>Ocupado</strong> e <strong>Caixa postal</strong> criam sozinhos um follow-up para daqui a 2 dias; <strong>Conversou</strong> move o lead para "Contatado"; <strong>Reunião agendada</strong> pede a data e gera o convite .ics.</div>
      <div class="call-row">
        <button class="call-btn" onclick="logCall('no_answer')" title="Cria um follow-up para daqui a 2 dias">Não atendeu</button>
        <button class="call-btn" onclick="logCall('busy')" title="Cria um follow-up para daqui a 2 dias">Ocupado</button>
        <button class="call-btn" onclick="logCall('voicemail')" title="Cria um follow-up para daqui a 2 dias">Caixa postal</button>
        <button class="call-btn" onclick="logCall('talked')" title="Registra o contato e move o lead para Contatado">Conversou</button>
        <button class="call-btn meet" onclick="toggleMeetRow()" title="Informar a data e gerar o convite de calendário">Reunião agendada</button>
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
  // Barra de ações: quando a integração não está configurada o botão continua
  // visível, em estado apagado, dizendo o que falta — some não ensina nada.
  const integ=_integr||{};
  document.getElementById('lead-actions').innerHTML=[
    '<span class="la-lbl">Ações do lead</span>',
    integ.ai
      ? `<button class="la-btn" onclick="genAiSummary()" title="Gera um resumo executivo desta empresa com IA">${IC_SPARK} Resumo com IA</button>`
      : `<span class="la-btn off" title="Disponível quando a chave de IA está configurada no servidor">${IC_SPARK} Resumo com IA</span>`,
    integ.crm_webhook
      ? `<button class="la-btn accent" onclick="pushToCrm()" title="Envia este lead ao webhook configurado em Configurações">${IC_PUSH} Enviar ao CRM</button>`
      : `<span class="la-btn off" onclick="nav('settings')" title="Configure um webhook em Configurações para habilitar" style="cursor:pointer">${IC_PUSH} Enviar ao CRM · configurar</span>`,
    `<button class="la-btn" onclick="openExportModal()" title="Baixar seus leads em Excel ou CSV">${IC_DOWN} Exportar leads</button>`,
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
  list.innerHTML=`<div class="muted-box">Procurando pessoas com o cargo “${esc(role)}” nesta empresa… (até 15s)</div>`;
  try{
    const resp=await authFetch('/api/decisores',{method:'POST',body:JSON.stringify({lead_id:currentLeadId,roles:[role]})});
    const json=await resp.json();
    if(!resp.ok||!json.success){list.innerHTML=`<div class="muted-box">${esc(json.detail||json.message||'Erro.')}</div>`;return;}
    renderDecisores(json.decisores);
  }catch(e){list.innerHTML='<div class="muted-box">Erro de conexão.</div>';}
  finally{btn.disabled=false;bText.textContent='Buscar decisores';bSpin.style.display='none';}
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

/* ══════ INFRAESTRUTURA DE DNS E E-MAIL ══════
   A ficha comercial mostra só o servidor MX. Aqui embaixo fica o relatório
   técnico completo (estilo DNS Dumpster), fechado por padrão: registros MX,
   NS, TXT, SOA, CAA, SRV, autenticação de e-mail, hosts com ASN/PTR/banner
   HTTP e o registro do domínio. A coleta é sob demanda (~15-25 s) e o
   resultado fica guardado no lead — abrir de novo é instantâneo. */
const VERIF_LABELS={google_verify:'Google',ms_verify:'Microsoft',fb_verify:'Facebook',atlassian_verify:'Atlassian'};
const _dnsFull={};    // leadId → relatório completo já coletado
const _dnsBusy={};    // leadId → coleta em andamento

/* Servidor MX de menor prioridade — é o que a ficha mostra no lugar do nome
   comercial do provedor. */
function primaryMx(data){
  const list=(data.dns_report&&data.dns_report.mx)||data.mx_records||[];
  if(!list.length)return null;
  const sorted=[...list].sort((a,b)=>(a.priority==null?99:a.priority)-(b.priority==null?99:b.priority));
  return {host:sorted[0].host,count:list.length};
}

function _fmtDate(iso){
  if(!iso)return null;
  const d=new Date(iso);
  return isNaN(d)?null:d.toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric'});
}
function _fmtTtl(s){
  if(s==null)return '';
  if(s>=86400)return Math.round(s/86400)+'d';
  if(s>=3600)return Math.round(s/3600)+'h';
  if(s>=60)return Math.round(s/60)+'min';
  return s+'s';
}

/* Casca da seção: cabeçalho clicável com os chips de resumo + corpo vazio. */
function renderInfra(data){
  const e=(s)=>esc(String(s==null?'':s));
  const dns=data.dns_report||null;
  const mx=primaryMx(data);
  if(!data.domain&&!mx)return'';
  const provider=data.mx_provider||(dns&&dns.mx_provider);
  const chips=[
    provider?`<span class="dnsx-chip accent">${e(provider)}</span>`:'',
    data.hosting_provider?`<span class="dnsx-chip">${e(data.hosting_provider)}</span>`:'',
    dns&&dns.spf?'<span class="dnsx-chip ok">SPF</span>':'',
    dns&&dns.dmarc?'<span class="dnsx-chip ok">DMARC</span>':'',
  ].filter(Boolean).join('');
  return `<section class="dnsx" id="dnsx">
    <button type="button" class="dnsx-toggle" id="dnsx-toggle" aria-expanded="false" onclick="toggleDnsPanel()">
      <span class="infra-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="2" y="3" width="20" height="7" rx="2"/><rect x="2" y="14" width="20" height="7" rx="2"/><line x1="6" y1="6.5" x2="6.01" y2="6.5"/><line x1="6" y1="17.5" x2="6.01" y2="17.5"/></svg></span>
      <span class="dnsx-txt">
        <span class="dnsx-title">Relatório DNS completo</span>
        <span class="dnsx-sub">Registros MX, NS, TXT, SPF/DMARC/DKIM, hosts com IP e ASN, e titular de ${e(data.domain||'')}</span>
      </span>
      <span class="dnsx-chips">${chips}</span>
      <span class="dnsx-chev"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="6 9 12 15 18 9"/></svg></span>
    </button>
    <div class="dnsx-body" id="dnsx-body" hidden></div>
  </section>`;
}

async function toggleDnsPanel(){
  const sec=document.getElementById('dnsx');
  const body=document.getElementById('dnsx-body');
  const btn=document.getElementById('dnsx-toggle');
  if(!sec||!body)return;
  const open=!sec.classList.contains('open');
  sec.classList.toggle('open',open);
  btn.setAttribute('aria-expanded',open?'true':'false');
  body.hidden=!open;
  if(!open)return;
  const id=currentLeadId;
  if(_dnsFull[id]){body.innerHTML=renderDnsFull(_dnsFull[id]);return;}
  if(_dnsBusy[id])return;
  await loadDnsReport(false);
}

async function loadDnsReport(refresh){
  const body=document.getElementById('dnsx-body');
  const id=currentLeadId;
  if(!body||!id||_dnsBusy[id])return;
  _dnsBusy[id]=true;
  // O que o enriquecimento já coletou aparece na hora; o relatório completo
  // substitui quando chega — ninguém fica olhando para um spinner vazio.
  body.innerHTML=renderDnsBasic(currentLeadData)+
    `<div class="dnsx-load"><span class="spinner"></span>Consultando DNS, logs de certificado, ASN e RDAP… (até 25s)</div>`;
  try{
    const resp=await authFetch(`/api/leads/${id}/dns${refresh?'?refresh=true':''}`);
    const json=await resp.json();
    if(!resp.ok||!json.report)throw new Error(json.detail||json.message||'falhou');
    _dnsFull[id]=json.report;
    if(currentLeadId===id&&!body.hidden)body.innerHTML=renderDnsFull(json.report);
  }catch(err){
    if(err.message==='not_authenticated')return;
    body.innerHTML=renderDnsBasic(currentLeadData)+
      `<div class="dnsx-err">Não foi possível coletar o relatório completo. <a href="#" onclick="loadDnsReport(true);return false">Tentar de novo</a></div>`;
  }finally{_dnsBusy[id]=false;}
}

function copyDnsJson(){
  const rep=_dnsFull[currentLeadId];
  if(!rep)return;
  navigator.clipboard.writeText(JSON.stringify(rep,null,2)).then(()=>{
    const btn=document.getElementById('dnsx-copy');
    if(!btn)return;
    const old=btn.textContent;btn.textContent='Copiado ✓';
    setTimeout(()=>{btn.textContent=old;},1600);
  }).catch(()=>{});
}

/* Prévia com o que a ficha já tem guardado (sem rede). */
function renderDnsBasic(data){
  const dns=(data&&data.dns_report)||((data&&(data.mx_records||[]).length)?{mx:data.mx_records}:null);
  if(!dns)return'';
  const e=(s)=>esc(String(s==null?'':s));
  const mx=dns.mx||[];
  const ns=dns.ns_records&&dns.ns_records.length?dns.ns_records:(dns.ns||[]).map(h=>({host:h,ip:null}));
  if(!mx.length&&!ns.length)return'';
  const mxRows=mx.map(m=>`<div class="ir">
    <div class="ir-c"><span class="ir-line"><span class="ir-prio">${e(m.priority)}</span><span class="ir-mx">${e(m.host)}</span></span></div>
    <div class="ir-c">${m.ip?`<span class="ir-ip">${e(m.ip)}</span>`:'<span class="ir-nil">sem IP</span>'}${m.ptr&&m.ptr!==m.host?`<span class="ir-sub">${e(m.ptr)}</span>`:''}</div>
    <div class="ir-c">${m.asn?`<span class="ir-line"><span class="ir-k">ASN:</span><span class="ir-asn">${e(m.asn)}</span></span>`:'<span class="ir-nil">—</span>'}${m.asn_cidr?`<span class="ir-sub net">${e(m.asn_cidr)}</span>`:''}</div>
    <div class="ir-c">${m.asn_org?`<span class="ir-org">${e(m.asn_org)}</span>`:'<span class="ir-nil">—</span>'}${(m.country_name||m.country)?`<span class="ir-sub geo">${e(m.country_name||m.country)}</span>`:''}</div>
  </div>`).join('');
  return `<div class="infra-panel">
    <div class="infra-blk">
      <div class="infra-blk-hdr">Registros MX<span class="infra-count">${mx.length}</span></div>
      ${mx.length?mxRows:'<div class="infra-empty">Nenhum registro MX publicado — o domínio não recebe e-mail.</div>'}
    </div>
  </div>`;
}

/* ── Relatório completo ──────────────────────────────────────────────────── */
function renderDnsFull(r){
  const e=(s)=>esc(String(s==null?'':s));
  const nil='<span class="ir-nil">—</span>';
  const s=r.summary||{},rec=r.records||{},em=r.email||{},reg=r.registration||null;
  const mono=(v)=>v?`<span class="dcode">${e(v)}</span>`:nil;
  const blk=(title,count,inner,meta)=>`<div class="infra-blk">
    <div class="infra-blk-hdr">${title}${count!=null?`<span class="infra-count">${count}</span>`:''}${meta?`<span class="infra-hdr-meta">${meta}</span>`:''}</div>
    ${inner}</div>`;
  const tab=(cols,rows)=>rows.length?`<div class="dtab-wrap"><table class="dtab">
    <thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(cells=>`<tr>${cells.map(c=>`<td>${c==null||c===''?nil:c}</td>`).join('')}</tr>`).join('')}</tbody>
  </table></div>`:'<div class="infra-empty">Nada publicado.</div>';
  const net=(x)=>[x.asn?`<span class="dcode">AS${e(x.asn)}</span>`:'',x.asn_cidr?`<span class="ir-sub net">${e(x.asn_cidr)}</span>`:''].filter(Boolean).join('<br/>')||nil;
  const org=(x)=>[x.asn_org?e(x.asn_org):'',(x.country_name||x.country)?`<span class="ir-sub geo">${e(x.country_name||x.country)}</span>`:''].filter(Boolean).join('<br/>')||nil;

  /* resumo */
  const tile=(lbl,val,extra)=>`<div class="dtile"><span class="dtile-lbl">${lbl}</span><span class="dtile-val">${val||nil}</span>${extra?`<span class="dtile-sub">${extra}</span>`:''}</div>`;
  const dmarcTag=s.dmarc_policy?`<span class="ir-pol ${e(s.dmarc_policy)}">p=${e(s.dmarc_policy)}</span>`:'<span class="ir-nil">sem DMARC</span>';
  const summary=`<div class="dsum">
    ${tile('Servidor MX',s.mx_host?mono(s.mx_host):nil,s.mx_count>1?`+${s.mx_count-1} de reserva`:'')}
    ${tile('Provedor de e-mail',s.mx_provider?`<span class="mx-tag">${e(s.mx_provider)}</span>`:nil)}
    ${tile('Hospedagem do site',s.hosting_provider?e(s.hosting_provider):nil,[s.hosting_asn?'AS'+e(s.hosting_asn):'',s.hosting_country?e(s.hosting_country):''].filter(Boolean).join(' · '))}
    ${tile('Autenticação',`${s.spf?'<span class="dnsx-chip ok">SPF</span>':'<span class="dnsx-chip off">sem SPF</span>'} ${dmarcTag} ${s.dkim_selectors?`<span class="dnsx-chip ok">DKIM ${s.dkim_selectors}</span>`:'<span class="dnsx-chip off">sem DKIM</span>'}`)}
    ${tile('Titular do domínio',reg&&(reg.owner||reg.registrar)?e(reg.owner||reg.registrar):nil,reg&&reg.owner_cnpj?`CNPJ ${e(reg.owner_cnpj)}`:(reg&&reg.registrar&&reg.owner?`Registrar: ${e(reg.registrar)}`:''))}
    ${tile('Registro',_fmtDate(s.registered_on)?e(_fmtDate(s.registered_on)):nil,_fmtDate(s.expires_on)?`expira em ${e(_fmtDate(s.expires_on))}`:'')}
    ${tile('DNSSEC',s.dnssec?'<span class="dnsx-chip ok">assinado</span>':'<span class="dnsx-chip off">não assinado</span>')}
    ${tile('Volume',`${e(s.records_total||0)} registros`,`${e(s.hosts_total||0)} hosts mapeados`)}
  </div>`;

  /* MX */
  const mxBlk=blk('Registros MX',(rec.mx||[]).length,
    tab(['Prio','Servidor de e-mail','IP · PTR','ASN · rede','Organização · país','TTL'],
      (rec.mx||[]).map(m=>[
        `<span class="ir-prio">${e(m.priority)}</span>`,
        `<span class="ir-mx">${e(m.host)}</span>`,
        [m.ip?`<span class="ir-ip">${e(m.ip)}</span>`:'',m.ptr&&m.ptr!==m.host?`<span class="ir-sub">${e(m.ptr)}</span>`:''].filter(Boolean).join('<br/>'),
        net(m),org(m),`<span class="ir-sub">${e(_fmtTtl(m.ttl))}</span>`,
      ])),
    s.mx_provider?`<span class="ir-tag">${e(s.mx_provider)}</span>`:'');

  /* NS */
  const nsBlk=blk('Servidores DNS (NS)',(rec.ns||[]).length,
    tab(['Servidor','IP','ASN','Organização · país','TTL'],
      (rec.ns||[]).map(n=>[
        `<span class="ir-ns">${e(n.host)}</span>`,
        n.ip?`<span class="ir-ip">${e(n.ip)}</span>`:'',
        n.asn?`<span class="dcode">AS${e(n.asn)}</span>`:'',
        org(n),`<span class="ir-sub">${e(_fmtTtl(n.ttl))}</span>`,
      ])));

  /* Hosts */
  const roleLbl={site:'site',mx:'MX',ns:'NS',host:'host'};
  const http=(h)=>{
    if(!h.http)return '';
    const st=h.http.status;
    const cls=st>=200&&st<300?'ok':(st>=300&&st<400?'warn':'off');
    return [`<span class="dnsx-chip ${cls}">${e(st)}</span>`,
            h.http.server?`<span class="ir-sub">${e(h.http.server)}</span>`:'',
            h.http.powered_by?`<span class="ir-sub">${e(h.http.powered_by)}</span>`:'',
            h.http.title?`<span class="ir-sub geo">${e(h.http.title)}</span>`:''].filter(Boolean).join('<br/>');
  };
  const hostsBlk=blk('Hosts e subdomínios',(r.hosts||[]).length,
    tab(['Host','Tipo','IP · PTR','ASN · rede','Organização · país','HTTP'],
      (r.hosts||[]).map(h=>[
        `<span class="ir-ns">${e(h.host)}</span>`,
        `<span class="ir-tag">${e(roleLbl[h.role]||h.role)}</span>`,
        [h.ip?`<span class="ir-ip">${e(h.ip)}</span>`:'',h.ptr&&h.ptr!==h.host?`<span class="ir-sub">${e(h.ptr)}</span>`:''].filter(Boolean).join('<br/>'),
        net(h),org(h),http(h),
      ])),
    'IP, PTR e ASN de cada nome encontrado em logs de certificado e na varredura de nomes comuns');

  /* Autenticação de e-mail */
  const spf=em.spf,dmarc=em.dmarc;
  const spfBlk=blk('SPF — quem pode enviar como '+e(r.domain),spf?spf.mechanisms.length:null,
    spf?`<div class="dnsx-raw">${e(spf.raw)}</div>`+
      tab(['Mecanismo','Valor','Efeito'],
        spf.mechanisms.map(m=>[
          `<span class="ir-tag">${e(m.type)}</span>`,
          m.value?`<span class="dcode">${e(m.value)}</span>`:'',
          `${e(m.qualifier_label)}${m.costs_lookup?' <span class="ir-sub">(consulta DNS)</span>':''}`,
        ]))+
      `<div class="infra-auth"><div class="ia"><div class="ia-k">Política final</div><div class="ia-v"><span class="dcode">${e(spf.all||'—')}</span><span class="ir-sub geo">${e(spf.policy_label)}</span></div></div>
       <div class="ia"><div class="ia-k">Consultas DNS</div><div class="ia-v">${e(spf.lookups)} de ${e(spf.lookup_limit)}${spf.over_limit?' <span class="dnsx-chip off">acima do limite — SPF inválido</span>':''}</div></div></div>`
    :'<div class="infra-empty">Sem SPF publicado — qualquer servidor pode enviar e-mail em nome do domínio.</div>');

  const dmarcRows=dmarc?Object.entries(dmarc.tags).map(([k,v])=>[
    `<span class="dcode">${e(k)}</span>`,`<span class="ir-sub">${e(v)}</span>`,
    e({v:'Versão do protocolo',p:'Política para o domínio',sp:'Política para subdomínios',pct:'% das mensagens sob a política',rua:'Relatórios agregados',ruf:'Relatórios forenses',adkim:'Alinhamento DKIM',aspf:'Alinhamento SPF',fo:'Quando gerar relatório',ri:'Intervalo dos relatórios'}[k]||''),
  ]):[];
  const dmarcBlk=blk('DMARC — o que fazer com o e-mail falso',dmarc?dmarcRows.length:null,
    dmarc?`<div class="dnsx-raw">${e(dmarc.raw)}</div>`+tab(['Tag','Valor','O que significa'],dmarcRows)+
      `<div class="infra-auth"><div class="ia"><div class="ia-k">Efeito</div><div class="ia-v"><span class="ir-pol ${e(dmarc.policy||'none')}">p=${e(dmarc.policy||'—')}</span><span class="ir-sub geo">${e(dmarc.policy_label)}</span></div></div></div>`
    :'<div class="infra-empty">Sem DMARC publicado — ninguém é avisado quando o domínio é usado em fraude.</div>');

  const dkimBlk=blk('DKIM — chaves de assinatura',(em.dkim||[]).length,
    tab(['Seletor','Registro','Tipo','Tamanho da chave'],
      (em.dkim||[]).map(d=>[
        `<span class="ir-tag">${e(d.selector)}</span>`,
        `<span class="dcode">${e(d.host)}</span>`,
        e(d.key_type||'—'),
        d.key_bits?`${e(d.key_bits)} bits${d.weak_key?' <span class="dnsx-chip off">fraca</span>':' <span class="dnsx-chip ok">ok</span>'}`:'',
      ])),
    // Não existe enumeração de seletor DKIM no DNS: o que aparece é o que
    // respondeu na varredura dos nomes que os provedores usam.
    (em.dkim||[]).length?'<span class="ir-sub">encontrados por varredura de seletores conhecidos</span>'
      :'<span class="ir-sub">nenhum seletor conhecido respondeu</span>');

  const policyRows=[];
  if(em.mta_sts)policyRows.push(['<span class="ir-tag">MTA-STS</span>',`<span class="dcode">${e(em.mta_sts.host)}</span>`,[em.mta_sts.txt?e(em.mta_sts.txt):'',em.mta_sts.policy?`modo <span class="dcode">${e(em.mta_sts.policy.mode)}</span> · MX na política: ${e((em.mta_sts.policy.mx||[]).join(', '))}`:''].filter(Boolean).join('<br/>')]);
  if(em.tls_rpt)policyRows.push(['<span class="ir-tag">TLS-RPT</span>',`<span class="dcode">${e(em.tls_rpt.host)}</span>`,e(em.tls_rpt.raw)]);
  if(em.bimi)policyRows.push(['<span class="ir-tag">BIMI</span>',`<span class="dcode">${e(em.bimi.host)}</span>`,e(em.bimi.raw)]);
  const policyBlk=policyRows.length?blk('Políticas de transporte e marca',policyRows.length,tab(['Padrão','Registro','Conteúdo'],policyRows)):'';

  /* TXT */
  const txtBlk=blk('Registros TXT',(rec.txt||[]).length,
    tab(['Tipo','Valor publicado'],
      (rec.txt||[]).map(t=>[
        t.label?`<span class="ir-tag">${e(t.label)}</span>`:'<span class="ir-sub">não classificado</span>',
        `<span class="dcode wrap">${e(t.value)}</span>`,
      ])),
    rec.ttl&&rec.ttl.TXT?`<span class="ir-sub">TTL ${e(_fmtTtl(rec.ttl.TXT))}</span>`:'');

  /* Stack */
  const stackBlk=(r.stack||[]).length?blk('Ferramentas identificadas',(r.stack||[]).length,
    `<div class="infra-chips">${r.stack.map(x=>`<span class="ir-tag">${e(x.label)}</span>`).join('')}</div>`,
    'Serviços que a empresa verificou no próprio DNS'):'';

  /* Endereços do site */
  const ipsBlk=((rec.a||[]).length||(rec.aaaa||[]).length)?blk('Endereços do site (A / AAAA)',(rec.a||[]).length+(rec.aaaa||[]).length,
    `<div class="infra-chips">${(rec.a||[]).map(ip=>`<span class="ir-ip">${e(ip)}</span>`).join('')}${(rec.aaaa||[]).map(ip=>`<span class="ir-ip v6">${e(ip)}</span>`).join('')}</div>`,
    (rec.cname||[]).length?`<span class="ir-sub">CNAME: ${(rec.cname||[]).map(c=>e(c.host)+' → '+e(c.target)).join(' · ')}</span>`:''):'';

  /* SOA / CAA / SRV / DNSSEC */
  const soa=rec.soa;
  const soaBlk=soa?blk('SOA — autoridade da zona',null,
    tab(['Campo','Código','Valor'],[
      ['Servidor primário','<span class="dcode">MNAME</span>',`<span class="dcode">${e(soa.mname)}</span>`],
      ['Contato responsável','<span class="dcode">RNAME</span>',`<span class="dcode">${e(soa.rname)}</span>`],
      ['Versão da zona','<span class="dcode">SERIAL</span>',`<span class="dcode">${e(soa.serial)}</span>`],
      ['Atualização','<span class="dcode">REFRESH</span>',e(_fmtTtl(soa.refresh))],
      ['Nova tentativa','<span class="dcode">RETRY</span>',e(_fmtTtl(soa.retry))],
      ['Expiração','<span class="dcode">EXPIRE</span>',e(_fmtTtl(soa.expire))],
      ['Cache negativo','<span class="dcode">MINIMUM</span>',e(_fmtTtl(soa.minimum))],
    ])):'';

  const caaBlk=(rec.caa||[]).length?blk('CAA — quem pode emitir certificado',(rec.caa||[]).length,
    tab(['Flags','Tag','Autoridade'],(rec.caa||[]).map(c=>[`<span class="dcode">${e(c.flags)}</span>`,`<span class="ir-tag">${e(c.tag)}</span>`,`<span class="dcode">${e(c.value)}</span>`]))):'';

  const srvBlk=(rec.srv||[]).length?blk('Registros SRV',(rec.srv||[]).length,
    tab(['Serviço','Registro','Destino','Prioridade · peso'],(rec.srv||[]).map(x=>[
      e(x.service),`<span class="dcode">${e(x.name)}</span>`,
      `<span class="dcode">${e(x.target)}:${e(x.port)}</span>`,
      `<span class="ir-sub">${e(x.priority)} · ${e(x.weight)}</span>`,
    ]))):'';

  const ds=r.dnssec||{};
  const dnssecBlk=blk('DNSSEC',null,
    ds.signed?tab(['Key tag','Algoritmo','Digest'],(ds.ds||[]).map(d=>[`<span class="dcode">${e(d.key_tag)}</span>`,`<span class="dcode">${e(d.algorithm)}</span>`,`<span class="dcode">${e(d.digest_type)}</span>`]))
      :'<div class="infra-empty">Zona não assinada — as respostas DNS deste domínio não podem ser validadas criptograficamente.</div>',
    ds.dnskey_count?`<span class="ir-sub">${e(ds.dnskey_count)} DNSKEY</span>`:'');

  /* Registro do domínio */
  const regBlk=reg?blk('Registro do domínio',null,
    tab(['Campo','Valor'],[
      ['Titular',reg.owner?e(reg.owner):''],
      ['CNPJ',reg.owner_cnpj?`<span class="dcode">${e(reg.owner_cnpj)}</span>`:''],
      ['Registrar',reg.registrar?e(reg.registrar):''],
      ['Registrado em',_fmtDate(reg.registered_on)?e(_fmtDate(reg.registered_on)):''],
      ['Expira em',_fmtDate(reg.expires_on)?e(_fmtDate(reg.expires_on)):''],
      ['Última alteração',_fmtDate(reg.changed_on)?e(_fmtDate(reg.changed_on)):''],
      ['Status',(reg.status||[]).map(x=>`<span class="ir-tag">${e(x)}</span>`).join(' ')],
      ['Contatos',(reg.contacts||[]).map(c=>`${e(c.name||'—')}${c.email?` <span class="dcode">${e(c.email)}</span>`:''} <span class="ir-sub">${e((c.roles||[]).join(', '))}</span>`).join('<br/>')],
      ['NS declarados',(reg.nameservers||[]).map(n=>`<span class="dcode">${e(n)}</span>`).join(' ')],
    ].filter(row=>row[1])),
    `<span class="ir-sub">fonte: ${e(reg.source||'RDAP')}</span>`):'';

  const warn=(r.warnings||[]).length?`<div class="dnsx-warn">${r.warnings.map(w=>`<div>${e(w)}</div>`).join('')}</div>`:'';
  const when=r.collected_at?new Date(r.collected_at).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'';

  return `${summary}
    <div class="infra-panel">
      ${mxBlk}${spfBlk}${dmarcBlk}${dkimBlk}${policyBlk}${txtBlk}${stackBlk}${nsBlk}${ipsBlk}${hostsBlk}${soaBlk}${caaBlk}${srvBlk}${dnssecBlk}${regBlk}
    </div>
    ${warn}
    <div class="dnsx-foot">
      <span>Coletado em ${e(when)}${r.elapsed_ms?` · ${(r.elapsed_ms/1000).toFixed(1)}s`:''} · DNS ao vivo, Team Cymru (ASN), Cert Spotter (certificados) e RDAP</span>
      <span class="dnsx-foot-btns">
        <button class="la-btn" id="dnsx-copy" onclick="copyDnsJson()">Copiar JSON</button>
        <button class="la-btn" onclick="loadDnsReport(true)">Recoletar</button>
      </span>
    </div>`;
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
    case 'stage':return STAGE_ORDER.indexOf(l.stage||'novo');
    case 'employees':return _histEmpMin(l);
    default:return l.created_at||'';
  }
}
function sortHistory(key){
  if(_histSort.key===key)_histSort.dir*=-1;
  else _histSort={key,dir:(key==='created_at'||key==='employees')?-1:1};
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
  count.textContent=_histLeads.length
    ? `Mostrando ${leads.length} de ${_histLeads.length} empresa${_histLeads.length!==1?'s':''} · clique no nome para ver o resumo`
    : '';
  if(!_histLeads.length){
    body.innerHTML=`<div class="muted-box">Nenhuma empresa analisada ainda.<br/>O histórico guarda tudo o que você pesquisar, com exportação para Excel.<br/><a class="empty-cta" href="#" onclick="nav('');focusSearch();return false">Fazer minha primeira análise</a></div>`;
    return;
  }
  if(!leads.length){body.innerHTML='<div class="muted-box">Nenhuma empresa corresponde ao filtro.</div>';return;}

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
    return `<tr id="row-${l.id}">
      <td class="td-co"><button class="td-name" onclick="openLeadSummary(${l.id})">${esc(l.company_name||l.domain||'—')}</button></td>
      <td class="td-mono">${esc(l.domain||'—')}</td>
      <td><span class="stage-chip">${STAGE_LABELS[l.stage||'novo']||esc(l.stage||'')}</span></td>
      <td class="td-mono">${esc(String(emp))}</td>
      <td class="td-mono td-date">${date}</td>
      <td class="td-actions">
        <button class="hist-btn primary" onclick="openLeadSummary(${l.id})">Ver</button>
        <button class="hist-btn danger" onclick="deleteLead(${l.id})" title="Remover">✕</button>
      </td>
    </tr>`;
  }).join('');
  body.innerHTML=`<div class="tbl-scroll"><table class="lead-tbl">
    <thead><tr>
      ${th('Empresa','company')}${th('Domínio','domain')}${th('Estágio','stage')}${th('Funcionários','employees')}${th('Data','created_at')}${th('Ações',null)}
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

/* ══════ RESUMO RÁPIDO (popup) ══════ */
function openLeadSummary(leadId){
  const lead=_histLeads.find(l=>l.id===leadId);
  if(!lead)return;
  document.getElementById('lead-summary-body').innerHTML=renderLeadSummary(lead);
  document.getElementById('lead-summary-modal').classList.add('open');
}
function closeLeadSummary(){document.getElementById('lead-summary-modal').classList.remove('open');}

function renderLeadSummary(l){
  const smap={enriched:['Enriquecido','enriched'],partial:['Parcial','partial'],failed:['Falhou','failed']};
  const[sl,sc]=smap[l.status]||['—','partial'];
  const init=(l.company_name||l.domain||'?').trim()[0].toUpperCase();
  const fav=l.domain?`https://www.google.com/s2/favicons?domain=${l.domain}&sz=64`:'';
  const cb=(conf)=>{if(!conf||conf==='none')return'';const m={verified:['OK','verified'],probable:['~','probable'],unverified:['?','unverified'],high:['OK','verified'],medium:['~','probable'],low:['?','unverified']};const[lb,c]=m[conf]||[conf,'probable'];return ` <span class="conf-badge ${c}">${lb}</span>`;};
  let emp='';
  if(l.employee_count){if(typeof l.employee_count==='object'){const e=l.employee_count;if(e.exact)emp=e.exact.toLocaleString('pt-BR');else if(e.min&&e.max)emp=`${e.min.toLocaleString('pt-BR')}–${e.max.toLocaleString('pt-BR')} (faixa)`;else if(e.min)emp=`${e.min.toLocaleString('pt-BR')}+ (faixa)`;else if(e.band)emp=e.band;else emp=e.raw||'';}else emp=l.employee_count;}
  const ws=l.website?l.website.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,''):'';
  const li=l.linkedin_url?l.linkedin_url.replace(/^https?:\/\/(www\.)?/,'').replace(/\/$/,''):'';
  const row=(lbl,val)=>val?`<div class="set-row"><span class="set-lbl">${lbl}</span><span class="set-val">${val}</span></div>`:'';
  const rows=[
    row('Site',ws?`<a class="data-val link" href="${l.website}" target="_blank" rel="noopener">${esc(ws)}</a>`:''),
    row('LinkedIn',l.linkedin_url?`<a class="data-val link" href="${l.linkedin_url}" target="_blank" rel="noopener">${esc(li)}</a>${cb(l.linkedin_confidence)}`:''),
    row('Funcionários',emp?esc(String(emp)):''),
    row('Localização',l.location?esc(l.location):''),
    row('Setor',l.sector?esc(l.sector):''),
    row('Provedor de e-mail',l.mx_provider?`<span class="mx-tag">${esc(l.mx_provider)}</span>${cb(l.mx_provider_confidence)}`:''),
    row('Estágio',`<span class="stage-chip">${STAGE_LABELS[l.stage||'novo']||esc(l.stage||'')}</span>`),
    row('Criado em',new Date(l.created_at).toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit',year:'2-digit'})),
  ].join('');
  const ai=l.ai_summary?`<div class="ai-box" style="margin:16px 0 0"><div class="ai-title">${IC_SPARK} Resumo IA</div><div class="ai-text">${esc(l.ai_summary)}</div></div>`:'';
  return `
    <div class="ls-hdr">
      <div class="ls-co">
        <div class="result-fav">${fav?`<img src="${fav}" onerror="this.style.display='none'" alt=""/>`:''}${init}</div>
        <div><div class="result-name">${esc(l.company_name||l.domain||'Empresa')}</div><div class="result-domain">${esc(l.domain||'')}</div></div>
      </div>
      <span class="status-pill ${sc}">${sl}</span>
    </div>
    <div class="ls-rows">${rows}</div>
    ${ai}
    <div class="ls-footer">
      <button class="la-btn" onclick="closeLeadSummary();loadLeadIntoView(${l.id})">Ver ficha completa e decisores →</button>
    </div>`;
}

/* ══════ EXPORTAÇÃO EM MASSA ══════ */
function openExportModal(){
  document.getElementById('export-modal').classList.add('open');
  setExportPreset('30days');
}
function closeExportModal(){document.getElementById('export-modal').classList.remove('open');}

function setExportPreset(preset){
  document.querySelectorAll('.export-opt').forEach(b=>b.classList.toggle('active',b.dataset.preset===preset));
  document.getElementById('export-custom-range').style.display=preset==='custom'?'flex':'none';
  document.getElementById('export-modal').dataset.preset=preset;
}

async function runExport(fmt){
  const preset=document.getElementById('export-modal').dataset.preset||'30days';
  const params=new URLSearchParams({format:fmt,preset});
  if(preset==='custom'){
    const start=document.getElementById('export-date-start').value;
    const end=document.getElementById('export-date-end').value;
    if(!start||!end){alert('Informe as duas datas do intervalo.');return;}
    params.set('date_start',start);params.set('date_end',end);
  }
  const token=await getToken();if(!token)return;
  const btn=document.getElementById(`export-btn-${fmt}`);
  const label=btn.textContent;btn.disabled=true;btn.textContent='Gerando…';
  try{
    const resp=await fetch(`/api/export?${params.toString()}`,{headers:{Authorization:`Bearer ${token}`}});
    if(!resp.ok){
      const j=await resp.json().catch(()=>({}));
      alert(j.detail||'Nenhum lead encontrado para o período selecionado.');
      return;
    }
    const blob=await resp.blob();
    const url=URL.createObjectURL(blob);
    const cd=resp.headers.get('content-disposition')||'';
    const match=cd.match(/filename="([^"]+)"/);
    const a=document.createElement('a');
    a.href=url;a.download=match?match[1]:`leads.${fmt==='xlsx'?'xlsx':'csv'}`;
    document.body.appendChild(a);a.click();
    setTimeout(()=>{URL.revokeObjectURL(url);a.remove();},1000);
    closeExportModal();
  }catch(e){alert('Erro de conexão ao exportar.');}
  finally{btn.disabled=false;btn.textContent=label;}
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
  // Créditos da extensão: medidor separado das análises de empresa
  const revealsUsed=me?.reveals_used??0,revealsLimit=me?.reveals_limit??0;
  const revealsUnlimited=revealsLimit<0;

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

  const head=(icon,title,desc)=>`<div class="set-head">
    <span class="set-ic">${icon}</span>
    <span><div class="set-title">${title}</div><p class="set-desc">${desc}</p></span>
  </div>`;
  const IC_USER='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
  const IC_PLUG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 2v6M15 2v6"/><path d="M6 8h12v4a6 6 0 0 1-12 0z"/><path d="M12 18v4"/></svg>';
  const IC_EXT='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/></svg>';
  const IC_EXIT='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';

  body.innerHTML=`
    <div class="panel panel-pad">
      ${head(IC_USER,'Conta e uso do ciclo','Quanto da sua cota já foi usada e quando ela renova. Uma análise é consumida por domínio novo; reabrir uma empresa dos últimos 7 dias é grátis.')}
      <div class="set-row"><span class="set-lbl">E-mail</span><span class="set-val">${esc(me?.email||(demo?'sessão demo':'—'))}</span></div>
      <div class="set-row"><span class="set-lbl">Plano</span><span class="set-val"><span class="plan-badge">${esc(plan)}</span></span></div>
      <div class="set-row" style="display:block">
        <span class="set-lbl">Análises de empresa</span>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:6px">
          <span class="set-val" style="text-align:left">${unlimited?'Ilimitadas':`${used} de ${limit} usadas`}</span>
          ${reset&&!unlimited?`<span class="set-lbl">renova em ${reset}</span>`:''}
        </div>
        ${unlimited?'':`<div class="usage-track"><div class="usage-fill${pctUsed>=100?' full':''}" style="width:${pctUsed}%"></div></div>`}
      </div>
      <div class="set-row">
        <span class="set-lbl">Revelações de contato (extensão)</span>
        <span class="set-val">${revealsUnlimited?'ilimitadas':`${revealsUsed} de ${revealsLimit} usadas`}</span>
      </div>
      ${planActions}
    </div>

    <div class="panel panel-pad">
      ${head(IC_PLUG,'Enviar leads para o seu CRM','Cada lead — com decisores e atividades — é enviado por <strong>POST assinado com HMAC-SHA256</strong> ao endereço que você informar. Funciona com Zapier, Make, Power Automate ou um sistema próprio. Com o webhook ativo, o botão “Enviar ao CRM” fica habilitado na ficha do lead.')}
      ${connCard}
      <div class="set-form">
        <div class="set-field">
          <label for="crm-url">URL que vai receber os leads</label>
          <input id="crm-url" class="set-input" placeholder="https://hooks.zapier.com/…" autocomplete="off" spellcheck="false"/>
        </div>
        <div class="set-field">
          <label for="crm-secret">Segredo para assinar o envio (opcional, recomendado)</label>
          <input id="crm-secret" class="set-input" placeholder="Uma frase secreta que só você e o seu sistema conhecem" autocomplete="off" spellcheck="false"/>
        </div>
        <div class="set-actions">
          <button class="set-btn primary" onclick="saveCrmWebhook()">${webhook?'Atualizar webhook':'Salvar e ativar webhook'}</button>
        </div>
        <p id="crm-feedback" class="set-feedback"></p>
      </div>
    </div>

    <div class="panel panel-pad">
      ${head(IC_EXT,'Extensão do navegador (LinkedIn)','Mostra decisores, e-mail corporativo e telefone da empresa direto nas páginas do LinkedIn, e salva o lead no seu pipeline. Gere o código abaixo e cole no popup da extensão para conectar este navegador. Cada revelação consome 1 crédito — <strong>e nada é cobrado quando não encontramos contato</strong>.')}
      <div class="set-actions">
        <button class="set-btn primary" onclick="generatePairCode()">Gerar código de pareamento</button>
        <span class="set-lbl">Válido por poucos minutos, uso único</span>
      </div>
      <p id="ext-feedback" class="set-feedback"></p>
    </div>

    <div class="panel panel-pad">
      ${head(IC_EXIT,'Sessão','Encerra o acesso neste navegador. Seus leads continuam salvos na conta.')}
      <div class="set-actions"><button class="set-btn danger" onclick="signOut()">Sair da conta</button></div>
    </div>`;
}

/* ══════ EXTENSÃO: código de pareamento ══════ */
async function generatePairCode(){
  const el=document.getElementById('ext-feedback');
  if(!el)return;
  el.textContent='Gerando…';el.className='set-feedback ok';
  try{
    const resp=await authFetch('/api/extension/pair-code',{method:'POST',body:'{}'});
    if(!resp.ok){el.textContent='Não conseguimos gerar o código agora.';el.className='set-feedback err';return;}
    const data=await resp.json();
    el.innerHTML=`Cole este código no popup da extensão:<br><span class="pair-code">${esc(data.code)}</span>
      <br><span class="set-lbl">Válido por ${data.expires_in_minutes} minutos.</span>`;
    el.className='set-feedback ok';
  }catch(e){
    if(e.message!=='not_authenticated'){el.textContent='Erro de conexão.';el.className='set-feedback err';}
  }
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


/* ══════ IMPORTAÇÃO DE PLANILHA ══════
   Sobe .xlsx/.csv, mostra o que foi reconhecido e cria as linhas como leads.
   A grade em si vive em sheet.js — aqui fica só o caminho até ela. */
function loadImport(){
  _imp={preview:null,file:null,busy:false};
  renderImportDrop();
}

function renderImportDrop(errorMsg){
  document.getElementById('import-body').innerHTML=`
    <div class="panel panel-pad">
      ${errorMsg?`<div class="imp-error">${esc(errorMsg)}</div>`:''}
      <div class="imp-drop" id="imp-drop"
           ondragover="impDragOver(event)" ondragleave="impDragLeave(event)" ondrop="impDrop(event)"
           onclick="document.getElementById('imp-file').click()">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        <strong>Arraste a planilha aqui ou clique para escolher</strong>
        <span>.xlsx ou .csv · até 5.000 linhas · todas as colunas são preservadas</span>
      </div>
      <input type="file" id="imp-file" accept=".xlsx,.xlsm,.csv,.tsv" style="display:none"
             onchange="impFileChange(this)" />
      <div class="imp-hint">
        Nada é descartado: cada coluna do seu arquivo vira uma coluna da planilha, com o mesmo
        nome e na mesma ordem. As que o sistema entende — <em>Empresa, Domínio, Site, LinkedIn,
        E-mail, Telefone, Funcionários</em> — também alimentam o enriquecimento, que acrescenta
        colunas novas sem mexer nas suas.
      </div>
    </div>`;
}

function impDragOver(e){e.preventDefault();document.getElementById('imp-drop').classList.add('over');}
function impDragLeave(e){
  const drop=document.getElementById('imp-drop');
  if(!drop.contains(e.relatedTarget))drop.classList.remove('over');
}
function impDrop(e){
  e.preventDefault();
  document.getElementById('imp-drop')?.classList.remove('over');
  const file=e.dataTransfer?.files?.[0];
  if(file)uploadImportFile(file);
}
function impFileChange(input){if(input.files&&input.files[0])uploadImportFile(input.files[0]);}

async function uploadImportFile(file,sheet){
  const token=await getToken();
  if(!token){openAuthModal();return;}
  _imp.file=file;
  document.getElementById('import-body').innerHTML=
    `<div class="panel"><div class="muted-box">Lendo <strong>${esc(file.name)}</strong>…</div></div>`;
  const form=new FormData();
  form.append('file',file,file.name);
  if(sheet)form.append('sheet',sheet);
  try{
    // FormData define o próprio Content-Type (com boundary) — por isso o fetch
    // aqui é manual, sem o header JSON do authFetch.
    const resp=await fetch('/api/import/preview',{method:'POST',
      headers:{Authorization:`Bearer ${token}`},body:form});
    const json=await resp.json().catch(()=>({}));
    if(!resp.ok){renderImportDrop(json.detail||'Não consegui ler esta planilha.');return;}
    _imp.preview=json;
    renderImportPreview();
  }catch(e){renderImportDrop('Erro de conexão ao enviar o arquivo.');}
}

function renderImportPreview(){
  const p=_imp.preview;
  const mapped=p.columns.filter(c=>c.field);
  const chips=mapped.map(c=>
    `<span class="imp-chip"><span class="imp-chip-f">${IMP_FIELDS[c.field]||c.field}</span>${esc(c.label)}</span>`).join('');
  const extras=p.columns.length-mapped.length;

  const sheetPicker=p.sheets.length>1?`
    <div class="imp-sheets">
      <span class="imp-map-lbl">Aba</span>
      ${p.sheets.map(s=>`<button class="sh-chip${s.name===p.sheet?' on':''}"
        onclick="uploadImportFile(_imp.file,${JSON.stringify(s.name).replace(/"/g,'&quot;')})">
        ${esc(s.name)} <span class="imp-sheet-n">${s.rows}</span></button>`).join('')}
    </div>`:'';

  // Mostra as primeiras colunas do arquivo, do jeito que estão
  const cols=p.columns.slice(0,IMP_PREVIEW_COLS);
  const head=cols.map(c=>`<th>${esc(c.label)}</th>`).join('');
  const rows=p.rows.map(r=>{
    const st=IMP_STATUS[r.status]||{label:r.status,cls:''};
    const tds=cols.map(c=>{
      const v=r.cells[c.label];
      return `<td>${v==null?'':esc(String(v)).slice(0,80).replace(/\n/g,' ⏎ ')}</td>`;
    }).join('');
    return `<tr class="${r.status==='invalid'?'imp-row-off':''}">
      <td class="td-mono">${r.row_number}</td>${tds}
      <td><span class="imp-badge ${st.cls}">${st.label}</span></td>
    </tr>`;
  }).join('');

  document.getElementById('import-body').innerHTML=`
    <div class="panel">
      <div class="imp-head">
        <div>
          <div class="imp-file">${esc(p.filename)} <span class="imp-sheet-tag">${esc(p.sheet)}</span></div>
          <div class="imp-sum">${esc(p.message)}</div>
        </div>
        <button class="ghost-btn" onclick="loadImport()">Trocar arquivo</button>
      </div>
      ${sheetPicker}
      <div class="imp-map">
        <div class="imp-map-lbl">${p.columns.length} colunas — ${mapped.length} reconhecidas pelo sistema</div>
        <div class="imp-chips">${chips||'<span class="imp-unmapped">nenhuma</span>'}</div>
        ${extras?`<div class="imp-unmapped">As outras ${extras} coluna(s) entram na planilha do mesmo jeito, sem interpretação.</div>`:''}
      </div>
      ${p.truncated?'<div class="imp-warn">O arquivo passa de 5.000 linhas — só as primeiras 5.000 foram lidas.</div>':''}
      <div class="tbl-scroll"><table class="lead-tbl imp-tbl">
        <thead><tr><th>Linha</th>${head}<th>Situação</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="imp-more">Mostrando ${p.rows.length} de ${p.total_rows} linhas e ${cols.length} de ${p.columns.length} colunas — a planilha completa aparece depois de importar.</div>
      <div class="imp-foot">
        <label class="imp-check">
          <input type="checkbox" id="imp-skip-dup" ${p.counts.duplicate_db?'checked':''} />
          Pular ${p.counts.duplicate_db||0} empresa(s) que já estão no histórico
        </label>
        <button class="app-btn-primary" id="imp-commit-btn" onclick="commitImport()"
                ${p.importable?'':'disabled'}>
          Importar ${p.importable} empresa(s)
        </button>
      </div>
    </div>`;
}

async function commitImport(){
  const p=_imp.preview;
  if(!p)return;
  const btn=document.getElementById('imp-commit-btn');
  if(btn){btn.disabled=true;btn.textContent='Importando…';}
  const skipExisting=document.getElementById('imp-skip-dup')?.checked!==false;
  try{
    const resp=await authFetch(`/api/import/${p.batch_id}/commit`,{method:'POST',
      body:JSON.stringify({skip_existing:skipExisting,skip_duplicates:false})});
    const json=await resp.json().catch(()=>({}));
    if(!resp.ok){renderImportPreview();alert(json.detail||'Erro ao importar a planilha.');return;}
    renderImportDone(json);
  }catch(e){
    if(e.message!=='not_authenticated'){renderImportPreview();alert('Erro de conexão.');}
  }
}

function renderImportDone(result){
  const quota=_profile&&_profile.searches_limit>0
    ? Math.max(_profile.searches_limit-_profile.searches_used,0) : null;
  document.getElementById('import-body').innerHTML=`
    <div class="panel panel-pad imp-done">
      <div class="imp-done-ico">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="20 6 9 17 4 12"/></svg>
      </div>
      <h3>${result.created} empresa(s) importada(s)</h3>
      <p class="imp-done-sub">
        ${result.skipped?`${result.skipped} linha(s) ignorada(s) por já existirem ou por não terem identificação.<br/>`:''}
        A planilha completa já está no sistema, com todas as colunas do seu arquivo.
        O enriquecimento roda de lá e acrescenta site, LinkedIn, DNS/MX, telefone e score.
      </p>
      <p class="imp-quota">${quota!==null?`Você tem ${quota} busca(s) na cota — cada empresa enriquecida consome 1.`:'Cada empresa enriquecida consome 1 busca da cota.'}</p>
      <div class="imp-done-actions">
        <button class="app-btn-primary" onclick="nav('sheet')">Abrir a planilha</button>
        <button class="ghost-btn" onclick="loadImport()">Importar outra</button>
      </div>
    </div>`;
}

async function downloadImportTemplate(fmt){
  const token=await getToken();
  if(!token){openAuthModal();return;}
  const resp=await fetch(`/api/import/template?format=${fmt}`,{headers:{Authorization:`Bearer ${token}`}});
  if(!resp.ok){alert('Erro ao baixar o modelo.');return;}
  const url=URL.createObjectURL(await resp.blob());
  const a=document.createElement('a');
  a.href=url;a.download=`modelo_importacao.${fmt}`;
  document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(url);a.remove();},1000);
}

/* Enriquecimento avulso de um lead importado (botão do histórico) */
async function enrichImportedLead(leadId,btn){
  if(btn){btn.disabled=true;btn.textContent='…';}
  try{
    const resp=await authFetch(`/api/leads/${leadId}/enrich`,{method:'POST'});
    if(resp.status===402){openPaywall();return;}
    const json=await resp.json().catch(()=>({}));
    if(!resp.ok){alert(json.detail||'Erro ao enriquecer.');return;}
    loadProfile();
    loadHistory();
  }catch(e){
    if(e.message!=='not_authenticated')alert('Erro de conexão.');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Enriquecer';}
  }
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
function hideResults(){
  document.getElementById('results-section').innerHTML='';
  document.getElementById('view-search')?.classList.remove('has-result');
  const block=document.getElementById('recent-block');
  if(block&&document.getElementById('recent-grid')?.children.length)block.style.display='block';
}
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

  // Lote: contagem ao digitar e leitura do CSV no próprio navegador (o
  // servidor recebe texto, não arquivo — uma dependência a menos).
  document.getElementById('lote-input')?.addEventListener('input',atualizarContagemLote);
  document.getElementById('lote-file')?.addEventListener('change',ev=>{
    const arquivo=ev.target.files&&ev.target.files[0];
    if(!arquivo)return;
    const leitor=new FileReader();
    leitor.onload=()=>{
      document.getElementById('lote-input').value=String(leitor.result||'');
      atualizarContagemLote();
    };
    leitor.readAsText(arquivo);
  });

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
