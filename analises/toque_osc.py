"""
Toque do pé pela altura do pé (OSC) x pico de aceleração da IMU, numa caminhada salva.

O teste da batida (11/11) mostrou que OSC e IMU chegam juntos (±20 ms). Então a altura do pé
no OSC serve de referência para o instante do toque. Para cada pousada de cada pé:
  * toque OSC = depois do ponto mais alto do balanço, o instante em que o pé freia a descida
    (maior aceleração para cima);
  * pico de aceleração da IMU daquele pé mais próximo (e o seu valor em m/s²);
  * passo marcado pelo sistema (pegadas.csv, se houver) e o instante da distância máxima entre os pés.
Tempos positivos = depois do toque OSC.

    uv run python analises/toque_osc.py dados/debug_casa/<pasta>
"""
import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calculo_passo as cp          # noqa: E402

TAXA = 100.0
SUBIDA_MIN_CM = 2.0      # balanço com pelo menos isso de altura conta como passo
PE_TRACKER = {"ESQ": cp.TORNOZELO_ESQ, "DIR": cp.TORNOZELO_DIR}


def carregar(pasta):
    with open(os.path.join(pasta, "dados_brutos_entrada.json"), encoding="utf-8") as f:
        e = json.load(f)
    for tid, d in e["trackers"].items():
        if d:
            n = min(len(v) for v in d.values())
            cp.dados[tid] = {k: list(v)[:n] for k, v in d.items()}
    for lado, d in e["imu"].items():
        n = min(len(v) for v in d.values())
        cp.dados_imu[lado] = {k: list(v)[:n] for k, v in d.items()}
    c = e.get("calibracao") or {}
    for k in ("dist_qp_e_ref", "dist_qp_d_ref", "yq_ref", "chao_esq_ref", "chao_dir_ref",
              "limiar_ruido_chao", "pitch_tronco_ref"):
        if c.get(k) is not None:
            setattr(cp, k, c[k])
    cp.calibrado = True
    cp.cadeia = None
    cp.fases_sessao[:] = [tuple(f) for f in (e.get("fases") or [])]


def _grade(t, v, tg):
    t, v = np.asarray(t, float), np.asarray(v, float)
    t, i = np.unique(t, return_index=True)
    return np.interp(tg, t, v[i])


def trecho_caminhada():
    for nome, t0, t1 in cp.fases_sessao:
        if "calibracao_passo" in nome or "teste_passo" in nome or "caminhada" in nome:
            return float(t0), float(t1)
    t = cp.dados[cp.TORNOZELO_ESQ]["t"]
    return float(t[0]), float(t[-1])


def toques_osc(lado, t0, t1):
    from scipy.signal import find_peaks
    tg = np.arange(t0, t1, 1.0 / TAXA)
    p = cp.dados[PE_TRACKER[lado]]
    y = cp.aplicar_filtro(_grade(p["t"], p["y"], tg), 10.0, TAXA) * 100.0
    vy = np.gradient(y, tg)
    ay = np.gradient(vy, tg)
    chao = float(np.percentile(y, 10))
    topos, _ = find_peaks(y, prominence=SUBIDA_MIN_CM, distance=int(0.4 * TAXA))
    out = []
    for k in topos:
        # descida: do topo até o pé parar (velocidade volta a ~0) ou 0,8 s
        fim = min(len(tg) - 1, k + int(0.8 * TAXA))
        j = np.arange(k, fim + 1)
        i_fre = j[np.argmax(ay[j])]
        out.append({"t_topo": float(tg[k]), "t_toque": float(tg[i_fre]),
                    "subida_cm": float(y[k] - chao)})
    return out, tg


def picos_imu(lado, tg):
    from scipy.signal import find_peaks
    imu = cp.dados_imu[lado]
    acc = cp.aplicar_filtro(_grade(imu["t"], imu["acc"], tg), 12.0, TAXA)
    pk, _ = find_peaks(acc, distance=int(0.2 * TAXA))
    return tg[pk], acc[pk], acc


def ler_pegadas(pasta):
    arq = os.path.join(pasta, "pegadas.csv")
    if not os.path.exists(arq):
        return []
    with open(arq, encoding="utf-8-sig") as f:
        return [(float(r["t_s"]), r["pe_da_frente"]) for r in csv.DictReader(f, delimiter=";")]


def _med(v):
    v = np.array([x for x in v if x is not None and np.isfinite(x)], float)
    if len(v) == 0:
        return "sem valores"
    return f"mediana {np.median(v):+5.0f} ms (de {v.min():+.0f} a {v.max():+.0f}; n={len(v)})"


