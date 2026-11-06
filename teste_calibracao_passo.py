"""
TESTE — calibração PARADA do comprimento do passo, conferida em caminhadas com pegadas de talco.

Script à parte (não muda o experimento de comprimento). Ideia (06/11): o sistema mede o passo no
platô, quando os dois pés estão no chão afastados. Então dá para calibrar com a pessoa PARADA nessa
posição, em distâncias conhecidas, como a caixa na altura. As caminhadas ficam todas para teste.

Coleta:
  1. calibração estática do calculo_passo (os dois pés no chão), sem caixa;
  2. CALIBRAÇÃO PARADA: para cada distância (padrão 25, 35, 45 e 55 cm, nenhuma igual a um circuito) e cada pé na frente, a pessoa
     põe o calcanhar de trás na marca 0 e o calcanhar da frente na marca da distância, [Enter],
     fica parada 3 s até o bipe. Conferir no olho se os calcanhares estão nas marcas;
  3. CIRCUITOS com talco (padrão 30, 40 e 50 cm, em ordem sorteada): [Enter] começa, [Enter]
     termina, uma direção só. Entre um circuito e outro: ler as pegadas, limpar, mudar a EVA;
  4. CAMINHADA LIVRE (opcional, padrão sim): ritmo confortável, numa faixa ao lado da EVA.
No fim o programa salva:
  - calibracao_parada.json: distância medida pelo sistema em cada posição parada;
  - pegadas.csv: um passo detectado por linha (caminhada, número, pé da frente, valor do sistema)
    e a coluna real_cm vazia, para preencher com o talco (borda de trás do calcanhar até a borda de
    trás do calcanhar da pegada seguinte).

Depois de preencher real_cm (Excel serve; vírgula ou ponto):

    uv run python teste_calibracao_passo.py --analisar dados/debug_casa/<pasta>

A análise compara, em cada caminhada, o erro (sistema − talco):
  - sem correção;
  - com o FATOR da calibração parada (por pé: mediana de real / sistema);
  - com a RETA da calibração parada (por pé: real = a + b · sistema);
  - com o RODÍZIO dos circuitos (fator dos outros circuitos; a livre usa todos os circuitos).
Se a calibração parada errar perto do rodízio, ela serve. Resultado em analise_pegadas.txt/.json.

    uv run python teste_calibracao_passo.py
    uv run python teste_calibracao_passo.py --parado 25 35 45 55 --circuitos 30 40 50 --sem-livre
    uv run python teste_calibracao_passo.py --ordem          # circuitos na ordem dada, sem sorteio
"""
import argparse
import csv
import json
import os
import random
import sys
import time

import numpy as np

import calculo_passo as cp

NOME_PEGADAS = "pegadas.csv"
NOME_PARADA = "calibracao_parada.json"
FATOR_LIMITES = (0.6, 1.4)
PREFIXO_PARADO = "parado_"
PREFIXO_CIRCUITO = "circuito_"
ETAPA_LIVRE = "livre"
TEMPO_PARADO_S = 3.0
DESCARTAR_INICIO_PARADO_S = 0.5   # tempo para a pessoa assentar depois do Enter
LIMIAR_ESTAVEL_CM = 1.5           # desvio padrão acima disso: a pessoa mexeu durante a posição

cp.PEDIR_PARTICIPANTE = False   # sem cadastro
cp.CALIBRAR_CAIXA = False       # OSC bruto, sem ganho da caixa
cp.CALIBRAR_PASSO = True        # faz o main chamar as etapas abaixo depois da calibração estática
cp.ROTEIRO_GUIADO = False

DIST_PARADO_CM = [25.0, 35.0, 45.0, 55.0]   # nenhuma igual aos circuitos de teste
CIRCUITOS_CM = [30.0, 40.0, 50.0]
FAZER_LIVRE = True
SORTEAR = True

_sinal_ap = {}   # t e distância anteroposterior entre os pés, guardados no processamento


# ----------------------------- coleta -----------------------------
def _nome_pe(pe):
    return "ESQUERDO" if pe == "E" else "DIREITO"


