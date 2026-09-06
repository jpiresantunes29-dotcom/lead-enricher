"""
Fila de enriquecimento — o que torna o lote possível.

Uma busca leva 15-30 s e a função serverless morre em 60 s: 200 domínios em
uma requisição não é lento, é impossível. Aqui o pedido só enfileira, e o
processamento acontece em rodadas curtas, cada uma com orçamento próprio.

Quem roda as rodadas:
  - o navegador do usuário, enquanto a tela do lote está aberta
    (`POST /api/batches/{id}/run`) — funciona em qualquer plano de hospedagem
  - o cron (`POST /api/internal/jobs/run`) — cobre o usuário que fechou a aba

Os dois usam `run_pending()`. Rodar em paralelo é seguro: cada job é reservado
com um UPDATE condicional, e quem perde a corrida simplesmente pega o próximo.
"""
import logging
import os
import time
import uuid
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from models.database import Job, Lead, Profile, RELATIONSHIP_LEAD, utcnow
from services import enrichment_service
from services._utils import normalize_domain

logger = logging.getLogger(__name__)

# Teto de tempo de UMA rodada. Precisa caber com folga no limite da função
# serverless (60 s na Vercel) contando a busca em andamento, que pode levar
# até ENRICH_BUDGET_SECONDS sozinha.
ROUND_BUDGET_SECONDS = int(os.getenv("JOBS_ROUND_BUDGET", "45"))

# Uma busca não iniciada precisa de tempo para terminar; começar sem isso é
# garantir job interrompido no meio.
_JOB_RESERVE_SECONDS = 20.0

# Tentativas por job antes de desistir. Falha de rede costuma ser transitória;
# domínio que não existe falha igual nas três e vira "failed" rápido.
MAX_ATTEMPTS = 3

# Teto de domínios por lote. Protege o banco (e o tempo de coleta) de um CSV
# colado por engano e enfileirado inteiro de uma vez.
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "200"))

# Empresa muda: gente troca de cargo, decisor sai, telefone é atualizado. Um
# lead nunca revisitado depois da primeira coleta vai ficando cada vez mais
# desatualizado — este é o prazo depois do qual ele volta sozinho para a fila.
STALE_LEAD_DAYS = int(os.getenv("STALE_LEAD_DAYS", "30"))

# Quantos leads antigos entram na fila por rodada do cron. Sem teto, a
# primeira vez que esta rotina roda numa base grande enfileiraria todo o
# histórico de uma vez — cada um levando 15-30 s de coleta.
MAX_STALE_REFRESH_PER_ROUND = int(os.getenv("MAX_STALE_REFRESH_PER_ROUND", "20"))

# Jobs em paralelo por rodada. Enriquecimento é I/O de rede (scraping, DNS,
# busca) — a GIL do Python libera durante isso, e paralelizar é o que permite
# uma rodada de 45 s processar vários domínios em vez de um atrás do outro. Um
# lote de 200 domínios a ~20 s cada levaria mais de uma hora em série; poucos
# workers de cada vez evita sobrecarregar os motores de busca gratuitos que a
# coleta usa por baixo (decision_finder, quando chamado à parte).
JOBS_MAX_WORKERS = int(os.getenv("JOBS_MAX_WORKERS", "4"))

# Depois de quanto tempo um job reservado é considerado abandonado. Precisa ser
# maior que a rodada mais longa possível, senão duas rodadas simultâneas
# processam o mesmo domínio.
STALE_JOB_SECONDS = int(os.getenv("JOBS_STALE_SECONDS", "300"))

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED)


# ── Enfileiramento ───────────────────────────────────────────────────────────

def normalize_domains(raw: List[str], limit: int = MAX_BATCH_SIZE) -> List[str]:
    """
    Limpa, deduplica e corta a lista na ordem em que o usuário informou.

    A ordem importa: quem colou 300 domínios e vê o lote parar no teto espera
    que os processados sejam os primeiros da lista dele.
    """
    seen = set()
    out = []
    for item in raw:
        domain = normalize_domain(item or "")
        if not domain or "." not in domain or domain in seen:
            continue
        seen.add(domain)
        out.append(domain)
        if len(out) >= limit:
            break
    return out


