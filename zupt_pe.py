"""
Altura e comprimento de cada passada usando só a IMU do próprio pé (ZUPT).

Por que existe: as posições OSC do SlimeVR saem do esqueleto calculado a partir
da cabeça. Qualquer inclinação do tronco, da pelve ou das coxas muda a posição
dos pés no OSC, mesmo com os pés parados no chão. Aqui a trajetória do pé é
obtida integrando a aceleração do próprio tracker do pé (SolarXR), entre dois
momentos em que o pé está parado no chão. Nenhum outro segmento entra na conta.

Passos do algoritmo, por pé:
1. Lê o samples.csv do cliente SolarXR e descarta as linhas repetidas
   (o feed reenvia o último valor quando não há amostra nova).
2. Reamostra para 100 Hz e corrige o referencial da aceleração com a rotação
   de montagem do pé (estimada numa caminhada, ver --calibrar-montagem).
3. Detecta o pé parado: aceleração linear baixa E velocidade angular baixa
   (calculada a partir do quaternion), por pelo menos REPOUSO_MIN_S.
4. Para cada intervalo entre dois repousos (o balanço do pé):
   integra a aceleração -> velocidade, força a velocidade a ser zero no
   início e no fim (remove a deriva linear), integra de novo -> posição.
5. Altura máxima = maior elevação vertical durante o balanço.
   Comprimento da passada = deslocamento horizontal entre os dois repousos.
   Desnível = diferença de altura entre o fim e o início (0 em chão plano,
   +/- altura da caixa ao subir/descer).

Uso:
    python zupt_pe.py CAMINHADA/samples.csv --calibrar-montagem
    python zupt_pe.py slimevr_client_raw/captures/SESSAO/samples.csv
    python zupt_pe.py SESSAO/samples.csv --csv dados/zupt_passadas.csv --grafico
"""

import argparse
import csv
import json
import os
import sys

import numpy as np

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
TAXA_HZ = 100.0             # Reamostragem uniforme
LIMIAR_ACC_REPOUSO = 0.8    # m/s² (aceleração linear, já sem gravidade)
LIMIAR_GIRO_REPOUSO = 0.8   # rad/s (velocidade angular do pé)
REPOUSO_MIN_S = 0.08        # Duração mínima de um repouso válido
MARGEM_REPOUSO_S = 0.25    # Quanto do repouso vizinho entra na integração de cada balanço
BALANCO_MIN_S = 0.20        # Balanço mais curto aceito
BALANCO_MAX_S = 2.50        # Balanço mais longo aceito (acima disso a deriva domina)
ELEVACAO_MIN_CM = 1.0       # Abaixo disso considera que o pé só girou/arrastou, sem passada
EIXO_VERTICAL = 1           # SlimeVR usa Y para cima (x=0, y=1, z=2)
# Correção de montagem: nas capturas de teste, a aceleração do SolarXR não fecha
# (velocidade não volta a zero no fim do balanço) porque vem num referencial girado
# em relação à orientação do pé. Uma rotação constante por pé, estimada numa
# caminhada curta, corrige isso. Fica salva neste arquivo e é reaproveitada.
ARQUIVO_MONTAGEM = os.path.join("dados", "zupt_montagem.json")
PESO_CHAO_PLANO = 20.0      # Peso das restrições de chão plano no ajuste da montagem
PESO_LATERAL = 5.0          # Peso do desvio lateral do pé durante o balanço

PES = {"ESQ": "LEFT_FOOT", "DIR": "RIGHT_FOOT"}
# ==============================================================


