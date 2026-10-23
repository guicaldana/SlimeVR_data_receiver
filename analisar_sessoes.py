"""
Junta várias sessões guiadas do calculo_passo.py ou do piloto.py e resume os resultados.

Cada sessão fica em dados/participantes/<Nome>/AAAAMMDD_HHMMSS/ (antes: dados/sessoes/; o calculo_passo.py grava
sozinho no fim). O script reprocessa todas com o código atual e mede, para
cada método (OSC, cadeia, pipeline):

  1. Caixa (6,7 cm) com o tronco reto e inclinado: erro de cada pé.
  2. Inclinar parado: quanto a diferença entre os pés muda só por inclinar.
  3. Caminhadas reta e inclinada:
     - comprimento de cada passo contra as marcas do chão (marcas_chao.py);
     - altura de cada passo pelo OSC e pela cadeia, e a concordância entre
       os dois (Bland-Altman).

Uso:
    uv run python analisar_sessoes.py                 # todas em dados/participantes/ e dados/sessoes/
    uv run python analisar_sessoes.py pasta1 pasta2   # só essas

Saída em dados/analise_sessoes/: caixa.csv, inclinar.csv, passos.csv,
resumo.txt e analise_sessoes.png.
"""

import csv
import re
import glob
import json
import os
import sys

import numpy as np

import cadeia_pernas
import marcas_chao

PASTA_SAIDA = os.path.join("dados", "analise_sessoes")
CAIXA_CM = 6.7               # caixa usada nas sessões até 22/10 (valor nominal da calibração)
ALTURA_TESTE_SESSAO = None   # altura conhecida da sessão sendo lida (piloto.py grava no JSON)
ETAPAS_CAIXA = {
    "caixa_reto_ESQ": ("reto", "ESQ"), "caixa_reto_DIR": ("reto", "DIR"),
    "caixa_incl_ESQ": ("inclinado", "ESQ"), "caixa_incl_DIR": ("inclinado", "DIR"),
}
METODOS = ("OSC", "cadeia", "pipeline")


def _importar_calculo():
    # Importa sem abrir portas: o calculo_passo só abre servidores dentro de main()
    import calculo_passo as cp
    return cp


def carregar_sessao(cp, pasta):
    """Coloca os dados de uma sessão salva nas variáveis do calculo_passo e recalcula."""
    with open(os.path.join(pasta, "dados_brutos_entrada.json"), encoding="utf-8") as f:
        e = json.load(f)
    # Sessões antigas (sem o campo) usaram o percurso de 20/10
    marcas_chao.definir_circuito(e.get("circuito") or "20-10")
    # Altura do objeto de teste (piloto.py); sessões antigas usaram a caixa de calibração (6,7 nominal)
    global ALTURA_TESTE_SESSAO
    ALTURA_TESTE_SESSAO = e.get("altura_teste_cm")
    for tid, d in e["trackers"].items():
        if tid in ("4", "5"):
            cp.dados[tid] = {k: list(v) for k, v in d.items()}
            continue
        n = min(len(v) for v in d.values())  # listas podem diferir em 1 se a captura não parou limpa
        cp.dados[tid] = {k: list(v)[:n] for k, v in d.items()}
    for lado, d in e["imu"].items():
        n = min(len(v) for v in d.values())
        cp.dados_imu[lado] = {k: list(v)[:n] for k, v in d.items()}
    for seg, d in e["quats"].items():
        n = min(len(d["t"]), len(d["q"]))
        cp.dados_quat[seg] = {"t": d["t"][:n], "q": [tuple(q) for q in d["q"][:n]]}
    c = e["calibracao"]
    for k in ("dist_qp_e_ref", "dist_qp_d_ref", "yq_ref", "chao_esq_ref", "chao_dir_ref",
              "limiar_ruido_chao", "pitch_tronco_ref"):
        if k in c:
            setattr(cp, k, c[k])
    cp.calibrado = True
    cad = cadeia_pernas.CadeiaPernas(c["cadeia_comprimentos"])
    cad.vetor_local = {k: np.array(v) for k, v in c["cadeia_vetor_local"].items()}
    cad.calibrado = True
    cp.cadeia = cad
    ganho_cadeia_caixa = e.get("ganhos_cadeia_caixa") or e["ganhos_metodo"]["cadeia"]
    cp.ganhos_metodo = {"osc": tuple(e["ganhos_metodo"]["osc"]), "cadeia": tuple(ganho_cadeia_caixa)}
    cp.ganho_esq, cp.ganho_dir = e.get("ganho_esq", 1.0), e.get("ganho_dir", 1.0)
    fases = e.get("fases") or []
    if not fases:
        with open(os.path.join(pasta, "fases_sessao.csv"), encoding="utf-8") as f:
            fases = [(r["etapa"], float(r["t0"]), float(r["t1"])) for r in csv.DictReader(f)]
    cp.fases_sessao[:] = [tuple(f) for f in fases]
    del cp.ganhos_cadeia_caixa[:]
    sinais = cp.calcular_sinais_completos(cp.dados[cp.TORNOZELO_ESQ], cp.dados[cp.TORNOZELO_DIR],
                                          cp.dados[cp.TRACKER_QUADRIL], cp.dados[cp.TRACKER_COXA_ESQ],
                                          cp.dados[cp.TRACKER_COXA_DIR])
    return sinais, {n: (a, b) for n, a, b in cp.fases_sessao}


