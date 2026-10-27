"""
Chat de IA sobre o pipeline do usuário (Fase 5a do redesenho com o AI CRM
dashboard da 21st como referência visual).

Só lê. A IA não consulta o banco: quem decide o que ela vê é este módulo, com
consultas comuns e sempre filtradas pelo dono da conta. Ela recebe um retrato
compacto da conta e o detalhe dos leads citados com @, e responde sobre isso.
Desde a Fase 5b ela também pode *propor* três mudanças (ver `extrair_propostas`),
que voltam validadas para a tela e só acontecem com o clique do vendedor.

O que nunca sai daqui para o provedor de IA:

- telefone e e-mail de qualquer pessoa ou da empresa (nem o `snippet` da
  busca do decisor, que costuma trazer os dois);
- leads marcados como "não contatar" ou bloqueados — quem pediu para sair não
  deveria continuar sendo assunto de prompt;
- números de telefone e e-mails digitados dentro das notas, trocados por
  um marcador antes do envio.

Mesmo provedor do resumo da ficha (Groq, `GROQ_API_KEY`), chamado sem SDK.
"""
import json
import logging
import os
import re
from datetime import datetime, time
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import (
    Activity,
    HIDDEN_LEAD_STATUSES,
    Lead,
    RELATIONSHIP_BLOCKED,
    RELATIONSHIP_DO_NOT_CONTACT,
    utcnow,
)
from services.cadence import TZ

logger = logging.getLogger(__name__)

_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# A função serverless tem 60 s; a IA fica com metade e sobra folga para o banco.
_TIMEOUT = 30
_MAX_TOKENS = 900

#: Quantos leads entram na tabela do contexto. Acima disto a tela avisa que o
#: chat só enxerga os mais recentes (e os citados com @, que entram sempre).
MAX_LEADS_CONTEXTO = 150
#: Quantas trocas anteriores da conversa voltam para a IA.
MAX_HISTORICO = 10
MAX_TEXTO = 2000
#: Atividades por lead citado: as mais recentes contam a história.
MAX_ATIVIDADES_DETALHE = 15

_FORA_DO_CONTEXTO = (RELATIONSHIP_DO_NOT_CONTACT, RELATIONSHIP_BLOCKED)

_ESTAGIOS = {
    "novo": "Novo", "contatado": "Contatado", "reuniao_agendada": "Reunião agendada",
    "oportunidade": "Oportunidade", "ganho": "Ganho", "perdido": "Perdido",
}

# Telefone: 8+ dígitos com separadores comuns; e-mail: o formato de sempre.
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RE_TELEFONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_OMITIDO = "[contato omitido]"

#: Como a IA cita um lead na resposta; a tela troca o marcador por um link.
_RE_CITACAO = re.compile(r"\[\[lead:(\d+)\]\]")


def is_configured() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def _sem_contato(texto: Optional[str]) -> str:
    """Tira e-mails e telefones de um texto livre antes de ir para a IA."""
    if not texto:
        return ""
    return _RE_TELEFONE.sub(_OMITIDO, _RE_EMAIL.sub(_OMITIDO, texto))


def _porte(lead: Lead) -> str:
    e = lead.employee_count
    if isinstance(e, dict):
        v = e.get("exact") or e.get("band") or e.get("raw")
        return str(v) if v else ""
    return str(e) if e else ""


def _data(dt: Optional[datetime]) -> str:
    return dt.strftime("%d/%m/%Y") if dt else ""


def _leads_visiveis(db: Session, user_id: str):
    return db.query(Lead).filter(
        Lead.user_id == user_id,
        Lead.status.notin_(HIDDEN_LEAD_STATUSES),
        Lead.relationship.notin_(_FORA_DO_CONTEXTO),
    )


