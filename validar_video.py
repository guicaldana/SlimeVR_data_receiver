"""
Validação da altura do pé com vídeo.

Filme a sessão guiada do calculo_passo.py de lado, com o celular parado:
  - a uns 3 m da linha da caminhada, na altura do pé, a 60 fps ou mais;
  - um adesivo de cor forte em cada tracker dos pés (cores diferentes ajudam);
  - a caixa de 6,7 cm (ou uma régua) no chão, de lado para a câmera, para a escala;
  - caminhe sempre perpendicular à câmera (de lado para ela).

O script segue os dois adesivos quadro a quadro, converte a altura em cm pela
escala da caixa e sincroniza o vídeo com a sessão sozinho (correlação com a
altura do OSC). Depois compara, em cada passo das caminhadas, o pico de altura
do vídeo com o do OSC, da cadeia e do pipeline.

Uso:
    uv pip install opencv-python
    uv run python validar_video.py caminho/do/video.mp4

Na primeira janela, clique em:
  1) no topo da caixa e 2) na base da caixa (mesma vertical) -> escala;
  3) no adesivo do pé ESQUERDO e 4) no adesivo do pé DIREITO -> cores e posição inicial.
Pressione qualquer tecla para confirmar. Para escolher outro quadro, use --quadro N.

Sem janela (por exemplo, para repetir a análise):
    uv run python validar_video.py video.mp4 --cm-por-px 0.12 \
        --esq 412,880 --dir 455,884 --quadro 30

Saída em dados/validacao_video/: video_alturas.csv (altura de cada pé no vídeo),
validacao_passos.csv (comparação passo a passo), validacao_video.png e o resumo
no terminal.
"""

import argparse
import csv
import json
import os
import sys

import numpy as np

PASTA_DADOS = "dados"
PASTA_SAIDA = os.path.join(PASTA_DADOS, "validacao_video")
ALTURA_CAIXA_CM = 6.7
FASES_MARCHA = ("caminhada_reta", "caminhada_inclinada")


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        sys.exit("Instale o OpenCV antes: uv pip install opencv-python")


# ------------------------------------------------------------------ vídeo
def ler_quadro(caminho, indice):
    cv2 = _cv2()
    cap = cv2.VideoCapture(caminho)
    cap.set(cv2.CAP_PROP_POS_FRAMES, indice)
    ok, quadro = cap.read()
    cap.release()
    if not ok:
        sys.exit(f"Não consegui ler o quadro {indice} de {caminho}")
    return quadro


