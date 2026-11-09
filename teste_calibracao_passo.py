"""
TESTE — calibração do comprimento do passo com pegadas de talco medidas com fita.

Script à parte (não muda o experimento de comprimento). A referência não é a distância das
marcas, e sim onde o pé caiu de verdade (talco na sola e fita métrica depois).

Coleta:
  1. calibração estática do calculo_passo (os dois pés no chão), sem caixa;
  2. caminhada de CALIBRAÇÃO (circuito de 30 cm): [Enter] começa, [Enter] termina. Uma direção só;
  3. pausa para limpar o talco e ajustar o circuito para 40 cm;
  4. caminhada de TESTE (circuito de 40 cm): [Enter] começa, [Enter] termina. Uma direção só.
No fim o programa salva pegadas.csv na pasta da coleta, com um passo detectado por linha
(caminhada, número, pé da frente, valor do sistema) e a coluna real_cm vazia.

Depois de medir as pegadas (borda de trás do calcanhar até a borda de trás do calcanhar da
pegada seguinte), preencha real_cm no pegadas.csv (Excel serve; vírgula ou ponto) e rode:

    uv run python teste_calibracao_passo.py --analisar dados/debug_casa/<pasta>

A análise calcula o fator de cada pé com a caminhada de calibração (mediana de real / sistema),
aplica na de teste e mostra o erro passo a passo, por pé, sem e com o fator; também o contrário
(fator do teste conferido na calibração). Linhas com real_cm vazio ficam fora. Resultado em
analise_pegadas.txt e analise_pegadas.json.

    uv run python teste_calibracao_passo.py                  # coleta (circuitos de 30 e 40 cm)
    uv run python teste_calibracao_passo.py --calib 30 --teste 50
"""
import argparse
import csv
import json
import os
import sys
import time

import numpy as np

import calculo_passo as cp

ETAPA_CALIB = "calibracao_passo"
ETAPA_TESTE = "teste_passo"
NOME_PEGADAS = "pegadas.csv"
NOME_SCRIPT = "teste_calibracao_passo.py"   # aparece na instrução do --analisar
FATOR_LIMITES = (0.6, 1.4)

cp.PEDIR_PARTICIPANTE = False   # sem cadastro
cp.CALIBRAR_CAIXA = False       # OSC bruto, sem ganho da caixa
cp.CALIBRAR_PASSO = True        # faz o main chamar as caminhadas abaixo depois da calibração estática
cp.ROTEIRO_GUIADO = False

DIST_CALIB_CM = 30.0
DIST_TESTE_CM = 40.0


# ----------------------------- coleta -----------------------------
def _caminhada(etapa, titulo, dist_cm):
    print("\n" + "=" * 90)
    print(f"   👣 {titulo}: circuito de {dist_cm:.0f} cm, UMA direção só")
    print("=" * 90)
    print("    Talco na sola, pessoa parada no início, pés juntos, tronco reto.")
    input("    [Enter] para COMEÇAR: ")
    cp._aviso_sonoro(2)
    t0 = time.time() - cp.tempo_inicial
    print("    Caminhando... a pessoa para no fim, com os pés juntos.")
    input("    [Enter] quando ela PARAR no fim: ")
    cp.fases_sessao.append((etapa, t0, time.time() - cp.tempo_inicial))
    cp._aviso_sonoro(1)


def _caminhadas():
    """Substitui a caminhada de calibração do calculo_passo pelas duas caminhadas com talco."""
    _caminhada(ETAPA_CALIB, "CALIBRAÇÃO", DIST_CALIB_CM)
    print(f"\n    Limpe as pegadas e ajuste o circuito para {DIST_TESTE_CM:.0f} cm. A gravação continua,")
    print("    mas esse tempo não entra no resultado. (Fotografe/meça as pegadas antes de limpar.)")
    _caminhada(ETAPA_TESTE, "TESTE", DIST_TESTE_CM)
    print("\n    Caminhadas concluídas. Encerrando a gravação...")
    cp.roteiro_terminou = True   # o laço principal termina e processa a coleta


def _sem_fator(tempos, comprimentos, impactos, pes, projecoes):
    """Não aplica fator nominal: o fator sai depois das pegadas. Só guarda os passos."""
    cp.comprimentos_brutos_m = np.asarray(comprimentos, dtype=float).copy()
    _sem_fator.passos = (np.asarray(tempos, dtype=float), np.asarray(comprimentos, dtype=float),
                         [str(p) for p in pes])
    return tempos, comprimentos, impactos, pes, projecoes


