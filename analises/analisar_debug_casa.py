"""
TEMPORÁRIO — analisa a gravação do teste_altura_casa.py (dados/debug_casa/<data_hora>/amostras.csv).

Separa os trechos parados (dois pés no chão, pé E em cima, pé D em cima, pela altura medida)
e mostra, em cada um, a mediana de:
  - quanto cada pé, cada joelho e a pelve se moveram na vertical (OSC, cm);
  - o ângulo da coxa e da canela em relação à vertical, pelas posições do SlimeVR (OSC);
  - o mesmo ângulo pelas rotações das IMUs (SolarXR), relativo ao primeiro trecho no chão;
  - os comprimentos de coxa e canela que o SlimeVR está usando (OSC, cm).

    uv run python analises/analisar_debug_casa.py            # gravação mais recente
    uv run python analises/analisar_debug_casa.py CAMINHO/amostras.csv
"""
import csv
import glob
import os
import sys

import warnings

import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cadeia_pernas as cp  # noqa: E402

arq = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(os.path.join("dados", "debug_casa", "*", "amostras.csv")))[-1]
print("Arquivo:", arq)
with open(arq, encoding="utf-8") as f:
    linhas = list(csv.DictReader(f))


def col(nome):
    return np.array([float(l[nome]) if l.get(nome) not in (None, "", "nan") else np.nan for l in linhas])


def pos(k):
    return np.column_stack([col(f"osc{k}_{e}") for e in "xyz"])


def ang_vert(v):
    """Ângulo (graus) entre o vetor e a vertical para baixo."""
    n = np.linalg.norm(v, axis=1)
    return np.degrees(np.arccos(np.clip(-v[:, 1] / n, -1, 1)))


ae, ad = np.nan_to_num(col("altura_E_cm")), np.nan_to_num(col("altura_D_cm"))
estado = np.where(ad > 5, "pe_D_em_cima", np.where(ae > 5, "pe_E_em_cima",
                  np.where((ae < 1) & (ad < 1), "chao", "transicao")))
marca = np.array([l.get("marca", "") for l in linhas])

hip, pe_e, pe_d, jo_e, jo_d = pos(1), pos(2), pos(3), pos(4), pos(5)
lat = jo_d - jo_e
lat[:, 1] = 0
w = np.linalg.norm(lat, axis=1, keepdims=True)
u = lat / np.where(w > 0, w, 1)
art_e, art_d = hip - u * w / 2, hip + u * w / 2
art_e[:, 1] = art_d[:, 1] = hip[:, 1]

s = {
    "altura E (cm)": ae, "altura D (cm)": ad,
    "sobe pé E (cm)": pe_e[:, 1] * 100, "sobe pé D (cm)": pe_d[:, 1] * 100,
    "sobe joelho E (cm)": jo_e[:, 1] * 100, "sobe joelho D (cm)": jo_d[:, 1] * 100,
    "sobe pelve (cm)": hip[:, 1] * 100,
    "coxa E SlimeVR (°)": ang_vert(jo_e - art_e), "coxa D SlimeVR (°)": ang_vert(jo_d - art_d),
    "canela E SlimeVR (°)": ang_vert(pe_e - jo_e), "canela D SlimeVR (°)": ang_vert(pe_d - jo_d),
    "comp. coxa E (cm)": np.linalg.norm(jo_e - art_e, axis=1) * 100,
    "comp. coxa D (cm)": np.linalg.norm(jo_d - art_d, axis=1) * 100,
    "comp. canela E (cm)": np.linalg.norm(pe_e - jo_e, axis=1) * 100,
    "comp. canela D (cm)": np.linalg.norm(pe_d - jo_d, axis=1) * 100,
    "tronco frente (°)": col("tronco_frente_graus"), "tronco lado (°)": col("tronco_lado_graus"),
}

# blocos parados de pelo menos 2 s (10 amostras a 5 Hz)
blocos, ini = [], 0
for i in range(1, len(estado) + 1):
    if i == len(estado) or estado[i] != estado[ini]:
        if estado[ini] != "transicao" and i - ini >= 10:
            blocos.append((estado[ini], ini, i))
        ini = i
if not blocos:
    sys.exit("Nenhum trecho parado de 2 s ou mais.")
ref0, ref1 = next(((a, b) for e, a, b in blocos if e == "chao"), (blocos[0][1], blocos[0][2]))

nomes_seg = {"LEFT_UPPER_LEG": "coxa E IMU (°)", "LEFT_LOWER_LEG": "canela E IMU (°)",
             "RIGHT_UPPER_LEG": "coxa D IMU (°)", "RIGHT_LOWER_LEG": "canela D IMU (°)"}
for seg, nome in nomes_seg.items():
    q = np.column_stack([col(f"{seg}_q{c}") for c in "xyzw"])
    ok = ~np.isnan(q).any(axis=1)
    if ok[ref0:ref1].sum() < 3:
        continue
    qc = cp._media_quat(q[ref0:ref1][ok[ref0:ref1]])
    vloc = cp._rotacionar(cp._conj(qc), np.array([[0, -1.0, 0]]))[0]
    v = np.full((len(q), 3), np.nan)
    v[ok] = cp._rotacionar(q[ok], np.tile(vloc, (ok.sum(), 1)))
    s[nome] = ang_vert(v)

# deslocamentos verticais relativos ao primeiro trecho no chão
for k in [k for k in s if k.startswith("sobe")]:
    s[k] = s[k] - np.nanmedian(s[k][ref0:ref1])

cab = [f"{e}#{n}" for n, (e, a, b) in enumerate(blocos, 1)]
print(f"\n{'':24s}" + "".join(f"{c:>15s}" for c in cab))
print(f"{'marca (tecla)':24s}" + "".join(f"{(marca[a:b][len(marca[a:b]) // 2] or '-'):>15s}" for e, a, b in blocos))
print(f"{'duração (s)':24s}" + "".join(f"{(b - a) / 5:15.1f}" for e, a, b in blocos))
for k, v in s.items():
    print(f"{k:24s}" + "".join(f"{np.nanmedian(v[a:b]):15.1f}" for e, a, b in blocos))
