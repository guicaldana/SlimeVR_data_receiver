import csv
import sys
import time
import math
import argparse
import threading
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Suporte a UTF-8 no console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
PORTA_OSC = 9000
PORTA_VMC = 39539
IP = "127.0.0.1"

TORNOZELO_ESQ = "2"
TORNOZELO_DIR = "3"
TRACKER_PEITO = "6"

FREQUENCIA_CORTE_POSICAO = 5.0   # Hz para distância e elevação
FREQUENCIA_CORTE_ACEL = 8.0      # Hz para derivada de aceleração
ORDEM_FILTRO = 2

DISTANCIA_MINIMA_PASSO = 0.30    # metros
PROEMINENCIA_MINIMA = 0.08       # metros
ARQUIVO_SAIDA_CSV = "comparacao_metodos_passos.csv"
INTERVALO_FEEDBACK = 0.25
# ==============================================================

dados_ao_vivo = {
    TORNOZELO_ESQ: {"t": [], "x": [], "y": [], "z": []},
    TORNOZELO_DIR: {"t": [], "x": [], "y": [], "z": []},
}
tempo_inicial = None
ultimo_feedback = 0
lock = threading.Lock()
pos_atual = {TORNOZELO_ESQ: None, TORNOZELO_DIR: None}


# ----------------- RECEPÇÃO AO VIVO (OSC) -----------------
def osc_handler(address, *args):
    global tempo_inicial
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5:
        return
    tracker_id = partes[3]
    tipo = partes[4]
    tempo_atual = time.time()

    if tipo == "position" and len(args) == 3 and tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR):
        x, y, z = args
        with lock:
            if tempo_inicial is None:
                tempo_inicial = tempo_atual
            t_rel = tempo_atual - tempo_inicial
            dados_ao_vivo[tracker_id]["t"].append(t_rel)
            dados_ao_vivo[tracker_id]["x"].append(x)
            dados_ao_vivo[tracker_id]["y"].append(y)
            dados_ao_vivo[tracker_id]["z"].append(z)
            pos_atual[tracker_id] = (x, y, z)


def mostrar_feedback():
    global ultimo_feedback
    agora = time.time()
    if agora - ultimo_feedback < INTERVALO_FEEDBACK:
        return
    ultimo_feedback = agora

    with lock:
        p_esq = pos_atual[TORNOZELO_ESQ]
        p_dir = pos_atual[TORNOZELO_DIR]
        t_init = tempo_inicial

    if p_esq is None or p_dir is None:
        print("\r  Aguardando trackers dos tornozelos (2 e 3)...", end="", flush=True)
        return

    tempo = agora - t_init if t_init else 0
    dist = math.sqrt((p_dir[0] - p_esq[0])**2 + (p_dir[2] - p_esq[2])**2)
    barra = "#" * int(min(dist / 1.0, 1.0) * 12)
    print(f"\r  Tempo: {tempo:4.1f}s | Dist Tornozelos: {dist:.3f}m [{barra:<12}] (Pressione Ctrl+C para finalizar)", end="", flush=True)


# ----------------- CARREGAMENTO DE ARQUIVO CSV -----------------
def carregar_dados_arquivo(caminho_csv):
    """Carrega dados brutos previamente salvos no formato CSV."""
    t2, x2, y2, z2 = [], [], [], []
    t3, x3, y3, z3 = [], [], [], []
    with open(caminho_csv, mode="r", encoding="utf-8") as f:
        leitor = csv.reader(f)
        _ = next(leitor, None)
        for linha in leitor:
            if len(linha) >= 6 and linha[2] == "position":
                try:
                    ts = float(linha[0])
                    v1, v2, v3 = float(linha[3]), float(linha[4]), float(linha[5])
                    if linha[1] == TORNOZELO_ESQ:
                        t2.append(ts); x2.append(v1); y2.append(v2); z2.append(v3)
                    elif linha[1] == TORNOZELO_DIR:
                        t3.append(ts); x3.append(v1); y3.append(v2); z3.append(v3)
                except (ValueError, IndexError):
                    continue

    if len(t2) == 0 or len(t3) == 0:
        raise ValueError(f"Não foram encontrados dados de posição válidos para os tornozelos no arquivo {caminho_csv}")

    d_esq = {"t": t2, "x": x2, "y": y2, "z": z2}
    d_dir = {"t": t3, "x": x3, "y": y3, "z": z3}
    return d_esq, d_dir