def create_batch(db: Session, user_id: str, domains: List[str]) -> tuple[str, List[Job]]:
    """Cria um lote com um job por domínio. Não processa nada. Commita."""
    batch_id = uuid.uuid4().hex
    jobs = [
        Job(user_id=user_id, batch_id=batch_id, kind="enrich",
            payload={"domain": domain}, status=STATUS_QUEUED)
        for domain in domains
    ]
    db.add_all(jobs)
    db.commit()
    logger.info("Lote criado batch=%s user=%s jobs=%d", batch_id, user_id, len(jobs))
    return batch_id, jobs


def enqueue_stale_refreshes(db: Session, max_leads: int = MAX_STALE_REFRESH_PER_ROUND,
                            older_than_days: int = STALE_LEAD_DAYS) -> int:
    """
    Enfileira leads que não são revisitados há `older_than_days` para uma
    re-coleta (mesmo domínio, mesma ficha — não cria lead duplicado).

    Só leads com `relationship == LEAD`: cliente atual ou contato bloqueado
    não precisa da ficha mantida em dia, e recoletar um deles seria trabalho
    sem propósito nenhum. Um lead com job "refresh" já na fila não entra de
    novo — sem essa checagem, rodar o cron várias vezes num dia empilharia o
    mesmo lead repetidas vezes.
    """
    corte = utcnow() - timedelta(days=older_than_days)
    payloads_em_voo = (
        db.query(Job.payload)
        .filter(Job.kind == "refresh", Job.status.in_((STATUS_QUEUED, STATUS_RUNNING)))
        .all()
    )
    ja_enfileirados = {linha[0].get("lead_id") for linha in payloads_em_voo if linha[0]}

    candidatos = (
        db.query(Lead)
        .filter(
            Lead.relationship == RELATIONSHIP_LEAD,
            func.coalesce(Lead.refreshed_at, Lead.created_at) < corte,
        )
        .order_by(func.coalesce(Lead.refreshed_at, Lead.created_at).asc())
        .limit(max_leads + len(ja_enfileirados))  # folga para descontar os já na fila
        .all()
    )

    novos = [
        Job(user_id=lead.user_id, kind="refresh", payload={"lead_id": lead.id},
            status=STATUS_QUEUED)
        for lead in candidatos
        if lead.id not in ja_enfileirados
    ][:max_leads]

    if novos:
        db.add_all(novos)
        db.commit()
        logger.info("Leads antigos reenfileirados para atualização: %d", len(novos))
    return len(novos)


# ── Execução ─────────────────────────────────────────────────────────────────

def _claim(db: Session, job: Job) -> bool:
    """
    Reserva o job para esta rodada.

    O UPDATE condicional (`WHERE status = 'queued'`) é o que permite navegador
    e cron rodarem juntos sem processar o mesmo domínio duas vezes: só uma das
    transações encontra a linha ainda na fila.
    """
    result = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == STATUS_QUEUED)
        .values(status=STATUS_RUNNING, started_at=utcnow(), attempts=Job.attempts + 1)
    )
    db.commit()
    return result.rowcount == 1


