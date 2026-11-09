"""
TESTE — só a caminhada de 30 cm com talco e 20 pegadas (10 de cada pé), medidas com fita.

Script à parte. Mesma coleta e mesma planilha do teste_calibracao_passo.py, mas com UMA caminhada:
  1. calibração estática do calculo_passo (os dois pés no chão), sem caixa;
  2. caminhada no circuito de 30 cm (calcanhar a calcanhar), 20 pegadas, UMA direção:
     [Enter] começa, [Enter] quando a pessoa parar no fim com os pés juntos. A gravação termina.
No fim salva pegadas.csv na pasta da coleta (um passo detectado por linha, coluna real_cm vazia).

Depois da captura o programa pergunta, no terminal, o d de cada pegada: distância da borda de
trás da placa de EVA até a borda de trás do calcanhar marcado no talco, medida reta na direção da
caminhada. O passo é calculado sozinho: passo = espaçamento + d (atual) − d (anterior), com o
espaçamento entre as bordas de trás das placas (30 cm, ou o medido daquele par). Dá para parar
com 'f' e continuar depois; cada resposta já fica gravada no pegadas.csv:

    uv run python teste_passo_30_talco.py --medir dados/debug_casa/<pasta>     # continua as medidas
    uv run python teste_passo_30_talco.py --analisar dados/debug_casa/<pasta>  # só refaz a análise

A análise mostra, por pé e passo a passo, o erro do sistema contra a fita (sem fator) e com um
fator por pé calculado SEM o próprio passo (deixa-um-fora), para o ajuste não validar a si mesmo.
Resultado em analise_pegadas_30.txt e analise_pegadas_30.json.

    uv run python teste_passo_30_talco.py               # coleta
"""
import argparse
import csv
import json
import os
import sys
import time

import numpy as np

import calculo_passo as cp
import teste_calibracao_passo as T   # reaproveita a planilha e a leitura das pegadas

T.NOME_SCRIPT = "teste_passo_30_talco.py"
DIST_CM = 30.0
N_PEGADAS = 20


def _uma_caminhada():
    T._caminhada(T.ETAPA_CALIB, f"CAMINHADA DE {DIST_CM:.0f} cm ({N_PEGADAS} pegadas, {N_PEGADAS // 2} de cada pé)", DIST_CM)
    print("\n    Caminhada concluída. Encerrando a gravação... (meça as pegadas antes de limpar)")
    cp.roteiro_terminou = True


ESPACAMENTO_PADRAO_CM = 30.0


