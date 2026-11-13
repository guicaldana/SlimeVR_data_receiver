"""
Experimento de comprimento do passo: um corredor único de 22 placas de EVA com três trechos em
sequência (30, 40 e 50 cm entre os calcanhares), com talco no calcanhar e o d de cada pegada medido com fita.

Corredor (12/11): partida (calcanhar E na placa 0) e 22 pegadas, primeiro passo do pé D
(marcas_chao.PRIMEIRO_PE_UNICO; pegadas ímpares do pé D). Os passos
que chegam nas pegadas 1-8 são de 30 cm, 9-14 de 40 cm e 15-22 de 50 cm (8,80 m).
Talco (13/11): mede-se o d de TODAS as 22 pegadas. A partida é a caixa com talco (calcanhar E), de
onde sai o 1º passo, do pé D. As pegadas 1, 2 e 22 são guias (arrancada e parada) e ficam fora da
análise; entram os 19 passos das pegadas 3-21 (6 de 30 cm = 3-8, 6 de 40 cm = 9-14 e 7 de 50 cm =
15-21). Um passo só tem valor real se houver o d da própria pegada e o da anterior: nas coletas da
manhã de 13/11 (talco só nas pegadas 3-21) a análise fica com os passos 4-21.

Coleta (mesmo cadastro e cálculos do piloto.py):
  1. nome e altura do participante e rodada (1 = familiarização, uma passagem SEM talco e sem medidas;
     2 = coleta, três passagens com talco);
  2. calibração estática (os dois pés no chão), sem caixa;
  3. em cada passagem: talco no calcanhar, calcanhar E na placa 0 → [Enter] começa → caminha pisando
     nas placas, no ritmo → para com os pés juntos depois da última → [Enter] termina; a tela mostra
     os passos contados;
  4. logo depois de cada passagem, o programa pede o d das pegadas 1 a 22 (borda de trás da placa até
     a borda de trás do calcanhar no talco) e só então o talco é limpo para a próxima passagem.
     Passo real (pegada k) = espaçamento + d(k) − d(k−1). Na familiarização não há talco nem medidas.
  5. no fim da familiarização, [Enter] já abre a coleta com o mesmo participante (gancho do piloto.py).

O comprimento do sistema sai de duas formas e as duas são comparadas com o real:
  - máximo do apoio: maior distância à frente-atrás entre os pés enquanto os dois estão no chão;
  - toque do calcanhar: distância no pico de aceleração da IMU do pé (+25 ms).
Cada passo detectado é ligado à sua pegada pela alternância dos pés (marcas_chao.casar_com_marcas).
A análise mostra o erro por trecho (30, 40, 50 cm) e por pé, sem fator e com um fator por pé
calculado sem o próprio passo (deixa-um-fora). Arquivos: medidas_talco.json, pegadas.csv,
analise_pegadas.txt e analise_pegadas.json.

    uv run python experimento_comprimento.py                    # coleta + medidas + análise
    uv run python experimento_comprimento.py --medir PASTA      # completa medidas que ficaram faltando
    uv run python experimento_comprimento.py --analisar PASTA   # só refaz a tabela e a análise
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

import marcas_chao
import piloto

# --------------------------- corredor único ---------------------------
PASSAGENS_COLETA = ["P1", "P2", "P3"]
PASSAGENS_FAMILIARIZACAO = ["P1"]
PERCURSO = marcas_chao.CIRCUITO_UNICO
N_PEGADAS = len(PERCURSO) - 1                  # 22
# Histórico: até a manhã de 13/11 o terminal pedia as pegadas 2-20, mas a equipe anotava 3-21 nelas
# (o pé mostrado ficava trocado); essas coletas foram renumeradas (d da "pegada k" -> pegada k+1),
# com backup em backup_antes_renumerar, e só têm d nas pegadas 3-21.
# 13/11 (tarde): o talco passou a ser medido em todas as 22 pegadas.
PEGADAS_MEDIDAS = list(range(1, N_PEGADAS + 1))  # 1..22
PASSOS_ANALISE = (3, N_PEGADAS - 1)              # passos 3..21; 1, 2 e 22 são guias


def passo_valido(k):
    """Passos que entram na análise (pegadas 3-21). O real ainda exige o d de k e de k-1."""
    return PASSOS_ANALISE[0] <= k <= PASSOS_ANALISE[1]


for _p in PASSAGENS_COLETA:
    marcas_chao.CIRCUITOS[_p] = PERCURSO
    marcas_chao.DESCRICAO[_p] = f"corredor 30/40/50 cm, passagem {_p[1:]}"
marcas_chao.PEGADAS_POR_CIRCUITO = N_PEGADAS

piloto.EXPERIMENTO = "comprimento"
piloto.CALIBRAR_CAIXA = False
piloto.VERIFICAR_CALIBRACAO = False
piloto.COM_ENTER = True
piloto.MOSTRAR_GRAFICOS = False       # os PNG são salvos; as janelas travariam as medidas
piloto.CIRCUITOS_COLETA = list(PASSAGENS_COLETA)
piloto.CIRCUITOS_FAMILIARIZACAO = list(PASSAGENS_FAMILIARIZACAO)
piloto.ORDEM_CIRCUITOS = list(PASSAGENS_COLETA)
piloto.SORTEAR_ORDEM_CIRCUITOS = False   # as passagens são iguais: nada a sortear
piloto.TEMPO_CAMINHADA_S = {p: 30 for p in PASSAGENS_COLETA}
piloto.DESCREVER_CIRCUITO = lambda c: f"CORREDOR (passagem {c[1:]}, 22 pegadas: 30, 40 e 50 cm)"
piloto.PE_PARTIDA = "E" if PERCURSO[0][1] == "ESQ" else "D"
piloto.ROTEIRO = piloto.montar_roteiro()

NOME_PEGADAS = "pegadas.csv"
NOME_MEDIDAS = "medidas_talco.json"
CAB = ["passagem", "pegada", "trecho_cm", "valido", "pe_da_frente", "t_s", "sistema_cm", "sistema_toque_cm",
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


def _pe_da_pegada(k):
    return "E" if PERCURSO[k][1] == "ESQ" else "D"


# ----------------------------- medidas do talco -----------------------------
def _ler_medidas(pasta):
    arq = os.path.join(pasta, NOME_MEDIDAS)
    if os.path.exists(arq):
        with open(arq, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _gravar_medidas(pasta, med):
    with open(os.path.join(pasta, NOME_MEDIDAS), "w", encoding="utf-8") as f:
        json.dump(med, f, indent=1, ensure_ascii=False)


def medir_passagem(pasta, passagem):
    """Pede o d das pegadas 1 a 22 desta passagem, na ordem. Resposta "d" ou "d espaçamento" (se o par
    de placas não estiver no espaçamento do trecho). Enter vazio = pegada sem marca; v = volta;
    f = para (continua depois com --medir; não limpar as placas). Retorna True se terminou."""
    med = _ler_medidas(pasta)
    m = med.setdefault(passagem, {"d": {}, "feitas": 0})
    print("\n" + "=" * 90)
    print(f"  📏 MEDIDAS DA PASSAGEM {passagem[1:]}: d = borda de trás da placa até a borda de trás do calcanhar no talco (cm)")
    print("     Meça reto, na direção da caminhada, todas as 22 pegadas, da 1 (1º passo, pé D) até a 22.")
    print("     Digite \"d\" ou \"d espaçamento\" (se aquele par de placas não estiver no espaçamento do trecho).")
    print("     Enter vazio = pegada sem marca de talco   v = volta   f = para (não limpe as placas)")
    print("=" * 90)
    k = m.get("feitas", 0)
    while k < len(PEGADAS_MEDIDAS):
        peg = PEGADAS_MEDIDAS[k]
        esp = float(marcas_chao.trecho_do_passo(peg, PERCURSO))
        atual = (m["d"].get(str(peg)) or [None])[0]
        nota = "" if passo_valido(peg) else " (guia, fora da análise)"
        perg = f"    Pegada {peg:2d} pé {_pe_da_pegada(peg)}, trecho {esp:.0f} cm{nota} — d"
        try:
            r = input(f"{perg}{' [' + _fmt(atual) + ']' if atual is not None else ''}: ").strip().lower()
        except EOFError:
            r = "f"
        if r == "f":
            m["feitas"] = k
            _gravar_medidas(pasta, med)
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
        m["d"][str(peg)] = [dd, e] if dd is not None else None
        m["feitas"] = k + 1
        _gravar_medidas(pasta, med)
        if dd is not None and peg > 1:
            ant = m["d"].get(str(peg - 1))
            if ant:
                print(f"      passo = {_fmt(e + dd - ant[0])} cm")
        k += 1
    m["feitas"] = len(PEGADAS_MEDIDAS)
    _gravar_medidas(pasta, med)
    return True


def _familiarizacao():
    return (piloto.PARTICIPANTE or {}).get("rodada") == "familiarizacao"


def _apos_caminhada(c):
    """Chamado pelo roteiro logo depois de cada passagem: mede o talco antes de limpar as placas.
    Na familiarização (12/11) a passagem é sem talco e não há medidas."""
    pasta = piloto.PASTA_COLETA
    if _familiarizacao():
        return
    terminou = medir_passagem(pasta, c)
    if terminou:
        print(f"    ✔ Passagem {c[1:]} medida. 🧹 Limpe o talco das placas antes da próxima passagem.")
    else:
        print("    ⚠ Medidas incompletas: só limpe as placas depois de terminar (--medir).")
        input("    [Enter] para seguir: ")


piloto.APOS_CAMINHADA = _apos_caminhada
piloto.PREPARO_CAMINHADA = lambda: "SEM talco (familiarização). " if _familiarizacao() else "Talco no calcanhar. "


# ----------------------------- tabela de pegadas -----------------------------
_captura = {}
_orig_plato = piloto.comprimento_pelo_plato
_orig_processar = piloto.processar_coleta


def _plato(t, d_ap, *args):
    res = _orig_plato(t, d_ap, *args)
    _captura.update(t=np.asarray(t, float), d=np.asarray(d_ap, float), passos=res)
    return res


def _linhas_sistema():
    """Uma linha por pegada (1..22) de cada passagem gravada, com os valores do sistema."""
    linhas = []
    t, d = _captura["t"], _captura["d"]
    tempos, comps, _, pes, _ = _captura["passos"]
    tempos = np.asarray(tempos, float)
    for nome, a, b in piloto.fases_sessao:
        if not nome.startswith("caminhada"):
            continue
        p = nome.rsplit("_", 1)[-1]
        idx = [i for i in range(len(tempos)) if a <= tempos[i] <= b + 1.0]
        casados = marcas_chao.casar_com_marcas([pes[i] for i in idx], [comps[i] for i in idx],
                                               [tempos[i] for i in idx], percurso=PERCURSO)
        por_peg = {}
        for c in casados:
            i = idx[int(np.argmin(np.abs(tempos[idx] - c["t"])))]
            toque = None if "*" in str(pes[i]) else abs(float(np.interp(tempos[i] + piloto.TAU_SYNC_OSC, t, d))) * 100
            por_peg[c["marca"]] = (tempos[i], comps[i] * 100, toque)
        for k in range(1, N_PEGADAS + 1):
            ti, s, to = por_peg.get(k, (None, None, None))
            linhas.append({"passagem": p, "pegada": str(k), "trecho_cm": str(marcas_chao.trecho_do_passo(k, PERCURSO)),
                           "valido": "1" if passo_valido(k) else "0",
                           "pe_da_frente": _pe_da_pegada(k), "t_s": "" if ti is None else f"{ti:.2f}",
                           "sistema_cm": _fmt(s), "sistema_toque_cm": _fmt(to)})
        n = len(casados)
        print(f"  📝 Passagem {p[1:]}: {n} de {N_PEGADAS} passos ligados às pegadas")
    return linhas


def _preencher_real(linhas, med):
    for l in linhas:
        k = int(l["pegada"])
        # trecho, pé e validade vêm sempre da regra atual (tabelas antigas tinham a faixa 3-20)
        l["trecho_cm"] = str(marcas_chao.trecho_do_passo(k, PERCURSO))
        l["pe_da_frente"] = _pe_da_pegada(k)
        l["valido"] = "1" if passo_valido(k) else "0"
        m = (med.get(l["passagem"]) or {}).get("d", {})
        atual, ant = m.get(str(k)), m.get(str(k - 1))
        l["d_cm"] = _fmt(atual[0]) if atual else ""
        l["espacamento_cm"] = _fmt(atual[1]) if atual else ""
        # o real é preenchido em todo passo com os dois d (guias também, só para consulta: valido=0)
        l["real_cm"] = _fmt(atual[1] + atual[0] - ant[0]) if (atual and ant) else ""
    return linhas


def _escrever_pegadas(pasta, linhas):
    with open(os.path.join(pasta, NOME_PEGADAS), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CAB, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)


def _ler_pegadas(pasta):
    arq = os.path.join(pasta, NOME_PEGADAS)
    if not os.path.exists(arq):
        return None
    with open(arq, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def atualizar_tabela(pasta):
    """Junta as medidas do talco à tabela de pegadas já gravada."""
    linhas = _ler_pegadas(pasta)
    if linhas is None:
        print("pegadas.csv não existe: a gravação não foi processada.")
        return False
    _escrever_pegadas(pasta, _preencher_real(linhas, _ler_medidas(pasta)))
    return True


def _medidas_completas(pasta, passagens):
    med = _ler_medidas(pasta)
    return all((med.get(p) or {}).get("feitas", 0) >= len(PEGADAS_MEDIDAS) for p in passagens)


def _processar():
    _orig_processar()
    pasta = piloto.PASTA_COLETA
    if "passos" not in _captura:
        print("Nenhum passo detectado: pegadas.csv não foi criado.")
        return
    linhas = _preencher_real(_linhas_sistema(), _ler_medidas(pasta))
    _escrever_pegadas(pasta, linhas)
    gravadas = list(dict.fromkeys(l["passagem"] for l in linhas))
    if _familiarizacao():
        print("    Familiarização sem talco: só os passos do sistema foram salvos (sem análise de erro).")
        return
    if _medidas_completas(pasta, gravadas):
        analisar(pasta)
    elif any(l["real_cm"] for l in linhas):
        analisar(pasta)
        print(f"    Faltam medidas. Para completar: uv run python experimento_comprimento.py --medir \"{pasta}\"")
    else:
        print(f"    Sem medidas do talco. Para medir: uv run python experimento_comprimento.py --medir \"{pasta}\"")


piloto.comprimento_pelo_plato = _plato
piloto.processar_coleta = _processar


def medir_e_analisar(pasta):
    linhas = _ler_pegadas(pasta)
    passagens = list(dict.fromkeys(l["passagem"] for l in linhas)) if linhas else list(_ler_medidas(pasta))
    for p in passagens:
        if not _medidas_completas(pasta, [p]) and not medir_passagem(pasta, p):
            return
    if atualizar_tabela(pasta):
        analisar(pasta)


# ----------------------------- análise -----------------------------
def ler(pasta):
    return [r for r in (_ler_pegadas(pasta) or []) if r.get("valido") == "1" and _num(r.get("real_cm"))]


def _analisar_metodo(linhas, chave):
    ps = [(r["trecho_cm"], r["pe_da_frente"], _num(r[chave]), _num(r["real_cm"])) for r in linhas if _num(r.get(chave))]
    if not ps:
        return None, ["   sem passos com este método"]
    c = np.array([p[0] for p in ps]); pe = np.array([p[1] for p in ps])
    s = np.array([p[2] for p in ps]); r = np.array([p[3] for p in ps])
    corr = np.full(len(s), np.nan)
    for i in range(len(s)):           # fator do pé sem o próprio passo (todos os trechos)
        o = (pe == pe[i]); o[i] = False
        if o.sum() >= 2:
            corr[i] = s[i] * np.median(r[o] / s[o])
    out, res = [], {}
    out.append(f"   {'trecho':10s} {'pé':5s} {'n':>3s} {'real':>6s} {'sistema':>8s} {'erro':>6s} {'|erro|':>7s}"
               f" {'erro c/fator':>12s} {'|erro| c/fator':>14s}")
    trechos = sorted(dict.fromkeys(c), key=float)
    for cc in trechos + ["todos"]:
        for pp in ("E", "D", "ambos"):
            k = ((c == cc) | (cc == "todos")) & ((pe == pp) | (pp == "ambos"))
            if not k.any():
                continue
            e, ec = s[k] - r[k], corr[k] - r[k]
            ok = ~np.isnan(ec)
            nome = f"{cc} cm" if cc != "todos" else "todos"
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
    texto = [f"EXPERIMENTO DE COMPRIMENTO DO PASSO — {pasta}",
             f"Corredor único 30/40/50 cm, 22 pegadas com talco; passos 3-21 analisados (guias 1, 2 e 22 fora). Passos válidos com medida de talco: {len(linhas)}",
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
    ap = argparse.ArgumentParser(description="Experimento de comprimento do passo: corredor único 30/40/50 cm com talco.")
    ap.add_argument("--medir", metavar="PASTA", help="completa as medidas do talco de uma coleta")
    ap.add_argument("--analisar", metavar="PASTA", help="refaz só a tabela e a análise de uma coleta medida")
    ap.add_argument("--passagens", type=int, metavar="N",
                    help="número de passagens da coleta (padrão 3), ex. para repetir só as que falharam")
    args = ap.parse_args()
    if args.passagens:
        # 13/11: repetir só parte das passagens (ex. tracker congelado em P1 e P2)
        PASSAGENS_COLETA[:] = [f"P{i}" for i in range(1, args.passagens + 1)]
        for _p in PASSAGENS_COLETA:
            marcas_chao.CIRCUITOS[_p] = PERCURSO
            marcas_chao.DESCRICAO[_p] = f"corredor 30/40/50 cm, passagem {_p[1:]}"
        piloto.CIRCUITOS_COLETA = list(PASSAGENS_COLETA)
        piloto.ORDEM_CIRCUITOS = list(PASSAGENS_COLETA)
        piloto.TEMPO_CAMINHADA_S = {p: 30 for p in PASSAGENS_COLETA}
        piloto.ROTEIRO = piloto.montar_roteiro()
    if args.medir:
        medir_e_analisar(args.medir)
        sys.exit(0)
    if args.analisar:
        if atualizar_tabela(args.analisar):
            analisar(args.analisar)
        sys.exit(0)
    piloto.main()
