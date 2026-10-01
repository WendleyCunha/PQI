import os
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import date, datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, field_validator
from typing import Optional, List

import db_diario as db

def _init_em_segundo_plano():
    try:
        db.init_dados_exemplo()
        print("[OK] Firestore conectado e dados de exemplo verificados.")
    except Exception as e:
        print(f"[AVISO] init_dados_exemplo falhou: {e!r}")


@asynccontextmanager
async def lifespan(_app):
    # [AJUSTADO v2.2] A conexão com o Firestore agora só é criada DEPOIS que
    # o servidor já subiu (e numa thread separada, pra não atrasar o health
    # check). Antes ela era criada no import do arquivo — e uma conexão gRPC
    # criada antes do servidor iniciar pode ficar travada para sempre.
    threading.Thread(target=_init_em_segundo_plano, daemon=True).start()
    yield


app = FastAPI(title="Diário de Bordo API - Firestore", version="2.2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# [NOVO v2.1] Rota de "estou vivo" exigida pelo Streamlit Cloud. Ele chama
# /healthz (e, em algumas versões, /_stcore/health) pra saber se o app subiu;
# sem estas rotas o FastAPI responde 404 e o Cloud derruba o app.
@app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/_stcore/health", methods=["GET", "HEAD"], include_in_schema=False)
def health():
    return PlainTextResponse("ok")


STATUS_VALIDOS = [
    "Planejamento", "Iniciar", "Em Andamento", "Aprovação", "Reprovado",
    "Concluído", "Bloqueado", "Pausado", "Cancelado",
]


# [NOVO v2.2] Diagnóstico: testa o Firestore e devolve o erro REAL na tela
# (em vez de ficar carregando). Abra /api/diag no navegador.
@app.get("/api/diag", include_in_schema=False)
def diagnostico():
    try:
        qtd = len(db.atividades_listar(excluido=False))
        return {"firestore": "ok", "atividades_ativas": qtd, "pid": os.getpid()}
    except Exception as e:
        return {"firestore": "ERRO", "erro": repr(e), "detalhe": traceback.format_exc()[-1500:]}


class AtividadeCreate(BaseModel):
    data: str
    responsavel: str
    area_impactada: str
    problema_causa: str
    demanda: str
    atividade: str
    prazo_previsto: str
    prioridade: str = "Baixa"
    indicador_relacionado: str
    volume: Optional[int] = 0
    bloqueio: Optional[str] = "—"
    valor_gerado: Optional[str] = "Produtividade"
    observacoes: Optional[str] = ""

    # Validação que faltava por completo na primeira versão — é o motivo
    # direto de existirem atividades sem nome no SQLite antigo. Os campos
    # obrigatórios do cadastro (seção "Regras do Cadastro de Atividades" da
    # especificação) agora são checados de verdade: string vazia ou só
    # espaço é rejeitada com erro 422.
    @field_validator(
        "responsavel", "area_impactada", "problema_causa", "demanda",
        "atividade", "prazo_previsto", "indicador_relacionado",
    )
    @classmethod
    def nao_pode_ser_vazio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("campo obrigatório não pode ficar em branco")
        return v.strip()


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def status_valido(cls, v: str) -> str:
        if v not in STATUS_VALIDOS:
            raise ValueError(f"status precisa ser um de: {', '.join(STATUS_VALIDOS)}")
        return v


def _dias_ate_prazo(prazo_iso: str) -> Optional[int]:
    try:
        d = datetime.strptime(prazo_iso, "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None


@app.get("/api/kpis")
def get_kpis():
    ativas_docs = db.atividades_listar(excluido=False)

    ativas = sum(1 for a in ativas_docs if a.get("status") not in ("Concluído", "Cancelado", "Reprovado"))
    bloqueios = sum(1 for a in ativas_docs if a.get("bloqueio", "—") != "—")

    vencimento = 0
    atrasados = 0
    for a in ativas_docs:
        if a.get("status") in ("Concluído", "Cancelado", "Reprovado"):
            continue
        dias = _dias_ate_prazo(a.get("prazo", ""))
        if dias is not None and dias <= 7:
            vencimento += 1
            if dias < 0:
                atrasados += 1

    return {
        "ativas": ativas,
        "vencimento": vencimento,
        "vencimento_atrasados": atrasados,
        "bloqueios": bloqueios,
        "valor_mes": "R$ 482 mil",
    }


@app.get("/api/atividades", response_model=List[dict])
def get_atividades(excluido: Optional[bool] = False):
    return db.atividades_listar(excluido=bool(excluido))


@app.post("/api/atividades")
def create_atividade(payload: AtividadeCreate):
    mes_val = payload.prazo_previsto.split("-")[1] if "-" in payload.prazo_previsto else "10"
    bloqueio_val = payload.bloqueio if payload.bloqueio else "—"

    novo_id = db.atividades_inserir({
        "nome": payload.atividade,
        "area": payload.area_impactada,
        "resp": payload.responsavel,
        "prio": payload.prioridade,
        "status": "Planejamento",
        "prazo": payload.prazo_previsto,
        "bloqueio": bloqueio_val,
        "mes": mes_val,
    })
    return {"sucesso": True, "mensagem": "Atividade salva com sucesso no Firestore!", "id": novo_id}


@app.put("/api/atividades/{atividade_id}/status")
def atualizar_status(atividade_id: str, payload: StatusUpdate):
    ok = db.atividades_status_atualizar(atividade_id, payload.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Atividade não encontrada (ou já excluída).")
    return {"sucesso": True}


@app.delete("/api/atividades/{atividade_id}")
def excluir_atividade(atividade_id: str):
    ok = db.atividades_excluir(atividade_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    return {"sucesso": True}


@app.get("/")
def servir_frontend():
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    # [AJUSTADO v2.1] Mensagem clara em vez de erro 500 genérico caso o
    # index.html não tenha sido enviado pro repositório.
    if not os.path.exists(caminho):
        raise HTTPException(status_code=404, detail="index.html não encontrado na pasta do main.py.")
    return FileResponse(caminho)


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=(porta == 8000))