def _parado():
    print("\n" + "=" * 90)
    print("   🦶 CALIBRAÇÃO PARADA DO PASSO")
    print("=" * 90)
    print("    Em cada posição: calcanhar de TRÁS na marca 0, calcanhar da FRENTE na marca da distância,")
    print("    peso nos dois pés, tronco reto, olhando para a frente. Confira os calcanhares nas marcas.")
    for d in DIST_PARADO_CM:
        for pe in ("E", "D"):
            input(f"\n    {d:.0f} cm, pé {_nome_pe(pe)} na FRENTE. [Enter] quando estiver na posição: ")
            cp._aviso_sonoro(1)
            t0 = time.time() - cp.tempo_inicial
            fim = time.time() + TEMPO_PARADO_S
            while cp.rodando and time.time() < fim:
                print(f"    Parado... {fim - time.time():3.1f}s   ", end="\r", flush=True)
                time.sleep(0.1)
            cp.fases_sessao.append((f"{PREFIXO_PARADO}{d:g}_{pe}", t0, time.time() - cp.tempo_inicial))
            cp._aviso_sonoro(2)
            print("    Pronto.                ")


def _caminhada(etapa, titulo, instrucao):
    print("\n" + "=" * 90)
    print(f"   👣 {titulo}, UMA direção só")
    print("=" * 90)
    print(f"    {instrucao}")
    input("    [Enter] para COMEÇAR: ")
    cp._aviso_sonoro(2)
    t0 = time.time() - cp.tempo_inicial
    print("    Caminhando... a pessoa para no fim, com os pés juntos.")
    input("    [Enter] quando ela PARAR no fim: ")
    cp.fases_sessao.append((etapa, t0, time.time() - cp.tempo_inicial))
    cp._aviso_sonoro(1)


def _etapas():
    """Substitui a caminhada de calibração do calculo_passo: parado, circuitos e livre."""
    _parado()
    ordem = list(CIRCUITOS_CM)
    if SORTEAR:
        random.shuffle(ordem)
    print("\n    Ordem dos circuitos: " + ", ".join(f"{c:.0f} cm" for c in ordem))
    for i, c in enumerate(ordem):
        if i > 0:
            print("\n    Leia as pegadas do circuito anterior, limpe a EVA e monte o próximo circuito.")
            print("    A gravação continua, mas esse tempo não entra no resultado.")
        _caminhada(f"{PREFIXO_CIRCUITO}{c:g}", f"CIRCUITO DE {c:.0f} cm",
                   "Talco na sola (caixa no início), pessoa parada no início, pés juntos, tronco reto.")
    if FAZER_LIVRE:
        print("\n    As pegadas do último circuito podem ficar para ler depois.")
        _caminhada(ETAPA_LIVRE, "CAMINHADA LIVRE",
                   "Talco na sola, faixa ao LADO da EVA, ritmo confortável, sem mirar em nada.")
    print("\n    Etapas concluídas. Encerrando a gravação...")
    cp.roteiro_terminou = True   # o laço principal termina e processa a coleta


_calcular_sinais_original = cp.calcular_sinais_completos


def _calcular_sinais(*args, **kwargs):
    saida = _calcular_sinais_original(*args, **kwargs)
    _sinal_ap["t"], _sinal_ap["d"] = np.asarray(saida[0]), np.asarray(saida[1])
    return saida


def medidas_paradas(t, d_ap, fases):
    """Distância anteroposterior entre os pés em cada posição parada (cm).

    d_ap > 0: pé ESQ à frente. Usa a mediana depois de DESCARTAR_INICIO_PARADO_S."""
    t, d_ap = np.asarray(t), np.asarray(d_ap)
    medidas = []
    for nome, t0, t1 in fases:
        if not nome.startswith(PREFIXO_PARADO):
            continue
        dist_txt, pe = nome[len(PREFIXO_PARADO):].rsplit("_", 1)
        m = (t >= t0 + DESCARTAR_INICIO_PARADO_S) & (t <= t1)
        if np.count_nonzero(m) < 10:
            medidas.append({"distancia_cm": float(dist_txt), "pe_pedido": pe, "sistema_cm": None,
                            "obs": "sem dados"})
            continue
        v = d_ap[m] * 100.0
        med = float(np.median(v))
        pe_medido = "E" if med > 0 else "D"
        dp = float(np.std(v))
        obs = []
        if pe_medido != pe:
            obs.append(f"sistema viu o pé {pe_medido} na frente")
        if dp > LIMIAR_ESTAVEL_CM:
            obs.append(f"mexeu (desvio {dp:.1f} cm)")
        medidas.append({"distancia_cm": float(dist_txt), "pe_pedido": pe, "pe_medido": pe_medido,
                        "sistema_cm": abs(med), "dp_cm": dp, "n": int(np.count_nonzero(m)),
                        "obs": "; ".join(obs)})
    return medidas


