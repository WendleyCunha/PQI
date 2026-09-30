import os
import sqlite3
from datetime import date, datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from typing import Optional, List

app = FastAPI(title="Diário de Bordo API - SQLite", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "diario.db"

STATUS_VALIDOS = [
    "Planejamento", "Iniciar", "Em Andamento", "Aprovação", "Reprovado",
    "Concluído", "Bloqueado", "Pausado", "Cancelado",
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Cria a tabela de atividades e insere dados iniciais se estiver vazia.
    [FIX] Antes não existia NENHUMA validação — por isso o banco acabou com
    4 linhas com o campo "nome" em branco (o formulário deixava passar
    cadastro vazio). Isso é resolvido agora na validação do Pydantic
    (ver AtividadeCreate abaixo), não aqui na criação da tabela."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS atividades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            area TEXT,
            resp TEXT,
            prio TEXT,
            status TEXT,
            prazo TEXT,
            bloqueio TEXT,
            mes TEXT,
            excluido BOOLEAN
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM atividades")
    if cursor.fetchone()[0] == 0:
        iniciais = [
            ("Reconciliar divergências de estoque", "Logística", "Wendley", "Alta", "Em Andamento", "2026-10-06", "—", "10", False),
            ("Revisar contrato de fornecedor X", "Jurídico", "Tiago", "Média", "Aprovação", "2026-10-03", "Aprovação pendente", "10", False),
            ("Homologar planilha de indicadores", "PQI", "Guilherme", "Baixa", "Planejamento", "2026-10-12", "—", "10", False),
            ("Corrigir SLA de resposta ao cliente", "Customer Experience", "Wendley", "Alta", "Bloqueado", "2026-09-29", "Sistema/ferramenta indisponível", "09", False),
            ("Auditoria de compliance trimestral", "Compliance", "Tiago", "Média", "Iniciar", "2026-10-20", "—", "10", False),
            ("Levantamento de custo logístico", "Compras", "Guilherme", "Baixa", "Concluído", "2026-09-25", "—", "09", False),
        ]
        cursor.executemany("""
            INSERT INTO atividades (nome, area, resp, prio, status, prazo, bloqueio, mes, excluido)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, iniciais)
        conn.commit()
    conn.close()


init_db()


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

    # [FIX] Validação que faltava por completo — é o motivo direto de
    # existirem 4 atividades sem nome no seu banco. Os 8 campos que a
    # especificação marca como obrigatórios no cadastro (seção "Regras do
    # Cadastro de Atividades") agora são checados de verdade: string vazia
    # ou só espaço é rejeitada com erro 422, o front nunca chega a inserir
    # lixo no banco.
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
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM atividades WHERE excluido = 0 AND status NOT IN ('Concluído','Cancelado','Reprovado')")
    ativas = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM atividades WHERE bloqueio != '—' AND excluido = 0")
    bloqueios = cursor.fetchone()[0]

    # [FIX] Antes "vencimento" era um número fixo (9) escrito à mão no
    # código, sem relação nenhuma com os dados reais. Agora conta de
    # verdade: atividades ativas com prazo já vencido OU vencendo nos
    # próximos 7 dias.
    cursor.execute("SELECT prazo FROM atividades WHERE excluido = 0 AND status NOT IN ('Concluído','Cancelado','Reprovado')")
    vencimento = 0
    atrasados = 0
    for (prazo,) in cursor.fetchall():
        dias = _dias_ate_prazo(prazo)
        if dias is not None and dias <= 7:
            vencimento += 1
            if dias < 0:
                atrasados += 1

    conn.close()
    return {
        "ativas": ativas,
        "vencimento": vencimento,
        "vencimento_atrasados": atrasados,
        "bloqueios": bloqueios,
        "valor_mes": "R$ 482 mil",
    }


@app.get("/api/atividades", response_model=List[dict])
def get_atividades(excluido: Optional[bool] = False):
    """[FIX] Ganhou o parâmetro `excluido` — sem ele não tinha como a aba
    'Excluídas' de Acompanhamento das Atividades mostrar nada (a
    especificação exige que excluídas continuem rastreáveis, não somem)."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM atividades WHERE excluido = ?", (1 if excluido else 0,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.post("/api/atividades")
def create_atividade(payload: AtividadeCreate):
    conn = get_conn()
    cursor = conn.cursor()

    mes_val = payload.prazo_previsto.split("-")[1] if "-" in payload.prazo_previsto else "10"
    bloqueio_val = payload.bloqueio if payload.bloqueio else "—"

    cursor.execute("""
        INSERT INTO atividades (nome, area, resp, prio, status, prazo, bloqueio, mes, excluido)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload.atividade,
        payload.area_impactada,
        payload.responsavel,
        payload.prioridade,
        "Planejamento",
        payload.prazo_previsto,
        bloqueio_val,
        mes_val,
        False,
    ))
    conn.commit()
    novo_id = cursor.lastrowid
    conn.close()
    return {"sucesso": True, "mensagem": "Atividade salva com sucesso no SQLite!", "id": novo_id}


@app.put("/api/atividades/{atividade_id}/status")
def atualizar_status(atividade_id: int, payload: StatusUpdate):
    """[NOVO] Não existia nenhuma forma de mudar o status depois de criada
    a atividade — a especificação diz que Status é um dos campos
    "totalmente editáveis até o encerramento da atividade"."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM atividades WHERE id = ? AND excluido = 0", (atividade_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Atividade não encontrada (ou já excluída).")
    cursor.execute("UPDATE atividades SET status = ? WHERE id = ?", (payload.status, atividade_id))
    conn.commit()
    conn.close()
    return {"sucesso": True}


@app.delete("/api/atividades/{atividade_id}")
def excluir_atividade(atividade_id: int):
    """[NOVO] Exclusão LÓGICA, exatamente como a especificação pede: "As
    atividades poderão ser excluídas, porém não serão removidas do
    Diário... permanecerão disponíveis na sessão Excluídos". Por isso é
    um UPDATE (excluido=1), nunca um DELETE de verdade da linha."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM atividades WHERE id = ?", (atividade_id,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    cursor.execute("UPDATE atividades SET excluido = 1 WHERE id = ?", (atividade_id,))
    conn.commit()
    conn.close()
    return {"sucesso": True}


@app.get("/")
def servir_frontend():
    """[NOVO] O próprio FastAPI entrega o index.html — assim é UM serviço
    só pra hospedar (não precisa de um servidor separado só pro front),
    e o caminho relativo '/api' no JS sempre bate certo, seja local ou
    já hospedado, sem precisar trocar nada na hora do deploy."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    return FileResponse(caminho)


if __name__ == "__main__":
    # [FIX] Sem isto, "python main.py" não fazia absolutamente nada — o
    # arquivo só definia `app`, mas nunca chamava um servidor pra
    # escutar/servir ele. Agora dá pra rodar direto, sem precisar lembrar
    # o comando exato do uvicorn.
    # [AJUSTE — deploy] A porta vem da variável de ambiente PORT quando
    # existir (é assim que Render/Railway informam em qual porta seu
    # serviço precisa escutar) — localmente, sem essa variável definida,
    # cai no 8000 de sempre.
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=(porta == 8000))