# ----------------- PROCESSAMENTO E SINAIS -----------------
def aplicar_filtro_passa_baixa(sinal, frequencia_corte, taxa_amostragem):
    nyquist = taxa_amostragem / 2.0
    freq_norm = frequencia_corte / nyquist
    if freq_norm >= 1.0 or freq_norm <= 0:
        return sinal
    b, a = butter(ORDEM_FILTRO, freq_norm, btype="low")
    return filtfilt(b, a, sinal)


def preparar_sinais(dados_esq, dados_dir):
    t_esq = np.array(dados_esq["t"])
    t_dir = np.array(dados_dir["t"])
    t_zero = min(t_esq[0], t_dir[0])
    t_esq -= t_zero
    t_dir -= t_zero

    x_esq, y_esq, z_esq = np.array(dados_esq["x"]), np.array(dados_esq["y"]), np.array(dados_esq["z"])
    x_dir, y_dir, z_dir = np.array(dados_dir["x"]), np.array(dados_dir["y"]), np.array(dados_dir["z"])

    t_inicio = max(t_esq[0], t_dir[0])
    t_fim = min(t_esq[-1], t_dir[-1])
    dt = 0.01  # Resolução uniforme de 100 Hz
    t_comum = np.arange(t_inicio, t_fim, dt)
    taxa_amostragem = 1.0 / dt

    # Interpolação para base de tempo unificada
    x2 = np.interp(t_comum, t_esq, x_esq)
    y2 = np.interp(t_comum, t_esq, y_esq)
    z2 = np.interp(t_comum, t_esq, z_esq)

    x3 = np.interp(t_comum, t_dir, x_dir)
    y3 = np.interp(t_comum, t_dir, y_dir)
    z3 = np.interp(t_comum, t_dir, z_dir)

    # 1. Distância Euclidiana Horizontal entre tornozelos
    dist_bruta = np.sqrt((x3 - x2)**2 + (z3 - z2)**2)
    dist_filt = aplicar_filtro_passa_baixa(dist_bruta, FREQUENCIA_CORTE_POSICAO, taxa_amostragem)

    # 2. Elevação Vertical e Velocidade Vertical (Clearance / Landing)
    y2_filt = aplicar_filtro_passa_baixa(y2, FREQUENCIA_CORTE_POSICAO, taxa_amostragem)
    y3_filt = aplicar_filtro_passa_baixa(y3, FREQUENCIA_CORTE_POSICAO, taxa_amostragem)
    elev2 = np.maximum(0.0, y2_filt - np.percentile(y2_filt, 5))
    elev3 = np.maximum(0.0, y3_filt - np.percentile(y3_filt, 5))
    vy2 = np.gradient(y2_filt, dt)
    vy3 = np.gradient(y3_filt, dt)

    # 3. Aceleração Resultante (Impacto Mecânico / Deceleração de Choque)
    def calcular_aceleracao(x, y, z):
        x_f = aplicar_filtro_passa_baixa(x, FREQUENCIA_CORTE_ACEL, taxa_amostragem)
        y_f = aplicar_filtro_passa_baixa(y, FREQUENCIA_CORTE_ACEL, taxa_amostragem)
        z_f = aplicar_filtro_passa_baixa(z, FREQUENCIA_CORTE_ACEL, taxa_amostragem)
        ax = np.gradient(np.gradient(x_f, dt), dt)
        ay = np.gradient(np.gradient(y_f, dt), dt)
        az = np.gradient(np.gradient(z_f, dt), dt)
        return np.sqrt(ax**2 + ay**2 + az**2)

    acc2 = calcular_aceleracao(x2, y2, z2)
    acc3 = calcular_aceleracao(x3, y3, z3)

    return {
        "t": t_comum, "dt": dt,
        "dist_bruta": dist_bruta, "dist_filt": dist_filt,
        "elev2": elev2, "elev3": elev3,
        "vy2": vy2, "vy3": vy3,
        "acc2": acc2, "acc3": acc3
    }