def _sem_fator(tempos, comprimentos, impactos, pes, projecoes):
    """Não aplica fator: a correção sai depois, na análise. Só guarda os passos."""
    cp.comprimentos_brutos_m = np.asarray(comprimentos, dtype=float).copy()
    _sem_fator.passos = (np.asarray(tempos, dtype=float), np.asarray(comprimentos, dtype=float),
                         [str(p) for p in pes])
    return tempos, comprimentos, impactos, pes, projecoes


def _salvar(pes, comprimentos):
    """Escreve calibracao_parada.json e pegadas.csv (um passo detectado por linha)."""
    medidas = medidas_paradas(_sinal_ap.get("t", []), _sinal_ap.get("d", []), cp.fases_sessao)
    arq_p = os.path.join(cp.PASTA_COLETA, NOME_PARADA)
    with open(arq_p, "w", encoding="utf-8") as f:
        json.dump({"medidas": medidas}, f, indent=2, ensure_ascii=False)
    print("\n" + "=" * 90)
    print("  🦶 Calibração parada (distância entre os pés vista pelo sistema):")
    for m in medidas:
        s = "  sem dados" if m["sistema_cm"] is None else f"{m['sistema_cm']:5.1f} cm"
        print(f"     {m['distancia_cm']:4.0f} cm, pé {m['pe_pedido']} na frente: {s}"
              + (f"   ⚠️ {m['obs']}" if m.get("obs") and m["sistema_cm"] is not None else ""))

    t, comp, pp = getattr(_sem_fator, "passos", (np.zeros(0), np.zeros(0), []))
    linhas = []
    for etapa, a, b in cp.fases_sessao:
        if not (etapa.startswith(PREFIXO_CIRCUITO) or etapa == ETAPA_LIVRE):
            continue
        idx = np.flatnonzero((t >= a) & (t <= b + 1.0))
        for k, i in enumerate(idx, 1):
            linhas.append([etapa, k, "E" if "ESQ" in pp[i] else "D", f"{t[i]:.2f}",
                           f"{comp[i] * 100:.1f}".replace(".", ","), ""])
    arq = os.path.join(cp.PASTA_COLETA, NOME_PEGADAS)
    with open(arq, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["caminhada", "passo", "pe_da_frente", "t_s", "sistema_cm", "real_cm"])
        w.writerows(linhas)
    contagem = {}
    for l in linhas:
        contagem[l[0]] = contagem.get(l[0], 0) + 1
    print(f"\n  📝 Passos detectados: " + ", ".join(f"{k} {v}" for k, v in contagem.items()))
    print(f"     Planilha: {arq}")
    print("     Preencha real_cm com o talco (calcanhar a calcanhar, na ordem).")
    print("     Se o número de pegadas não bater com o de passos, deixe real_cm vazio onde não souber.")
    print(f"     Depois: uv run python teste_calibracao_passo.py --analisar \"{cp.PASTA_COLETA}\"")
    print("=" * 90)