def series_metodos(cp):
    s = cp.serie_altura
    g_osc, g_cad = cp.ganhos_metodo["osc"], cp.ganhos_metodo["cadeia"]
    out = {"OSC": (s["osc_esq_cm"] * g_osc[0], s["osc_dir_cm"] * g_osc[1])}
    if s.get("cadeia_esq_cm") is not None:
        out["cadeia"] = (s["cadeia_esq_cm"] * g_cad[0], s["cadeia_dir_cm"] * g_cad[1])
        # Pipeline sem o corte em zero da série salva, para comparar com os outros dois
        w = cp.peso_cadeia(s.get("tronco_graus"))
        out["pipeline"] = tuple((1.0 - w) * o + w * c for o, c in zip(out["OSC"], out["cadeia"]))
    else:
        out["pipeline"] = (s["altura_esq_cm"], s["altura_dir_cm"])
    return s["t"], out


def analisar(cp, pasta):
    nome = os.path.basename(os.path.normpath(pasta))
    sinais, fases = carregar_sessao(cp, pasta)
    t, d_passo_ap = sinais[0], sinais[1]
    t_s, met = series_metodos(cp)
    tronco = cp.serie_altura.get("tronco_graus")
    if tronco is not None:  # inclinação em relação à postura da calibração, em graus positivos
        tronco = np.abs(np.asarray(tronco, dtype=float) - cp.pitch_tronco_ref)
    caixa, inclinar, passos = [], [], []
    altura_ref = ALTURA_TESTE_SESSAO or CAIXA_CM

    # 1. Altura conhecida (caixa ou objeto de teste)
    for etapa, (cond, pe) in ETAPAS_CAIXA.items():
        if etapa not in fases:
            continue
        a, b = fases[etapa]
        m = (t_s > a + 2.0) & (t_s < b - 0.5)
        linha = {"sessao": nome, "condicao": cond, "pe": pe, "altura_ref_cm": altura_ref,
                 "tronco_graus": float(np.median(tronco[m])) if tronco is not None else np.nan}
        for k, (he, hd) in met.items():
            dif = np.median((hd - he)[m])
            linha[k] = float(-dif if pe == "ESQ" else dif)
        caixa.append(linha)

    # 2. Inclinar parado
    if "inclinar" in fases:
        a, b = fases["inclinar"]
        m = (t_s > a + 2.0) & (t_s < b - 0.5)
        linha = {"sessao": nome, "tronco_graus": float(np.median(tronco[m])) if tronco is not None else np.nan}
        for k, (he, hd) in met.items():
            linha[k] = float(np.median((hd - he)[m]))
        inclinar.append(linha)

    # 3. Caminhadas
    tempos, comp, imp, pes, proj = cp.detectar_passos_multimodal(t, d_passo_ap, 100.0)
    if cp.COMPRIMENTO_PELO_PLATO and len(tempos) > 0:
        tempos, comp, imp, pes, proj = cp.comprimento_pelo_plato(t, d_passo_ap, tempos, comp, imp, pes, proj)
    alturas = {k: np.array(cp.calcular_alturas_passos(t_s, tempos, pes, he, hd)) for k, (he, hd) in met.items()}
    for etapa, a, b in cp.fases_sessao:
        tipo = re.sub(r"_\d+$", "", etapa)
        if tipo not in ("caminhada_reta", "caminhada_inclinada"):
            continue
        sel = np.flatnonzero((tempos >= a) & (tempos <= b + 1.0))
        casados = marcas_chao.casar_com_marcas(pes[sel], comp[sel], tempos[sel])
        por_tempo = {c["t"]: c for c in casados}
        for i in sel:
            c = por_tempo.get(float(tempos[i]))
            linha = {"sessao": nome, "circuito": marcas_chao.CIRCUITO_ATIVO,
                     "caminhada": tipo.replace("caminhada_", ""), "repeticao": etapa, "t": float(tempos[i]),
                     "pe": str(pes[i]).replace("*", ""), "imu": "*" not in str(pes[i]),
                     "tronco_graus": float(np.interp(tempos[i], t_s, tronco)) if tronco is not None else np.nan,
                     "comp_medido_m": float(comp[i]),
                     "comp_real_m": c["real_m"] if c else np.nan}
            for k in alturas:
                linha[f"altura_{k}_cm"] = float(alturas[k][i])
            passos.append(linha)
    return caixa, inclinar, passos


