"""
TESTE — atraso entre a aceleração da IMU do pé e a posição do pé (OSC), com o pé batendo parado.

Sem caminhada: a pessoa fica no lugar, levanta um pé ~10 cm e bate firme no chão, várias vezes.
Em cada batida o pico de aceleração da IMU (SolarXR) e a freada do pé na posição (OSC) acontecem
no mesmo instante físico. A diferença de tempo entre os dois no computador é o atraso entre os
dois caminhos de dados.

    uv run python teste_batida_pe.py                       # coleta guiada (~65 s) + análise
    uv run python teste_batida_pe.py --analisar PASTA      # refaz só a análise de uma coleta salva

Roteiro: calibração estática (dois pés no chão), parado 5 s, pé ESQUERDO batendo 25 s,
parado 5 s, pé DIREITO batendo 25 s, parado 3 s.

Para cada batida mede:
  * pico de aceleração da IMU (módulo, filtro 12 Hz de fase zero);
  * freada do pé no OSC: maior aceleração para cima da altura do pé (o instante em que a descida
    para), em até 0,5 s antes e 0,8 s depois do pico;
  * chegada ao chão no OSC: primeiro instante, depois do ponto mais alto, em que o pé fica a
    menos de 1 cm do chão.
Atraso positivo = a posição (OSC) chega DEPOIS da aceleração. Também calcula a correlação cruzada
entre a aceleração da IMU e a aceleração vertical do OSC em cada etapa.
Resultado em analise_batida.txt, analise_batida.json e batida_pe.png na pasta da coleta.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

import calculo_passo as cp

cp.PEDIR_PARTICIPANTE = False   # sem cadastro
cp.CALIBRAR_CAIXA = False
cp.CALIBRAR_PASSO = False
cp.ROTEIRO_GUIADO = True
cp.ROTEIRO = [
    ("parado_1",   5, "Fique PARADO, os dois pés no chão, tronco reto"),
    ("batida_ESQ", 25, "Bata o pé ESQUERDO: levante ~10 cm e bata FIRME no chão, uma batida a cada ~2 s. Pé direito parado"),
    ("parado_2",   5, "Pé esquerdo no chão. Fique PARADO"),
    ("batida_DIR", 25, "Bata o pé DIREITO: levante ~10 cm e bata FIRME no chão, uma batida a cada ~2 s. Pé esquerdo parado"),
    ("parado_3",   3, "Fique PARADO. Fim"),
]

TAXA = 100.0          # Hz, grade comum das duas séries
CORTE_ACC = 12.0      # Hz, mesmo filtro da detecção de passos
CORTE_POS = 10.0      # Hz, altura do pé antes de derivar
JANELA = (-0.5, 0.8)  # s em volta do pico de aceleração onde a freada do OSC é procurada
CHAO_CM = 1.0
PE_TRACKER = {"ESQ": cp.TORNOZELO_ESQ, "DIR": cp.TORNOZELO_DIR}


# ----------------------------- análise -----------------------------
def _grade(t, v, t0, t1):
    t, v = np.asarray(t, float), np.asarray(v, float)
    k = (t >= t0 - 1.0) & (t <= t1 + 1.0)
    t, v = t[k], v[k]
    if len(t) < 10:
        return None, None
    t, i = np.unique(t, return_index=True)
    tg = np.arange(t0, t1, 1.0 / TAXA)
    return tg, np.interp(tg, t, v[i])


def _taxa_e_buraco(t, t0, t1):
    t = np.asarray(t, float)
    t = t[(t >= t0) & (t <= t1)]
    if len(t) < 2:
        return 0.0, float("nan")
    return (len(t) - 1) / (t[-1] - t[0]), float(np.max(np.diff(t)))


def analisar_etapa(lado, t0, t1):
    from scipy.signal import find_peaks
    imu = cp.dados_imu[lado]
    pos = cp.dados[PE_TRACKER[lado]]
    tg, acc = _grade(imu["t"], imu["acc"], t0, t1)
    tp, y = _grade(pos["t"], pos["y"], t0, t1)
    if tg is None or tp is None:
        return None
    n = min(len(tg), len(tp))
    tg, acc, y = tg[:n], acc[:n], y[:n]
    acc_f = cp.aplicar_filtro(acc, CORTE_ACC, TAXA)
    y_cm = cp.aplicar_filtro(y, CORTE_POS, TAXA) * 100.0
    vy = np.gradient(y_cm, tg)
    ay = np.gradient(vy, tg)           # cm/s², positivo = para cima
    chao = float(np.percentile(y_cm, 10))

    base = float(np.median(acc_f))
    altura_min = base + 0.4 * (float(np.max(acc_f)) - base)
    picos, _ = find_peaks(acc_f, height=altura_min, distance=int(0.8 * TAXA))

    batidas = []
    for p in picos:
        tpk = tg[p]
        j = (tg >= tpk + JANELA[0]) & (tg <= tpk + JANELA[1])
        if j.sum() < 10:
            continue
        idx = np.where(j)[0]
        fre = idx[np.argmax(ay[idx])]
        # chegada ao chão: depois do ponto mais alto antes da freada, primeiro instante a < 1 cm
        antes = np.where((tg >= tpk - 1.0) & (tg <= tg[fre]))[0]
        topo = antes[np.argmax(y_cm[antes])] if len(antes) else fre
        subida = y_cm[topo] - chao
        cheg = None
        for i in range(topo, min(len(tg), idx[-1] + 1)):
            if y_cm[i] - chao < CHAO_CM:
                cheg = i
                break
        batidas.append({
            "t_pico_s": round(float(tpk), 3),
            "pico_ms2": round(float(acc_f[p]), 1),
            "subida_cm": round(float(subida), 1),
            "atraso_freada_ms": round(float(tg[fre] - tpk) * 1000.0),
            "atraso_chao_ms": None if cheg is None else round(float(tg[cheg] - tpk) * 1000.0),
        })

    # correlação cruzada: aceleração da IMU x freada vertical do OSC (só a parte para cima)
    a1 = acc_f - np.mean(acc_f)
    a2 = np.clip(ay, 0, None)
    a2 = a2 - np.mean(a2)
    lags = np.arange(-int(1.0 * TAXA), int(1.0 * TAXA) + 1)
    cc = []
    for L in lags:   # L > 0: OSC atrasado em relação à IMU
        if L >= 0:
            x, z = a1[:len(a1) - L], a2[L:]
        else:
            x, z = a1[-L:], a2[:len(a2) + L]
        den = np.sqrt(np.sum(x * x) * np.sum(z * z))
        cc.append(float(np.sum(x * z) / den) if den > 0 else 0.0)
    k = int(np.argmax(cc))

    taxa_imu, buraco_imu = _taxa_e_buraco(imu["t"], t0, t1)
    taxa_osc, buraco_osc = _taxa_e_buraco(pos["t"], t0, t1)
    return {
        "lado": lado, "t0": t0, "t1": t1, "batidas": batidas,
        "xcorr_atraso_ms": round(float(lags[k] / TAXA) * 1000.0), "xcorr_r": round(cc[k], 2),
        "taxa_imu_hz": round(taxa_imu, 1), "maior_buraco_imu_ms": round(buraco_imu * 1000.0),
        "taxa_osc_hz": round(taxa_osc, 1), "maior_buraco_osc_ms": round(buraco_osc * 1000.0),
        "_serie": (tg, acc_f, y_cm, ay),
    }


def _resumo(v):
    v = np.array([x for x in v if x is not None], float)
    if len(v) == 0:
        return "sem valores"
    return (f"mediana {np.median(v):+5.0f} ms (de {np.min(v):+.0f} a {np.max(v):+.0f}; "
            f"média {np.mean(v):+.0f} ± {np.std(v, ddof=1) if len(v) > 1 else 0:.0f}; n={len(v)})")


def _grafico(resultados, arq):
    try:
        import matplotlib
        if not cp.MOSTRAR_GRAFICOS:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    fig, eixos = plt.subplots(len(resultados), 1, figsize=(13, 4 * len(resultados)), squeeze=False)
    for ax, r in zip(eixos[:, 0], resultados):
        tg, acc_f, y_cm, ay = r["_serie"]
        ax.plot(tg, acc_f, color="tab:red", lw=1, label="aceleração IMU (m/s²)")
        ax.set_ylabel("aceleração IMU (m/s²)")
        ax2 = ax.twinx()
        ax2.plot(tg, y_cm - np.percentile(y_cm, 10), color="tab:blue", lw=1, label="altura do pé OSC (cm)")
        ax2.set_ylabel("altura do pé OSC (cm)")
        for b in r["batidas"]:
            ax.axvline(b["t_pico_s"], color="tab:red", ls="--", lw=0.7)
            ax.axvline(b["t_pico_s"] + b["atraso_freada_ms"] / 1000.0, color="tab:blue", ls=":", lw=0.9)
        ax.set_title(f"Pé {r['lado']}: tracejado vermelho = pico da IMU; pontilhado azul = freada do pé no OSC")
        ax.set_xlabel("tempo (s)")
    fig.tight_layout()
    fig.savefig(arq, dpi=110)
    print(f"Gráfico salvo em {arq}")


def analisar(pasta):
    fases = {f[0]: (float(f[1]), float(f[2])) for f in cp.fases_sessao}
    resultados = []
    for lado in ("ESQ", "DIR"):
        if f"batida_{lado}" not in fases:
            print(f"Etapa batida_{lado} não encontrada.")
            continue
        r = analisar_etapa(lado, *fases[f"batida_{lado}"])
        if r is None:
            print(f"Pé {lado}: dados insuficientes.")
            continue
        resultados.append(r)

    linhas = ["TESTE DA BATIDA DO PÉ: atraso entre a aceleração da IMU e a posição do pé (OSC)",
              "Atraso positivo = a posição chega DEPOIS da aceleração.", ""]
    for r in resultados:
        linhas.append(f"PÉ {r['lado']}  (etapa {r['t0']:.1f}–{r['t1']:.1f} s)")
        linhas.append(f"  IMU {r['taxa_imu_hz']} amostras/s (maior buraco {r['maior_buraco_imu_ms']} ms); "
                      f"OSC {r['taxa_osc_hz']} amostras/s (maior buraco {r['maior_buraco_osc_ms']} ms)")
        linhas.append("    t do pico   pico m/s²   subida cm   freada OSC   chão OSC")
        for b in r["batidas"]:
            ch = "   —" if b["atraso_chao_ms"] is None else f"{b['atraso_chao_ms']:+5d} ms"
            linhas.append(f"    {b['t_pico_s']:8.2f} s   {b['pico_ms2']:8.1f}   {b['subida_cm']:8.1f}   "
                          f"{b['atraso_freada_ms']:+5d} ms   {ch}")
        linhas.append(f"  Freada do pé (OSC) − pico IMU: {_resumo([b['atraso_freada_ms'] for b in r['batidas']])}")
        linhas.append(f"  Chegada ao chão (OSC) − pico IMU: {_resumo([b['atraso_chao_ms'] for b in r['batidas']])}")
        linhas.append(f"  Correlação cruzada da etapa: OSC atrasado {r['xcorr_atraso_ms']:+d} ms (r = {r['xcorr_r']})")
        linhas.append("")
    todas = [b["atraso_freada_ms"] for r in resultados for b in r["batidas"]]
    if todas:
        linhas.append(f"DOIS PÉS: freada − pico {_resumo(todas)}")
    if resultados:
        # a leitura usa a correlação cruzada (etapa inteira), que não depende de batidas fracas
        xc = float(np.median([r["xcorr_atraso_ms"] for r in resultados]))
        if abs(xc) <= 60:
            linhas.append(f"Leitura (correlação cruzada, {xc:+.0f} ms): aceleração e posição chegam praticamente juntas. "
                          "O atraso de 0,3–1 s visto na caminhada não vem do nosso programa.")
        elif xc > 0:
            linhas.append(f"Leitura (correlação cruzada): a posição chega ~{xc:.0f} ms DEPOIS da aceleração mesmo com o pé parado. "
                          "Há atraso entre os dois caminhos de dados (SlimeVR/OSC ou programa).")
        else:
            linhas.append(f"Leitura (correlação cruzada): a posição aparece ~{-xc:.0f} ms ANTES da aceleração. "
                          "Confira o gráfico: costuma indicar batidas fracas ou mal detectadas.")
    texto = "\n".join(linhas)
    print("\n" + texto)
    with open(os.path.join(pasta, "analise_batida.txt"), "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    with open(os.path.join(pasta, "analise_batida.json"), "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in r.items() if k != "_serie"} for r in resultados], f, ensure_ascii=False, indent=1)
    if resultados:
        _grafico(resultados, os.path.join(pasta, "batida_pe.png"))
    print(f"\nAnálise salva em {os.path.join(pasta, 'analise_batida.txt')}")


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
    cp.fases_sessao[:] = [tuple(f) for f in (e.get("fases") or [])]


# ----------------------------- coleta -----------------------------
def _processar(pasta):
    def processar():
        cp.salvar_fases()
        cp.salvar_dados_brutos()
        analisar(pasta)
        if cp.MOSTRAR_GRAFICOS:
            import matplotlib.pyplot as plt
            plt.show()
    return processar


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Batida do pé parado: atraso entre aceleração da IMU e posição (OSC).")
    ap.add_argument("--analisar", metavar="PASTA", help="refaz a análise de uma coleta salva")
    args = ap.parse_args()
    if args.analisar:
        cp.MOSTRAR_GRAFICOS = False
        carregar(args.analisar)
        analisar(args.analisar)
        sys.exit(0)
    pasta = os.path.join("dados", "debug_casa", time.strftime("%Y%m%d_%H%M%S") + "_batida_pe")
    os.makedirs(pasta, exist_ok=True)
    cp.definir_pasta_saida(pasta)
    cp.processar_coleta = _processar(pasta)
    print(f"Os arquivos desta coleta vão para {pasta}")
    print("Teste parado, sem caminhada. Depois da calibração o programa guia: pé ESQUERDO batendo, "
          "pausa, pé DIREITO batendo.")
    cp.main()