# ----------------- COMPARAÇÃO DOS 3 MÉTODOS -----------------
def executar_comparacao_tres_metodos(sinais):
    t = sinais["t"]
    dist_f = sinais["dist_filt"]
    elev2 = sinais["elev2"]
    elev3 = sinais["elev3"]
    vy2 = sinais["vy2"]
    vy3 = sinais["vy3"]
    acc2 = sinais["acc2"]
    acc3 = sinais["acc3"]

    # Detecção de passos base pelo perfil de oscilação / separação
    intervalo_min_pontos = 25  # ~0.25 s a 100 Hz
    picos_m1, _ = find_peaks(
        dist_f,
        height=DISTANCIA_MINIMA_PASSO,
        distance=intervalo_min_pontos,
        prominence=PROEMINENCIA_MINIMA
    )

    resultados = []

    for i, idx1 in enumerate(picos_m1):
        t_m1 = t[idx1]
        d_m1 = dist_f[idx1]

        # Janela do ciclo da passada
        t_ant = t[picos_m1[i - 1]] if i > 0 else max(t[0], t_m1 - 0.8)
        janela_ciclo = (t >= t_ant) & (t <= min(t[-1], t_m1 + 0.35))

        # Determina qual pé estava na fase de oscilação (maior elevação vertical)
        pico_e2 = np.max(elev2[janela_ciclo]) if np.any(janela_ciclo) else 0
        pico_e3 = np.max(elev3[janela_ciclo]) if np.any(janela_ciclo) else 0
        pe_ativo = "DIR" if pico_e3 >= pico_e2 else "ESQ"

        elev_swing = elev3 if pe_ativo == "DIR" else elev2
        vy_swing = vy3 if pe_ativo == "DIR" else vy2
        acc_swing = acc3 if pe_ativo == "DIR" else acc2

        # Ponto máximo de elevação da perna (Mid-Swing Apex)
        idx_janela = np.where(janela_ciclo)[0]
        idx_apex = idx_janela[np.argmax(elev_swing[janela_ciclo])]

        # --- MÉTODO 2: Cinemática de Contato (Landing / Zero-Velocity Descent) ---
        # Procura entre o ápice de elevação e um pouco após o pico de distância
        janela_pouso = (t >= t[idx_apex]) & (t <= min(t[-1], t_m1 + 0.35))
        idx_pouso = np.where(janela_pouso)[0]

        idx_m2 = idx1
        if len(idx_pouso) > 2:
            vy_desce = vy_swing[idx_pouso]
            # Momento em que a velocidade vertical de descida cruza de volta para zero (freio de descida)
            cruzamento_zero = np.where((vy_desce[:-1] < 0) & (vy_desce[1:] >= 0))[0]
            if len(cruzamento_zero) > 0:
                idx_m2 = idx_pouso[cruzamento_zero[0]]
            else:
                # Caso não haja cruzamento exato, seleciona o mínimo de elevação
                idx_m2 = idx_pouso[np.argmin(elev_swing[idx_pouso])]

        t_m2 = t[idx_m2]
        d_m2 = dist_f[idx_m2]

        # --- MÉTODO 3: Impacto / Choque Mecânico de Desaceleração ---
        janela_impacto = (t >= t[idx_apex]) & (t <= min(t[-1], t_m1 + 0.35))
        idx_impacto = np.where(janela_impacto)[0]

        idx_m3 = idx1
        if len(idx_impacto) > 0:
            picos_acc, props = find_peaks(acc_swing[idx_impacto], prominence=0.5)
            if len(picos_acc) > 0:
                # Prioriza o pico mais proeminente dentro da janela de término do passo
                idx_m3 = idx_impacto[picos_acc[np.argmax(props["prominences"])]]
            else:
                idx_m3 = idx_impacto[np.argmax(acc_swing[idx_impacto])]

        t_m3 = t[idx_m3]
        d_m3 = dist_f[idx_m3]

        resultados.append({
            "passo": i + 1,
            "pe": pe_ativo,
            "idx_m1": idx1, "t_m1": t_m1, "d_m1": d_m1,
            "idx_m2": idx_m2, "t_m2": t_m2, "d_m2": d_m2,
            "idx_m3": idx_m3, "t_m3": t_m3, "d_m3": d_m3,
            "delta_t_m1_m2_ms": (t_m1 - t_m2) * 1000.0,
            "delta_t_m1_m3_ms": (t_m1 - t_m3) * 1000.0,
            "delta_d_m1_m2_cm": (d_m1 - d_m2) * 100.0,
            "delta_d_m1_m3_cm": (d_m1 - d_m3) * 100.0,
        })

    return resultados


