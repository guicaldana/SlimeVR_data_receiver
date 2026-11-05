"""
TESTE — calibração do comprimento do passo numa distância e conferência em outra.

Script à parte (não muda o experimento de comprimento). Sequência:
  1. calibração estática do calculo_passo (os dois pés no chão), sem caixa;
  2. caminhada de calibração nas marcas de 30 cm (calcanhar a calcanhar), 30 s, ida e volta:
     dá o fator de cada pé = distância real / mediana medida;
  3. 10 s parado no início das marcas de 40 cm;
  4. caminhada de teste nas marcas de 40 cm, 30 s, ida e volta. A gravação termina sozinha.
Os passos da calibração não entram no resultado. O relatório mostra, para cada pé, o erro no
40 cm sem o fator e com o fator do 30 cm, e também o contrário (fator do 40 conferido no 30),
para ver se o resultado depende da distância usada na calibração.

Saída em dados/debug_casa/<AAAAMMDD_HHMMSS>_calib<C>_teste<T>/ (o enviar_dados.py manda a
mais recente pelo git): os arquivos normais do calculo_passo + teste_calibracao_passo.csv
(um passo por linha) e teste_calibracao_passo.json (resumo).

    uv run python teste_calibracao_passo.py                  # calibra em 30, testa em 40
    uv run python teste_calibracao_passo.py --calib 30 --teste 50
"""
import argparse
import csv
import json
import os
import time

import numpy as np

import calculo_passo as cp

TEMPO_TESTE_S = 30        # caminhada de teste (ida e volta nas marcas)
TEMPO_IR_AO_INICIO_S = 10  # parado no início das marcas de teste antes de caminhar
NOME_ETAPA_TESTE = "teste_passo"

cp.PEDIR_PARTICIPANTE = False   # sem cadastro
cp.CALIBRAR_CAIXA = False       # OSC bruto, sem ganho da caixa
cp.CALIBRAR_PASSO = True        # caminhada de calibração do passo antes da gravação
cp.ROTEIRO_GUIADO = True        # roteiro curto (ver montar_roteiro): termina sozinho

DIST_TESTE_CM = 40.0
_passos_brutos = {}             # todos os passos antes do fator (preenchido no processamento)
_aplicar_original = cp.aplicar_fator_passo
_resumo_original = cp.resumo_fator_passo


def montar_roteiro():
    return [
        ("parado_inicio_teste", TEMPO_IR_AO_INICIO_S,
         f"Vá para o INÍCIO das marcas de {DIST_TESTE_CM:.0f} cm e fique PARADO, tronco reto"),
        (NOME_ETAPA_TESTE, TEMPO_TESTE_S,
         f"CAMINHE pisando com o calcanhar em cada marca de {DIST_TESTE_CM:.0f} cm; no fim vire e volte"),
    ]


def _aplicar_guardando(tempos, comprimentos, impactos, pes, projecoes):
    """Guarda todos os passos sem fator (inclusive os da calibração) e segue o cálculo normal."""
    _passos_brutos.update({"t": np.asarray(tempos, dtype=float).copy(),
                           "comp": np.asarray(comprimentos, dtype=float).copy(),
                           "pes": np.asarray([str(p) for p in pes])})
    return _aplicar_original(tempos, comprimentos, impactos, pes, projecoes)


def _janela(nome):
    for n, t0, t1 in cp.fases_sessao:
        if n == nome:
            return t0, t1
    return None


def _passos_da_etapa(nome):
    """Índices dos passos da etapa, sem os outliers (mesmo critério do calculo_passo)."""
    j = _janela(nome)
    if j is None or not _passos_brutos:
        return np.zeros(0, dtype=int)
    t = _passos_brutos["t"]
    idx = np.flatnonzero((t >= j[0]) & (t <= j[1] + 1.0))
    if len(idx) == 0:
        return idx
    return idx[cp.remover_outliers(_passos_brutos["comp"][idx])]


def _do_pe(idx, pe):
    return idx[np.array([pe in p for p in _passos_brutos["pes"][idx]], dtype=bool)] if len(idx) else idx


def _fatores(idx, real_cm):
    """Fator de cada pé = real / mediana medida (com poucos passos de um pé, usa os dois)."""
    f = {}
    todos = _passos_brutos["comp"][idx] * 100
    for pe in ("ESQ", "DIR"):
        v = _passos_brutos["comp"][_do_pe(idx, pe)] * 100
        fonte = v if len(v) >= 3 else todos
        f[pe] = float(np.clip(real_cm / np.median(fonte), *cp.FATOR_PASSO_LIMITES)) if len(fonte) >= 3 else 1.0
    return f


def _erros(idx, real_cm, fatores):
    """Para cada pé: n, média medida e erro médio / erro absoluto médio sem e com o fator."""
    r = {}
    for pe in ("ESQ", "DIR", "AMBOS"):
        ii = idx if pe == "AMBOS" else _do_pe(idx, pe)
        if len(ii) == 0:
            continue
        bruto = _passos_brutos["comp"][ii] * 100
        f = np.array([fatores["ESQ"] if "ESQ" in p else fatores["DIR"] for p in _passos_brutos["pes"][ii]])
        corr = bruto * f
        r[pe] = {"n": int(len(ii)),
                 "media_sem_cm": float(np.mean(bruto)), "erro_sem_cm": float(np.mean(bruto - real_cm)),
                 "eam_sem_cm": float(np.mean(np.abs(bruto - real_cm))),
                 "media_com_cm": float(np.mean(corr)), "erro_com_cm": float(np.mean(corr - real_cm)),
                 "eam_com_cm": float(np.mean(np.abs(corr - real_cm))),
                 "dp_com_cm": float(np.std(corr, ddof=1)) if len(corr) > 1 else float("nan")}
    return r