def _resumo_da_conta(db: Session, user_id: str, total: int) -> str:
    """Os números saem do banco, não da IA — é o que ela deve repetir."""
    estagios = dict(
        _leads_visiveis(db, user_id)
        .with_entities(Lead.stage, func.count(Lead.id))
        .group_by(Lead.stage).all()
    )
    tiers = dict(
        _leads_visiveis(db, user_id)
        .with_entities(Lead.tier, func.count(Lead.id))
        .group_by(Lead.tier).all()
    )
    # "Hoje" no fuso da agenda, não no do servidor.
    agora = utcnow()
    fim_do_dia = datetime.combine(agora.astimezone(TZ).date(), time(23, 59, 59), tzinfo=TZ)
    abertas = db.query(Activity).filter(
        Activity.user_id == user_id,
        Activity.due_at.isnot(None),
        Activity.completed_at.is_(None),
    )
    atrasadas = abertas.filter(Activity.due_at < agora).count()
    hoje = abertas.filter(Activity.due_at >= agora, Activity.due_at <= fim_do_dia).count()

    linhas = [f"Total de leads: {total}"]
    linhas.append("Por estágio: " + ", ".join(
        f"{rot} {estagios.get(k, 0)}" for k, rot in _ESTAGIOS.items()))
    linhas.append("Por tier: " + ", ".join(
        f"{'Sem tier' if t is None else f'T{t}'} {n}"
        for t, n in sorted(tiers.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))))
    linhas.append(f"Tarefas abertas: {atrasadas} atrasadas, {hoje} para hoje")
    return "\n".join(linhas)


def _tabela_de_leads(db: Session, leads: List[Lead]) -> str:
    ids = [l.id for l in leads]
    ultima = dict(
        db.query(Activity.lead_id, func.max(Activity.created_at))
        .filter(Activity.lead_id.in_(ids)).group_by(Activity.lead_id).all()
    ) if ids else {}
    linhas = ["id | empresa | domínio | estágio | tier | setor | porte | analisado em | última atividade"]
    for l in leads:
        linhas.append(" | ".join([
            str(l.id),
            l.company_name or "",
            l.domain or "",
            _ESTAGIOS.get(l.stage or "novo", l.stage or ""),
            f"T{l.tier}" if l.tier is not None else "",
            l.sector or "",
            _porte(l),
            _data(l.created_at),
            _data(ultima.get(l.id)),
        ]))
    return "\n".join(linhas)


def _detalhe(lead: Lead) -> str:
    partes = [f"### Lead [[lead:{lead.id}]] — {lead.company_name or lead.domain}"]

    def add(rotulo, valor):
        if valor:
            partes.append(f"{rotulo}: {valor}")

    add("Domínio", lead.domain)
    add("Estágio", _ESTAGIOS.get(lead.stage or "novo", lead.stage))
    add("Tier", f"T{lead.tier}" if lead.tier is not None else None)
    add("Motivo do tier", lead.tier_reason)
    add("Setor", lead.sector)
    add("Localização", lead.location)
    add("Porte", _porte(lead))
    add("Provedor de e-mail", lead.mx_provider)
    add("Cadência", lead.cadence)
    add("Descrição", _sem_contato((lead.description or "")[:400]))

    for dm in list(lead.decision_makers)[:5]:
        if dm.name:
            cargo = dm.title_found or dm.title_searched or "cargo n/d"
            principal = " (contato principal)" if dm.is_primary else ""
            partes.append(f"Contato: {dm.name} — {cargo}{principal}")

    # Pelo id: segue a ordem de criação sem comparar datas com e sem fuso
    # (o SQLite dos testes devolve sem, o Postgres com).
    atividades = sorted(lead.activities, key=lambda a: a.id or 0, reverse=True)
    for a in atividades[:MAX_ATIVIDADES_DETALHE]:
        quando = (f"concluída {_data(a.completed_at)}" if a.completed_at
                  else f"prazo {_data(a.due_at)}" if a.due_at
                  else f"registrada {_data(a.created_at)}")
        detalhe = " · ".join(filter(None, [a.type, a.step, a.outcome, quando]))
        nota = _sem_contato((a.notes or "")[:300])
        partes.append(f"Atividade: {detalhe}" + (f" — {nota}" if nota else ""))
    return "\n".join(partes)


def montar_contexto(db: Session, user_id: str, mencionados: List[int]) -> Dict[str, Any]:
    """
    O retrato da conta que vai para a IA, e os ids que ela pode citar.

    Lead citado com @ entra sempre com o detalhe, mesmo fora dos 150 mais
    recentes — desde que seja do usuário e não esteja fora do contexto. Um id
    de outra conta simplesmente não aparece.
    """
    base = _leads_visiveis(db, user_id)
    total = base.count()
    recentes = base.order_by(Lead.created_at.desc()).limit(MAX_LEADS_CONTEXTO).all()

    detalhados: List[Lead] = []
    if mencionados:
        detalhados = base.filter(Lead.id.in_(mencionados[:10])).all()

    citaveis = {l.id: (l.company_name or l.domain or f"Lead {l.id}") for l in recentes}
    citaveis.update({l.id: (l.company_name or l.domain or f"Lead {l.id}") for l in detalhados})
    estagios = {l.id: (l.stage or "novo") for l in [*recentes, *detalhados]}

    blocos = [
        "## Resumo da conta",
        _resumo_da_conta(db, user_id, total),
        f"## Leads ({len(recentes)} mais recentes de {total})",
        _tabela_de_leads(db, recentes),
    ]
    if detalhados:
        blocos.append("## Leads citados pelo usuário")
        blocos.extend(_detalhe(l) for l in detalhados)
    return {
        "texto": "\n\n".join(blocos),
        "citaveis": citaveis,
        "estagios": estagios,
        "total": total,
        "no_contexto": len(recentes),
    }


