"""
Captura livre: grava os dados das IMUs do SlimeVR sem roteiro e calcula as variáveis da marcha.

Usa os mesmos cálculos do calculo_passo.py (que não é alterado):
  1. nome e altura do participante;
  2. calibração parada com os dois pés no chão (sem caixa: CALIBRAR_CAIXA = False,
     o OSC bruto acertou o cone nos Testes 4 e 5 de 26/10);
  3. gravação livre: a pessoa caminha, para, vira, como quiser. Termina com Ctrl + C
     ou sozinha depois da duração escolhida no início (Enter = só com Ctrl + C);
  4. processamento: altura do pé (OSC com o tronco reto, cadeia das pernas com o tronco
     inclinado), comprimento do passo (máximo do platô), largura da base, tronco e,
     além do relatório do calculo_passo, os tempos dos passos por trecho de caminhada:
     tempo do passo, tempo da passada, cadência, velocidade, variabilidade e simetria.

Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_livre/:
  relatorio.txt, resultado_completo_marcha.csv, passos_livre.csv (uma linha por passo),
  resumo_livre.csv (uma linha com as médias), dados_brutos_sessao.csv,
  dados_brutos_entrada.json e os gráficos (painel_completo, comprimento_passo,
  altura_passo e tempos_passo.png).

    uv run python captura_livre.py                     # captura e processa
    uv run python captura_livre.py --duracao 120       # para sozinha depois de 120 s
    uv run python captura_livre.py --reprocessar dados/participantes/Nome/20261027_150000_livre
"""
import argparse
import csv
import json
import os
import sys
import threading
import time

import numpy as np

import cadeia_pernas
import calculo_passo as cp

# ======================== CONFIGURAÇÃO ========================
cp.ROTEIRO_GUIADO = False      # sem roteiro: grava até Ctrl + C (ou até a duração)
cp.CALIBRAR_CAIXA = False      # OSC bruto, sem ganho da caixa
cp.MODO_ALTURA = "pipeline"    # OSC com tronco até 10°, cadeia das pernas a partir de 30°

PAUSA_MAX_S = 1.5              # passos separados por mais que isso começam outro trecho de caminhada
PASSOS_MIN_TRECHO = 4          # trechos com menos passos não entram nas médias dos tempos
NOME_PASSOS_LIVRE = "passos_livre.csv"
NOME_RESUMO_LIVRE = "resumo_livre.csv"
# ==============================================================

DURACAO_S = None               # None = só termina com Ctrl + C
_relatorio_original = cp.gerar_relatorio_e_graficos
_calibracao_original = cp.executar_calibracao_inicial


def _lado(pe):
    return "DIR" if "DIR" in str(pe) else "ESQ"


def _ms(v, casas=1, un=""):
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return "—"
    return f"{v.mean():.{casas}f} ± {v.std():.{casas}f}{un}"


def separar_trechos(tempos):
    """Número do trecho de caminhada de cada passo (pausas maiores que PAUSA_MAX_S separam)."""
    trechos, atual = [], 1
    for i, t in enumerate(tempos):
        if i > 0 and t - tempos[i - 1] > PAUSA_MAX_S:
            atual += 1
        trechos.append(atual)
    return np.array(trechos, dtype=int)


def tempos_dos_passos(tempos, pes, trechos):
    """Tempo do passo (contato anterior do outro pé) e da passada (contato anterior do mesmo pé).

    Só dentro do mesmo trecho; fora disso fica NaN."""
    n = len(tempos)
    t_passo = np.full(n, np.nan)
    t_passada = np.full(n, np.nan)
    for i in range(1, n):
        if trechos[i] == trechos[i - 1] and _lado(pes[i]) != _lado(pes[i - 1]):
            t_passo[i] = tempos[i] - tempos[i - 1]
        for j in range(i - 1, -1, -1):
            if trechos[j] != trechos[i]:
                break
            if _lado(pes[j]) == _lado(pes[i]):
                t_passada[i] = tempos[i] - tempos[j]
                break
    return t_passo, t_passada


def _angulo_nos_passos(chave, tempos):
    d = cp.dados.get(chave, {})
    if len(d.get("t", [])) < 10:
        return None
    n = min(len(d["t"]), len(d["pitch"]))
    return np.interp(tempos, np.array(d["t"][:n]), np.array(d["pitch"][:n]))


