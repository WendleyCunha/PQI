"""
KingPonto Agente — o programa que fica no computador da loja, ligado ao leitor USB.

O que ele faz
  • fala com o leitor de digital (pelo "driver" escolhido em config.json);
  • guarda na memória as digitais da empresa (recebidas CIFRADAS do PQI);
  • quando alguém encosta o dedo: identifica quem é (1 contra todos) e ASSINA
    o resultado com o segredo do terminal — o PQI só aceita batida assinada;
  • no cadastro: lê o dedo 3 vezes e devolve o template CIFRADO para o PQI.

Só escuta em 127.0.0.1 (o próprio computador) e só atende a página do PQI
configurada em "origem". Nenhuma imagem de digital é gravada em disco.

Uso
  python agente.py            → na 1ª vez pergunta o terminal e o segredo
  python agente.py --config   → refaz a configuração

Requisitos: Python 3.10+ e  pip install cryptography
"""
import base64
import hashlib
import hmac
import importlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSAO = "1.0"
PASTA = os.path.dirname(os.path.abspath(__file__))
ARQ_CFG = os.path.join(PASTA, "config.json")
PORTA = 7071


# ── configuração ──────────────────────────────────────────────────────────
def configurar() -> dict:
    print("\n=== KingPonto Agente — configuração ===")
    print("No PQI: Ponto → Cadastros → Terminais e leitores → Novo terminal.\n")
    cfg = ler_cfg() or {}
    cfg["terminal_id"] = input(f"Id do terminal [{cfg.get('terminal_id', '')}]: ").strip() or cfg.get("terminal_id", "")
    seg = input("Segredo do terminal (KP-...): ").strip()
    if seg:
        cfg["segredo"] = seg
    cfg["origem"] = input(f"Endereço do PQI [{cfg.get('origem', 'https://SEU-PQI.onrender.com')}]: ").strip() or cfg.get("origem", "")
    cfg["leitor"] = input(f"Driver do leitor (simulado | ...) [{cfg.get('leitor', 'simulado')}]: ").strip() or cfg.get("leitor", "simulado")
    cfg.setdefault("limiar", 40)
    with open(ARQ_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("Configuração salva em", ARQ_CFG)
    return cfg


def ler_cfg():
    try:
        with open(ARQ_CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def k_terminal(segredo: str, uso: str) -> bytes:
    """Igual ao servidor (ponto.py → _k_terminal)."""
    return hashlib.sha256(f"kingponto|{uso}|{segredo}".encode("utf-8")).digest()


def cifrar(chave: bytes, dados: bytes, aad: bytes) -> str:
    n = os.urandom(12)
    return base64.b64encode(n + AESGCM(chave).encrypt(n, dados, aad)).decode("ascii")


def decifrar(chave: bytes, texto: str, aad: bytes) -> bytes:
    b = base64.b64decode(texto)
    return AESGCM(chave).decrypt(b[:12], b[12:], aad)


# ── estado ────────────────────────────────────────────────────────────────
CFG: dict = {}
LEITOR = None
DIGITAIS: list = []            # [(pessoa, dedo, template_bytes)]
TRAVA_LEITOR = threading.Lock()


def carregar_leitor(nome: str):
    mod = importlib.import_module(f"leitores.{nome}")
    l = mod.Leitor()
    l.abrir()
    return l


# ── HTTP ──────────────────────────────────────────────────────────────────
class Atendente(BaseHTTPRequestHandler):
    server_version = "KingPontoAgente/" + VERSAO

    def log_message(self, fmt, *args):   # silencioso (sem dados pessoais no console)
        pass

    def _origem_ok(self) -> bool:
        o = self.headers.get("Origin")
        return o is None or o.rstrip("/") == CFG.get("origem", "").rstrip("/")

    def _cors(self):
        o = self.headers.get("Origin")
        if o and self._origem_ok():
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")   # Chrome: site público → 127.0.0.1

    def _json(self, obj, status=200):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204 if self._origem_ok() else 403)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._origem_ok():
            return self._json({"ok": False, "motivo": "origem não permitida"}, 403)
        if self.path.startswith("/status"):
            return self._json({"ok": LEITOR is not None and LEITOR.conectado(), "versao": VERSAO, "terminal_id": CFG.get("terminal_id"),
                               "modelo": getattr(LEITOR, "modelo", ""), "digitais": len(DIGITAIS)})
        self._json({"ok": False}, 404)

    def do_POST(self):
        if not self._origem_ok():
            return self._json({"ok": False, "motivo": "origem não permitida"}, 403)
        try:
            corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "motivo": "pedido inválido"}, 400)
        if corpo.get("terminal_id") and corpo["terminal_id"] != CFG.get("terminal_id"):
            return self._json({"ok": False, "motivo": "Este agente está configurado para outro terminal."}, 409)
        rota = {"/pacote": self.pacote, "/identificar": self.identificar, "/capturar": self.capturar}.get(self.path)
        if not rota:
            return self._json({"ok": False}, 404)
        try:
            return self._json(rota(corpo))
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "motivo": f"Erro no agente: {type(e).__name__}"}, 500)

    # recebe as digitais da empresa (cifradas com a chave DESTE terminal)
    def pacote(self, corpo):
        global DIGITAIS
        try:
            dados = json.loads(decifrar(k_terminal(CFG["segredo"], "pacote"), corpo["pacote"], CFG["terminal_id"].encode()))
        except Exception:
            return {"ok": False, "motivo": "Não consegui abrir as digitais: o segredo do agente não é o deste terminal. Refaça a configuração."}
        DIGITAIS = [(i["p"], i["d"], base64.b64decode(i["t"])) for i in dados.get("itens", [])]
        return {"ok": True, "qtd": len(DIGITAIS)}

    # 1 contra todos + assinatura
    def identificar(self, corpo):
        espera = min(60, int(corpo.get("espera_seg") or 25))
        with TRAVA_LEITOR:
            lido = LEITOR.capturar(espera)
        if not lido:
            return {"ok": False, "motivo": "tempo"}
        template, _q = lido
        melhor, score = None, 0
        for pessoa, _dedo, t in DIGITAIS:
            s = LEITOR.comparar(template, t)
            if s > score:
                melhor, score = pessoa, s
        if not melhor or score < int(CFG.get("limiar", 40)):
            return {"ok": False, "motivo": "nao_reconhecido"}
        momento = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        msg = f"{corpo.get('desafio', '')}|{melhor}|{int(score)}|{momento}".encode("utf-8")
        assin = hmac.new(k_terminal(CFG["segredo"], "assina"), msg, hashlib.sha256).hexdigest()
        return {"ok": True, "pessoa": melhor, "score": int(score), "momento_aparelho": momento, "assinatura": assin}

    # cadastro: N leituras do mesmo dedo → template cifrado
    def capturar(self, corpo):
        n = max(1, min(5, int(corpo.get("leituras") or 3)))
        leituras = []
        with TRAVA_LEITOR:
            for _ in range(n):
                lido = LEITOR.capturar(20)
                if not lido:
                    return {"ok": False, "motivo": "O dedo não foi lido a tempo. Tente de novo."}
                leituras.append(lido)
                LEITOR.esperar_soltar()
        template, qualidade = LEITOR.unir(leituras)
        if qualidade < int(CFG.get("qualidade_minima", 0)):
            return {"ok": False, "motivo": f"Qualidade baixa ({qualidade}). Limpe o leitor e o dedo e tente de novo."}
        return {"ok": True, "qualidade": qualidade, "pacote": cifrar(k_terminal(CFG["segredo"], "captura"), template, b"captura")}


def main():
    global CFG, LEITOR
    sys.path.insert(0, PASTA)
    CFG = configurar() if "--config" in sys.argv or not ler_cfg() else ler_cfg()
    if not (CFG.get("terminal_id") and CFG.get("segredo") and CFG.get("origem")):
        CFG = configurar()
    LEITOR = carregar_leitor(CFG.get("leitor", "simulado"))
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Atendente)
    print(f"KingPonto Agente {VERSAO} · terminal {CFG['terminal_id']} · leitor {LEITOR.modelo}")
    print(f"Aguardando a página do PQI ({CFG['origem']}) em http://127.0.0.1:{PORTA}  — deixe esta janela aberta.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