_INSTRUCOES = """Você é o assistente comercial do LeadEnricher e conversa em português do \
Brasil com um vendedor B2B sobre o pipeline dele.

Regras:
- Responda APENAS com base nos DADOS abaixo. Se a resposta não estiver neles, diga isso.
- Os números do "Resumo da conta" são oficiais: use-os como estão, sem recontar.
- A tabela de leads pode não ter todos os leads da conta (ela diz quantos mostra de quantos). \
Quando isso afetar a resposta, avise.
- Ao mencionar um lead, escreva o marcador [[lead:ID]] logo depois do nome, com o id da tabela.
- Seja direto. Use tabela em markdown quando listar mais de três leads.
- Você não executa nada. Pode PROPOR mudanças, que o vendedor aprova uma a uma.
- Hoje é {hoje}.

Propostas (opcional, só quando o vendedor pedir ou quando houver uma ação óbvia):
- Existem APENAS três ações:
  - "mover_estagio": campos lead_id, estagio (um de: {estagios}), motivo
  - "criar_tarefa": campos lead_id, titulo (curto), prazo (AAAA-MM-DD, de hoje até 90 dias), motivo
  - "adicionar_nota": campos lead_id, texto, motivo
- No máximo {max_propostas} propostas, só para leads da tabela.
- Nunca proponha excluir, enviar mensagem, ligar, revelar contato ou mudar tier.
- Ponha as propostas no FIM da resposta, num bloco assim, com JSON válido:
```propostas
[{{"acao": "mover_estagio", "lead_id": 12, "estagio": "contatado", "motivo": "..."}}]
```"""

# ── Propostas de mudança (Fase 5b) ───────────────────────────────────────────
#
# A IA só sugere. Cada proposta passa por esta validação, vira um cartão na
# tela e só acontece quando o vendedor clica em "Aplicar" — e o clique chama a
# mesma rota que a tela já usa (PATCH /stage, POST /activities), com as mesmas
# checagens de dono. Este módulo nunca grava nada.

ACOES = ("mover_estagio", "criar_tarefa", "adicionar_nota")
MAX_PROPOSTAS = 5
PRAZO_MAXIMO_DIAS = 90
#: Hora do dia em que a tarefa proposta vence, no fuso da agenda.
HORA_DA_TAREFA = time(9, 0)

_RE_BLOCO = re.compile(r"```propostas\s*(.*?)```", re.DOTALL)


def _texto_curto(valor: Any, limite: int) -> str:
    return " ".join(str(valor or "").split())[:limite]


def _validar(p: Any, ctx: Dict[str, Any], hoje) -> Optional[Dict[str, Any]]:
    """Uma proposta crua → a versão limpa que vai para a tela, ou None."""
    if not isinstance(p, dict) or p.get("acao") not in ACOES:
        return None
    try:
        lead_id = int(p.get("lead_id"))
    except (TypeError, ValueError):
        return None
    # Só lead que a IA viu — e que, por isso, é do dono e não está fora do contexto.
    if lead_id not in ctx["citaveis"]:
        return None
    base = {
        "acao": p["acao"],
        "lead_id": lead_id,
        "lead_nome": ctx["citaveis"][lead_id],
        "motivo": _texto_curto(p.get("motivo"), 200),
    }

    if p["acao"] == "mover_estagio":
        estagio = p.get("estagio")
        atual = ctx["estagios"].get(lead_id)
        if estagio not in _ESTAGIOS or estagio == atual:
            return None
        return {**base, "estagio": estagio, "estagio_atual": atual,
                "resumo": f"Mover {base['lead_nome']} de {_ESTAGIOS.get(atual, atual)} para {_ESTAGIOS[estagio]}"}

    if p["acao"] == "criar_tarefa":
        titulo = _texto_curto(p.get("titulo"), 200)
        try:
            prazo = datetime.strptime(str(p.get("prazo")), "%Y-%m-%d").date()
        except ValueError:
            return None
        if not titulo or prazo < hoje or (prazo - hoje).days > PRAZO_MAXIMO_DIAS:
            return None
        due_at = datetime.combine(prazo, HORA_DA_TAREFA, tzinfo=TZ)
        return {**base, "titulo": titulo, "prazo": prazo.isoformat(), "due_at": due_at.isoformat(),
                "resumo": f"Criar tarefa para {base['lead_nome']} em {prazo.strftime('%d/%m')}: {titulo}"}

    texto = str(p.get("texto") or "").strip()[:1000]
    if not texto:
        return None
    return {**base, "texto": texto, "resumo": f"Adicionar nota em {base['lead_nome']}"}