# ----------------------------- análise -----------------------------
def _num(s):
    s = (s or "").strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def ler_pegadas(pasta):
    passos = []
    with open(os.path.join(pasta, NOME_PEGADAS), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            s, real = _num(r.get("sistema_cm")), _num(r.get("real_cm"))
            if s is None or real is None or s <= 0 or real <= 0:
                continue
            passos.append({"caminhada": r["caminhada"].strip(), "passo": r["passo"],
                           "pe": r["pe_da_frente"].strip().upper()[:1], "sistema": s, "real": real})
    return passos


def ler_parada(pasta):
    """Pontos da calibração parada válidos: [{"pe", "sistema", "real"}]."""
    arq = os.path.join(pasta, NOME_PARADA)
    if not os.path.exists(arq):
        return []
    with open(arq, encoding="utf-8") as f:
        medidas = json.load(f).get("medidas", [])
    pts = []
    for m in medidas:
        if m.get("sistema_cm") is None or m.get("pe_medido", m["pe_pedido"]) != m["pe_pedido"]:
            continue
        pts.append({"pe": m["pe_pedido"], "sistema": float(m["sistema_cm"]), "real": float(m["distancia_cm"])})
    return pts


def fatores(pontos):
    """Fator por pé = mediana de real / sistema (sem outliers); com < 3 pontos de um pé usa os dois."""
    def mediana_razao(v, minimo):
        r = np.array([p["real"] / p["sistema"] for p in v])
        r = r[cp.remover_outliers(r)] if len(r) else r
        return float(np.clip(np.median(r), *FATOR_LIMITES)) if len(r) >= minimo else None
    minimo = 2 if len(pontos) <= 6 else 3      # a parada tem poucos pontos (2-3 por pé)
    geral = mediana_razao(pontos, minimo)
    out = {}
    for pe in ("E", "D"):
        f = mediana_razao([p for p in pontos if p["pe"] == pe], minimo)
        out[pe] = f if f is not None else (geral if geral is not None else 1.0)
    return out


def retas(pontos):
    """Reta por pé real = a + b · sistema (mínimos quadrados); com < 2 distâncias usa os dois pés."""
    def ajuste(v):
        if len({round(p["real"], 1) for p in v}) < 2:
            return None
        b, a = np.polyfit([p["sistema"] for p in v], [p["real"] for p in v], 1)
        return (float(a), float(b))
    geral = ajuste(pontos) or (0.0, 1.0)
    return {pe: (ajuste([p for p in pontos if p["pe"] == pe]) or geral) for pe in ("E", "D")}


def corrigir(passos, metodo, par):
    if metodo == "sem":
        return np.array([p["sistema"] for p in passos])
    if metodo == "fator":
        return np.array([p["sistema"] * par[p["pe"]] for p in passos])
    return np.array([par[p["pe"]][0] + par[p["pe"]][1] * p["sistema"] for p in passos])


def erros(passos, corrigidos):
    r = {}
    real_todos = np.array([p["real"] for p in passos])
    for pe in ("E", "D", "ambos"):
        m = np.array([pe == "ambos" or p["pe"] == pe for p in passos], dtype=bool)
        if not np.any(m):
            continue
        d = corrigidos[m] - real_todos[m]
        dp = float(np.std(d, ddof=1)) if len(d) > 1 else float("nan")
        r[pe] = {"n": int(m.sum()), "real_medio_cm": float(real_todos[m].mean()),
                 "erro_medio_cm": float(d.mean()), "eam_cm": float(np.abs(d).mean()), "dp_cm": dp,
                 "limites_concordancia_cm": [float(d.mean() - 1.96 * dp), float(d.mean() + 1.96 * dp)]}
    return r


def analisar(pasta):
    passos = ler_pegadas(pasta)
    pontos = ler_parada(pasta)
    caminhadas = []
    for p in passos:
        if p["caminhada"] not in caminhadas:
            caminhadas.append(p["caminhada"])
    circuitos = [c for c in caminhadas if c.startswith(PREFIXO_CIRCUITO)]
    arq_txt = os.path.join(pasta, "analise_pegadas.txt")
    terminal = sys.stdout
    saida = {"calibracao_parada": pontos, "caminhadas": {}}
    with open(arq_txt, "w", encoding="utf-8") as f:
        sys.stdout = cp._Tee(f, terminal)
        try:
            print("=" * 90)
            print("  🧪 CALIBRAÇÃO PARADA DO PASSO x PEGADAS DE TALCO (erro = sistema − talco, em cm)")
            print("=" * 90)
            metodos = [("sem", "sem correção", None)]
            if pontos:
                f_par, r_par = fatores(pontos), retas(pontos)
                saida.update({"fator_parado": f_par, "reta_parada": r_par})
                print(f"  Calibração parada: {len(pontos)} posições válidas")
                for pe in ("E", "D"):
                    a, b = r_par[pe]
                    print(f"    pé {pe}: fator {f_par[pe]:.3f}   reta real = {a:+.1f} + {b:.3f} · sistema")
                metodos += [("fator", "fator parado", f_par), ("reta", "reta parada", r_par)]
            else:
                print("  Sem calibracao_parada.json válido: só sem correção e rodízio.")
            for cam in caminhadas:
                v = [p for p in passos if p["caminhada"] == cam]
                if len(v) < 3:
                    print(f"\n  {cam}: só {len(v)} passos com real_cm, fica fora.")
                    continue
                outros = [p for p in passos if p["caminhada"] in circuitos and p["caminhada"] != cam]
                linhas = list(metodos)
                if len(outros) >= 3:
                    nome_rod = "rodízio (outros circuitos)" if cam in circuitos else "fator dos circuitos"
                    linhas.append(("fator", nome_rod, fatores(outros)))
                print(f"\n  {cam} ({len(v)} passos, talco médio {np.mean([p['real'] for p in v]):.1f} cm)")
                print(f"    {'correção':28s} {'erro médio':>10s} {'|erro| médio':>12s} {'limites (95%)':>17s}   pé E / pé D |erro|")
                saida["caminhadas"][cam] = {}
                for metodo, nome, par in linhas:
                    e = erros(v, corrigir(v, metodo, par))
                    saida["caminhadas"][cam][nome] = e
                    x = e["ambos"]
                    lim = x["limites_concordancia_cm"]
                    pes = " / ".join(f"{e[pe]['eam_cm']:.1f}" if pe in e else "-" for pe in ("E", "D"))
                    print(f"    {nome:28s} {x['erro_medio_cm']:+10.1f} {x['eam_cm']:12.1f}   "
                          f"{lim[0]:+5.1f} a {lim[1]:+5.1f}   {pes}")
            print("\n  Leitura: se 'fator parado' ou 'reta parada' erram perto do rodízio, a calibração parada")
            print("  serve e as caminhadas ficam todas para teste. Se erram bem mais, o pé se comporta")
            print("  diferente andando e parado, e o rodízio continua sendo a calibração.")
            with open(os.path.join(pasta, "analise_pegadas.json"), "w", encoding="utf-8") as fj:
                json.dump(saida, fj, indent=2, ensure_ascii=False)
            print(f"\n  Salvo em {arq_txt} e analise_pegadas.json")
        finally:
            sys.stdout = terminal


cp.caminhada_calibracao_passo = _etapas
cp.calcular_sinais_completos = _calcular_sinais
cp.aplicar_fator_passo = _sem_fator
cp.resumo_fator_passo = _salvar

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Calibração parada do passo conferida com pegadas de talco.")
    ap.add_argument("--parado", type=float, nargs="+", default=DIST_PARADO_CM,
                    help="distâncias da calibração parada (cm), padrão 25 35 45 55")
    ap.add_argument("--circuitos", type=float, nargs="+", default=CIRCUITOS_CM,
                    help="circuitos das caminhadas com talco (cm), padrão 30 40 50")
    ap.add_argument("--ordem", action="store_true", help="circuitos na ordem dada, sem sorteio")
    ap.add_argument("--sem-livre", action="store_true", help="não faz a caminhada livre")
    ap.add_argument("--analisar", metavar="PASTA", help="analisa o pegadas.csv preenchido de uma coleta")
    args = ap.parse_args()
    if args.analisar:
        analisar(args.analisar)
        sys.exit(0)
    DIST_PARADO_CM, CIRCUITOS_CM = list(args.parado), list(args.circuitos)
    SORTEAR, FAZER_LIVRE = not args.ordem, not args.sem_livre
    nome = time.strftime("%Y%m%d_%H%M%S") + "_passo_parado"
    pasta = os.path.join("dados", "debug_casa", nome)
    os.makedirs(pasta, exist_ok=True)
    cp.definir_pasta_saida(pasta)
    print(f"Os arquivos desta coleta vão para {pasta}")
    cp.main()
