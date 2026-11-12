"""
Experimento de comprimento do passo: três circuitos de placas de EVA (30, 40 e 50 cm entre os
calcanhares, 20 pegadas cada), com talco no calcanhar e o d de cada pegada medido com fita.

Coleta (mesmo cadastro e cálculos do piloto.py):
  1. nome e altura do participante e rodada (1 = familiarização, só o circuito de 30 cm; 2 = coleta, os três em ordem sorteada);
  2. calibração estática (os dois pés no chão), sem caixa;
  3. em cada circuito: pessoa parada com o calcanhar D na placa 0 → [Enter] começa → caminha pisando
     nas placas → para com os pés juntos → [Enter] termina. Talco no calcanhar antes de cada circuito.
No fim da familiarização (depois das medidas), [Enter] já abre a coleta com o mesmo participante,
sem novo cadastro (GANCHO_COLETA, 12/11); a calibração é feita de novo.
Depois da gravação, o programa pede, circuito por circuito, o d de cada pegada: distância da borda de
trás da placa até a borda de trás do calcanhar no talco. Passo real = espaçamento + d atual − d anterior.

O comprimento do sistema sai de duas formas e as duas são comparadas com o real:
  - máximo do apoio: maior distância à frente-atrás entre os pés enquanto os dois estão no chão;
  - toque do calcanhar: distância no pico de aceleração da IMU do pé (+25 ms).
A análise mostra o erro por circuito e por pé, sem fator e com um fator por pé calculado sem o
próprio passo (deixa-um-fora). Arquivos: pegadas.csv, analise_pegadas.txt e analise_pegadas.json.

    uv run python experimento_comprimento.py                    # coleta + medidas + análise
    uv run python experimento_comprimento.py --medir PASTA      # continua as medidas
    uv run python experimento_comprimento.py --analisar PASTA   # só refaz a análise
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

import marcas_chao
import piloto

piloto.EXPERIMENTO = "comprimento"
piloto.CALIBRAR_CAIXA = False
piloto.VERIFICAR_CALIBRACAO = False
piloto.COM_ENTER = True
piloto.MOSTRAR_GRAFICOS = False       # os PNG são salvos; as janelas travariam as medidas
piloto.ROTEIRO = piloto.montar_roteiro()

NOME_PEGADAS = "pegadas.csv"
CAB = ["circuito", "passo", "pe_da_frente", "t_s", "sistema_cm", "sistema_toque_cm",
       "d_cm", "espacamento_cm", "real_cm"]
METODOS = (("sistema_cm", "MÁXIMO DO APOIO (maior distância entre os pés com os dois no chão)"),
           ("sistema_toque_cm", "TOQUE DO CALCANHAR (distância no pico de aceleração da IMU)"))


def _num(s):
    try:
        return float(str(s).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _fmt(v):
    return "" if v is None or v != v else f"{v:.1f}".replace(".", ",")


# ----------------------------- coleta -----------------------------
_captura = {}
_orig_plato = piloto.comprimento_pelo_plato
_orig_processar = piloto.processar_coleta


def _plato(t, d_ap, *args):
    res = _orig_plato(t, d_ap, *args)
    _captura.update(t=np.asarray(t, float), d=np.asarray(d_ap, float), passos=res)
    return res


def salvar_pegadas(pasta):
    if "passos" not in _captura:
        print("Nenhum passo detectado: pegadas.csv não foi criado.")
        return False
    t, d = _captura["t"], _captura["d"]
    tempos, comps, _, pes, _ = _captura["passos"]
    linhas = []
    for nome, a, b in piloto.fases_sessao:
        if not nome.startswith("caminhada"):
            continue
        c = nome.rsplit("_", 1)[-1]
        idx = [i for i in range(len(tempos)) if a <= tempos[i] <= b + 1.0]
        for k, i in enumerate(idx, 1):
            pe = str(pes[i])
            toque = None if "*" in pe else abs(float(np.interp(tempos[i] + piloto.TAU_SYNC_OSC, t, d))) * 100
            linhas.append([c, k, "E" if "ESQ" in pe else "D", f"{tempos[i]:.2f}",
                           _fmt(comps[i] * 100), _fmt(toque), "", "", ""])
    with open(os.path.join(pasta, NOME_PEGADAS), "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f, delimiter=";").writerows([CAB] + linhas)
    por_c = {c: sum(1 for l in linhas if l[0] == c) for c in dict.fromkeys(l[0] for l in linhas)}
    print("\n  📝 Passos detectados: " + ", ".join(f"circuito {c}: {n}" for c, n in por_c.items()))
    return True


def _processar():
    _orig_processar()
    pasta = piloto.PASTA_COLETA
    if not salvar_pegadas(pasta):
        return
    r = input("\n    Medir as pegadas agora? [Enter] sim | [f] depois: ").strip().lower()
    if r == "f":
        print(f"    Para medir depois: uv run python experimento_comprimento.py --medir \"{pasta}\"")
        return
    medir_e_analisar(pasta)


piloto.comprimento_pelo_plato = _plato
piloto.processar_coleta = _processar


# ----------------------------- medidas -----------------------------
def digitar_d(pasta):
    """Pede, por circuito, o d da pegada de partida e de cada passo. Resposta "d" ou "d espaçamento"
    (se aquele par de placas não estiver no espaçamento do circuito). Enter vazio = sem medida;
    v = volta; f = para e continua depois (--medir)."""
    arq = os.path.join(pasta, NOME_PEGADAS)
    arq_cfg = os.path.join(pasta, "pegadas_placas.json")
    with open(arq, encoding="utf-8-sig") as f:
        dados = list(csv.reader(f, delimiter=";"))[1:]
    cfg = {}
    if os.path.exists(arq_cfg):
        with open(arq_cfg, encoding="utf-8") as f:
            cfg = json.load(f)
    I = {c: CAB.index(c) for c in CAB}

    def gravar():
        for c in dict.fromkeys(l[0] for l in dados):
            d_ant = (cfg.get("d0_cm") or {}).get(c)
            for l in (l for l in dados if l[0] == c):
                dd, e = _num(l[I["d_cm"]]), _num(l[I["espacamento_cm"]])
                l[I["real_cm"]] = _fmt(e + dd - d_ant) if None not in (dd, e, d_ant) else ""
                d_ant = dd
        with open(arq, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerows([CAB] + dados)
        with open(arq_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    print("\n" + "=" * 90)
    print("  📏 MEDIDAS: d = borda de trás da placa até a borda de trás do calcanhar no talco (cm)")
    print("     Meça reto, na direção da caminhada. Siga as pegadas na ordem, a partir da partida.")
    print("     Digite \"d\" ou \"d espaçamento\" (se aquele par de placas não estiver no espaçamento do circuito).")
    print("     Enter vazio = sem medida (pegada sem talco ou passo que não casa)   v = volta   f = para")
    print("=" * 90)
    cfg.setdefault("d0_cm", {})
    # lista de perguntas: (circuito, índice na tabela ou None para a partida)
    perguntas = []
    for c in dict.fromkeys(l[0] for l in dados):
        perguntas.append((c, None))
        perguntas += [(c, i) for i, l in enumerate(dados) if l[0] == c]
    feitas = cfg.get("feitas", 0)
    k = feitas
    while k < len(perguntas):
        c, i = perguntas[k]
        esp = float(marcas_chao.COMPRIMENTO_FIXO_CM.get(c, 30))
        if i is None:
            if k == 0 or perguntas[k - 1][0] != c:
                print(f"\n   --- Circuito {c} (placas a {esp:.0f} cm) ---")
            atual = cfg["d0_cm"].get(c)
            perg = "    Pegada de PARTIDA (calcanhar D parado) — d"
        else:
            l = dados[i]
            atual = _num(l[I["d_cm"]])
            perg = f"    Passo {l[1]:>2s} pé {l[2]} (sistema {l[4]} cm) — d"
        r = input(f"{perg}{' [' + _fmt(atual) + ']' if atual is not None else ''}: ").strip().lower()
        if r == "f":
            cfg["feitas"] = k
            gravar()
            print(f"    Parado. Para continuar: uv run python experimento_comprimento.py --medir \"{pasta}\"")
            return False
        if r == "v":
            k = max(0, k - 1)
            continue
        partes = r.replace(";", " ").split()
        dd = _num(partes[0]) if partes else None
        e = _num(partes[1]) if len(partes) > 1 else esp
        if partes and (dd is None or dd < 0 or e is None or e <= 0):
            print("    Valor inválido. Ex.: 6,5   ou   6,5 41,2")
            continue
        if i is None:
            cfg["d0_cm"][c] = dd
        else:
            dados[i][I["d_cm"]] = _fmt(dd) if dd is not None else ""
            dados[i][I["espacamento_cm"]] = _fmt(e) if dd is not None else ""
        cfg["feitas"] = k + 1
        gravar()
        if i is not None and dados[i][I["real_cm"]]:
            print(f"      passo = {dados[i][I['real_cm']]} cm")
        k += 1
    gravar()
    print(f"    Medidas gravadas em {arq}")
    return True


def medir_e_analisar(pasta):
    if digitar_d(pasta):
        analisar(pasta)


# ----------------------------- análise -----------------------------
def ler(pasta):
    with open(os.path.join(pasta, NOME_PEGADAS), encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f, delimiter=";") if _num(r.get("real_cm"))]


def _analisar_metodo(linhas, chave):
    ps = [(r["circuito"], r["pe_da_frente"], _num(r[chave]), _num(r["real_cm"])) for r in linhas if _num(r.get(chave))]
    if not ps:
        return None, ["   sem passos com este método"]
    c = np.array([p[0] for p in ps]); pe = np.array([p[1] for p in ps])
    s = np.array([p[2] for p in ps]); r = np.array([p[3] for p in ps])
    corr = np.full(len(s), np.nan)
    for i in range(len(s)):           # fator do pé sem o próprio passo (todos os circuitos)
        o = (pe == pe[i]); o[i] = False
        if o.sum() >= 2:
            corr[i] = s[i] * np.median(r[o] / s[o])
    out, res = [], {}
    out.append(f"   {'circuito':10s} {'pé':5s} {'n':>3s} {'real':>6s} {'sistema':>8s} {'erro':>6s} {'|erro|':>7s}"
               f" {'erro c/fator':>12s} {'|erro| c/fator':>14s}")
    for cc in list(dict.fromkeys(c)) + ["todos"]:
        for pp in ("E", "D", "ambos"):
            k = ((c == cc) | (cc == "todos")) & ((pe == pp) | (pp == "ambos"))
            if not k.any():
                continue
            e, ec = s[k] - r[k], corr[k] - r[k]
            ok = ~np.isnan(ec)
            nome = f"{cc} ({marcas_chao.COMPRIMENTO_FIXO_CM.get(cc, '')} cm)" if cc != "todos" else "todos"
            out.append(f"   {nome:10s} {pp:5s} {k.sum():3d} {np.mean(r[k]):6.1f} {np.mean(s[k]):8.1f} {np.mean(e):+6.1f}"
                       f" {np.mean(np.abs(e)):7.1f} {np.mean(ec[ok]) if ok.any() else np.nan:+12.1f}"
                       f" {np.mean(np.abs(ec[ok])) if ok.any() else np.nan:14.1f}")
            res[f"{cc}_{pp}"] = {"n": int(k.sum()), "erro_medio_cm": float(np.mean(e)),
                                 "erro_abs_cm": float(np.mean(np.abs(e))),
                                 "erro_medio_fator_cm": float(np.mean(ec[ok])) if ok.any() else None,
                                 "erro_abs_fator_cm": float(np.mean(np.abs(ec[ok]))) if ok.any() else None}
    fat = {pp: float(np.median(r[pe == pp] / s[pe == pp])) for pp in ("E", "D") if (pe == pp).any()}
    out.append("   Fator por pé (mediana real/sistema, todos os passos): "
               + ", ".join(f"{pp} {v:.3f}" for pp, v in fat.items()))
    if len(s) > 2:
        out.append(f"   Correlação real x sistema: r = {np.corrcoef(r, s)[0, 1]:.2f}")
        e = s - r
        out.append(f"   Limites de 95% do erro (sem fator): {np.mean(e) - 1.96 * np.std(e, ddof=1):+.1f} a "
                   f"{np.mean(e) + 1.96 * np.std(e, ddof=1):+.1f} cm")
    res["fator"] = fat
    return res, out


def analisar(pasta):
    linhas = ler(pasta)
    texto = [f"EXPERIMENTO DE COMPRIMENTO DO PASSO — {pasta}", f"Passos com medida de talco: {len(linhas)}",
             "Erro = sistema − real (cm). Fator por pé deixa-um-fora: calculado sem o próprio passo.", ""]
    resumo = {}
    for chave, titulo in METODOS:
        res, out = _analisar_metodo(linhas, chave)
        texto += [titulo] + out + [""]
        resumo[chave] = res
    t = "\n".join(texto)
    print("\n" + t)
    with open(os.path.join(pasta, "analise_pegadas.txt"), "w", encoding="utf-8") as f:
        f.write(t + "\n")
    with open(os.path.join(pasta, "analise_pegadas.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=1)
    print(f"Análise salva em {os.path.join(pasta, 'analise_pegadas.txt')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Experimento de comprimento do passo com talco (30/40/50 cm).")
    ap.add_argument("--medir", metavar="PASTA", help="continua as medidas do talco de uma coleta")
    ap.add_argument("--analisar", metavar="PASTA", help="refaz só a análise de uma coleta medida")
    args = ap.parse_args()
    if args.medir:
        medir_e_analisar(args.medir)
        sys.exit(0)
    if args.analisar:
        analisar(args.analisar)
        sys.exit(0)
    piloto.main()