def _gravar(caminho, linhas):
    if not linhas:
        return
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)


def _ms(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return "   —        "
    return f"{x.mean():+5.2f} ± {x.std(ddof=1) if len(x) > 1 else 0:4.2f}"


def resumir(caixa, inclinar, passos, n_sessoes):
    L = [f"Sessões analisadas: {n_sessoes}", ""]
    metodos = [m for m in METODOS if caixa and m in caixa[0]]
    L.append("1. ALTURA CONHECIDA (caixa ou objeto de teste) — erro (cm), média ± desvio entre sessões e pés")
    L.append(f"   {'condição':10s} {'n':>3s}  " + "  ".join(f"{m:>13s}" for m in metodos) + "   tronco")
    for cond in ("reto", "inclinado"):
        ls = [c for c in caixa if c["condicao"] == cond]
        if ls:
            L.append(f"   {cond:10s} {len(ls):3d}  " + "  ".join(f"{_ms([c[m] - c["altura_ref_cm"] for c in ls]):>13s}" for m in metodos)
                     + f"   {np.nanmedian([c['tronco_graus'] for c in ls]):5.0f}°")
    L.append("   erro absoluto médio (todas as subidas): " + "  ".join(
        f"{m} {np.nanmean([abs(c[m] - c["altura_ref_cm"]) for c in caixa]):.2f} cm" for m in metodos))
    L.append("")
    if inclinar:
        L.append("2. INCLINAR PARADO — diferença entre os pés (cm) só por inclinar (ideal = 0)")
        L.append("   " + "  ".join(f"{m} {_ms([c[m] for c in inclinar])}" for m in metodos)
                 + f"   tronco {np.nanmedian([c['tronco_graus'] for c in inclinar]):.0f}°")
        L.append("")
    if passos:
        L.append("3. CAMINHADAS")
        for cam in ("reta", "inclinada"):
            ps = [p for p in passos if p["caminhada"] == cam]
            if not ps:
                continue
            casados = [p for p in ps if not np.isnan(p["comp_real_m"])]
            n_imu = sum(p["imu"] for p in ps)
            L.append(f"   {cam}: {len(ps)} passos detectados ({n_imu} pela IMU), {len(casados)} casados com as marcas,"
                     f" tronco {np.nanmedian([p['tronco_graus'] for p in ps]):.0f}°")
            if casados:
                e = np.array([(p["comp_medido_m"] - p["comp_real_m"]) * 100 for p in casados])
                r = np.array([p["comp_medido_m"] / p["comp_real_m"] for p in casados])
                L.append(f"     comprimento x marcas: erro {e.mean():+.1f} ± {e.std(ddof=1) if len(e) > 1 else 0:.1f} cm,"
                         f" erro absoluto {np.abs(e).mean():.1f} cm, medido/real {np.median(r):.2f}")
            for m in [k for k in METODOS if f"altura_{k}_cm" in ps[0]]:
                h = np.array([p[f"altura_{m}_cm"] for p in ps])
                L.append(f"     altura do passo {m:9s}: mediana {np.median(h):4.1f} cm")
            if "altura_cadeia_cm" in ps[0]:
                d = np.array([p["altura_cadeia_cm"] - p["altura_OSC_cm"] for p in ps])
                L.append(f"     cadeia − OSC (Bland-Altman): viés {d.mean():+.1f} cm, limites ±{1.96 * d.std(ddof=1):.1f} cm")
        reta = [p for p in passos if p["caminhada"] == "reta" and not np.isnan(p["comp_real_m"])]
        incl = [p for p in passos if p["caminhada"] == "inclinada" and not np.isnan(p["comp_real_m"])]
        if reta and incl:
            er = np.mean([(p["comp_medido_m"] - p["comp_real_m"]) * 100 for p in reta])
            ei = np.mean([(p["comp_medido_m"] - p["comp_real_m"]) * 100 for p in incl])
            L.append(f"   efeito da inclinação no comprimento (erro inclinada − erro reta): {ei - er:+.1f} cm")
    return "\n".join(L)


def grafico(caixa, passos, caminho):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    metodos = [m for m in METODOS if caixa and m in caixa[0]]
    fig, ax = plt.subplots(1, 3, figsize=(14, 4))
    cores = {"OSC": "#d9822b", "cadeia": "#2b7bd9", "pipeline": "#555555"}
    for j, cond in enumerate(("reto", "inclinado")):
        for i, m in enumerate(metodos):
            v = [c[m] - c["altura_ref_cm"] for c in caixa if c["condicao"] == cond]
            x = j * (len(metodos) + 1) + i
            ax[0].scatter(np.full(len(v), x), v, color=cores[m], s=18, label=m if j == 0 else None)
            if v:
                ax[0].hlines(np.mean(v), x - 0.3, x + 0.3, color=cores[m])
    ax[0].axhline(0, color="k", lw=0.6)
    ax[0].set_xticks([1, len(metodos) + 2], ["tronco reto", "tronco inclinado"])
    ax[0].set_ylabel("Erro na caixa (cm)")
    ax[0].legend(frameon=False, fontsize=8)
    ax[0].set_title("Caixa 6,7 cm")
    for cam, cor in (("reta", "#2b7bd9"), ("inclinada", "#d94b2b")):
        ps = [p for p in passos if p["caminhada"] == cam and not np.isnan(p["comp_real_m"])]
        ax[1].scatter([p["comp_real_m"] * 100 for p in ps], [p["comp_medido_m"] * 100 for p in ps],
                      color=cor, s=18, label=cam)
    ax[1].plot([20, 70], [20, 70], color="k", lw=0.6)
    ax[1].set_xlabel("Comprimento real (marcas, cm)")
    ax[1].set_ylabel("Comprimento medido (cm)")
    ax[1].legend(frameon=False, fontsize=8)
    ax[1].set_title("Comprimento do passo")
    for cam, cor in (("reta", "#2b7bd9"), ("inclinada", "#d94b2b")):
        ps = [p for p in passos if p["caminhada"] == cam and "altura_cadeia_cm" in p]
        mm = [(p["altura_cadeia_cm"] + p["altura_OSC_cm"]) / 2 for p in ps]
        dd = [p["altura_cadeia_cm"] - p["altura_OSC_cm"] for p in ps]
        ax[2].scatter(mm, dd, color=cor, s=18, label=cam)
    todos = [p["altura_cadeia_cm"] - p["altura_OSC_cm"] for p in passos if "altura_cadeia_cm" in p]
    if len(todos) > 1:
        mu, sd = np.mean(todos), np.std(todos, ddof=1)
        for y, ls in ((mu, "-"), (mu + 1.96 * sd, "--"), (mu - 1.96 * sd, "--")):
            ax[2].axhline(y, color="k", lw=0.7, ls=ls)
    ax[2].set_xlabel("Média OSC e cadeia (cm)")
    ax[2].set_ylabel("Cadeia − OSC (cm)")
    ax[2].legend(frameon=False, fontsize=8)
    ax[2].set_title("Altura do passo: Bland-Altman")
    fig.tight_layout()
    fig.savefig(caminho, dpi=150)


def main():
    pastas = sys.argv[1:] or sorted(
        glob.glob(os.path.join("dados", "sessoes", "*"))
        + [p for p in glob.glob(os.path.join("dados", "participantes", "*", "*")) if os.path.isdir(p)])
    pastas = [p for p in pastas if os.path.exists(os.path.join(p, "dados_brutos_entrada.json"))]
    if not pastas:
        sys.exit("Nenhuma sessão encontrada em dados/sessoes/ nem em dados/participantes/.")
    cp = _importar_calculo()
    caixa, inclinar, passos = [], [], []
    for p in pastas:
        print(f"\n=== {p}")
        try:
            c, i, ps = analisar(cp, p)
        except Exception as e:
            print(f"   [erro] sessão ignorada: {e}")
            continue
        caixa += c
        inclinar += i
        passos += ps
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    _gravar(os.path.join(PASTA_SAIDA, "caixa.csv"), caixa)
    _gravar(os.path.join(PASTA_SAIDA, "inclinar.csv"), inclinar)
    _gravar(os.path.join(PASTA_SAIDA, "passos.csv"), passos)
    texto = resumir(caixa, inclinar, passos, len(pastas))
    with open(os.path.join(PASTA_SAIDA, "resumo.txt"), "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    grafico(caixa, passos, os.path.join(PASTA_SAIDA, "analise_sessoes.png"))
    print("\n" + texto)
    print(f"\nArquivos em {PASTA_SAIDA}/")


if __name__ == "__main__":
    main()