def _salvar_pegadas(pes, comprimentos):
    """Escreve pegadas.csv: um passo detectado por linha, para preencher real_cm."""
    t, comp, pp = getattr(_sem_fator, "passos", (np.zeros(0), np.zeros(0), []))
    linhas = []
    for etapa in (ETAPA_CALIB, ETAPA_TESTE):
        j = [(a, b) for n, a, b in cp.fases_sessao if n == etapa]
        if not j:
            continue
        a, b = j[0]
        idx = np.flatnonzero((t >= a) & (t <= b + 1.0))
        for k, i in enumerate(idx, 1):
            linhas.append([etapa, k, "E" if "ESQ" in pp[i] else "D", f"{t[i]:.2f}",
                           f"{comp[i] * 100:.1f}".replace(".", ","), ""])
    arq = os.path.join(cp.PASTA_COLETA, NOME_PEGADAS)
    with open(arq, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["caminhada", "passo", "pe_da_frente", "t_s", "sistema_cm", "real_cm"])
        w.writerows(linhas)
    n_c = sum(1 for l in linhas if l[0] == ETAPA_CALIB)
    n_t = len(linhas) - n_c
    print("\n" + "=" * 90)
    print(f"  📝 Passos detectados: {n_c}" + (f" na calibração, {n_t} no teste" if n_t else "") + f". Planilha: {arq}")
    print("     Meça as pegadas (calcanhar a calcanhar, na ordem) e preencha real_cm.")
    print("     Se o número de pegadas não bater com o de passos, deixe real_cm vazio onde não souber.")
    print(f"     Depois: uv run python {NOME_SCRIPT} --analisar \"{cp.PASTA_COLETA}\"")
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


