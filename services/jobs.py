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
from typing import List, Optional

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from models.database import Job, Profile, utcnow
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

# Teto de domínios por lote. Protege a cota do usuário de um CSV colado por
# engano e o banco de um lote gigante enfileirado de uma vez.
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "200"))

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED)


# ── Enfileiramento ───────────────────────────────────────────────────────────

def normalize_domains(raw: List[str], limit: int = MAX_BATCH_SIZE) -> List[str]:
    """
    Limpa, deduplica e corta a lista na ordem em que o usuário informou.

    A ordem importa: quem colou 300 domínios e tem cota para 50 espera que os
    50 processados sejam os 50 primeiros da lista dele.
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


def _next_queued(db: Session, user_id: Optional[str] = None,
                 batch_id: Optional[str] = None) -> Optional[Job]:
    query = db.query(Job).filter(Job.status == STATUS_QUEUED)
    if user_id:
        query = query.filter(Job.user_id == user_id)
    if batch_id:
        query = query.filter(Job.batch_id == batch_id)
    return query.order_by(Job.id.asc()).first()


def run_job(db: Session, job: Job) -> str:
    """
    Executa um job já reservado e devolve o resultado
    (`enrichment_service.RESULT_*`). Não levanta exceção.
    """
    profile = db.query(Profile).filter(Profile.id == job.user_id).first()
    if not profile:
        job.status = STATUS_FAILED
        job.result = enrichment_service.RESULT_ERROR
        job.error = "perfil inexistente"
        job.finished_at = utcnow()
        db.commit()
        return enrichment_service.RESULT_ERROR

    domain = (job.payload or {}).get("domain", "")
    outcome = enrichment_service.enrich_for_user(db, profile, domain)

    job.result = outcome.result
    job.lead_id = outcome.lead.id if outcome.lead else None
    job.error = outcome.error

    if outcome.result == enrichment_service.RESULT_QUOTA:
        # Não é erro do domínio: o lote inteiro para aqui até haver cota. Volta
        # para a fila para que o usuário não perca a lista que montou.
        job.status = STATUS_QUEUED
        job.attempts = max(0, (job.attempts or 1) - 1)   # não gasta tentativa
        job.started_at = None
    elif outcome.result == enrichment_service.RESULT_ERROR and (job.attempts or 0) < MAX_ATTEMPTS:
        job.status = STATUS_QUEUED                        # falha transitória
        job.started_at = None
    elif outcome.result == enrichment_service.RESULT_ERROR:
        job.status = STATUS_FAILED
        job.finished_at = utcnow()
    else:
        job.status = STATUS_DONE
        job.finished_at = utcnow()

    db.commit()
    return outcome.result


def run_pending(db: Session, budget_seconds: float = ROUND_BUDGET_SECONDS,
                user_id: Optional[str] = None, batch_id: Optional[str] = None,
                max_jobs: Optional[int] = None) -> dict:
    """
    Processa jobs até acabar a fila, o orçamento de tempo ou a cota.

    Devolve o resumo da rodada — é o que a UI usa para decidir se chama outra.
    """
    started = time.monotonic()
    deadline = started + budget_seconds
    processed = 0
    resumo = {"processed": 0, "done": 0, "failed": 0, "quota_reached": False,
              "remaining": 0, "elapsed_ms": 0}

    while True:
        if max_jobs is not None and processed >= max_jobs:
            break
        if deadline - time.monotonic() < _JOB_RESERVE_SECONDS:
            break

        job = _next_queued(db, user_id=user_id, batch_id=batch_id)
        if job is None:
            break
        if not _claim(db, job):
            continue        # outra rodada pegou este; tenta o próximo

        result = run_job(db, job)
        processed += 1

        if result == enrichment_service.RESULT_QUOTA:
            resumo["quota_reached"] = True
            break
        if result == enrichment_service.RESULT_ERROR and job.status == STATUS_FAILED:
            resumo["failed"] += 1
        elif job.status == STATUS_DONE:
            resumo["done"] += 1

    resumo["processed"] = processed
    resumo["remaining"] = count_queued(db, user_id=user_id, batch_id=batch_id)
    resumo["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if processed:
        logger.info(
            "Rodada de jobs batch=%s user=%s processados=%d restantes=%d cota_esgotada=%s",
            batch_id, user_id, processed, resumo["remaining"], resumo["quota_reached"],
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
