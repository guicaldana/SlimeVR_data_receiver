"""
Altura do pé pela cadeia de transformações das pernas (sem tronco).

Ideia: na calibração (parado, reto) guardamos a orientação de cada segmento
da perna (coxa e canela, IMUs do SolarXR) e o comprimento dele. A cada
instante, a rotação de cada segmento em relação à calibração é

    R(t) = q(t) · q_calib⁻¹

e o vetor do segmento é R(t) · (0, -L, 0): na calibração cada segmento é
considerado reto, apontando para baixo, com comprimento L.

A posição de um pé em relação ao outro sai somando os vetores pela cadeia
    pé ESQ → canela ESQ → coxa ESQ → quadril → coxa DIR → canela DIR → pé DIR
Tronco, cabeça e o tracker do quadril não entram: a pelve é considerada
nivelada (o tracker do quadril acompanha a inclinação do tronco e piorava a
medida nos testes). Como só usamos orientações, não há integração de
aceleração, e a taxa do SolarXR (~40 Hz) basta.

Saída principal: dif_vertical(t) = altura do pé DIR menos altura do pé ESQ (m),
zero na calibração. A altura de cada pé é a parte positiva dessa diferença,
igual ao método "diferença entre os pés" do OSC, mas sem passar pelo tronco.

A aceleração continua sendo usada só para o tempo dos passos (heel-strike),
no calculo_passo.py.
"""

import csv

import numpy as np

SEGMENTOS = ("LEFT_UPPER_LEG", "LEFT_LOWER_LEG", "RIGHT_UPPER_LEG", "RIGHT_LOWER_LEG")
EIXO_VERTICAL = 1  # SlimeVR usa Y para cima

# Comprimentos padrão (m) se não houver medida; só a proporção entre segmentos
# importa muito, porque a altura é a diferença entre as duas pernas.
COMPRIMENTOS_PADRAO = {
    "LEFT_UPPER_LEG": 0.45, "LEFT_LOWER_LEG": 0.50,
    "RIGHT_UPPER_LEG": 0.45, "RIGHT_LOWER_LEG": 0.50,
}