def reclaim_stale(db: Session, older_than_seconds: int = STALE_JOB_SECONDS) -> int:
    """
    Devolve à fila os jobs reservados por uma rodada que nunca terminou.

    A função serverless pode morrer no meio (limite de tempo, deploy, falta de
    memória) e o job fica `running` para sempre: `_next_queued` nunca mais o
    escolhe e o lote trava perto do fim, sem erro nenhum na tela. É a diferença
    entre "a fila roda com a aba fechada" e "a fila roda até alguma rodada
    morrer".

    Job que já gastou as tentativas vira `failed` — repor na fila para sempre
    seria trocar um lote travado por um lote que nunca acaba.
    """
    limite = utcnow() - timedelta(seconds=older_than_seconds)
    devolvidos = db.execute(
        update(Job)
        .where(
            Job.status == STATUS_RUNNING,
            Job.attempts < MAX_ATTEMPTS,
            # started_at nulo é job reservado por uma versão anterior do código;
            # tratá-lo como recente o deixaria preso pelo mesmo motivo.
            or_(Job.started_at.is_(None), Job.started_at < limite),
        )
        .values(status=STATUS_QUEUED, started_at=None)
    ).rowcount
    desistidos = db.execute(
        update(Job)
        .where(
            Job.status == STATUS_RUNNING,
            Job.attempts >= MAX_ATTEMPTS,
            or_(Job.started_at.is_(None), Job.started_at < limite),
        )
        .values(status=STATUS_FAILED, finished_at=utcnow(),
                error="interrompido antes de terminar")
    ).rowcount
    db.commit()

    if devolvidos or desistidos:
        logger.warning(
            "Jobs interrompidos recuperados: %d devolvidos à fila, %d marcados como falha.",
            devolvidos, desistidos,
        )
    return devolvidos + desistidos


def _next_queued(db: Session, user_id: Optional[str] = None,
                 batch_id: Optional[str] = None) -> Optional[Job]:
    query = db.query(Job).filter(Job.status == STATUS_QUEUED)
    if user_id:
        query = query.filter(Job.user_id == user_id)
    if batch_id:
        query = query.filter(Job.batch_id == batch_id)
    return query.order_by(Job.id.asc()).first()


def _apply_outcome(db: Session, job: Job, outcome) -> None:
    """Grava o resultado de um outcome já pronto no job e fecha a transação."""
    job.result = outcome.result
    job.lead_id = outcome.lead.id if outcome.lead else None
    job.error = outcome.error

    if outcome.result == enrichment_service.RESULT_ERROR and (job.attempts or 0) < MAX_ATTEMPTS:
        job.status = STATUS_QUEUED                        # falha transitória
        job.started_at = None
    elif outcome.result == enrichment_service.RESULT_ERROR:
        job.status = STATUS_FAILED
        job.finished_at = utcnow()
    else:
        job.status = STATUS_DONE
        job.finished_at = utcnow()

    db.commit()


def _contar(resumo: dict, job: Job) -> None:
    if job.result == enrichment_service.RESULT_ERROR and job.status == STATUS_FAILED:
        resumo["failed"] += 1
    elif job.status == STATUS_DONE:
        resumo["done"] += 1


def run_job(db: Session, job: Job) -> str:
    """
    Executa um job já reservado, do início ao fim (reserva + coleta + grava),
    e devolve o resultado (`enrichment_service.RESULT_*`). Não levanta exceção.

    Usado direto pelos jobs `kind="refresh"` (poucos por rodada, e a descoberta
    de domínio já mistura rede com banco por dentro — não vale coordenar
    paralelismo para isso). Jobs `kind="enrich"`, o caso comum de lote grande,
    passam por `run_pending`, que paraleliza só a coleta — ver lá.
    """
    profile = db.query(Profile).filter(Profile.id == job.user_id).first()
    if not profile:
        job.status = STATUS_FAILED
        job.result = enrichment_service.RESULT_ERROR
        job.error = "perfil inexistente"
        job.finished_at = utcnow()
        db.commit()
        return enrichment_service.RESULT_ERROR

    if job.kind == "refresh":
        lead = db.query(Lead).filter(Lead.id == (job.payload or {}).get("lead_id")).first()
        if lead is None:
            job.status = STATUS_FAILED
            job.result = enrichment_service.RESULT_ERROR
            job.error = "lead inexistente"
            job.finished_at = utcnow()
            db.commit()
            return enrichment_service.RESULT_ERROR
        outcome = enrichment_service.enrich_existing_lead(db, profile, lead)
    else:
        domain = (job.payload or {}).get("domain", "")
        outcome = enrichment_service.enrich_for_user(db, profile, domain)

    _apply_outcome(db, job, outcome)
    return outcome.result