def escolher_pontos(quadro):
    """Janela para clicar: topo da caixa, base da caixa, adesivo E, adesivo D."""
    cv2 = _cv2()
    nomes = ["TOPO da caixa", "BASE da caixa", "adesivo pé ESQUERDO", "adesivo pé DIREITO"]
    pontos = []
    vis = quadro.copy()

    def clique(evento, x, y, *_):
        if evento == cv2.EVENT_LBUTTONDOWN and len(pontos) < 4:
            pontos.append((x, y))
            cv2.circle(vis, (x, y), 6, (0, 0, 255), 2)
            cv2.putText(vis, str(len(pontos)), (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    janela = "Clique: topo da caixa, base da caixa, pe E, pe D (tecla = ok)"
    cv2.namedWindow(janela, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(janela, clique)
    while True:
        tela = vis.copy()
        if len(pontos) < 4:
            cv2.putText(tela, f"Clique no {nomes[len(pontos)]}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        cv2.imshow(janela, tela)
        tecla = cv2.waitKey(30)
        if tecla != -1 and len(pontos) == 4:
            break
        if tecla == 27:
            sys.exit("Cancelado.")
    cv2.destroyAllWindows()
    return pontos


def cor_hsv(quadro, ponto, raio=4):
    cv2 = _cv2()
    hsv = cv2.cvtColor(quadro, cv2.COLOR_BGR2HSV)
    x, y = ponto
    bloco = hsv[max(0, y - raio):y + raio + 1, max(0, x - raio):x + raio + 1].reshape(-1, 3)
    return np.median(bloco, axis=0)


def mascara_cor(hsv, cor, tol_h=12, tol_sv=70):
    cv2 = _cv2()
    h, s, v = [int(c) for c in cor]
    lo_s, lo_v = max(40, s - tol_sv), max(40, v - tol_sv)
    faixas = []
    for h0, h1 in ((h - tol_h, h + tol_h),):
        if h0 < 0:
            faixas += [(0, h1), (180 + h0, 179)]
        elif h1 > 179:
            faixas += [(h0, 179), (0, h1 - 180)]
        else:
            faixas.append((h0, h1))
    m = None
    for a, b in faixas:
        mi = cv2.inRange(hsv, np.array([a, lo_s, lo_v]), np.array([b, 255, 255]))
        m = mi if m is None else cv2.bitwise_or(m, mi)
    return m


def seguir_marcadores(caminho, cores, posicoes, janela_busca=120):
    """Centróide de cada adesivo em cada quadro. Retorna t (s) e y (px) de cada pé (NaN se perdido)."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(caminho)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ys = {lado: [] for lado in cores}
    ult = {lado: np.array(p, dtype=float) for lado, p in posicoes.items()}
    perdido = {lado: 0 for lado in cores}
    i = 0
    while True:
        ok, quadro = cap.read()
        if not ok:
            break
        hsv = cv2.cvtColor(quadro, cv2.COLOR_BGR2HSV)
        alt, larg = hsv.shape[:2]
        for lado, cor in cores.items():
            r = janela_busca * (1 + min(perdido[lado], 10))
            x0, y0 = int(max(0, ult[lado][0] - r)), int(max(0, ult[lado][1] - r))
            x1, y1 = int(min(larg, ult[lado][0] + r)), int(min(alt, ult[lado][1] + r))
            m = mascara_cor(hsv[y0:y1, x0:x1], cor)
            nc, rot, est, cen = cv2.connectedComponentsWithStats(m)
            if nc > 1:
                k = 1 + int(np.argmax(est[1:, cv2.CC_STAT_AREA]))
                if est[k, cv2.CC_STAT_AREA] >= 6:
                    c = cen[k] + (x0, y0)
                    ult[lado] = c
                    perdido[lado] = 0
                    ys[lado].append(c[1])
                    continue
            perdido[lado] += 1
            ys[lado].append(np.nan)
        i += 1
        if n and i % 300 == 0:
            print(f"  seguindo adesivos: {100 * i / n:4.0f}%", end="\r", flush=True)
    cap.release()
    print()
    t = np.arange(i) / fps
    return t, {lado: np.array(v) for lado, v in ys.items()}, fps


def alturas_video_cm(y_px, cm_por_px, fps):
    """Altura acima do chão (cm). O chão é o nível do adesivo nos apoios (pé parado e baixo)."""
    y = np.array(y_px, dtype=float)
    ok = ~np.isnan(y)
    if ok.sum() < 10:
        return np.full_like(y, np.nan)
    y = np.interp(np.arange(len(y)), np.flatnonzero(ok), y[ok])
    # filtro de média móvel ~50 ms contra o ruído do centróide
    k = max(1, int(round(0.05 * fps)))
    y = np.convolve(y, np.ones(k) / k, mode="same")
    vel = np.abs(np.gradient(y)) * fps * cm_por_px  # cm/s
    # chão local: mediana dos quadros parados num janela de 4 s (y maior = mais baixo)
    janela = int(4 * fps)
    chao = np.empty_like(y)
    for i in range(len(y)):
        a, b = max(0, i - janela // 2), min(len(y), i + janela // 2)
        seg, vs = y[a:b], vel[a:b]
        parado = seg[vs < 5.0]
        base = parado if len(parado) > 5 else seg
        chao[i] = np.percentile(base, 90)
    h = (chao - y) * cm_por_px
    h[~ok] = np.nan
    return h


# ------------------------------------------------------------------ sessão
def carregar_sessao(pasta):
    serie = np.genfromtxt(os.path.join(pasta, "dados_brutos_sessao.csv"), delimiter=",", names=True)
    with open(os.path.join(pasta, "dados_brutos_entrada.json"), encoding="utf-8") as f:
        entrada = json.load(f)
    fases = entrada.get("fases") or []
    if not fases and os.path.exists(os.path.join(pasta, "fases_sessao.csv")):
        with open(os.path.join(pasta, "fases_sessao.csv"), encoding="utf-8") as f:
            fases = [(r["etapa"], float(r["t0"]), float(r["t1"])) for r in csv.DictReader(f)]
    return serie, entrada, fases


def sincronizar(t_video, h_video, t_sessao, h_sessao, max_lag=300.0, passo=0.02):
    """Atraso (s) a somar ao tempo do vídeo para cair no tempo da sessão (correlação das alturas)."""
    grade = np.arange(0, t_sessao[-1], passo)
    ref = np.interp(grade, t_sessao, np.nan_to_num(h_sessao))
    ref = ref - ref.mean()
    hv = np.nan_to_num(h_video)
    melhor, lag_ok = -np.inf, 0.0
    for lag in np.arange(-max_lag, max_lag, passo * 5):
        v = np.interp(grade, t_video + lag, hv, left=0.0, right=0.0)
        if np.count_nonzero(v) < 50:
            continue
        v = v - v.mean()
        c = float(np.dot(ref, v) / (np.linalg.norm(ref) * np.linalg.norm(v) + 1e-9))
        if c > melhor:
            melhor, lag_ok = c, lag
    # refino fino
    for lag in np.arange(lag_ok - passo * 5, lag_ok + passo * 5, passo / 2):
        v = np.interp(grade, t_video + lag, hv, left=0.0, right=0.0)
        v = v - v.mean()
        c = float(np.dot(ref, v) / (np.linalg.norm(ref) * np.linalg.norm(v) + 1e-9))
        if c > melhor:
            melhor, lag_ok = c, lag
    return lag_ok, melhor


def balancos(h, minimo=2.0, fim=0.8):
    """Intervalos (i0, i1) em que o pé está no ar, pela altura do vídeo."""
    out, i, n = [], 0, len(h)
    while i < n:
        if h[i] > minimo:
            a = i
            while a > 0 and h[a - 1] > fim:
                a -= 1
            b = i
            while b < n and h[b] > fim:
                b += 1
            out.append((a, b))
            i = b
        else:
            i += 1
    return out


def comparar(serie, entrada, fases, t_sinc, alt_video):
    g_osc = entrada["ganhos_metodo"]["osc"]
    g_cad_marcha = entrada["ganhos_metodo"]["cadeia"]
    g_cad_caixa = entrada.get("ganhos_cadeia_caixa") or g_cad_marcha
    t = serie["t"]
    metodos = {
        "OSC": lambda lado: serie[f"osc_{lado}_cm"] * g_osc[0 if lado == "esq" else 1],
        "cadeia (ganho caixa)": lambda lado: serie[f"cadeia_{lado}_cm"] * g_cad_caixa[0 if lado == "esq" else 1],
        "cadeia (ganho marcha)": lambda lado: serie[f"cadeia_{lado}_cm"] * g_cad_marcha[0 if lado == "esq" else 1],
        "pipeline (final)": lambda lado: serie[f"altura_{lado}_cm"],
    }
    linhas = []
    for nome_fase, t0, t1 in fases:
        if nome_fase not in FASES_MARCHA:
            continue
        for lado in ("esq", "dir"):
            hv = np.interp(t, t_sinc, np.nan_to_num(alt_video[lado]), left=np.nan, right=np.nan)
            m = (t >= t0) & (t <= t1 + 1.0)
            idx = np.flatnonzero(m)
            if len(idx) == 0:
                continue
            for a, b in balancos(np.nan_to_num(hv[idx])):
                sl = idx[a:b]
                if len(sl) < 3:
                    continue
                linha = {"fase": nome_fase, "pe": lado.upper(), "t": float(t[sl[0]]),
                         "video_cm": float(np.nanmax(hv[sl]))}
                for nm, f in metodos.items():
                    linha[nm] = float(np.nanmax(f(lado)[sl]))
                linhas.append(linha)
    return linhas, list(metodos)


def resumo(linhas, metodos):
    print("\nErro do pico de cada passo em relação ao vídeo (mediana, cm) e razão método/vídeo:")
    print(f"  {'fase':20s} {'pé':4s} {'n':>3s} {'vídeo':>7s} " + " ".join(f"{m:>22s}" for m in metodos))
    saida = []
    for fase in FASES_MARCHA:
        for pe in ("ESQ", "DIR"):
            ls = [l for l in linhas if l["fase"] == fase and l["pe"] == pe]
            if not ls:
                continue
            v = np.array([l["video_cm"] for l in ls])
            cols = []
            for m in metodos:
                x = np.array([l[m] for l in ls])
                erro = np.median(x - v)
                razao = np.median(x / np.maximum(v, 0.5))
                cols.append(f"{erro:+6.1f} cm (x{razao:4.2f})")
                saida.append((fase, pe, m, len(ls), float(np.median(v)), float(erro), float(razao)))
            print(f"  {fase:20s} {pe:4s} {len(ls):3d} {np.median(v):6.1f}  " + " ".join(f"{c:>22s}" for c in cols))
    return saida


def grafico(serie, entrada, t_sinc, alt_video, fases, caminho):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    g_osc = entrada["ganhos_metodo"]["osc"]
    g_cad = entrada["ganhos_metodo"]["cadeia"]
    t = serie["t"]
    fig, eixos = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    for ax, lado, i in ((eixos[0], "esq", 0), (eixos[1], "dir", 1)):
        for nome, t0, t1 in fases:
            if nome in FASES_MARCHA:
                ax.axvspan(t0, t1, color="#e8f0e0", lw=0)
        ax.plot(t_sinc, alt_video[lado], color="k", lw=1.2, label="vídeo")
        ax.plot(t, serie[f"osc_{lado}_cm"] * g_osc[i], color="#d9822b", lw=0.8, label="OSC")
        ax.plot(t, serie[f"cadeia_{lado}_cm"] * g_cad[i], color="#2b7bd9", lw=0.8, label="cadeia (ganho marcha)")
        ax.set_ylabel(f"Altura pé {lado.upper()} (cm)")
        ax.set_ylim(-2, 30)
        ax.legend(loc="upper right", fontsize=8, frameon=False)
    eixos[1].set_xlabel("Tempo da sessão (s)")
    fig.tight_layout()
    fig.savefig(caminho, dpi=150)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--sessao", default=PASTA_DADOS, help="pasta com dados_brutos_sessao.csv e dados_brutos_entrada.json")
    ap.add_argument("--quadro", type=int, default=0, help="quadro usado para clicar a escala e os adesivos")
    ap.add_argument("--cm-por-px", type=float, help="escala sem janela (cm por pixel)")
    ap.add_argument("--esq", help="posição x,y do adesivo do pé E no quadro escolhido (sem janela)")
    ap.add_argument("--dir", help="posição x,y do adesivo do pé D no quadro escolhido (sem janela)")
    ap.add_argument("--atraso", type=float, help="atraso vídeo→sessão em s (pula a sincronização automática)")
    args = ap.parse_args()

    os.makedirs(PASTA_SAIDA, exist_ok=True)
    quadro = ler_quadro(args.video, args.quadro)
    if args.cm_por_px and args.esq and args.dir:
        cm_por_px = args.cm_por_px
        p_esq = tuple(int(v) for v in args.esq.split(","))
        p_dir = tuple(int(v) for v in args.dir.split(","))
    else:
        topo, base, p_esq, p_dir = escolher_pontos(quadro)
        cm_por_px = ALTURA_CAIXA_CM / max(1.0, abs(base[1] - topo[1]))
    cores = {"esq": cor_hsv(quadro, p_esq), "dir": cor_hsv(quadro, p_dir)}
    print(f"Escala: {cm_por_px:.4f} cm/px | cor E (HSV) {cores['esq'].astype(int)} | cor D {cores['dir'].astype(int)}")
    print(f"Repetir sem janela: --quadro {args.quadro} --cm-por-px {cm_por_px:.4f} "
          f"--esq {p_esq[0]},{p_esq[1]} --dir {p_dir[0]},{p_dir[1]}")

    print("Seguindo os adesivos no vídeo...")
    t_v, ys, fps = seguir_marcadores(args.video, cores, {"esq": p_esq, "dir": p_dir})
    alt_v = {lado: alturas_video_cm(ys[lado], cm_por_px, fps) for lado in ys}
    perdidos = {lado: float(np.mean(np.isnan(ys[lado]))) for lado in ys}
    print(f"  {len(t_v)} quadros a {fps:.0f} fps | adesivo perdido: E {100 * perdidos['esq']:.0f}% D {100 * perdidos['dir']:.0f}%")

    serie, entrada, fases = carregar_sessao(args.sessao)
    g_osc = entrada["ganhos_metodo"]["osc"]
    if args.atraso is not None:
        atraso, corr = args.atraso, float("nan")
    else:
        h_ses = serie["osc_esq_cm"] * g_osc[0] - serie["osc_dir_cm"] * g_osc[1]
        h_vid = np.nan_to_num(alt_v["esq"]) - np.nan_to_num(alt_v["dir"])
        atraso, corr = sincronizar(t_v, h_vid, serie["t"], h_ses)
    print(f"Sincronização: vídeo + {atraso:.2f} s = sessão (correlação {corr:.2f})")
    if corr == corr and corr < 0.5:
        print("  [Aviso] Correlação baixa: confira se é o vídeo desta sessão, ou use --atraso.")
    t_sinc = t_v + atraso

    with open(os.path.join(PASTA_SAIDA, "video_alturas.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_sessao", "altura_esq_cm", "altura_dir_cm"])
        for i in range(len(t_sinc)):
            w.writerow([f"{t_sinc[i]:.4f}", f"{alt_v['esq'][i]:.2f}", f"{alt_v['dir'][i]:.2f}"])

    linhas, metodos = comparar(serie, entrada, fases, t_sinc, alt_v)
    if not linhas:
        print("Nenhum passo encontrado nas caminhadas. Confira as etapas e a sincronização.")
        return
    with open(os.path.join(PASTA_SAIDA, "validacao_passos.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    resumo(linhas, metodos)
    grafico(serie, entrada, t_sinc, alt_v, fases, os.path.join(PASTA_SAIDA, "validacao_video.png"))
    print(f"\nArquivos em {PASTA_SAIDA}/")


if __name__ == "__main__":
    main()