def _rotacionar(q, v):
    """Aplica quaternions q (N,4) em ordem x,y,z,w aos vetores v (N,3)."""
    q = np.atleast_2d(q)
    v = np.atleast_2d(v)
    u = q[:, :3]
    w = q[:, 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def _conj(q):
    return np.atleast_2d(q) * np.array([-1.0, -1.0, -1.0, 1.0])


def _media_quat(qs):
    """Média simples de quaternions próximos (alinha o sinal antes de somar)."""
    qs = np.atleast_2d(np.asarray(qs, dtype=float))
    ref = qs[0]
    sinais = np.sign(qs @ ref)
    sinais[sinais == 0] = 1.0
    m = (qs * sinais[:, None]).mean(axis=0)
    return m / np.linalg.norm(m)


class CadeiaPernas:
    """Reconstrução das pernas a partir da calibração."""

    def __init__(self, comprimentos=None):
        self.comprimentos = dict(COMPRIMENTOS_PADRAO)
        if comprimentos:
            self.comprimentos.update({k: float(v) for k, v in comprimentos.items() if v and v > 0})
        self.vetor_local = {}  # vetor do segmento no referencial do próprio sensor
        self.calibrado = False

    def calibrar(self, quats_calibracao):
        """quats_calibracao: {segmento: lista de quaternions (x,y,z,w) do período parado}."""
        for seg in SEGMENTOS:
            amostras = quats_calibracao.get(seg)
            if amostras is None or len(amostras) == 0:
                raise ValueError(f"Sem orientação de {seg} na calibração")
            q_cal = _media_quat(amostras)
            v_mundo = np.array([0.0, -self.comprimentos[seg], 0.0])
            # Vetor no referencial do sensor: assim v(t) = q(t) · v_local = q(t)·q_cal⁻¹·v_mundo
            self.vetor_local[seg] = _rotacionar(_conj(q_cal), v_mundo)[0]
        self.calibrado = True

    def vetor(self, seg, q):
        """Vetor do segmento no mundo para quaternions q (N,4)."""
        q = np.atleast_2d(q)
        return _rotacionar(q, np.tile(self.vetor_local[seg], (len(q), 1)))

    def dif_vertical(self, quats):
        """Altura do pé DIR menos a do pé ESQ (m), zero na calibração.

        quats: {segmento: quaternions (N,4)} todos com o mesmo N.
        """
        ev = EIXO_VERTICAL
        perna_esq = self.vetor("LEFT_UPPER_LEG", quats["LEFT_UPPER_LEG"])[:, ev] + \
            self.vetor("LEFT_LOWER_LEG", quats["LEFT_LOWER_LEG"])[:, ev]
        perna_dir = self.vetor("RIGHT_UPPER_LEG", quats["RIGHT_UPPER_LEG"])[:, ev] + \
            self.vetor("RIGHT_LOWER_LEG", quats["RIGHT_LOWER_LEG"])[:, ev]
        base = -(self.comprimentos["RIGHT_UPPER_LEG"] + self.comprimentos["RIGHT_LOWER_LEG"]) + \
            (self.comprimentos["LEFT_UPPER_LEG"] + self.comprimentos["LEFT_LOWER_LEG"])
        # pé DIR = quadril + perna_dir ; pé ESQ = quadril + perna_esq (pelve nivelada)
        return (perna_dir - perna_esq) - base

    @staticmethod
    def alturas_pes(dif):
        """Converte a diferença DIR-ESQ em altura de cada pé acima do outro (m)."""
        dif = np.asarray(dif, dtype=float)
        return np.maximum(0.0, -dif), np.maximum(0.0, dif)  # (ESQ, DIR)


def comprimentos_de_osc(pos_quadril, pos_joelho_e, pos_joelho_d, pos_pe_e, pos_pe_d):
    """Estima os comprimentos (m) a partir das posições OSC na calibração.

    Coxa: do centro do quadril (deslocado para o lado até a linha do joelho) ao joelho.
    Canela: do joelho ao pé.
    """
    pq = np.asarray(pos_quadril, dtype=float)
    je, jd = np.asarray(pos_joelho_e, dtype=float), np.asarray(pos_joelho_d, dtype=float)
    pe, pd = np.asarray(pos_pe_e, dtype=float), np.asarray(pos_pe_d, dtype=float)
    lat = jd - je
    lat[EIXO_VERTICAL] = 0.0
    w = float(np.linalg.norm(lat))
    u = lat / w if w > 0 else np.zeros(3)
    art_e = pq - u * w / 2.0
    art_d = pq + u * w / 2.0
    art_e[EIXO_VERTICAL] = art_d[EIXO_VERTICAL] = pq[EIXO_VERTICAL]
    return {
        "LEFT_UPPER_LEG": float(np.linalg.norm(je - art_e)),
        "LEFT_LOWER_LEG": float(np.linalg.norm(pe - je)),
        "RIGHT_UPPER_LEG": float(np.linalg.norm(jd - art_d)),
        "RIGHT_LOWER_LEG": float(np.linalg.norm(pd - jd)),
    }


def carregar_quats_solarxr(caminho_csv):
    """Lê os quaternions dos segmentos das pernas de um samples.csv do SolarXR.

    Retorna {segmento: (t_unix (N,), quats (N,4))}.
    """
    dados = {seg: ([], []) for seg in SEGMENTOS}
    with open(caminho_csv, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            seg = linha.get("body_part_name")
            if seg not in dados:
                continue
            try:
                q = (float(linha["rotation_x"]), float(linha["rotation_y"]),
                     float(linha["rotation_z"]), float(linha["rotation_w"]))
                t = float(linha["received_unix_ms"]) / 1000.0
            except (KeyError, ValueError):
                continue
            dados[seg][0].append(t)
            dados[seg][1].append(q)
    return {seg: (np.array(t), np.array(q)) for seg, (t, q) in dados.items() if len(t) > 0}


def quats_em(series, t):
    """Quaternions de cada segmento nos instantes t (amostra mais próxima anterior)."""
    saida = {}
    for seg, (ts, qs) in series.items():
        i = np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 1)
        saida[seg] = qs[i]
    return saida