def digitar_d(pasta):
    """Pede o d de cada pegada (borda de trás da placa até o calcanhar no talco) e calcula o passo:
    passo k = espaçamento + d_k − d_(k−1). A pegada 0 é o calcanhar de partida (pé de trás, parado).
    Resposta: "6,5" ou "6,5 31,2" (o segundo número é o espaçamento deste par, se não for o padrão).
    Enter vazio = sem medida; v = volta; f = para e continua depois (--medir)."""
    arq = os.path.join(pasta, T.NOME_PEGADAS)
    arq_cfg = os.path.join(pasta, "pegadas_placas.json")
    with open(arq, encoding="utf-8-sig") as f:
        linhas = list(csv.reader(f, delimiter=";"))
    cab, dados = linhas[0], linhas[1:]
    for c in ("d_cm", "espacamento_cm"):
        if c not in cab:
            cab.append(c)
            for l in dados:
                l.append("")
    ir, idd, iesp = cab.index("real_cm"), cab.index("d_cm"), cab.index("espacamento_cm")
    cfg = {}
    if os.path.exists(arq_cfg):
        with open(arq_cfg, encoding="utf-8") as f:
            cfg = json.load(f)

    def gravar():
        d_ant = cfg.get("d0_cm")
        for l in dados:
            d, e = T._num(l[idd]), T._num(l[iesp])
            l[ir] = f"{e + d - d_ant:.1f}".replace(".", ",") if (d is not None and d_ant is not None and e is not None) else ""
            d_ant = d
        with open(arq, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerows([cab] + dados)
        with open(arq_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    print("\n" + "=" * 90)
    print("  📏 MEDIDAS: d = borda de trás da placa até a borda de trás do calcanhar no talco (cm)")
    print("     Meça reto, na direção da caminhada. Use a marca do talco, não o desenho da placa.")
    print("     Digite \"d\" ou \"d espaçamento\" (se este par de placas não estiver a 30 cm).")
    print("     Enter vazio = sem medida   v = volta   f = para e continua depois")
    print("=" * 90)
    if "espacamento_cm" not in cfg:
        r = input(f"    Espaçamento padrão entre as bordas de trás das placas (cm) [{ESPACAMENTO_PADRAO_CM:.0f}]: ").strip()
        cfg["espacamento_cm"] = T._num(r) if T._num(r) else ESPACAMENTO_PADRAO_CM
    # passo -1 = pegada de partida; depois uma pegada por passo detectado
    i = -1 if "d0_cm" not in cfg else next((k for k, l in enumerate(dados) if not l[idd].strip() and not l[iesp].strip()), len(dados))
    while i < len(dados):
        if i == -1:
            pergunta = "    Pegada de PARTIDA (calcanhar de trás, parado) — d"
            atual = cfg.get("d0_cm")
        else:
            l = dados[i]
            pergunta = f"    Passo {l[1]:>2s} pé {l[2]} (sistema {l[4]} cm) — d"
            atual = T._num(l[idd])
        extra = f" [{atual:.1f}]".replace(".", ",") if atual is not None else ""
        r = input(f"{pergunta}{extra}: ").strip().lower()
        if r == "f":
            gravar()
            print(f"    Parado. Para continuar: uv run python teste_passo_30_talco.py --medir \"{pasta}\"")
            return False
        if r == "v":
            i = max(-1, i - 1)
            continue
        partes = r.replace(";", " ").split()
        d = T._num(partes[0]) if partes else None
        e = T._num(partes[1]) if len(partes) > 1 else cfg["espacamento_cm"]
        if partes and (d is None or d < 0 or e is None or e <= 0):
            print("    Valor inválido. Ex.: 6,5   ou   6,5 31,2")
            continue
        if i == -1:
            cfg["d0_cm"] = d
        else:
            dados[i][idd] = f"{d:.1f}".replace(".", ",") if d is not None else ""
            dados[i][iesp] = f"{e:.1f}".replace(".", ",") if d is not None else "-"
        gravar()
        if i >= 0 and dados[i][ir]:
            print(f"      passo = {dados[i][ir]} cm")
        i += 1
    gravar()
    print(f"    Medidas gravadas em {arq}")
    return True


def medir_e_analisar(pasta):
    if digitar_d(pasta):
        analisar(pasta)


cp.caminhada_calibracao_passo = _uma_caminhada


def _fator(v):
    r = np.array([p["real"] / p["sistema"] for p in v])
    if len(r) >= 4:
        r = r[cp.remover_outliers(r)]
    return float(np.clip(np.median(r), *T.FATOR_LIMITES)) if len(r) >= 2 else None


def _resumo(d):
    d = np.asarray(d, dtype=float)
    dp = float(np.std(d, ddof=1)) if len(d) > 1 else float("nan")
    return {"n": int(len(d)), "erro_medio_cm": float(d.mean()), "erro_abs_medio_cm": float(np.abs(d).mean()),
            "dp_cm": dp, "limites_95_cm": [float(d.mean() - 1.96 * dp), float(d.mean() + 1.96 * dp)]}


def analisar(pasta):
    passos = [p for p in T.ler_pegadas(pasta) if p["caminhada"] == T.ETAPA_CALIB]
    arq_txt = os.path.join(pasta, "analise_pegadas_30.txt")
    terminal = sys.stdout
    with open(arq_txt, "w", encoding="utf-8") as f:
        sys.stdout = cp._Tee(f, terminal)
        try:
            print("=" * 90)
            print(f"  👣 PASSO DE {DIST_CM:.0f} cm COM PEGADAS MEDIDAS (erro = sistema − fita, em cm)")
            print("=" * 90)
            print(f"  Passos com medida: {len(passos)}")
            if len(passos) < 3:
                print("  Poucos passos com real_cm preenchido.")
                return
            linhas, res = [], {}
            for p in passos:
                outros = [q for q in passos if q is not p and q["pe"] == p["pe"]]
                f_loo = _fator(outros) or _fator([q for q in passos if q is not p]) or 1.0
                linhas.append((p, f_loo, p["sistema"] * f_loo))
            for pe in ("E", "D", "ambos"):
                v = [l for l in linhas if pe == "ambos" or l[0]["pe"] == pe]
                if not v:
                    continue
                res[pe] = {"real_medio_cm": float(np.mean([l[0]["real"] for l in v])),
                           "sistema_medio_cm": float(np.mean([l[0]["sistema"] for l in v])),
                           "fator_todos": _fator([l[0] for l in v]),
                           "sem_fator": _resumo([l[0]["sistema"] - l[0]["real"] for l in v]),
                           "com_fator_deixa_um_fora": _resumo([l[2] - l[0]["real"] for l in v])}
            print(f"\n  {'pé':6s} {'n':>3s} {'fita':>6s} {'sistema':>8s} {'fator':>6s} | {'erro sem':>8s} {'|erro|':>6s} | "
                  f"{'erro com':>8s} {'|erro|':>6s} {'limites 95% (com)':>18s}")
            for pe, x in res.items():
                s, c = x["sem_fator"], x["com_fator_deixa_um_fora"]
                fat = f"{x['fator_todos']:.3f}" if x["fator_todos"] else "  -  "
                print(f"  {pe:6s} {s['n']:3d} {x['real_medio_cm']:6.1f} {x['sistema_medio_cm']:8.1f} {fat:>6s} | "
                      f"{s['erro_medio_cm']:+8.1f} {s['erro_abs_medio_cm']:6.1f} | {c['erro_medio_cm']:+8.1f} "
                      f"{c['erro_abs_medio_cm']:6.1f}   {c['limites_95_cm'][0]:+5.1f} a {c['limites_95_cm'][1]:+5.1f}")
            print("\n  Passo a passo (fator de cada linha calculado sem ela):")
            for p, fl, c in linhas:
                print(f"    {p['passo']:>3s} pé {p['pe']}: fita {p['real']:5.1f}  sistema {p['sistema']:5.1f} "
                      f"(erro {p['sistema'] - p['real']:+5.1f})  com fator {fl:.3f}: {c:5.1f} (erro {c - p['real']:+5.1f})")
            print("\n  O fator da coluna 'fator' usa todos os passos do pé; é o que iria para as próximas coletas.")
            with open(os.path.join(pasta, "analise_pegadas_30.json"), "w", encoding="utf-8") as fj:
                json.dump(res, fj, indent=2, ensure_ascii=False)
            print(f"\n  Salvo em {arq_txt} e analise_pegadas_30.json")
        finally:
            sys.stdout = terminal


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Caminhada de 30 cm com talco: sistema x pegadas medidas.")
    ap.add_argument("--analisar", metavar="PASTA", help="analisa o pegadas.csv preenchido de uma coleta")
    ap.add_argument("--medir", metavar="PASTA", help="continua a digitar as medidas da fita de uma coleta")
    args = ap.parse_args()
    if args.medir:
        medir_e_analisar(args.medir)
        sys.exit(0)
    if args.analisar:
        analisar(args.analisar)
        sys.exit(0)
    nome = time.strftime("%Y%m%d_%H%M%S") + "_pegadas_30cm"
    pasta = os.path.join("dados", "debug_casa", nome)
    os.makedirs(pasta, exist_ok=True)
    cp.definir_pasta_saida(pasta)
    print(f"Os arquivos desta coleta vão para {pasta}")
    print(f"Prepare o circuito de {DIST_CM:.0f} cm com {N_PEGADAS} marcas e talco na sola dos dois pés.")
    cp.main()
    if os.path.exists(os.path.join(pasta, T.NOME_PEGADAS)):
        input("\n    [Enter] para digitar as medidas das pegadas (ou feche e use --medir depois): ")
        medir_e_analisar(pasta)
