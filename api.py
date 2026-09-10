# -*- coding: utf-8 -*-
"""
api.py — API FastAPI que expõe a raspagem da caixa de processos do SEI-MG.

Reutiliza 100% do app3.py (login, troca de unidade, travas de segurança,
auditoria e extração das páginas da caixa de processos). Nenhum processo
é aberto — apenas a listagem (whitelist + blacklist do app3 continuam ativas).

Endpoints:
    GET  /api/health                  -> status do serviço
    GET  /api/tarefas                 -> lista as tarefas (id + status)
    POST /api/raspar                  -> dispara a raspagem em background (Job)
    GET  /api/tarefas/{task_id}       -> status + dados em JSON da tarefa
    GET  /api/tarefas/{task_id}/excel -> download do .xlsx gerado em memória

Como rodar:
    venv/Scripts/python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
"""

import io
import threading
import uuid
from datetime import datetime, timezone

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import app3


# ==========================================
# 1. MODELOS DA API
# ==========================================
class RasparRequest(BaseModel):
    filtro: str = Field(
        default="1080",
        description="Prefixo do número a manter. Use 'todos' (ou vazio) para manter tudo.",
    )
    limite_paginas: int = Field(default=5, ge=1, le=20)
    atraso: float = Field(
        default=0.6, ge=0.0, le=5.0,
        description="Intervalo em segundos entre uma página e outra (0 = mais rápido).",
    )


# ==========================================
# 2. ESTADO DAS TAREFAS (em memória)
# ==========================================
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
FILA_LOCK = threading.Lock()  # garante apenas UMA raspagem por vez


def _normalizar_filtro(filtro: str):
    """Converte 'todos' para None (sem filtro). Prefixo livre vira prefixo."""
    if not filtro or filtro.strip().lower() in ("todos", "all", "*"):
        return None
    return filtro.strip()


def _df_dos_dados(dados, prefixo):
    """Converte os itens em DataFrame e aplica o filtro de prefixo, se houver."""
    if not dados:
        return None
    df = pd.DataFrame(dados)
    if prefixo:
        df = df[df["Processo"].astype(str).str.startswith(prefixo)].copy()
    return df

def _executar_raspagem(task_id, filtro, limite_paginas, atraso):
    """
    Roda em thread de background. Segura FILA_LOCK para que as raspagens
    aconteçam em série (um login/sessão por vez, sem sobrecarregar o SEI).
    """
    with FILA_LOCK:
        try:
            JOBS[task_id]["status"] = "executando"

            api = app3.APIExtratorSEI(app3.USUARIO, app3.SENHA)
            if not api.autenticar():
                JOBS[task_id]["status"] = "erro"
                JOBS[task_id]["erro"] = "Falha na autenticação no SEI. Verifique as credenciais."
                return

            api.trocar_unidade()
            dados = api.extrair_caixa_recebidos(
                limite_paginas=limite_paginas,
                atraso=atraso,
            )

            prefixo = _normalizar_filtro(filtro)
            df = _df_dos_dados(dados, prefixo)

            JOBS[task_id]["total_raspados"] = len(dados)
            JOBS[task_id]["total_filtrados"] = 0 if df is None else len(df)
            JOBS[task_id]["nao_visualizados"] = (
                0 if df is None or df.empty else int(df["NaoVisualizado"].sum())
            )

            # Dados em JSON (lida com NaN/None corretamente)
            if df is not None and not df.empty:
                JOBS[task_id]["dados"] = (
                    df.astype(object).where(pd.notna(df), None).to_dict("records")
                )

            # Gera o Excel em memória (BytesIO) para download instantâneo depois
            if df is not None and not df.empty:
                buffer = io.BytesIO()
                df.to_excel(buffer, index=False, engine="openpyxl")
                JOBS[task_id]["excel_bytes"] = buffer.getvalue()

            JOBS[task_id]["status"] = "concluida"
            JOBS[task_id]["fim"] = datetime.now(timezone.utc).isoformat()

        except SystemExit as exc:  # bloqueio de segurança do app3
            JOBS[task_id]["status"] = "erro"
            JOBS[task_id]["erro"] = str(exc)
        except Exception as exc:  # pragma: no cover - rede/parser etc.
            JOBS[task_id]["status"] = "erro"
            JOBS[task_id]["erro"] = f"{type(exc).__name__}: {exc}"


def _criar_job(filtro, limite_paginas, atraso):
    task_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[task_id] = {
            "id": task_id,
            "status": "pendente",
            "criacao": datetime.now(timezone.utc).isoformat(),
            "fim": None,
            "erro": None,
            "dados": [],
            "excel_bytes": None,
            "total_raspados": 0,
            "total_filtrados": 0,
            "nao_visualizados": 0,
        }
    thread = threading.Thread(
        target=_executar_raspagem,
        args=(task_id, filtro, limite_paginas, atraso),
        daemon=True,
    )
    thread.start()
    return task_id

# ==========================================
# 3. APLICAÇÃO FASTAPI
# ==========================================
app = FastAPI(
    title="API SEI — Caixa de Processos",
    description=(
        "Expõe a raspagem da caixa de processos do SEI-MG (somente a listagem; "
        "nenhum processo é aberto). As travas de segurança do app3.py continuam ativas."
    ),
    version="1.0.0",
)


@app.get("/api/health")
def health():
    with JOBS_LOCK:
        ativas = sum(1 for j in JOBS.values() if j["status"] in ("pendente", "executando"))
    return {
        "status": "ok",
        "tarefas_ativas": ativas,
        "servidor": app3.BASE_SEI,
        "unidade_alvo": app3.UNIDADE_ALVO,
    }


@app.get("/api/tarefas")
def listar_tarefas():
    with JOBS_LOCK:
        resumo = [
            {
                "id": j["id"],
                "status": j["status"],
                "criacao": j["criacao"],
                "fim": j["fim"],
                "total_filtrados": j["total_filtrados"],
            }
            for j in JOBS.values()
        ]
    return {"total": len(resumo), "tarefas": resumo}


@app.post("/api/raspar", status_code=202)
def raspar(req: RasparRequest):
    """Dispara a raspagem em background e retorna imediatamente o task_id."""
    task_id = _criar_job(req.filtro, req.limite_paginas, req.atraso)
    return {
        "task_id": task_id,
        "status": "pendente",
        "consulta": f"/api/tarefas/{task_id}",
        "excel": f"/api/tarefas/{task_id}/excel",
    }

@app.get("/api/tarefas/{task_id}")
def obter_tarefa(task_id: str, incluir_dados: bool = True):
    with JOBS_LOCK:
        job = JOBS.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")

    resposta = {k: v for k, v in job.items() if k != "excel_bytes"}
    if not incluir_dados:
        resposta.pop("dados", None)
    return resposta


@app.get("/api/tarefas/{task_id}/excel")
def baixar_excel(task_id: str):
    with JOBS_LOCK:
        job = JOBS.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    bytes_excel = job["excel_bytes"]

    if job["status"] != "concluida" or not bytes_excel:
        raise HTTPException(
            status_code=409,
            detail="Excel ainda não disponível. Aguarde a tarefa concluir "
                   "ou o filtro não retornou processos.",
        )

    nome = f"caixa_{task_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(bytes_excel),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)