def _imprimir(titulo, fat, erros, real_cm):
    print(f"\n    {titulo}")
    print(f"    fator: pé ESQ {fat['ESQ']:.3f}  pé DIR {fat['DIR']:.3f}")
    print(f"    {'pé':6s} {'n':>3s}  {'média sem':>9s}  {'erro sem':>8s}  {'média com':>9s}  {'erro com':>8s}  {'|erro| com':>10s}")
    for pe, e in erros.items():
        print(f"    {pe:6s} {e['n']:3d}  {e['media_sem_cm']:9.1f}  {e['erro_sem_cm']:+8.1f}  "
              f"{e['media_com_cm']:9.1f}  {e['erro_com_cm']:+8.1f}  {e['eam_com_cm']:10.1f}")
    print(f"    (distância real {real_cm:.0f} cm; erro = medido − real; valores em cm)")


def _resumo_com_teste(pes, comprimentos):
    _resumo_original(pes, comprimentos)
    calib_cm, teste_cm = cp.PASSO_CALIBRACAO_CM, DIST_TESTE_CM
    i_cal, i_tst = _passos_da_etapa("calibracao_passo"), _passos_da_etapa(NOME_ETAPA_TESTE)
    print("\n" + "=" * 90)
    print(f"  🧪 TESTE DA CALIBRAÇÃO DO PASSO: calibra em {calib_cm:.0f} cm, confere em {teste_cm:.0f} cm")
    print("=" * 90)
    if len(i_cal) < 3 or len(i_tst) < 3:
        print(f"    Poucos passos (calibração {len(i_cal)}, teste {len(i_tst)}): não dá para comparar.")
        return
    f_cal = _fatores(i_cal, calib_cm)
    e_tst = _erros(i_tst, teste_cm, f_cal)
    _imprimir(f"Fator do {calib_cm:.0f} cm aplicado no {teste_cm:.0f} cm (resultado principal):", f_cal, e_tst, teste_cm)
    f_inv = _fatores(i_tst, teste_cm)
    e_inv = _erros(i_cal, calib_cm, f_inv)
    _imprimir(f"Contrário: fator do {teste_cm:.0f} cm aplicado no {calib_cm:.0f} cm:", f_inv, e_inv, calib_cm)
    print("\n    Leitura: se o erro com fator fica perto de 0 nas duas direções, o erro do passo é")
    print("    proporcional e um fator basta. Se sobra um erro fixo, precisa de reta com duas distâncias.")
    _salvar(calib_cm, teste_cm, i_cal, i_tst, f_cal, e_tst, f_inv, e_inv)


def _salvar(calib_cm, teste_cm, i_cal, i_tst, f_cal, e_tst, f_inv, e_inv):
    pasta = cp.PASTA_COLETA
    try:
        with open(os.path.join(pasta, "teste_calibracao_passo.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["etapa", "t_s", "pe", "real_cm", "medido_sem_fator_cm", "fator_usado", "medido_com_fator_cm"])
            for etapa, idx, real, fat in (("calibracao", i_cal, calib_cm, f_inv), ("teste", i_tst, teste_cm, f_cal)):
                for i in idx:
                    pe = _passos_brutos["pes"][i]
                    fa = fat["ESQ"] if "ESQ" in pe else fat["DIR"]
                    c = _passos_brutos["comp"][i] * 100
                    w.writerow([etapa, f"{_passos_brutos['t'][i]:.3f}", pe, f"{real:.1f}", f"{c:.2f}", f"{fa:.4f}", f"{c * fa:.2f}"])
        with open(os.path.join(pasta, "teste_calibracao_passo.json"), "w", encoding="utf-8") as f:
            json.dump({"calibracao_cm": calib_cm, "teste_cm": teste_cm,
                       "fator_da_calibracao": f_cal, "erros_no_teste": e_tst,
                       "fator_do_teste": f_inv, "erros_na_calibracao_invertido": e_inv}, f, indent=2, ensure_ascii=False)
        print(f"    Salvo em {pasta}/teste_calibracao_passo.csv e .json")
    except Exception as e:
        print(f"    Não foi possível salvar o teste: {e}")


cp.aplicar_fator_passo = _aplicar_guardando
cp.resumo_fator_passo = _resumo_com_teste

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Calibra o passo numa distância e confere em outra.")
    ap.add_argument("--calib", type=float, default=30.0, help="distância das marcas da calibração (cm), padrão 30")
    ap.add_argument("--teste", type=float, default=40.0, help="distância das marcas do teste (cm), padrão 40")
    args = ap.parse_args()
    cp.PASSO_CALIBRACAO_CM = args.calib
    DIST_TESTE_CM = args.teste
    cp.ROTEIRO = montar_roteiro()
    nome = time.strftime("%Y%m%d_%H%M%S") + f"_calib{args.calib:.0f}_teste{args.teste:.0f}"
    pasta = os.path.join("dados", "debug_casa", nome)
    os.makedirs(pasta, exist_ok=True)
    cp.definir_pasta_saida(pasta)
    print(f"Os arquivos desta coleta vão para {pasta}")
    print(f"Prepare duas fileiras de marcas: {args.calib:.0f} cm (calibração) e {args.teste:.0f} cm (teste).")
    cp.main()