def main(pasta):
    carregar(pasta)
    t0, t1 = trecho_caminhada()
    pegadas = ler_pegadas(pasta)
    platos = []
    try:
        s = cp.calcular_sinais_completos(cp.dados[cp.TORNOZELO_ESQ], cp.dados[cp.TORNOZELO_DIR],
                                         cp.dados[cp.TRACKER_QUADRIL], cp.dados[cp.TRACKER_COXA_ESQ],
                                         cp.dados[cp.TRACKER_COXA_DIR])
        platos = cp.platos_anteroposteriores(np.asarray(s[0]), np.asarray(s[1]))
    except Exception as e:
        print(f"(sem platôs: {e})")
    linhas = [f"TOQUE PELA ALTURA DO PÉ (OSC) x PICO DE ACELERAÇÃO (IMU) — {pasta}",
              f"Trecho {t0:.1f}–{t1:.1f} s. Tempos em ms em relação ao toque OSC (positivo = depois).", ""]
    resumo = {}
    for lado, letra in (("ESQ", "E"), ("DIR", "D")):
        toques, tg = toques_osc(lado, t0, t1)
        tpk, vpk, acc = picos_imu(lado, tg)
        limiar = getattr(cp, "LIMIAR_IMPACTO_ACC", None)
        linhas.append(f"PÉ {lado}: {len(toques)} pousadas no OSC")
        linhas.append("   toque OSC  subida cm | maior pico IMU ±0,4 s: atraso  m/s² | pico mais próximo: atraso  m/s² "
                      "| passo do sistema | distância máxima")
        r = {"maior": [], "proximo": [], "sistema": [], "dmax": [], "valor_maior": []}
        for tq in toques:
            tt = tq["t_toque"]
            k = np.abs(tpk - tt) <= 0.4
            if k.any():
                im = np.argmax(np.where(k, vpk, -np.inf))
                a_maior, v_maior = (tpk[im] - tt) * 1000, vpk[im]
            else:
                a_maior, v_maior = None, None
            ip = int(np.argmin(np.abs(tpk - tt))) if len(tpk) else None
            a_prox = (tpk[ip] - tt) * 1000 if ip is not None else None
            v_prox = vpk[ip] if ip is not None else None
            sis = [ts for ts, pe in pegadas if pe == letra and abs(ts - tt) <= 1.0]
            a_sis = (min(sis, key=lambda x: abs(x - tt)) - tt) * 1000 if sis else None
            pls = [pl for pl in platos if pl["pe"] == lado and pl["t0"] - 0.6 <= tt <= pl["t1"] + 0.3]
            a_dmax = (min(pls, key=lambda pl: abs(pl["t_max"] - tt))["t_max"] - tt) * 1000 if pls else None
            r["maior"].append(a_maior); r["proximo"].append(a_prox); r["sistema"].append(a_sis)
            r["dmax"].append(a_dmax); r["valor_maior"].append(v_maior)
            f = lambda x, fmt="{:+6.0f}": "     —" if x is None else fmt.format(x)  # noqa: E731
            linhas.append(f"   {tt:7.2f} s  {tq['subida_cm']:6.1f}   | {f(a_maior)} ms {f(v_maior, '{:5.1f}')}"
                          f"         | {f(a_prox)} ms {f(v_prox, '{:5.1f}')}           | {f(a_sis)} ms"
                          f"       | {f(a_dmax)} ms")
        linhas.append(f"   Maior pico IMU perto do toque: {_med(r['maior'])}")
        linhas.append(f"   Pico IMU mais próximo:         {_med(r['proximo'])}")
        if pegadas:
            linhas.append(f"   Passo marcado pelo sistema:    {_med(r['sistema'])}")
        if platos:
            linhas.append(f"   Distância máxima entre os pés: {_med(r['dmax'])}")
        v = np.array([x for x in r["valor_maior"] if x is not None])
        if len(v):
            linhas.append(f"   Valor do maior pico perto do toque: mediana {np.median(v):.1f} m/s² "
                          f"(de {v.min():.1f} a {v.max():.1f})"
                          + (f"; abaixo do limiar {limiar} m/s²: {int(np.sum(v < limiar))}" if limiar else ""))
        linhas.append("")
        resumo[lado] = {k: [None if x is None else float(x) for x in vals] for k, vals in r.items()}
    texto = "\n".join(linhas)
    print(texto)
    with open(os.path.join(pasta, "analise_toque_osc.txt"), "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    with open(os.path.join(pasta, "analise_toque_osc.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