def extrair_propostas(texto: str, ctx: Dict[str, Any]):
    """
    Separa o bloco de propostas da resposta e devolve (texto_sem_bloco, propostas).

    JSON quebrado não derruba a resposta: o texto vai sem o bloco e sem
    propostas. Duplicatas e excedentes são descartados.
    """
    blocos = _RE_BLOCO.findall(texto)
    limpo = _RE_BLOCO.sub("", texto).strip()
    hoje = utcnow().astimezone(TZ).date()
    propostas, vistas = [], set()
    for bloco in blocos:
        try:
            cruas = json.loads(bloco)
        except ValueError:
            continue
        for p in cruas if isinstance(cruas, list) else [cruas]:
            ok = _validar(p, ctx, hoje)
            if not ok:
                continue
            chave = (ok["acao"], ok["lead_id"], ok.get("estagio"), ok.get("titulo"), ok.get("texto"))
            if chave in vistas:
                continue
            vistas.add(chave)
            propostas.append(ok)
            if len(propostas) >= MAX_PROPOSTAS:
                return limpo, propostas
    return limpo, propostas


def _chamar_groq(mensagens: List[Dict[str, str]]) -> Optional[str]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={"model": _MODEL, "max_tokens": _MAX_TOKENS, "messages": mensagens},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        escolhas = resp.json().get("choices", [])
        if not escolhas:
            return None
        texto = (escolhas[0].get("message", {}).get("content") or "").strip()
        return texto or None
    except Exception as e:
        logger.warning("Chat de IA: chamada ao Groq falhou: %s", e)
        return None


def responder(
    db: Session,
    user_id: str,
    pergunta: str,
    historico: List[Dict[str, str]],
    mencionados: List[int],
) -> Optional[Dict[str, Any]]:
    """
    Pergunta → resposta. `None` quando a IA falha (a rota devolve 502).

    O histórico vem da tela e é tratado como texto de usuário: só os papéis
    `user` e `assistant` passam, e cada mensagem é cortada no mesmo limite da
    pergunta.
    """
    ctx = montar_contexto(db, user_id, mencionados)
    hoje = utcnow().astimezone(TZ).strftime("%d/%m/%Y")
    mensagens = [{
        "role": "system",
        "content": _INSTRUCOES.format(
            hoje=hoje, estagios=", ".join(_ESTAGIOS), max_propostas=MAX_PROPOSTAS,
        ) + "\n\nDADOS:\n" + ctx["texto"],
    }]
    for turno in historico[-MAX_HISTORICO:]:
        papel = turno.get("role")
        if papel in ("user", "assistant"):
            mensagens.append({"role": papel, "content": str(turno.get("content") or "")[:MAX_TEXTO]})
    mensagens.append({"role": "user", "content": pergunta[:MAX_TEXTO]})

    texto = _chamar_groq(mensagens)
    if not texto:
        return None
    texto, propostas = extrair_propostas(texto, ctx)

    # Só vira link o lead que a IA podia citar; um id inventado perde o marcador.
    citados: List[int] = []

    def _troca(m):
        lid = int(m.group(1))
        if lid in ctx["citaveis"]:
            if lid not in citados:
                citados.append(lid)
            return m.group(0)
        return ""

    texto = _RE_CITACAO.sub(_troca, texto)
    return {
        "resposta": texto,
        "leads": [{"id": i, "nome": ctx["citaveis"][i]} for i in citados],
        "propostas": propostas,
        "total_leads": ctx["total"],
        "leads_no_contexto": ctx["no_contexto"],
    }