def variaveis_livre(t_serie, larg_base_m, clear_esq_cm, clear_dir_cm,
                    tempos, comprimentos, alturas, pes, validos):
    """Imprime (no relatório) e salva as variáveis da marcha por passo e por trecho."""
    tempos = np.asarray(tempos, dtype=float)
    comprimentos = np.asarray(comprimentos, dtype=float)
    alturas = np.asarray(alturas, dtype=float)
    validos = np.asarray(validos, dtype=bool)
    lados = np.array([_lado(p) for p in pes])
    trechos = separar_trechos(tempos)
    t_passo, t_passada = tempos_dos_passos(tempos, pes, trechos)
    tronco = _angulo_nos_passos(cp.TRACKER_PEITO, tempos)
    cabeca = _angulo_nos_passos("cabeca", tempos)
    base_cm = np.interp(tempos, t_serie, np.asarray(larg_base_m) * 100.0) if len(t_serie) else np.full(len(tempos), np.nan)

    n_por_trecho = {k: int(np.sum(trechos == k)) for k in np.unique(trechos)}
    na_media = validos & np.array([n_por_trecho[k] >= PASSOS_MIN_TRECHO for k in trechos])

    print("\n" + "=" * 90)
    print("   CAPTURA LIVRE: VARIÁVEIS DA MARCHA POR TRECHO DE CAMINHADA")
    print("=" * 90)
    print(f"  Trechos: pausa de mais de {PAUSA_MAX_S:.1f} s entre passos separa um trecho do outro;")
    print(f"  trechos com menos de {PASSOS_MIN_TRECHO} passos e outliers não entram nas médias.")
    print(f"\n  {'Trecho':<7} {'Início(s)':>9} {'Fim(s)':>8} {'Passos':>7} {'Compr.(cm)':>14} {'Altura(cm)':>13} "
          f"{'T.passo(s)':>14} {'Cadência':>9} {'Veloc.(m/s)':>11}")
    linhas_trecho = []
    for k in np.unique(trechos):
        m = trechos == k
        mv = m & validos
        tp = t_passo[mv]
        tp = tp[np.isfinite(tp)]
        cad = 60.0 / tp.mean() if len(tp) else np.nan
        vel = comprimentos[mv].mean() * cad / 60.0 if len(tp) and np.any(mv) else np.nan
        curto = "" if n_por_trecho[k] >= PASSOS_MIN_TRECHO else "  (curto)"
        print(f"  {k:<7} {tempos[m][0]:>9.1f} {tempos[m][-1]:>8.1f} {int(m.sum()):>7} "
              f"{_ms(comprimentos[mv] * 100, 1):>14} {_ms(alturas[mv], 1):>13} {_ms(tp, 2):>14} "
              f"{cad:>9.1f} {vel:>11.2f}{curto}")
        linhas_trecho.append((k, cad, vel))

    # Médias gerais (só trechos com passos suficientes)
    resumo = {
        "participante": cp.PARTICIPANTE["nome"] if cp.PARTICIPANTE else "",
        "altura_participante_cm": cp.PARTICIPANTE["altura_cm"] if cp.PARTICIPANTE else "",
        "duracao_s": round(float(t_serie[-1] - t_serie[0]), 1) if len(t_serie) else "",
        "trechos": int(sum(1 for v in n_por_trecho.values() if v >= PASSOS_MIN_TRECHO)),
        "passos_detectados": int(len(tempos)),
        "passos_nas_medias": int(na_media.sum()),
    }
    print(f"\n  --- Médias (trechos com {PASSOS_MIN_TRECHO}+ passos, {int(na_media.sum())} passos) ---")
    if np.any(na_media):
        def bloco(nome, valores, fator, casas, un, chave):
            v = np.asarray(valores, dtype=float)
            txt = f"  {nome:<26} {_ms(v[na_media] * fator, casas, un)}"
            for lado, ch in (("ESQ", "E"), ("DIR", "D")):
                vl = v[na_media & (lados == lado)] * fator
                vl = vl[np.isfinite(vl)]
                resumo[f"{chave}_{ch}"] = round(float(vl.mean()), 3) if len(vl) else ""
                if len(vl):
                    txt += f" | {ch} {vl.mean():.{casas}f}"
            vg = v[na_media] * fator
            vg = vg[np.isfinite(vg)]
            resumo[f"{chave}_media"] = round(float(vg.mean()), 3) if len(vg) else ""
            resumo[f"{chave}_dp"] = round(float(vg.std()), 3) if len(vg) else ""
            cv = vg.std() / vg.mean() * 100 if len(vg) and vg.mean() > 0 else np.nan
            resumo[f"{chave}_cv_pct"] = round(float(cv), 1) if np.isfinite(cv) else ""
            print(txt + (f"  (CV {cv:.1f}%)" if np.isfinite(cv) else ""))
            return vg

        comp = bloco("Comprimento do passo:", comprimentos, 100.0, 1, " cm", "comprimento_cm")
        bloco("Altura do pé (máx.):", alturas, 1.0, 1, " cm", "altura_cm")
        tp = bloco("Tempo do passo:", t_passo, 1.0, 3, " s", "tempo_passo_s")
        bloco("Tempo da passada:", t_passada, 1.0, 3, " s", "tempo_passada_s")
        bloco("Largura da base:", base_cm, 1.0, 1, " cm", "largura_base_cm")
        if len(tp):
            cad = 60.0 / tp.mean()
            vel = comp.mean() / 100.0 * cad / 60.0
            resumo["cadencia_passos_min"] = round(float(cad), 1)
            resumo["velocidade_m_s"] = round(float(vel), 3)
            print(f"  {'Cadência:':<26} {cad:.1f} passos/min")
            print(f"  {'Velocidade estimada:':<26} {vel:.2f} m/s (comprimento médio × cadência)")
        if cp.PARTICIPANTE and len(comp):
            r = comp.mean() / cp.PARTICIPANTE["altura_cm"]
            resumo["comprimento_por_altura"] = round(float(r), 3)
            print(f"  {'Comprimento / altura:':<26} {r:.2f}")
        for nome, chave in (("comprimento_cm", "Comprimento"), ("tempo_passo_s", "Tempo do passo"),
                            ("altura_cm", "Altura do pé")):
            e, d = resumo.get(f"{nome}_E"), resumo.get(f"{nome}_D")
            if e not in ("", None) and d not in ("", None) and (e + d) > 0:
                si = (1.0 - abs(e - d) / ((e + d) / 2.0)) * 100.0
                resumo[f"simetria_{nome}_pct"] = round(si, 1)
                print(f"  Simetria {chave + ':':<17} {si:.1f}%")
        if tronco is not None:
            resumo["tronco_graus_media"] = round(float(np.mean(tronco[na_media])), 1)
            print(f"  {'Tronco nos passos:':<26} {_ms(tronco[na_media], 1, '°')} "
                  f"({cp.classificar_postura_tronco(float(np.mean(tronco[na_media])))})")
        if cabeca is not None:
            resumo["cabeca_graus_media"] = round(float(np.mean(cabeca[na_media])), 1)
            print(f"  {'Cabeça nos passos:':<26} {_ms(cabeca[na_media], 1, '°')} "
                  f"({cp.classificar_olhar(float(np.mean(cabeca[na_media])))[1]})")
    else:
        print("  Nenhum trecho com passos suficientes. Caminhe pelo menos alguns passos seguidos.")

    # Arquivos
    arq_passos = os.path.join(cp.PASTA_COLETA, NOME_PASSOS_LIVRE)
    with open(arq_passos, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Passo", "Trecho", "Tempo_s", "Pe", "Comprimento_cm", "Altura_pe_cm",
                    "Tempo_passo_s", "Tempo_passada_s", "Largura_base_cm", "Tronco_graus",
                    "Cabeca_graus", "Outlier", "Nas_medias"])
        fmt = lambda v, c: "" if v is None or not np.isfinite(v) else f"{v:.{c}f}"
        for i in range(len(tempos)):
            w.writerow([i + 1, trechos[i], f"{tempos[i]:.3f}", lados[i], fmt(comprimentos[i] * 100, 1),
                        fmt(alturas[i], 2), fmt(t_passo[i], 3), fmt(t_passada[i], 3), fmt(base_cm[i], 1),
                        fmt(tronco[i], 1) if tronco is not None else "",
                        fmt(cabeca[i], 1) if cabeca is not None else "",
                        "NAO" if validos[i] else "SIM", "SIM" if na_media[i] else "NAO"])
    arq_resumo = os.path.join(cp.PASTA_COLETA, NOME_RESUMO_LIVRE)
    with open(arq_resumo, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(list(resumo.keys()))
        w.writerow(list(resumo.values()))
    print(f"\n  Passos salvos em {arq_passos}")
    print(f"  Resumo salvo em {arq_resumo}")

    grafico_tempos(tempos, comprimentos, alturas, t_passo, lados, trechos, na_media)


def grafico_tempos(tempos, comprimentos, alturas, t_passo, lados, trechos, na_media):
    """Comprimento, altura e tempo de cada passo ao longo da coleta, com os trechos marcados."""
    import matplotlib.pyplot as plt
    cor = {"ESQ": "darkorange", "DIR": "green"}
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    series = ((comprimentos * 100, "Comprimento (cm)"), (alturas, "Altura do pé (cm)"), (t_passo, "Tempo do passo (s)"))
    for ax, (v, rot) in zip(axes, series):
        for lado, ch in (("ESQ", "Pé E"), ("DIR", "Pé D")):
            m = lados == lado
            ax.scatter(tempos[m & na_media], v[m & na_media], color=cor[lado], s=28, label=ch)
            ax.scatter(tempos[m & ~na_media], v[m & ~na_media], color=cor[lado], s=28, marker="x", alpha=0.5)
        for k in np.unique(trechos):
            tk = tempos[trechos == k]
            ax.axvspan(tk[0], tk[-1], color="gray", alpha=0.08)
        ax.set_ylabel(rot)
        ax.grid(True, alpha=0.3)
    axes[0].legend(fontsize=8, loc="upper right")
    axes[-1].set_xlabel("Tempo (s)  ·  faixas cinza = trechos de caminhada  ·  x = fora das médias")
    titulo = "Passos ao longo da captura livre"
    if cp.PARTICIPANTE:
        titulo += f" · {cp.PARTICIPANTE['nome']} · altura {cp.PARTICIPANTE['altura_cm']:.0f} cm"
    fig.suptitle(titulo, fontweight="bold")
    fig.tight_layout()
    cp.salvar_figura(fig, "tempos_passo.png")


def _relatorio_livre(t_passo, d_passo_ap, larg_base_m, clear_esq_cm, clear_dir_cm, torn_esq_cm, torn_dir_cm,
                     alt_quadril_cm, posturas, tempos_impactos, dist_impactos, impactos_ms2,
                     clearances_passos_cm, tornozelos_passos_cm, pes_passos, projecoes_ap, mascara_validos,
                     reais_m=None):
    """Relatório do calculo_passo e, em seguida, as variáveis da captura livre."""
    _relatorio_original(t_passo, d_passo_ap, larg_base_m, clear_esq_cm, clear_dir_cm, torn_esq_cm, torn_dir_cm,
                        alt_quadril_cm, posturas, tempos_impactos, dist_impactos, impactos_ms2,
                        clearances_passos_cm, tornozelos_passos_cm, pes_passos, projecoes_ap, mascara_validos,
                        reais_m=reais_m)
    try:
        variaveis_livre(t_passo, larg_base_m, clear_esq_cm, clear_dir_cm, tempos_impactos, dist_impactos,
                        clearances_passos_cm, pes_passos, mascara_validos)
    except Exception as e:
        print(f"Não foi possível calcular as variáveis da captura livre: {e}")


def _calibracao_livre():
    """Calibração parada do calculo_passo e, se houver duração, o cronômetro da gravação."""
    _calibracao_original()
    if DURACAO_S:
        def parar():
            fim = time.time() + DURACAO_S
            while cp.rodando and time.time() < fim:
                cp.etapa_atual = f"livre {fim - time.time():5.0f}s"
                time.sleep(0.2)
            cp.etapa_atual = ""
            cp._aviso_sonoro(3)
            print("\n\n   ✅ Tempo da gravação terminou. Encerrando...")
            cp.roteiro_terminou = True
        threading.Thread(target=parar, daemon=True).start()
    print("\n   🚶 Captura livre: caminhe, pare e vire à vontade."
          + (f" A gravação para sozinha em {DURACAO_S:.0f} s." if DURACAO_S else ""))


cp.gerar_relatorio_e_graficos = _relatorio_livre
cp.executar_calibracao_inicial = _calibracao_livre


def pedir_duracao(entrada=input):
    while True:
        txt = entrada("   Duração da gravação em segundos (Enter = até Ctrl + C): ").strip().replace(",", ".")
        if not txt:
            return None
        try:
            v = float(txt)
        except ValueError:
            print("   Digite um número de segundos ou só Enter.")
            continue
        if v >= 5:
            return v
        print("   Use pelo menos 5 segundos.")


def capturar(duracao=None):
    """Cadastro, calibração parada, gravação livre e processamento."""
    global DURACAO_S
    cp.PEDIR_PARTICIPANTE = False
    cp.PARTICIPANTE = cp.cadastro.pedir_participante()
    cp.PARTICIPANTE["experimento"] = "livre"
    DURACAO_S = duracao if duracao is not None else pedir_duracao()
    pasta = cp.cadastro.criar_pasta_coleta(cp.PARTICIPANTE)
    cp.definir_pasta_saida(pasta)
    print(f"   Os arquivos desta coleta vão para {pasta}\n")
    cp.main()


def reprocessar(pasta):
    """Refaz o processamento de uma coleta salva (dados_brutos_entrada.json) com o código atual."""
    arq = os.path.join(pasta, "dados_brutos_entrada.json")
    with open(arq, encoding="utf-8") as f:
        e = json.load(f)
    for tid, d in e["trackers"].items():
        if not d:
            continue
        n = min(len(v) for v in d.values())  # listas podem diferir se a captura não parou limpa
        cp.dados[tid] = {k: list(v)[:n] for k, v in d.items()}
    for lado, d in e["imu"].items():
        n = min(len(v) for v in d.values())
        cp.dados_imu[lado] = {k: list(v)[:n] for k, v in d.items()}
    for seg, d in e.get("quats", {}).items():
        n = min(len(d["t"]), len(d["q"]))
        cp.dados_quat[seg] = {"t": d["t"][:n], "q": [tuple(q) for q in d["q"][:n]]}
    c = e["calibracao"]
    for k in ("dist_qp_e_ref", "dist_qp_d_ref", "yq_ref", "chao_esq_ref", "chao_dir_ref",
              "limiar_ruido_chao", "pitch_tronco_ref"):
        if c.get(k) is not None:
            setattr(cp, k, c[k])
    cp.calibrado = True
    if c.get("cadeia_comprimentos") and c.get("cadeia_vetor_local"):
        cad = cadeia_pernas.CadeiaPernas(c["cadeia_comprimentos"])
        cad.vetor_local = {k: np.array(v) for k, v in c["cadeia_vetor_local"].items()}
        cad.calibrado = True
        cp.cadeia = cad
    else:
        cp.cadeia = None
    if e.get("ganhos_metodo"):
        cp.ganhos_metodo = {k: tuple(v) for k, v in e["ganhos_metodo"].items()}
    cp.ganho_esq, cp.ganho_dir = e.get("ganho_esq", 1.0), e.get("ganho_dir", 1.0)
    cp.fases_sessao[:] = [tuple(f) for f in (e.get("fases") or [])]
    cp.PARTICIPANTE = e.get("participante")
    cp.definir_pasta_saida(pasta)
    cp.salvar_dados_brutos = lambda: None  # não regrava as entradas originais
    cp.rodando = False
    print(f"Reprocessando {pasta}")
    cp.processar_coleta()


def main():
    ap = argparse.ArgumentParser(description="Captura livre das IMUs do SlimeVR (sem roteiro).")
    ap.add_argument("--duracao", type=float, help="segundos de gravação (sem isso, pergunta no início)")
    ap.add_argument("--reprocessar", metavar="PASTA", help="refaz o processamento de uma coleta salva")
    ap.add_argument("--sem-janelas", action="store_true", help="não abre as janelas dos gráficos (os PNG são salvos)")
    args = ap.parse_args()
    if args.sem_janelas:
        cp.MOSTRAR_GRAFICOS = False
    if args.reprocessar:
        reprocessar(args.reprocessar)
    else:
        capturar(args.duracao)


if __name__ == "__main__":
    main()