def digitar_medidas(pasta):
    """Pede no terminal a medida da fita de cada passo do pegadas.csv, na ordem, e grava a cada resposta.
    Enter vazio pula o passo (fica sem medida); 'v' volta um passo; 'f' para e continua depois
    (rode de novo com --medir PASTA: recomeça no primeiro passo sem medida)."""
    arq = os.path.join(pasta, NOME_PEGADAS)
    with open(arq, encoding="utf-8-sig") as f:
        linhas = list(csv.reader(f, delimiter=";"))
    cab, dados = linhas[0], linhas[1:]
    col = cab.index("real_cm")

    def gravar():
        with open(arq, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerows([cab] + dados)

    print("\n" + "=" * 90)
    print("  📏 MEDIDAS DA FITA (calcanhar a calcanhar, na ordem das pegadas), em cm")
    print("     Enter vazio = pula   v = volta um passo   f = para e continua depois")
    print("=" * 90)
    varias = len({l[0] for l in dados}) > 1
    i = next((k for k, l in enumerate(dados) if not l[col].strip()), len(dados))
    while i < len(dados):
        l = dados[i]
        atual = f" [{l[col]}]" if l[col].strip() else ""
        nome = (l[0].replace("_passo", "") + " ") if varias else ""
        r = input(f"    {nome}passo {l[1]:>2s} pé {l[2]} (sistema {l[4]} cm){atual}: ").strip().lower()
        if r == "f":
            print(f"    Parado. Para continuar: uv run python {NOME_SCRIPT} --medir \"{pasta}\"")
            return False
        if r == "v":
            i = max(0, i - 1)
            continue
        if r:
            v = _num(r)
            if v is None or v <= 0:
                print("    Valor inválido, digite em cm (ex.: 31,5).")
                continue
            l[col] = f"{v:.1f}".replace(".", ",")
            gravar()
        i += 1
    print(f"    Medidas gravadas em {arq}")
    return True


def fatores(passos):
    """Fator por pé = mediana de real / sistema (sem outliers); com < 3 passos de um pé usa os dois."""
    def mediana_razao(v):
        r = np.array([p["real"] / p["sistema"] for p in v])
        r = r[cp.remover_outliers(r)] if len(r) else r
        return float(np.clip(np.median(r), *FATOR_LIMITES)) if len(r) >= 3 else None
    geral = mediana_razao(passos)
    out = {}
    for pe in ("E", "D"):
        f = mediana_razao([p for p in passos if p["pe"] == pe])
        out[pe] = f if f is not None else (geral if geral is not None else 1.0)
    return out


def erros(passos, fat):
    r = {}
    for pe in ("E", "D", "ambos"):
        v = passos if pe == "ambos" else [p for p in passos if p["pe"] == pe]
        if not v:
            continue
        s = np.array([p["sistema"] for p in v])
        real = np.array([p["real"] for p in v])
        c = s * np.array([fat[p["pe"]] for p in v])
        d_sem, d_com = s - real, c - real
        dp = float(np.std(d_com, ddof=1)) if len(v) > 1 else float("nan")
        r[pe] = {"n": len(v), "real_medio_cm": float(real.mean()),
                 "erro_sem_cm": float(d_sem.mean()), "eam_sem_cm": float(np.abs(d_sem).mean()),
                 "erro_com_cm": float(d_com.mean()), "eam_com_cm": float(np.abs(d_com).mean()),
                 "dp_erro_com_cm": dp,
                 "limites_concordancia_cm": [float(d_com.mean() - 1.96 * dp), float(d_com.mean() + 1.96 * dp)]}
    return r


def _tabela(titulo, fat, e):
    print(f"\n  {titulo}")
    print(f"  fator: pé E {fat['E']:.3f}   pé D {fat['D']:.3f}")
    print(f"  {'pé':6s} {'n':>3s} {'real médio':>10s} {'erro sem':>9s} {'|erro| sem':>10s} {'erro com':>9s} {'|erro| com':>10s} {'limites (95%)':>17s}")
    for pe, x in e.items():
        lim = x["limites_concordancia_cm"]
        print(f"  {pe:6s} {x['n']:3d} {x['real_medio_cm']:10.1f} {x['erro_sem_cm']:+9.1f} {x['eam_sem_cm']:10.1f} "
              f"{x['erro_com_cm']:+9.1f} {x['eam_com_cm']:10.1f}   {lim[0]:+5.1f} a {lim[1]:+5.1f}")


def analisar(pasta):
    passos = ler_pegadas(pasta)
    cal = [p for p in passos if p["caminhada"] == ETAPA_CALIB]
    tst = [p for p in passos if p["caminhada"] == ETAPA_TESTE]
    arq_txt = os.path.join(pasta, "analise_pegadas.txt")
    terminal = sys.stdout
    with open(arq_txt, "w", encoding="utf-8") as f:
        sys.stdout = cp._Tee(f, terminal)
        try:
            print("=" * 90)
            print("  🧪 CALIBRAÇÃO DO PASSO COM PEGADAS MEDIDAS (erro = sistema − fita, em cm)")
            print("=" * 90)
            print(f"  Passos com medida: calibração {len(cal)}, teste {len(tst)}")
            if len(cal) < 3 or len(tst) < 3:
                print("  Poucos passos com real_cm preenchido: preencha pelo menos 3 em cada caminhada.")
                return
            f_cal, f_tst = fatores(cal), fatores(tst)
            e_tst, e_cal = erros(tst, f_cal), erros(cal, f_tst)
            _tabela("Fator da calibração aplicado no teste (resultado principal):", f_cal, e_tst)
            _tabela("Contrário: fator do teste aplicado na calibração:", f_tst, e_cal)
            print("\n  Passo a passo no teste (com o fator da calibração):")
            for p in tst:
                c = p["sistema"] * f_cal[p["pe"]]
                print(f"    {p['passo']:>3s} pé {p['pe']}: fita {p['real']:5.1f}  sistema {p['sistema']:5.1f}  "
                      f"com fator {c:5.1f}  erro {c - p['real']:+5.1f}")
            print("\n  Leitura: erro com fator perto de 0 nas duas direções = um fator basta. Se sobra um erro")
            print("  fixo (passos curtos e longos errando para o mesmo lado), precisa de reta com duas distâncias.")
            with open(os.path.join(pasta, "analise_pegadas.json"), "w", encoding="utf-8") as fj:
                json.dump({"fator_calibracao": f_cal, "erros_teste": e_tst,
                           "fator_teste": f_tst, "erros_calibracao_invertido": e_cal}, fj, indent=2, ensure_ascii=False)
            print(f"\n  Salvo em {arq_txt} e analise_pegadas.json")
        finally:
            sys.stdout = terminal


cp.caminhada_calibracao_passo = _caminhadas
cp.aplicar_fator_passo = _sem_fator
cp.resumo_fator_passo = _salvar_pegadas

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Calibração do passo com pegadas de talco medidas com fita.")
    ap.add_argument("--calib", type=float, default=30.0, help="circuito da caminhada de calibração (cm), padrão 30")
    ap.add_argument("--teste", type=float, default=40.0, help="circuito da caminhada de teste (cm), padrão 40")
    ap.add_argument("--analisar", metavar="PASTA", help="analisa o pegadas.csv preenchido de uma coleta")
    args = ap.parse_args()
    if args.analisar:
        analisar(args.analisar)
        sys.exit(0)
    DIST_CALIB_CM, DIST_TESTE_CM = args.calib, args.teste
    nome = time.strftime("%Y%m%d_%H%M%S") + f"_pegadas_calib{args.calib:.0f}_teste{args.teste:.0f}"
    pasta = os.path.join("dados", "debug_casa", nome)
    os.makedirs(pasta, exist_ok=True)
    cp.definir_pasta_saida(pasta)
    print(f"Os arquivos desta coleta vão para {pasta}")
    cp.main()
