import logging

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from sqlalchemy.orm import Session
from models.database import get_db, DecisionMaker, Lead
from models.schemas import (
    EnrichRequest, EnrichResponse, LeadOut,
    DecisoresRequest, DecisoresResponse, DecisionMakerOut,
    ContatosResponse, RevelarRequest, RevelarResponse,
)
from services import enrichment_service
from services.decision_finder import find_decision_makers
from services.lead_scorer import apply_score
from services.providers import lusha, lusha_prospecting
from middleware.auth import get_current_user, rate_limit_key
from routers.auth import get_or_create_profile
from routers.extension import _lusha_key_utilizavel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["enrichment"])
limiter = Limiter(key_func=rate_limit_key)


@router.post("/enrich", response_model=EnrichResponse)
@limiter.limit("10/minute")
def enrich(
    request: Request,
    body: EnrichRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Busca avulsa: continua síncrona porque um domínio cabe no orçamento da
    requisição (~15-30 s) e o usuário está olhando para a tela esperando a
    ficha. Volume vai pela fila — ver routers/batch.py.
    """
    if not body.domain or not body.domain.strip():
        raise HTTPException(status_code=422, detail="Domínio não pode estar vazio.")

    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)

    outcome = enrichment_service.enrich_for_user(db, profile, body.domain)

    if outcome.result == enrichment_service.RESULT_ERROR:
        raise HTTPException(status_code=500, detail=outcome.message)

    return EnrichResponse(
        success=outcome.result != enrichment_service.RESULT_FAILED,
        message=outcome.message,
        data=LeadOut.model_validate(outcome.lead),
    )


@router.post("/leads/{lead_id}/enrich", response_model=EnrichResponse)
@limiter.limit("120/minute")
def enrich_existing_lead(
    request: Request,
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Enriquece uma ficha que já existe — as que vieram de planilha.

    É o que a fila da planilha chama, um lead por requisição: a coleta leva
    10–30 s e o maxDuration da função na Vercel é 60 s. O limite alto por
    minuto existe para a fila rodar várias em paralelo.
    """
    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    profile = get_or_create_profile(db, user_id)

    outcome = enrichment_service.enrich_existing_lead(db, profile, lead)

    if outcome.error == "dominio_desconhecido":
        raise HTTPException(status_code=422, detail=outcome.message)
    if outcome.result == enrichment_service.RESULT_ERROR:
        raise HTTPException(status_code=500, detail=outcome.message)

    return EnrichResponse(
        success=outcome.result != enrichment_service.RESULT_FAILED,
        message=outcome.message,
        data=LeadOut.model_validate(outcome.lead),
    )


@router.post("/decisores", response_model=DecisoresResponse)
@limiter.limit("20/minute")
def buscar_decisores(
    request: Request,
    body: DecisoresRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not body.roles:
        raise HTTPException(status_code=422, detail="Informe pelo menos um cargo.")

    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    profile = get_or_create_profile(db, user_id)

    try:
        results = find_decision_makers(
            domain=lead.domain or lead.raw_input_domain,
            company_name=lead.company_name,
            roles=body.roles,
            limit=8,
            linkedin_url=lead.linkedin_url,
            # Com a sessão, os e-mails saem do padrão aprendido do domínio e os
            # decisores encontrados entram no banco global de pessoas.
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar decisores: {e}")

    saved = []
    for r in results:
        dm = DecisionMaker(
            lead_id=lead.id,
            name=r.get("name"),
            title_searched=r.get("title_searched"),
            title_found=r.get("title_found"),
            snippet=r.get("snippet"),
            linkedin_url=r.get("linkedin_url"),
            probable_emails=r.get("probable_emails"),
            match_confidence=r.get("match_confidence"),
            phone=r.get("phone"),
        )
        db.add(dm)
        saved.append(dm)

    # TESTE: completa com a Lusha quem ficou sem celular, se o usuário tiver
    # a conta conectada. Só entra aqui quem o caminho gratuito não resolveu.
    chave_lusha = _lusha_key_utilizavel(profile)
    if chave_lusha:
        for dm in saved:
            if dm.phone:
                continue
            achado = lusha.find_contacts(
                full_name=dm.name,
                domain=lead.domain or lead.raw_input_domain,
                company_name=lead.company_name,
                linkedin_url=dm.linkedin_url,
                api_key=chave_lusha,
            )
            if achado and achado.get("phones"):
                dm.phone = achado["phones"][0]["e164"]

    db.commit()
    for dm in saved:
        db.refresh(dm)
    db.refresh(lead)

    # Repontua: decisor com e-mail verificado e telefone é o eixo de maior peso
    # da régua, então a nota calculada na coleta está desatualizada no instante
    # em que esta busca termina. Recalcular aqui é o que faz a ficha subir na
    # lista assim que ela vira acionável, sem o usuário pedir.
    #
    # Depois do commit, e lendo da relação em vez de `saved`: a ficha pode já
    # ter decisores de uma busca anterior, e pontuar só os desta rodada daria
    # uma nota menor do que a ficha merece. `lead.decision_makers` é a única
    # fonte que enxerga os dois grupos.
    apply_score(lead, decision_makers=list(lead.decision_makers))
    db.commit()
    db.refresh(lead)

    return DecisoresResponse(
        success=True,
        message=f"{len(saved)} decisor(es) encontrado(s)." if saved else "Nenhum decisor encontrado para os cargos informados.",
        decisores=[DecisionMakerOut.model_validate(d) for d in saved],
    )


# ── Contatos da empresa (Lusha Prospecting, com queda para o gratuito) ──────


def _cargos_padrao() -> List[str]:
    """Cargos do caminho gratuito. Só é usado quando não há Lusha conectada."""
    return ["founder", "ceo", "cto", "cfo", "vp", "president", "executive"]


def _contato_lusha_para_dm(lead_id: int, c: dict) -> DecisionMaker:
    """
    Contato do search → linha em `decision_makers`, ainda não revelado.

    E-mail e telefone ficam vazios de propósito: o search não os traz, e
    inventar um palpite aqui faria a tela mostrar dado fraco com cara de dado
    pago. Quem preenche é o /reveal, sob clique.
    """
    return DecisionMaker(
        lead_id=lead_id,
        name=c.get("name"),
        title_found=c.get("title"),
        title_searched=c.get("title"),
        snippet=None,
        linkedin_url=c.get("linkedin_url"),
        probable_emails=None,
        match_confidence="high",   # veio de provedor pago e verificado
        phone=None,
        lusha_contact_id=c.get("lusha_contact_id"),
        revealed=False,
        can_reveal=c.get("can_reveal"),
        data_points=c.get("data_points"),
        department=c.get("department"),
        seniority=c.get("seniority"),
        location=c.get("location"),
        company_industries=c.get("company_industries"),
        source="lusha",
    )


@router.get("/leads/{lead_id}/contacts", response_model=ContatosResponse)
@limiter.limit("30/minute")
def contatos_da_empresa(
    request: Request,
    lead_id: int,
    page: int = 0,
    page_size: int = lusha_prospecting.PAGE_SIZE_DEFAULT,
    job_titles: Optional[List[str]] = Query(None),
    seniority: Optional[List[int]] = Query(None),
    departments: Optional[List[str]] = Query(None),
    countries: Optional[List[str]] = Query(None),
    data_points: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Contatos da empresa deste lead, paginados e filtráveis.

    Substitui o antigo `/popular-contacts`, que chamava `/v2/company` — um
    endpoint de dados firmográficos que nunca devolveu lista de pessoas. Aqui
    quem responde é a Prospecting API, que é o que a extensão da Lusha usa.

    Custo: 1 crédito por 25 contatos listados. Nada de e-mail ou telefone é
    revelado nesta chamada — para isso existe o /reveal, um contato por vez.
    """
    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    dominio = lead.domain or lead.raw_input_domain
    profile = get_or_create_profile(db, user_id)
    chave = _lusha_key_utilizavel(profile)

    # A validação do page_size acontece aqui também, e não só no provedor,
    # porque o caminho gratuito não passa pelo provedor — sem isto um
    # page_size absurdo cairia direto no fatiamento da lista gratuita.
    if page_size < lusha_prospecting.PAGE_SIZE_MIN:
        page_size = lusha_prospecting.PAGE_SIZE_MIN
    elif page_size > lusha_prospecting.PAGE_SIZE_MAX:
        page_size = lusha_prospecting.PAGE_SIZE_MAX
    if page < 0:
        page = 0

    erro_lusha: dict = {}
    resultado = None
    if chave and dominio:
        resultado = lusha_prospecting.search_contacts(
            chave,
            [dominio],
            page=page,
            page_size=page_size,
            job_titles=job_titles,
            seniority_ids=seniority,
            departments=departments,
            countries=countries,
            existing_data_points=data_points,
            erro_out=erro_lusha,
        )

    if resultado is not None:
        # Uma página nova substitui a anterior daquela fonte: sem isso, folhear
        # a lista acumularia duplicata a cada página e o contador da tela
        # passaria a mentir.
        db.query(DecisionMaker).filter(
            DecisionMaker.lead_id == lead.id,
            DecisionMaker.source == "lusha",
            DecisionMaker.revealed.isnot(True),
        ).delete(synchronize_session=False)

        ja_revelados = {
            dm.lusha_contact_id: dm
            for dm in db.query(DecisionMaker).filter(
                DecisionMaker.lead_id == lead.id,
                DecisionMaker.lusha_contact_id.isnot(None),
            ).all()
        }

        salvos = []
        for c in resultado["contacts"]:
            # Contato já revelado antes é reaproveitado em vez de regravado:
            # regravá-lo apagaria o e-mail e o telefone que o usuário já pagou.
            existente = ja_revelados.get(c.get("lusha_contact_id"))
            if existente is not None:
                salvos.append(existente)
                continue
            dm = _contato_lusha_para_dm(lead.id, c)
            db.add(dm)
            salvos.append(dm)

        db.commit()
        for dm in salvos:
            db.refresh(dm)

        return ContatosResponse(
            success=True,
            message=f"{len(salvos)} contato(s) na Lusha para {dominio}."
                    if salvos else "A Lusha não tem contatos desta empresa.",
            fonte="lusha",
            total=resultado["total"],
            page=resultado["page"],
            page_size=resultado["page_size"],
            contatos=[DecisionMakerOut.model_validate(d) for d in salvos],
        )

    # ── Queda para o caminho gratuito ───────────────────────────────────────
    #
    # Acontece em três situações que a tela precisa distinguir: sem chave
    # conectada, chave conectada mas Lusha recusou (sem crédito / rate limit),
    # e Lusha fora do ar. O produto funciona inteiro nos três.
    try:
        achados = find_decision_makers(
            domain=dominio,
            company_name=lead.company_name,
            roles=_cargos_padrao(),
            limit=15,
            linkedin_url=lead.linkedin_url,
            db=db,
        )
    except Exception as e:
        logger.error("Caminho gratuito falhou para %s: %s", dominio, e)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar contatos: {e}")

    # FILTRO DE FIDELIDADE: sem perfil pessoal do LinkedIn não dá para afirmar
    # que a pessoa trabalha lá. Um "contato" errado custa mais que nenhum.
    achados = [
        r for r in achados
        if r.get("linkedin_url") and "linkedin.com/in/" in r.get("linkedin_url", "").lower()
    ]

    db.query(DecisionMaker).filter(
        DecisionMaker.lead_id == lead.id,
        DecisionMaker.source == "free",
    ).delete(synchronize_session=False)

    salvos = []
    for r in achados:
        dm = DecisionMaker(
            lead_id=lead.id,
            name=r.get("name"),
            title_searched=r.get("title_searched"),
            title_found=r.get("title_found"),
            snippet=r.get("snippet"),
            linkedin_url=r.get("linkedin_url"),
            probable_emails=r.get("probable_emails"),
            match_confidence=r.get("match_confidence"),
            phone=r.get("phone"),
            revealed=False,
            source="free",
        )
        db.add(dm)
        salvos.append(dm)

    db.commit()
    for dm in salvos:
        db.refresh(dm)

    inicio = page * page_size
    pagina = salvos[inicio:inicio + page_size]

    if not chave:
        aviso = ("Conecte sua conta Lusha em Configurações para ver celular e "
                 "e-mail verificados desta empresa.")
    elif erro_lusha.get("status"):
        aviso = erro_lusha.get("mensagem")
    else:
        aviso = None

    return ContatosResponse(
        success=True,
        message=f"{len(pagina)} contato(s) encontrado(s) em fontes públicas."
                if pagina else "Nenhum contato encontrado em fontes públicas.",
        fonte="free",
        total=len(salvos),
        page=page,
        page_size=page_size,
        contatos=[DecisionMakerOut.model_validate(d) for d in pagina],
        erro=aviso,
    )


@router.post("/decision-makers/{dm_id}/reveal", response_model=RevelarResponse)
@limiter.limit("30/minute")
def revelar_contato(
    request: Request,
    dm_id: int,
    body: Optional[RevelarRequest] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Revela e-mail e/ou telefone de UM contato. Custa crédito do usuário:
    1 por e-mail, 5 por telefone.

    Um contato por chamada, e só por clique. Revelar em lote ou "por precaução"
    torraria o crédito de quem paga sem ele ter pedido — é a regra que mais
    importa nesta integração.
    """
    user_id = current_user.get("sub")
    dm = (
        db.query(DecisionMaker)
        .join(Lead, DecisionMaker.lead_id == Lead.id)
        .filter(DecisionMaker.id == dm_id, Lead.user_id == user_id)
        .first()
    )
    if not dm:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")

    # Já pago: devolve o que está gravado sem tocar na Lusha. Revelar duas
    # vezes cobra duas vezes pelo mesmo dado.
    if dm.revealed:
        return RevelarResponse(
            success=True,
            message="Contato já revelado.",
            contato=DecisionMakerOut.model_validate(dm),
            ja_revelado=True,
            creditos_gastos=0,
        )

    if not dm.lusha_contact_id:
        raise HTTPException(
            status_code=422,
            detail="Este contato veio de fonte pública e não pode ser revelado pela Lusha.",
        )

    profile = get_or_create_profile(db, user_id)
    chave = _lusha_key_utilizavel(profile)
    if not chave:
        raise HTTPException(
            status_code=422,
            detail="Conecte sua conta Lusha em Configurações para revelar contatos.",
        )

    campos = (body.reveal if body else None) or None
    # Pedir um campo que este contato não permite revelar é 400 na Lusha, e um
    # 400 pode custar crédito sem devolver dado. `canReveal` é a lista do que
    # ele aceita — filtramos por ela antes de sair da máquina.
    permitidos = {
        item.get("field") for item in (dm.can_reveal or [])
        if isinstance(item, dict) and item.get("field")
    }
    if campos and permitidos:
        campos = [c for c in campos if c in permitidos]
        if not campos:
            raise HTTPException(
                status_code=422,
                detail="A Lusha não tem esse dado para este contato.",
            )

    erro: dict = {}
    resposta = lusha_prospecting.enrich_contacts(
        chave, [dm.lusha_contact_id], reveal=campos, erro_out=erro,
    )
    if resposta is None:
        # 402 e 429 pedem ações diferentes do usuário — comprar crédito ou
        # esperar. A mensagem já vem separada do provedor.
        raise HTTPException(
            status_code=502,
            detail=erro.get("mensagem") or lusha_prospecting.erro_legivel(None),
        )

    contatos = resposta.get("contacts") or []
    if not contatos:
        raise HTTPException(
            status_code=404,
            detail="A Lusha não tinha e-mail nem telefone para este contato.",
        )

    c = contatos[0]
    emails = c.get("emails") or []
    phones = c.get("phones") or []

    if emails:
        dm.probable_emails = [
            {"email": e["email"], "status": e.get("status", "unknown"),
             "confidence": e.get("confidence")}
            for e in emails
        ]
    if phones:
        dm.phone = phones[0]["e164"]
    # Marca como revelado mesmo quando a Lusha devolveu vazio para um dos
    # campos: o crédito já foi gasto, e um segundo clique gastaria de novo
    # para receber o mesmo vazio.
    dm.revealed = True
    if c.get("location") and not dm.location:
        dm.location = c["location"]

    db.commit()
    db.refresh(dm)

    custo = 0
    for e in emails:
        custo += lusha_prospecting.PRICING["revealEmail"]["credits"]
    for _ in phones:
        custo += lusha_prospecting.PRICING["revealPhone"]["credits"]

    return RevelarResponse(
        success=True,
        message="Contato revelado.",
        contato=DecisionMakerOut.model_validate(dm),
        ja_revelado=False,
        creditos_gastos=custo,
    )


@router.get("/lusha/filters")
def filtros_lusha():
    """
    Vocabulário dos filtros da barra lateral.

    Servido de constantes do backend, não da Lusha: os valores foram
    verificados contra a API real e não mudam a cada requisição. Buscá-los na
    Lusha a cada abertura de tela gastaria requisição do limite do usuário para
    receber sempre a mesma lista.

    Não consome crédito.
    """
    return {
        "seniority": lusha_prospecting.SENIORITY,
        "departments": lusha_prospecting.DEPARTMENTS,
        "data_points": lusha_prospecting.DATA_POINTS,
        "pricing": lusha_prospecting.PRICING,
        "page_size": {
            "min": lusha_prospecting.PAGE_SIZE_MIN,
            "max": lusha_prospecting.PAGE_SIZE_MAX,
            "default": lusha_prospecting.PAGE_SIZE_DEFAULT,
        },
    }
