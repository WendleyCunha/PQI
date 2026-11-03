"""
Leitor SIMULADO — para testar o caminho inteiro (agente ⇄ PQI) sem o leitor físico.

Quando o PQI pede uma leitura, a janela do agente pergunta um "código de dedo"
(qualquer palavra, ex.: ana-indicador). A mesma palavra = o mesmo dedo.
Cadastre a pessoa com uma palavra e depois bata o ponto com a mesma palavra.

Um driver de verdade implementa os mesmos métodos com o SDK do fabricante
(ver leitores/MODELO_DRIVER.md).
"""
import hashlib
import queue
import sys
import threading


class Leitor:
    modelo = "Simulado"

    def __init__(self):
        self._fila = queue.Queue()

    def abrir(self):
        threading.Thread(target=self._ler_teclado, daemon=True).start()

    def _ler_teclado(self):
        for linha in sys.stdin:
            self._fila.put(linha.strip())

    def conectado(self) -> bool:
        return True

    def capturar(self, espera_seg: int):
        """Devolve (template_bytes, qualidade 0–100) ou None se ninguém encostou o dedo a tempo."""
        print("\n☝  Leitura pedida — digite o código do dedo e Enter:", flush=True)
        try:
            cod = self._fila.get(timeout=espera_seg)
        except queue.Empty:
            return None
        if not cod:
            return None
        return hashlib.sha256(f"simulado|{cod}".encode()).digest() * 2, 90

    def esperar_soltar(self):
        pass

    def comparar(self, a: bytes, b: bytes) -> int:
        """Pontuação de semelhança (0 = nada, quanto maior mais parecido)."""
        return 100 if a == b else 0

    def unir(self, leituras):
        """Várias leituras do mesmo dedo → um template. Aqui: exige que sejam iguais."""
        ts = {t for t, _ in leituras}
        if len(ts) != 1:
            raise ValueError("leituras diferentes")
        return leituras[0][0], min(q for _, q in leituras)
