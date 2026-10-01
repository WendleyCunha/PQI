import os
import re
import json
import urllib.request
import urllib.error
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import date, datetime
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, field_validator
from typing import Optional, List, Any

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


app = FastAPI(title="Diário de Bordo API - Firestore", version="2.4", lifespan=lifespan)

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
    evidencia: Optional[str] = ""          # [VOLTOU v2.3] o front envia e a tela usa
    prazo_entregue: Optional[str] = None   # [VOLTOU v2.3]

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


class AcaoCreate(BaseModel):
    descricao: str
    lembrete: Optional[str] = None  # data opcional (YYYY-MM-DD)

    @field_validator("descricao")
    @classmethod
    def nao_vazio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("descrição da ação não pode ficar em branco")
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
        # [VOLTOU v2.3] campos que a versão SQLite gravava e a tela usa
        # (indicadores, governança, tabela de acompanhamento) — na
        # primeira versão Firestore eles tinham ficado de fora.
        "indicador": payload.indicador_relacionado,
        "volume": payload.volume or 0,
        "valor_gerado": payload.valor_gerado,
        "observacoes": payload.observacoes,
        "evidencia": payload.evidencia,
        "prazo_entregue": payload.prazo_entregue,
        "problema_causa": payload.problema_causa,
        "demanda": payload.demanda,
        "data_cadastro": payload.data,
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


# ──────────────────────────────────────────────────────────────────────────
# [VOLTOU v2.3] AÇÕES dentro de uma atividade + RELATÓRIO de ações
# ──────────────────────────────────────────────────────────────────────────
@app.get("/api/atividades/{atividade_id}/acoes", response_model=List[dict])
def listar_acoes(atividade_id: str):
    return db.acoes_listar(atividade_id)


@app.post("/api/atividades/{atividade_id}/acoes")
def criar_acao(atividade_id: str, payload: AcaoCreate):
    if not db.atividade_existe(atividade_id):
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    novo_id = db.acoes_inserir(atividade_id, payload.descricao, payload.lembrete)
    return {"sucesso": True, "id": novo_id}


@app.delete("/api/acoes/{acao_id}")
def excluir_acao(acao_id: str):
    if not db.acoes_excluir(acao_id):
        raise HTTPException(status_code=404, detail="Ação não encontrada.")
    return {"sucesso": True}


@app.put("/api/acoes/{acao_id}/encerrar-lembrete")
def encerrar_lembrete(acao_id: str):
    if not db.acoes_encerrar_lembrete(acao_id):
        raise HTTPException(status_code=404, detail="Ação não encontrada.")
    return {"sucesso": True}


@app.get("/api/acoes", response_model=List[dict])
def listar_todas_acoes():
    return db.acoes_listar_todas()


# ──────────────────────────────────────────────────────────────────────────
# [VOLTOU v2.3] UPLOAD de evidência — agora salvo no Firestore (o disco do
# Render Free é apagado a cada reinício). Limite ~900 KB por arquivo.
# ──────────────────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def fazer_upload(arquivo: UploadFile = File(...)):
    conteudo = await arquivo.read()
    if len(conteudo) > db.LIMITE_ARQUIVO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(f"Arquivo de {len(conteudo)//1024} KB passa do limite de "
                    f"{db.LIMITE_ARQUIVO_BYTES//1024} KB. Use o modo Link (Drive/OneDrive)."),
        )
    novo_id = db.arquivos_salvar(arquivo.filename or "arquivo", arquivo.content_type, conteudo)
    return {"sucesso": True, "url": f"/api/arquivos/{novo_id}", "nome_original": arquivo.filename}


@app.get("/api/arquivos/{arquivo_id}", include_in_schema=False)
def baixar_arquivo(arquivo_id: str):
    dados = db.arquivos_ler(arquivo_id)
    if not dados:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    nome = (dados.get("nome") or "arquivo").replace('"', "")
    return Response(
        content=bytes(dados.get("conteudo") or b""),
        media_type=dados.get("tipo") or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{nome}"'},
    )


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v2.4] DIAGNÓSTICO N2 — API GENÉRICA
# 3 rotas que guardam/devolvem QUALQUER JSON por seção. Não tem nenhum
# nome de campo aqui de propósito: campos e seções novas se resolvem só
# no diagnostico.html, sem precisar mexer neste arquivo.
# ──────────────────────────────────────────────────────────────────────────
_SECAO_VALIDA = re.compile(r"^[a-z0-9_]{1,60}$")
_LIMITE_SECAO_BYTES = 950 * 1024  # Firestore aceita até 1 MB por documento


class SecaoDiagnostico(BaseModel):
    dados: Any = None


def _validar_secao(secao: str) -> str:
    if not _SECAO_VALIDA.match(secao or ""):
        raise HTTPException(status_code=400, detail="Nome de seção inválido (use só letras minúsculas, números e _).")
    return secao