# ----------------- RELATÓRIO E PLOTS -----------------
def exibir_relatorio_e_salvar_csv(resultados):
    if not resultados:
        print("\n[Aviso] Nenhum passo detectado para comparação.")
        return

    print("\n" + "=" * 96)
    print("      COMPARAÇÃO DE MÉTODOS DE DETECÇÃO E CÁLCULO DE COMPRIMENTO DO PASSO")
    print("=" * 96)
    print(f"{'#':<3} {'Pé':<4} | {'M1: Pico Dist':<16} | {'M2: Contato Cinem.':<18} | {'M3: Impacto Acel.':<18} | {'Δ(M1-M2)':<11} | {'Δ(M1-M3)':<11}")
    print(f"{'':<3} {'':<4} | {'Tempo   Comp.':<16} | {'Tempo    Comp.':<18} | {'Tempo    Comp.':<18} | {'Comp.(cm)':<11} | {'Comp.(cm)':<11}")
    print("-" * 96)

    m1_comps = [r["d_m1"] for r in resultados]
    m2_comps = [r["d_m2"] for r in resultados]
    m3_comps = [r["d_m3"] for r in resultados]
    diffs_m2 = [r["delta_d_m1_m2_cm"] for r in resultados]
    diffs_m3 = [r["delta_d_m1_m3_cm"] for r in resultados]

    for r in resultados:
        print(
            f"{r['passo']:<3} {r['pe']:<4} | "
            f"{r['t_m1']:5.2f}s  {r['d_m1']:5.3f}m | "
            f"{r['t_m2']:5.2f}s  {r['d_m2']:5.3f}m   | "
            f"{r['t_m3']:5.2f}s  {r['d_m3']:5.3f}m   | "
            f"{r['delta_d_m1_m2_cm']:+6.1f} cm   | "
            f"{r['delta_d_m1_m3_cm']:+6.1f} cm"
        )
    print("=" * 96)

    print(f"Média M1 (Pico de Distância):        {np.mean(m1_comps):.3f} m (± {np.std(m1_comps):.3f} m)")
    print(f"Média M2 (Contato Cinemático Solo):  {np.mean(m2_comps):.3f} m (± {np.std(m2_comps):.3f} m)")
    print(f"Média M3 (Pico de Impacto/Choque):   {np.mean(m3_comps):.3f} m (± {np.std(m3_comps):.3f} m)")
    print(f"Diferença Média (M1 - M2):           {np.mean(diffs_m2):+.1f} cm")
    print(f"Diferença Média (M1 - M3):           {np.mean(diffs_m3):+.1f} cm")
    print("=" * 96)

    # Gravação no CSV
    with open(ARQUIVO_SAIDA_CSV, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Passo", "Pe",
            "M1_Tempo_s", "M1_Comprimento_m",
            "M2_Tempo_s", "M2_Comprimento_m",
            "M3_Tempo_s", "M3_Comprimento_m",
            "Delta_Tempo_M1_M2_ms", "Delta_Tempo_M1_M3_ms",
            "Delta_Comp_M1_M2_cm", "Delta_Comp_M1_M3_cm"
        ])
        for r in resultados:
            escritor.writerow([
                r["passo"], r["pe"],
                f"{r['t_m1']:.3f}", f"{r['d_m1']:.3f}",
                f"{r['t_m2']:.3f}", f"{r['d_m2']:.3f}",
                f"{r['t_m3']:.3f}", f"{r['d_m3']:.3f}",
                f"{r['delta_t_m1_m2_ms']:.1f}", f"{r['delta_t_m1_m3_ms']:.1f}",
                f"{r['delta_d_m1_m2_cm']:.2f}", f"{r['delta_d_m1_m3_cm']:.2f}"
            ])
    print(f"\nResultados detalhados da comparação salvos em '{ARQUIVO_SAIDA_CSV}'.")