def _start_enrich_job(db: Session, pool: ThreadPoolExecutor, job: Job):
    """
    Prepara um job `kind="enrich"` (banco: cache/reserva da ficha — rápido) e
    dispara a coleta (rede pura) no pool. Devolve `(future, contexto)` para
    `run_pending` acompanhar, ou `None` se o job já foi resolvido na hora
    (cache ou domínio inválido) sem precisar de rede nenhuma.
    """
    profile = db.query(Profile).filter(Profile.id == job.user_id).first()
    if not profile:
        job.status = STATUS_FAILED
        job.result = enrichment_service.RESULT_ERROR
        job.error = "perfil inexistente"
        job.finished_at = utcnow()
        db.commit()
        return None

    raw_domain = (job.payload or {}).get("domain", "")
    prepared = enrichment_service.prepare_enrichment(db, profile, raw_domain)
    if prepared.outcome is not None:
        _apply_outcome(db, job, prepared.outcome)
        return None

    def _coletar():
        try:
            return (enrichment_service.enrich_company(raw_domain), None)
        except Exception as e:
            return (None, e)

    future = pool.submit(_coletar)
    return future, {"job": job, "profile": profile, "prepared": prepared}


def run_pending(db: Session, budget_seconds: float = ROUND_BUDGET_SECONDS,
                user_id: Optional[str] = None, batch_id: Optional[str] = None,
                max_jobs: Optional[int] = None) -> dict:
    """
    Processa jobs até acabar a fila ou o orçamento de tempo.

    Jobs `kind="enrich"` são coletados em paralelo (até `JOBS_MAX_WORKERS` de
    cada vez): a coleta é rede pura e roda solta no pool, mas toda escrita no
    banco — reserva da ficha e gravação do resultado — acontece nesta mesma
    sessão, sequencial. É a diferença entre paralelizar a espera de rede (que
    é o gargalo real) e paralelizar sessões de banco: a segunda opção corrompe
    uma conexão SQLite compartilhada entre threads (o que os testes usam) e,
    em Postgres, ganharia pouco sobre o que já se ganha aqui.

    Devolve o resumo da rodada — é o que a UI usa para decidir se chama outra.
    """
    started = time.monotonic()
    deadline = started + budget_seconds
    processed = 0
    resumo = {"processed": 0, "done": 0, "failed": 0, "remaining": 0, "elapsed_ms": 0}

    # Antes de procurar trabalho novo, resgata o que ficou preso: sem isto o
    # lote encolhe a cada rodada que morre e nunca chega ao fim.
    reclaim_stale(db)

    workers = max(1, JOBS_MAX_WORKERS)
    em_voo: dict = {}   # future -> {"job", "profile", "prepared"}

    def _drenar(bloqueante: bool, restante: float = 0.0) -> None:
        nonlocal processed
        concluidos, _ = wait(
            list(em_voo),
            timeout=None if bloqueante else restante,
            return_when=FIRST_COMPLETED if bloqueante else ALL_COMPLETED,
        )
        for fut in concluidos:
            contexto = em_voo.pop(fut)
            data, error = fut.result()
            outcome = enrichment_service.finish_enrichment(
                db, contexto["profile"], contexto["prepared"].domain,
                contexto["prepared"].lead, data, error,
            )
            _apply_outcome(db, contexto["job"], outcome)
            _contar(resumo, contexto["job"])
            processed += 1

    # Sem `with`: o context manager de ThreadPoolExecutor espera TODAS as
    # tasks na saída, o que anularia o orçamento de tempo se uma coleta
    # travar. O shutdown explícito no fim, com wait=False, devolve o controle
    # assim que o prazo acaba — o que sobrar continua rodando sozinho (rede
    # pura, sem sessão) e o job fica preso em `running` até `reclaim_stale`
    # da próxima rodada resgatar, se a coleta não terminar a tempo.
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        while True:
            if max_jobs is not None and processed + len(em_voo) >= max_jobs:
                break
            if deadline - time.monotonic() < _JOB_RESERVE_SECONDS:
                break

            if len(em_voo) >= workers:
                _drenar(bloqueante=True)
                continue

            job = _next_queued(db, user_id=user_id, batch_id=batch_id)
            if job is None:
                if not em_voo:
                    break
                _drenar(bloqueante=True)
                continue
            if not _claim(db, job):
                continue        # outra rodada pegou este; tenta o próximo

            if job.kind != "enrich":
                # Poucos por rodada (refresh de leads antigos) — sequencial.
                run_job(db, job)
                _contar(resumo, job)
                processed += 1
                continue

            iniciado = _start_enrich_job(db, pool, job)
            if iniciado is None:
                _contar(resumo, job)
                processed += 1
                continue
            future, contexto = iniciado
            em_voo[future] = contexto

        # Drena o que ainda está em voo, respeitando o que sobrou do
        # orçamento. Como um job só começa quando ainda resta pelo menos
        # `_JOB_RESERVE_SECONDS`, esta espera normalmente é suficiente para
        # ele terminar — é a mesma garantia que já valia na versão em série.
        if em_voo:
            _drenar(bloqueante=False, restante=max(0.0, deadline - time.monotonic()))
    finally:
        pool.shutdown(wait=False)

    resumo["processed"] = processed
    resumo["remaining"] = count_queued(db, user_id=user_id, batch_id=batch_id)
    resumo["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if processed:
        logger.info(
            "Rodada de jobs batch=%s user=%s processados=%d restantes=%d",
            batch_id, user_id, processed, resumo["remaining"],
        )
    return resumo


def count_queued(db: Session, user_id: Optional[str] = None,
                 batch_id: Optional[str] = None) -> int:
    query = db.query(Job).filter(Job.status.in_((STATUS_QUEUED, STATUS_RUNNING)))
    if user_id:
        query = query.filter(Job.user_id == user_id)
    if batch_id:
        query = query.filter(Job.batch_id == batch_id)
    return query.count()


# ── Consulta ─────────────────────────────────────────────────────────────────

def batch_progress(db: Session, batch_id: str, user_id: str) -> Optional[dict]:
    """Progresso do lote para a barra na tela. None se o lote não é do usuário."""
    jobs = (
        db.query(Job)
        .filter(Job.batch_id == batch_id, Job.user_id == user_id)
        .order_by(Job.id.asc())
        .all()
    )
    if not jobs:
        return None

    concluidos = [j for j in jobs if j.status in TERMINAL_STATUSES]
    return {
        "batch_id": batch_id,
        "total": len(jobs),
        "concluidos": len(concluidos),
        "na_fila": sum(1 for j in jobs if j.status == STATUS_QUEUED),
        "rodando": sum(1 for j in jobs if j.status == STATUS_RUNNING),
        "com_erro": sum(1 for j in jobs if j.status == STATUS_FAILED),
        "finalizado": len(concluidos) == len(jobs),
        "itens": [
            {
                "domain": (j.payload or {}).get("domain"),
                "status": j.status,
                "result": j.result,
                "lead_id": j.lead_id,
                "error": j.error,
            }
            for j in jobs
        ],
    }


def recent_batches(db: Session, user_id: str, limit: int = 5) -> List[dict]:
    """Últimos lotes do usuário, do mais novo para o mais antigo."""
    rows = (
        db.query(Job.batch_id, func.max(Job.id).label("ultimo_job"))
        .filter(Job.user_id == user_id, Job.batch_id.isnot(None))
        .group_by(Job.batch_id)
        .order_by(func.max(Job.id).desc())
        .limit(limit)
        .all()
    )
    out = []
    for batch_id, _ultimo in rows:
        progresso = batch_progress(db, batch_id, user_id)
        if progresso:
            progresso.pop("itens", None)
            out.append(progresso)
    return out