@app.get("/api/diagnostico")
def diagnostico_tudo():
    """Todas as seções de uma vez — o HTML carrega isso ao abrir."""
    return db.diag_ler_todos()


@app.get("/api/diagnostico/{secao}")
def diagnostico_ler(secao: str):
    return {"secao": secao, "dados": db.diag_ler(_validar_secao(secao))}


@app.put("/api/diagnostico/{secao}")
def diagnostico_salvar(secao: str, payload: SecaoDiagnostico):
    _validar_secao(secao)
    tamanho = len(json.dumps(payload.dados, ensure_ascii=False, default=str).encode("utf-8"))
    if tamanho > _LIMITE_SECAO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"A seção '{secao}' ficou com {tamanho // 1024} KB e passa do limite do Firestore (~1 MB por documento).",
        )
    db.diag_salvar(secao, payload.dados)
    return {"sucesso": True}


@app.post("/api/diagnostico-ia")
def diagnostico_resumo_ia():
    """Resumo executivo com a API da Anthropic. A chave fica SÓ no servidor
    (variável de ambiente ANTHROPIC_API_KEY no Render), nunca no HTML."""
    chave = os.environ.get("ANTHROPIC_API_KEY", "")
    if not chave:
        return {"erro": "Configure a variável ANTHROPIC_API_KEY no Render (Environment) para usar esta função."}

    todos = db.diag_ler_todos()
    secoes = ["inventario", "organograma", "raci", "raci_pessoas", "raci_matriz", "diario",
              "entrevistas", "gemba", "matriz", "jornadas", "respostas"]
    dados = {s: todos.get(s) for s in secoes}
    prompt = (
        "Você está ajudando a consolidar um mapeamento de atividades (estilo Lean/Gemba) de uma "
        "equipe de backoffice N2. Abaixo estão os dados brutos coletados, em JSON. Escreva um RESUMO "
        "EXECUTIVO em português, objetivo e assertivo, cobrindo nesta ordem: 1) o que a equipe faz de "
        "fato; 2) como o trabalho é dividido; 3) de onde vêm as demandas; 4) onde o tempo é mais "
        "consumido; 5) principais dependências externas; 6) trabalho invisível identificado; e termine "
        "com 3 a 5 recomendações práticas priorizadas. Não invente dados que não estejam no JSON — "
        "se uma seção estiver vazia, apenas mencione que precisa de mais coleta ali.\n\n"
        f"DADOS COLETADOS (JSON):\n{json.dumps(dados, ensure_ascii=False, default=str)}"
    )
    corpo = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=corpo, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": chave, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resposta = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", f"HTTP {e.code}")
        except Exception:
            msg = f"HTTP {e.code}"
        return {"erro": f"Erro da API Anthropic: {msg}"}
    except Exception as e:
        return {"erro": f"Erro de conexão com a API: {e}"}

    texto = "".join(b.get("text", "") for b in resposta.get("content", []) if b.get("type") == "text")
    return {"texto": texto}


# ──────────────────────────────────────────────────────────────────────────
# [AJUSTADO v2.3] PÁGINAS HTML — o mesmo servidor agora entrega o Painel
# (túnel) e todos os sistemas que são arquivo HTML.
#
# É uma LISTA FIXA de propósito: só estes arquivos podem ser abertos pelo
# navegador. Nunca "abrir a pasta inteira" — senão qualquer pessoa poderia
# baixar o main.py, o db_diario.py ou um firestore_key.json esquecido.
#
# Pra adicionar um sistema novo em HTML: suba o arquivo no repositório e
# acrescente uma linha aqui.
# ──────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PAGINAS = {
    "/": "index.html",                                                   # Painel de Sistemas (túnel)
    "/diario": "diario.html",                                            # Diário de Bordo
    "/rg-pedido-acompanhamento.html": "rg-pedido-acompanhamento.html",   # RG do Pedido
    "/diagnostico": "diagnostico.html",                                  # Diagnóstico N2
}


def _criar_rota_pagina(nome_arquivo: str):
    def servir():
        caminho = os.path.join(BASE_DIR, nome_arquivo)
        if not os.path.exists(caminho):
            raise HTTPException(
                status_code=404,
                detail=f"{nome_arquivo} não encontrado no repositório (mesma pasta do main.py).",
            )
        # no-cache: depois de um commit novo, o navegador já pega a versão nova
        return FileResponse(caminho, media_type="text/html", headers={"Cache-Control": "no-cache"})
    return servir


for _rota, _arquivo in PAGINAS.items():
    app.add_api_route(_rota, _criar_rota_pagina(_arquivo), methods=["GET", "HEAD"], include_in_schema=False)


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=(porta == 8000))