def plotar_graficos_comparacao(sinais, resultados):
    if not resultados:
        return

    t = sinais["t"]
    dist_f = sinais["dist_filt"]
    elev2 = sinais["elev2"] * 100.0  # cm
    elev3 = sinais["elev3"] * 100.0  # cm
    acc2 = sinais["acc2"]
    acc3 = sinais["acc3"]

    fig, axes = plt.subplots(4, 1, figsize=(14, 11), sharex=True)

    # Painel 1: Distância inter-tornozelos com marcações dos 3 métodos
    ax1 = axes[0]
    ax1.plot(t, dist_f, color="#1f77b4", label="Distância Intermembros (m)", linewidth=1.5)
    t_m1 = [r["t_m1"] for r in resultados]
    d_m1 = [r["d_m1"] for r in resultados]
    t_m2 = [r["t_m2"] for r in resultados]
    d_m2 = [r["d_m2"] for r in resultados]
    t_m3 = [r["t_m3"] for r in resultados]
    d_m3 = [r["d_m3"] for r in resultados]

    ax1.scatter(t_m1, d_m1, color="navy", marker="o", s=60, zorder=5, label="M1: Pico Distância")
    ax1.scatter(t_m2, d_m2, color="forestgreen", marker="s", s=60, zorder=5, label="M2: Contato Cinemático")
    ax1.scatter(t_m3, d_m3, color="crimson", marker="^", s=65, zorder=5, label="M3: Impacto / Choque")

    for tm1, tm2, tm3 in zip(t_m1, t_m2, t_m3):
        ax1.axvline(tm1, color="navy", linestyle=":", alpha=0.35)
        ax1.axvline(tm2, color="forestgreen", linestyle="--", alpha=0.35)
        ax1.axvline(tm3, color="crimson", linestyle="-.", alpha=0.35)

    ax1.set_ylabel("Distância (m)")
    ax1.set_title("1. Comparação de Timings e Comprimento de Passo no Sinal de Distância")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Painel 2: Trajetórias verticais de elevação (Clearance do pé)
    ax2 = axes[1]
    ax2.plot(t, elev2, color="cornflowerblue", label="Elevação Tornozelo ESQ (cm)")
    ax2.plot(t, elev3, color="darkorange", label="Elevação Tornozelo DIR (cm)")
    for tm2 in t_m2:
        ax2.axvline(tm2, color="forestgreen", linestyle="--", alpha=0.5)
    ax2.set_ylabel("Elevação (cm)")
    ax2.set_title("2. Cinemática Vertical e Momento de Pouso no Solo (Linhas Verdes = M2)")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.3)

    # Painel 3: Aceleração resultante de impacto dos tornozelos
    ax3 = axes[2]
    ax3.plot(t, acc2, color="teal", alpha=0.8, label="Aceleração ESQ (m/s²)")
    ax3.plot(t, acc3, color="coral", alpha=0.8, label="Aceleração DIR (m/s²)")
    for tm3 in t_m3:
        ax3.axvline(tm3, color="crimson", linestyle="-.", alpha=0.5)
    ax3.set_ylabel("Aceleração (m/s²)")
    ax3.set_title("3. Sinal Dinâmico de Aceleração e Picos de Choque (Linhas Vermelhas = M3)")
    ax3.legend(loc="upper right", fontsize=8)
    ax3.grid(True, alpha=0.3)

    # Painel 4: Gráfico comparativo de barras passo a passo
    ax4 = axes[3]
    passos_idx = np.arange(1, len(resultados) + 1)
    largura = 0.26
    # O quarto gráfico não precisa compartilhar o mesmo eixo x temporal
    # Vamos desativar o sharex para o painel 4 gerando outro eixo se necessário, ou plotando os pontos por tempo
    ax4.plot(t_m1, [r["d_m1"] for r in resultados], "o-", color="navy", label="M1: Pico Dist (m)", linewidth=1.5)
    ax4.plot(t_m1, [r["d_m2"] for r in resultados], "s--", color="forestgreen", label="M2: Contato Cinem. (m)", linewidth=1.5)
    ax4.plot(t_m1, [r["d_m3"] for r in resultados], "^-.", color="crimson", label="M3: Impacto Acel. (m)", linewidth=1.5)
    ax4.set_xlabel("Tempo (s)")
    ax4.set_ylabel("Comprimento (m)")
    ax4.set_title("4. Evolução do Comprimento Detectado por Cada Método ao Longo da Marcha")
    ax4.legend(loc="upper right", fontsize=8)
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def main():
    parser = argparse.ArgumentParser(
        description="Comparador de Métodos de Detecção e Timing de Passadas (SlimeVR)"
    )
    parser.add_argument(
        "--arquivo", "-a",
        type=str,
        default=None,
        help="Caminho de um arquivo CSV previamente gravado para análise offline (ex: dados_marcha_paciente.csv)"
    )
    args = parser.parse_args()

    if args.arquivo:
        print("=" * 70)
        print(f"MODO ARQUIVO: Analisando dados de '{args.arquivo}'")
        print("=" * 70)
        d_esq, d_dir = carregar_dados_arquivo(args.arquivo)
        sinais = preparar_sinais(d_esq, d_dir)
        resultados = executar_comparacao_tres_metodos(sinais)
        exibir_relatorio_e_salvar_csv(resultados)
        plotar_graficos_comparacao(sinais, resultados)
    else:
        print("=" * 70)
        print("MODO AO VIVO: Conectando aos servidores OSC do SlimeVR...")
        print(f"Porta 9000: Tornozelos ({TORNOZELO_ESQ} e {TORNOZELO_DIR})")
        print("Pressione 'Ctrl + C' quando terminar a caminhada para comparar.")
        print("=" * 70)

        disp = Dispatcher()
        disp.set_default_handler(osc_handler)
        server = ThreadingOSCUDPServer((IP, PORTA_OSC), disp)
        t_srv = threading.Thread(target=server.serve_forever, daemon=True)
        t_srv.start()

        try:
            while True:
                mostrar_feedback()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n\nCaptura concluída! Processando os sinais para os 3 métodos...")
            with lock:
                d_esq = dados_ao_vivo[TORNOZELO_ESQ]
                d_dir = dados_ao_vivo[TORNOZELO_DIR]

            if len(d_esq["t"]) < 20 or len(d_dir["t"]) < 20:
                print("[Erro] Poucos dados coletados. Caminhe por mais tempo para calibrar.")
                return

            # Salva cópia bruta para reanálise offline posterior
            arquivo_bruto = "dados_brutos_sessao.csv"
            with open(arquivo_bruto, mode="w", newline="", encoding="utf-8") as f:
                escritor = csv.writer(f)
                escritor.writerow(["Timestamp", "Tracker_ID", "Tipo", "Valor1", "Valor2", "Valor3"])
                for i in range(len(d_esq["t"])):
                    escritor.writerow([d_esq["t"][i], TORNOZELO_ESQ, "position", d_esq["x"][i], d_esq["y"][i], d_esq["z"][i]])
                for i in range(len(d_dir["t"])):
                    escritor.writerow([d_dir["t"][i], TORNOZELO_DIR, "position", d_dir["x"][i], d_dir["y"][i], d_dir["z"][i]])
            print(f"Dados brutos da captura salvos em '{arquivo_bruto}'.")

            sinais = preparar_sinais(d_esq, d_dir)
            resultados = executar_comparacao_tres_metodos(sinais)
            exibir_relatorio_e_salvar_csv(resultados)
            plotar_graficos_comparacao(sinais, resultados)


if __name__ == "__main__":
    main()
