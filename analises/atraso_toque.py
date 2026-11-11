"""
Atraso entre o pico de aceleração da IMU do pé (toque do calcanhar) e a posição dos pés (OSC).

Para cada passo da caminhada com fita (pegadas.csv), mede quanto tempo depois do pico de aceleração
os pés chegam à distância máxima (fim do afastamento). Depois testa vários atrasos fixos: para cada um,
o comprimento "no toque" é |distância à frente-atrás| no instante pico + atraso, e compara com a fita.

    uv run python analises/atraso_toque.py dados/debug_casa/<pasta>
"""
import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calculo_passo as cp          # noqa: E402
import teste_calibracao_passo as T  # noqa: E402


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
    c = e["calibracao"]
    for k in ("dist_qp_e_ref", "dist_qp_d_ref", "yq_ref", "chao_esq_ref", "chao_dir_ref",
              "limiar_ruido_chao", "pitch_tronco_ref"):
        if c.get(k) is not None:
            setattr(cp, k, c[k])
    cp.calibrado = True
    cp.cadeia = None
    cp.fases_sessao[:] = [tuple(f) for f in (e.get("fases") or [])]


def fator_loo(m, r, pe):
    out = np.zeros(len(m))
    for i in range(len(m)):
        o = (pe == pe[i]); o[i] = False
        out[i] = m[i] * np.median(r[o] / m[o])
    return out


def main(pasta):
    carregar(pasta)
    s = cp.calcular_sinais_completos(cp.dados[cp.TORNOZELO_ESQ], cp.dados[cp.TORNOZELO_DIR],
                                     cp.dados[cp.TRACKER_QUADRIL], cp.dados[cp.TRACKER_COXA_ESQ],
                                     cp.dados[cp.TRACKER_COXA_DIR])
    t, d = np.asarray(s[0]), np.asarray(s[1])
    platos = cp.platos_anteroposteriores(t, d)
    passos = []
    with open(os.path.join(pasta, "pegadas.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            real = T._num(r.get("real_cm"))
            if real:
                passos.append((float(r["t_s"]), r["pe_da_frente"], real))
    tp = np.array([p[0] for p in passos]); pe = np.array([p[1] for p in passos]); real = np.array([p[2] for p in passos])
    print(f"Passos com fita: {len(passos)}")
    print("\n1) Atraso de cada passo: do pico de aceleração até a distância máxima entre os pés")
    atrasos = []
    for ti, p_, r_ in passos:
        cand = [pl for pl in platos if pl["t0"] - 0.6 <= ti <= pl["t1"] + 0.3]
        if not cand:
            print(f"   t={ti:6.2f} pé {p_}: sem platô perto"); atrasos.append(np.nan); continue
        pl = min(cand, key=lambda pl: abs(pl["t_max"] - ti))
        a = pl["t_max"] - ti
        atrasos.append(a)
        print(f"   t={ti:6.2f} pé {p_}: platô {pl['t0']:6.2f}–{pl['t1']:6.2f} s, máximo em {pl['t_max']:6.2f} s "
              f"→ atraso {a * 1000:+5.0f} ms  (|d| no pico {abs(np.interp(ti + cp.TAU_SYNC_OSC, t, d)) * 100:4.1f} cm, máximo {pl['comprimento_m'] * 100:4.1f}, fita {r_:4.1f})")
    at = np.array(atrasos)
    ok = ~np.isnan(at)
    for lado in ("E", "D"):
        k = ok & (pe == lado)
        if k.any():
            print(f"   pé {lado}: atraso mediano {np.median(at[k]) * 1000:+.0f} ms (de {at[k].min() * 1000:+.0f} a {at[k].max() * 1000:+.0f})")
    print("\n2) Atraso fixo testado: comprimento = |d| no instante (pico + atraso), contra a fita (cm)")
    print(f"   {'atraso':>8s} {'erro E':>7s} {'erro D':>7s} {'|erro| sem':>10s} {'|erro| c/fator':>14s} {'r':>6s}")
    melhores = []
    for tau in np.arange(-0.20, 0.81, 0.025):
        m = np.abs(np.interp(tp + tau, t, d)) * 100
        if np.any(m < 1):
            continue
        c = fator_loo(m, real, pe)
        eE = np.mean((m - real)[pe == "E"]); eD = np.mean((m - real)[pe == "D"])
        linha = (tau, eE, eD, np.mean(np.abs(m - real)), np.mean(np.abs(c - real)), np.corrcoef(m, real)[0, 1])
        melhores.append(linha)
        print(f"   {tau * 1000:+6.0f} ms {eE:+7.1f} {eD:+7.1f} {linha[3]:10.1f} {linha[4]:14.1f} {linha[5]:6.2f}")
    b = min(melhores, key=lambda x: x[4])
    print(f"\n   Melhor atraso (menor |erro| com fator): {b[0] * 1000:+.0f} ms → |erro| {b[3]:.1f} sem fator, {b[4]:.1f} com fator, r {b[5]:.2f}")
    m_plato = np.array([max((pl for pl in platos if pl['t0'] - 0.6 <= ti <= pl['t1'] + 0.3), key=lambda pl: -abs(pl['t_max'] - ti))['comprimento_m'] * 100 for ti in tp])
    print(f"   Referência, máximo do apoio: |erro| {np.mean(np.abs(m_plato - real)):.1f} sem fator, "
          f"{np.mean(np.abs(fator_loo(m_plato, real, pe) - real)):.1f} com fator")


if __name__ == "__main__":
    main(sys.argv[1])