def carregar_pe(caminho_csv, body_part):
    """Lê as amostras novas de um pé. Retorna dict com t (s, relógio monotônico), t_unix, acc (N,3), quat (N,4)."""
    t_ns, t_unix, acc, quat = [], [], [], []
    ultimo = None
    with open(caminho_csv, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if linha.get("body_part_name") != body_part:
                continue
            try:
                a = (float(linha["linear_acc_x_ms2"]), float(linha["linear_acc_y_ms2"]), float(linha["linear_acc_z_ms2"]))
                q = (float(linha["rotation_x"]), float(linha["rotation_y"]), float(linha["rotation_z"]), float(linha["rotation_w"]))
            except (KeyError, ValueError):
                continue
            chave = a + q
            if chave == ultimo:  # reenvio do último valor, não é amostra nova
                continue
            ultimo = chave
            t_ns.append(int(linha["received_monotonic_ns"]))
            t_unix.append(float(linha["received_unix_ms"]) / 1000.0)
            acc.append(a)
            quat.append(q)
    if len(t_ns) < 10:
        return None
    t_ns = np.array(t_ns, dtype=np.int64)
    return {
        "t": (t_ns - t_ns[0]) / 1e9,
        "t_unix": np.array(t_unix),
        "acc": np.array(acc),
        "quat": np.array(quat),
    }


def rotacionar(q, v):
    """Aplica o quaternion q (x,y,z,w) aos vetores v. Ambos com N linhas."""
    u = q[:, :3]
    w = q[:, 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def velocidade_angular(t, quat):
    """Módulo da velocidade angular (rad/s) a partir de quaternions consecutivos."""
    dots = np.abs(np.sum(quat[1:] * quat[:-1], axis=1)).clip(0.0, 1.0)
    ang = 2.0 * np.arccos(dots)
    dt = np.diff(t)
    dt[dt <= 0] = np.nan
    w = ang / dt
    w = np.concatenate([[0.0], w])
    return np.nan_to_num(w)


def conj(q):
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def aplicar_montagem(quat, acc, rotvec):
    """Corrige o referencial da aceleração: a' = q · M · q⁻¹ · a, com M constante (vetor de rotação)."""
    if rotvec is None or not np.any(rotvec):
        return acc
    ang = float(np.linalg.norm(rotvec))
    eixo = np.asarray(rotvec) / ang
    m = np.concatenate([eixo * np.sin(ang / 2.0), [np.cos(ang / 2.0)]])
    m = np.tile(m, (len(quat), 1))
    local = rotacionar(conj(quat), acc)
    return rotacionar(quat, rotacionar(m, local))


def reamostrar(sinal):
    """Reamostra acc, quat e velocidade angular para uma base uniforme."""
    t = sinal["t"]
    giro = velocidade_angular(t, sinal["quat"])
    tu = np.arange(t[0], t[-1], 1.0 / TAXA_HZ)
    acc_u = np.column_stack([np.interp(tu, t, sinal["acc"][:, i]) for i in range(3)])
    idx = np.clip(np.searchsorted(t, tu), 0, len(t) - 1)
    quat_u = sinal["quat"][idx]
    giro_u = np.interp(tu, t, giro)
    t_unix_u = np.interp(tu, t, sinal["t_unix"])
    return tu, t_unix_u, acc_u, quat_u, giro_u


def media_movel(x, n):
    if n <= 1:
        return x
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def detectar_repouso(acc, giro):
    """Máscara booleana do pé parado no chão."""
    n_suave = max(1, int(0.05 * TAXA_HZ))
    acc_mag = media_movel(np.linalg.norm(acc, axis=1), n_suave)
    giro_s = media_movel(giro, n_suave)
    parado = (acc_mag < LIMIAR_ACC_REPOUSO) & (giro_s < LIMIAR_GIRO_REPOUSO)

    # Remove repousos curtos demais (cruzamentos de zero no meio do balanço)
    n_min = max(1, int(REPOUSO_MIN_S * TAXA_HZ))
    saida = parado.copy()
    i = 0
    while i < len(parado):
        if parado[i]:
            j = i
            while j < len(parado) and parado[j]:
                j += 1
            if j - i < n_min:
                saida[i:j] = False
            i = j
        else:
            i += 1
    return saida


def segmentos_balanco(repouso):
    """Lista de (inicio, fim) dos intervalos de movimento entre dois repousos."""
    segs = []
    n = len(repouso)
    i = 0
    # Só começa a contar depois do primeiro repouso
    while i < n and not repouso[i]:
        i += 1
    while i < n:
        while i < n and repouso[i]:
            i += 1
        ini = i - 1  # última amostra parada
        while i < n and not repouso[i]:
            i += 1
        if i >= n:
            break
        segs.append((ini, i))  # i = primeira amostra parada de novo
    # O começo e o fim do movimento têm aceleração pequena e caem dentro do "parado".
    # Estende cada balanço um pouco para dentro dos repousos vizinhos para não perder esse trecho.
    m = int(MARGEM_REPOUSO_S * TAXA_HZ)
    estendidos = []
    for k, (ini, fim) in enumerate(segs):
        lim_ini = segs[k - 1][1] if k > 0 else 0
        lim_fim = segs[k + 1][0] if k + 1 < len(segs) else n - 1
        ini2 = max(lim_ini + (ini - lim_ini) // 2, ini - m)
        fim2 = min(fim + (lim_fim - fim) // 2, fim + m)
        estendidos.append((ini2, fim2))
    return estendidos


def integrar_balanco(acc, ini, fim):
    """Integra um balanço com ZUPT. Retorna posições (N,3) relativas ao início, em metros."""
    dt = 1.0 / TAXA_HZ
    a = acc[ini:fim + 1]
    n = len(a)
    v = np.zeros_like(a)
    v[1:] = np.cumsum((a[1:] + a[:-1]) * 0.5 * dt, axis=0)
    # ZUPT: velocidade zero no fim. Remove a deriva de forma linear no tempo.
    rampa = np.linspace(0.0, 1.0, n)[:, None]
    v = v - rampa * v[-1]
    p = np.zeros_like(v)
    p[1:] = np.cumsum((v[1:] + v[:-1]) * 0.5 * dt, axis=0)
    return p


def balancos_validos(repouso):
    return [(i, f) for i, f in segmentos_balanco(repouso)
            if BALANCO_MIN_S <= (f - i) / TAXA_HZ <= BALANCO_MAX_S]


def erro_fechamento(acc, segs):
    """RMS da velocidade que sobra no fim de cada balanço, sem correção (m/s). Ideal: perto de 0."""
    dt = 1.0 / TAXA_HZ
    v = [np.linalg.norm(acc[i:f + 1].sum(axis=0) * dt) for i, f in segs]
    return float(np.sqrt(np.mean(np.square(v)))) if v else float("nan")


def estimar_montagem(caminho_csv, lado, tentativas=12, janela_unix=None):
    """Estima a rotação de montagem M de um pé a partir de uma caminhada em chão plano.

    Escolhe M que faz a velocidade integrada voltar a zero no fim de cada balanço,
    com o pé terminando cada passada na altura em que começou e sem passar abaixo do chão.
    Retorna (rotvec, erro_antes, erro_depois, n_balancos).
    """
    from scipy.optimize import minimize

    sinal = carregar_pe(caminho_csv, PES[lado])
    if sinal is None:
        return None, float("nan"), float("nan"), 0
    _, t_unix, acc, quat, giro = reamostrar(sinal)
    segs = balancos_validos(detectar_repouso(acc, giro))
    if janela_unix is not None:  # usa só os balanços da caminhada em chão plano
        segs = [(i, f) for i, f in segs if janela_unix[0] <= t_unix[i] <= janela_unix[1]]
    if len(segs) < 4:
        return None, float("nan"), float("nan"), len(segs)

    def custo(x):
        a = aplicar_montagem(quat, acc, x)
        c = erro_fechamento(a, segs) ** 2
        # Caminhada em chão plano: o pé termina cada passada na mesma altura e nunca fica
        # abaixo do chão. Isso fixa qual eixo é o vertical e o sentido para cima.
        # O pé também anda quase num plano vertical (pouco desvio para o lado), o que fixa
        # a inclinação lateral do eixo vertical, que a caminhada reta sozinha não define.
        pen = 0.0
        pen_lat = 0.0
        h = [k for k in range(3) if k != EIXO_VERTICAL]
        for i, f in segs:
            p = integrar_balanco(a, i, f)
            y = p[:, EIXO_VERTICAL]
            pen += y[-1] ** 2 + min(0.0, float(np.min(y))) ** 2
            d = p[-1, h]
            nd = float(np.hypot(d[0], d[1]))
            if nd > 0.1:
                lat = (p[:, h[0]] * -d[1] + p[:, h[1]] * d[0]) / nd
                pen_lat += float(np.max(np.abs(lat))) ** 2
        c += PESO_CHAO_PLANO * pen / len(segs) + PESO_LATERAL * pen_lat / len(segs)
        # Termo mínimo só para desempatar soluções equivalentes
        return c + 1e-6 * float(np.dot(x, x))

    melhor = None
    rng = np.random.default_rng(0)
    for x0 in np.vstack([np.zeros((1, 3)), rng.normal(0.0, 1.5, (tentativas, 3))]):
        r = minimize(custo, x0, method="Nelder-Mead", options={"maxiter": 1000, "xatol": 1e-3, "fatol": 1e-5})
        if melhor is None or r.fun < melhor.fun:
            melhor = r
    antes = erro_fechamento(acc, segs)
    depois = erro_fechamento(aplicar_montagem(quat, acc, melhor.x), segs)
    return melhor.x, antes, depois, len(segs)


def carregar_montagem(caminho=ARQUIVO_MONTAGEM):
    if not os.path.exists(caminho):
        return {}
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    return {lado: np.array(v["rotvec"]) for lado, v in dados.items()}


def salvar_montagem(resultado, caminho=ARQUIVO_MONTAGEM):
    pasta = os.path.dirname(caminho)
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2)


def processar_pe(caminho_csv, lado, montagem=None):
    """Roda o ZUPT em um pé. Retorna (lista de passadas, série contínua de altura)."""
    sinal = carregar_pe(caminho_csv, PES[lado])
    if sinal is None:
        return [], None
    tu, t_unix, acc, quat, giro = reamostrar(sinal)
    acc = aplicar_montagem(quat, acc, montagem)
    repouso = detectar_repouso(acc, giro)
    altura_serie = np.zeros(len(tu))
    nivel = 0.0  # altura acumulada do pé (sobe na caixa, desce da caixa)
    passadas = []
    for ini, fim in segmentos_balanco(repouso):
        dur = (fim - ini) / TAXA_HZ
        if dur < BALANCO_MIN_S or dur > BALANCO_MAX_S:
            altura_serie[ini:] = nivel
            continue
        p = integrar_balanco(acc, ini, fim)
        y = p[:, EIXO_VERTICAL]
        horiz = [k for k in range(3) if k != EIXO_VERTICAL]
        elev_max = float(np.max(y))
        desnivel = float(y[-1])
        if elev_max * 100.0 < ELEVACAO_MIN_CM and abs(desnivel) * 100.0 < ELEVACAO_MIN_CM:
            altura_serie[ini:] = nivel
            continue
        comp = float(np.hypot(p[-1, horiz[0]], p[-1, horiz[1]]))
        altura_serie[ini:fim + 1] = nivel + y
        nivel += desnivel
        altura_serie[fim + 1:] = nivel
        passadas.append({
            "pe": lado,
            "t_inicio_s": float(tu[ini]),
            "t_fim_s": float(tu[fim]),
            "t_inicio_unix": float(t_unix[ini]),
            "t_fim_unix": float(t_unix[fim]),
            "duracao_s": dur,
            "altura_max_cm": elev_max * 100.0,
            "desnivel_cm": desnivel * 100.0,
            "comprimento_m": comp,
            "v_residual_ms": float(np.linalg.norm(acc[ini:fim + 1].sum(axis=0) / TAXA_HZ)),
        })
    serie = {"t": tu, "t_unix": t_unix, "altura_cm": altura_serie * 100.0,
             "repouso": repouso, "acc_mag": np.linalg.norm(acc, axis=1), "giro": giro}
    return passadas, serie


def taxa_amostras_novas(caminho_csv):
    """Amostras novas por segundo de cada pé (qualidade do feed)."""
    taxas = {}
    for lado, bp in PES.items():
        s = carregar_pe(caminho_csv, bp)
        if s is not None and s["t"][-1] > 0:
            taxas[lado] = len(s["t"]) / s["t"][-1]
    return taxas


def salvar_csv(passadas, caminho):
    pasta = os.path.dirname(caminho)
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    campos = ["pe", "t_inicio_s", "t_fim_s", "duracao_s", "altura_max_cm", "desnivel_cm", "comprimento_m",
              "v_residual_ms", "t_inicio_unix", "t_fim_unix"]
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for p in passadas:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in p.items()})


def main():
    ap = argparse.ArgumentParser(description="Altura e comprimento da passada pela IMU do pé (ZUPT), sem depender do tronco.")
    ap.add_argument("samples", help="samples.csv gravado pelo cliente SolarXR (slimevr_client_raw)")
    ap.add_argument("--csv", help="Salva as passadas neste CSV")
    ap.add_argument("--grafico", action="store_true", help="Mostra o gráfico da altura de cada pé")
    ap.add_argument("--calibrar-montagem", action="store_true",
                    help="Usa esta captura (uma caminhada) para estimar a correção de montagem e salvar em " + ARQUIVO_MONTAGEM)
    ap.add_argument("--sem-montagem", action="store_true", help="Ignora a correção de montagem salva")
    args = ap.parse_args()

    if args.calibrar_montagem:
        resultado = {}
        for lado in PES:
            rotvec, antes, depois, n = estimar_montagem(args.samples, lado)
            if rotvec is None:
                print(f"Pé {lado}: só {n} balanços válidos. Grave uma caminhada com pelo menos 4 passadas por pé.")
                continue
            print(f"Pé {lado}: {n} balanços | velocidade residual {antes:.2f} -> {depois:.2f} m/s "
                  f"| rotação {np.degrees(np.linalg.norm(rotvec)) % 360:.0f}°")
            if depois > 0.5:
                print("    [Aviso] A correção não fechou bem (> 0,5 m/s). Confira a taxa de amostras novas.")
            resultado[lado] = {"rotvec": [float(v) for v in rotvec], "v_residual_antes": antes,
                               "v_residual_depois": depois, "balancos": n, "captura": os.path.abspath(args.samples)}
        if resultado:
            salvar_montagem(resultado)
            print(f"Correção de montagem salva em {ARQUIVO_MONTAGEM}\n")

    montagem = {} if args.sem_montagem else carregar_montagem()
    if montagem:
        print("Usando correção de montagem de " + ARQUIVO_MONTAGEM)
    else:
        print("Sem correção de montagem (rode com --calibrar-montagem numa caminhada).")

    taxas = taxa_amostras_novas(args.samples)
    print("Amostras novas por segundo: " + ", ".join(f"{k}={v:.0f} Hz" for k, v in taxas.items()))
    if taxas and min(taxas.values()) < 60:
        print("  [Aviso] Menos de 60 amostras novas/s. A altura pode sair subestimada; veja o README.")

    todas = []
    series = {}
    for lado in PES:
        passadas, serie = processar_pe(args.samples, lado, montagem.get(lado))
        series[lado] = serie
        todas.extend(passadas)
        print(f"\nPé {lado}: {len(passadas)} passadas")
        print(f"  {'#':>2} {'início(s)':>9} {'dur(s)':>6} {'altura(cm)':>10} {'desnível(cm)':>12} {'comp(m)':>7} {'v_resid':>7}")
        for i, p in enumerate(passadas, 1):
            print(f"  {i:>2} {p['t_inicio_s']:9.2f} {p['duracao_s']:6.2f} {p['altura_max_cm']:10.1f} "
                  f"{p['desnivel_cm']:12.1f} {p['comprimento_m']:7.2f} {p['v_residual_ms']:7.2f}")

    if args.csv:
        salvar_csv(todas, args.csv)
        print(f"\nPassadas salvas em {args.csv}")

    if args.grafico:
        import matplotlib.pyplot as plt
        fig, eixos = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
        for ax, lado in zip(eixos, PES):
            s = series[lado]
            if s is None:
                continue
            ax.plot(s["t"], s["altura_cm"], label="Altura do pé (ZUPT)")
            ax.fill_between(s["t"], 0, 1, where=s["repouso"], transform=ax.get_xaxis_transform(),
                            color="green", alpha=0.12, label="Pé parado")
            ax.set_ylabel(f"{lado} (cm)")
            ax.legend(loc="upper right")
            ax.grid(alpha=0.3)
        eixos[-1].set_xlabel("Tempo (s)")
        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    main()
