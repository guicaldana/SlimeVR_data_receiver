import csv
import sys
import os
import time
import math
import subprocess
import threading
import argparse
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Suporte a UTF-8 no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
PORTA_OSC = 9000
IP_OSC = "127.0.0.1"

# Identificação dos trackers no OSC
ID_OSC_ESQ = "2"
ID_OSC_DIR = "3"

ARQUIVO_COMPARACAO_CSV = "comparacao_zupt_vs_osc.csv"
INTERVALO_FEEDBACK = 0.15  # Atualiza terminal ~7 vezes por segundo
# ==============================================================

# Buffers em memória para os dados do OSC
dados_osc = {
    ID_OSC_ESQ: {"t_unix": [], "x": [], "y": [], "z": []},
    ID_OSC_DIR: {"t_unix": [], "x": [], "y": [], "z": []}
}
lock_osc = threading.Lock()
pos_atual_osc = {ID_OSC_ESQ: None, ID_OSC_DIR: None}
ultimo_feedback = 0


# ----------------- RECEPÇÃO DE DADOS OSC (PORTA 9000) -----------------
def osc_handler(address, *args):
    """Captura posições cinemáticas X, Y, Z dos tornozelos/pés via OSC."""
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5:
        return

    tracker_id = partes[3]
    tipo = partes[4]

    if tipo == "position" and len(args) == 3 and tracker_id in (ID_OSC_ESQ, ID_OSC_DIR):
        x, y, z = args
        agora = time.time()
        with lock_osc:
            dados_osc[tracker_id]["t_unix"].append(agora)
            dados_osc[tracker_id]["x"].append(x)
            dados_osc[tracker_id]["y"].append(y)
            dados_osc[tracker_id]["z"].append(z)
            pos_atual_osc[tracker_id] = (x, y, z)


def rodar_servidor_osc(server):
    try:
        server.serve_forever()
    except Exception:
        pass


# ----------------- CARREGAMENTO E SINCRONIZAÇÃO SOLARXR -----------------
def carregar_dados_solarxr(caminho_csv):
    """Carrega dados das IMUs gravados pelo cliente SolarXR (samples.csv)."""
    if not os.path.exists(caminho_csv):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_csv}")

    dados_imu = {}
    with open(caminho_csv, mode="r", encoding="utf-8") as f:
        leitor = csv.DictReader(f)
        for linha in leitor:
            nome_parte = linha.get("body_part_name") or ""
            # Foco nos pés ou tornozelos
            if "FOOT" not in nome_parte and "LOWER_LEG" not in nome_parte:
                continue

            lado = "ESQ" if "LEFT" in nome_parte else ("DIR" if "RIGHT" in nome_parte else None)
            if not lado:
                continue

            tipo_tag = "FOOT" if "FOOT" in nome_parte else "LEG"
            chave = f"{lado}_{tipo_tag}"

            if chave not in dados_imu:
                dados_imu[chave] = {
                    "lado": lado,
                    "tipo": tipo_tag,
                    "t_unix": [],
                    "ax": [], "ay": [], "az": []
                }

            try:
                t_unix = float(linha["received_unix_ms"]) / 1000.0
                ax = float(linha["linear_acc_x_ms2"])
                ay = float(linha["linear_acc_y_ms2"])
                az = float(linha["linear_acc_z_ms2"])
            except (ValueError, KeyError, TypeError):
                continue

            dados_imu[chave]["t_unix"].append(t_unix)
            dados_imu[chave]["ax"].append(ax)
            dados_imu[chave]["ay"].append(ay)
            dados_imu[chave]["az"].append(az)

    # Se houver FOOT e LEG, prioriza FOOT
    dados_finais = {}
    for lado in ("ESQ", "DIR"):
        chave_foot = f"{lado}_FOOT"
        chave_leg = f"{lado}_LEG"
        if chave_foot in dados_imu and len(dados_imu[chave_foot]["t_unix"]) > 30:
            dados_finais[lado] = dados_imu[chave_foot]
        elif chave_leg in dados_imu and len(dados_imu[chave_leg]["t_unix"]) > 30:
            dados_finais[lado] = dados_imu[chave_leg]

    return dados_finais


# ----------------- ALGORITMO ZUPT (FORMA 2: INERCIAL) -----------------
def processar_zupt_pe(t_uniforme, dt, ax, ay, az):
    """Calcula passadas, comprimento e altura via dupla integração com ZUPT."""
    acc_mag = np.sqrt(ax**2 + ay**2 + az**2)

    # Filtro passa-baixa para detecção estável de repouso (fase de apoio / Stance)
    b, a = butter(2, 6.0 / (50.0), btype="low")
    acc_suave = filtfilt(b, a, acc_mag)

    # Detecção de Heel-Strikes (impactos no chão)
    picos_impacto, _ = find_peaks(
        acc_mag,
        height=6.0,
        distance=int(0.40 / dt),
        prominence=3.0
    )

    if len(picos_impacto) < 2:
        return None

    passadas = []
    pos_y_total = np.zeros_like(t_uniforme)
    vel_total = np.zeros_like(t_uniforme)

    for i in range(len(picos_impacto) - 1):
        idx1 = picos_impacto[i]
        idx2 = picos_impacto[i + 1]

        t_sub = t_uniforme[idx1:idx2]
        if len(t_sub) < 5:
            continue

        n_p = len(t_sub)
        ax_sub, ay_sub, az_sub = ax[idx1:idx2], ay[idx1:idx2], az[idx1:idx2]

        # 1ª Integração: aceleração -> velocidade
        vx = np.cumsum(ax_sub) * dt
        vy = np.cumsum(ay_sub) * dt
        vz = np.cumsum(az_sub) * dt

        # Correção ZUPT linear de deriva (força v=0 no repouso da aterrissagem)
        vx_c = vx - np.linspace(0, vx[-1], n_p)
        vy_c = vy - np.linspace(0, vy[-1], n_p)
        vz_c = vz - np.linspace(0, vz[-1], n_p)

        # 2ª Integração: velocidade corrigida -> deslocamento e altura
        dx = np.cumsum(vx_c) * dt
        dy = np.cumsum(vy_c) * dt
        dz = np.cumsum(vz_c) * dt

        # Trajetória de elevação vertical corrigida
        perfil_y = dy - np.linspace(0, dy[-1], n_p)
        altura_max_cm = float(np.max(perfil_y) * 100.0)

        # Comprimento horizontal da passada
        comp_horizontal_m = float(np.sqrt((dx[-1] - dx[0])**2 + (dz[-1] - dz[0])**2))
        vel_mag = np.sqrt(vx_c**2 + vy_c**2 + vz_c**2)
        vel_pico_ms = float(np.max(vel_mag))

        pos_y_total[idx1:idx2] = perfil_y
        vel_total[idx1:idx2] = vel_mag

        passadas.append({
            "passo": i + 1,
            "idx_inicio": idx1,
            "idx_fim": idx2,
            "t_inicio": t_uniforme[idx1],
            "t_impacto": t_uniforme[idx2],
            "duracao_s": float(t_sub[-1] - t_sub[0]),
            "comp_zupt_m": comp_horizontal_m,
            "alt_zupt_cm": altura_max_cm,
            "vel_pico_ms": vel_pico_ms,
            "perfil_y": perfil_y,
            "t_sub": t_sub
        })

    return {
        "passadas": passadas,
        "acc_mag": acc_mag,
        "vel_total": vel_total,
        "pos_y_total": pos_y_total,
        "picos_impacto": picos_impacto
    }


# ----------------- MÉTODO OSC (CINEMÁTICA DE POSIÇÃO) -----------------
def processar_osc_correspondente(t_uniforme, dt, x_osc, y_osc, z_osc, passadas_zupt):
    """Calcula as mesmas métricas (comprimento e altura) a partir da posição OSC nas mesmas janelas temporais."""
    # Filtro passa-baixa para o sinal de posição do OSC
    b, a = butter(2, 5.0 / (50.0), btype="low")
    x_filt = filtfilt(b, a, x_osc)
    y_filt = filtfilt(b, a, y_osc)
    z_filt = filtfilt(b, a, z_osc)

    chao_y = np.percentile(y_filt, 5)
    elev_osc = np.maximum(0.0, y_filt - chao_y)

    resultados_osc = []
    for p in passadas_zupt:
        i1 = p["idx_inicio"]
        i2 = p["idx_fim"]

        # 1. Comprimento do passo via OSC: deslocamento horizontal do mesmo pé entre início e aterrissagem
        dx_osc = x_filt[i2] - x_filt[i1]
        dz_osc = z_filt[i2] - z_filt[i1]
        comp_osc_m = float(np.sqrt(dx_osc**2 + dz_osc**2))

        # 2. Altura da passada via OSC: máxima elevação vertical durante essa mesma passada
        alt_osc_cm = float(np.max(elev_osc[i1:i2]) * 100.0)

        resultados_osc.append({
            "comp_osc_m": comp_osc_m,
            "alt_osc_cm": alt_osc_cm
        })

    return {
        "elev_osc": elev_osc,
        "metricas": resultados_osc
    }


# ----------------- COMPARAÇÃO INTEGRADA -----------------
def comparar_membros(dados_imu_dict, dados_osc_dict):
    """Cruza e compara ZUPT vs OSC para cada pé em base de tempo comum."""
    t_min_list, t_max_list = [], []

    for lado, imu_info in dados_imu_dict.items():
        t_min_list.append(imu_info["t_unix"][0])
        t_max_list.append(imu_info["t_unix"][-1])

    for id_osc in (ID_OSC_ESQ, ID_OSC_DIR):
        if len(dados_osc_dict[id_osc]["t_unix"]) > 10:
            t_min_list.append(dados_osc_dict[id_osc]["t_unix"][0])
            t_max_list.append(dados_osc_dict[id_osc]["t_unix"][-1])

    if not t_min_list:
        return None

    t_inicio_comum = max(t_min_list)
    t_fim_comum = min(t_max_list)

    if t_fim_comum - t_inicio_comum < 3.0:
        raise ValueError("Tempo comum de interseção insuficiente entre SolarXR e OSC.")

    dt = 0.01  # 100 Hz
    t_comum = np.arange(t_inicio_comum, t_fim_comum, dt)
    t_relativo = t_comum - t_inicio_comum

    comparacoes = {}

    mapeamento_lados = {
        "ESQ": (ID_OSC_ESQ, "Pé Esquerdo"),
        "DIR": (ID_OSC_DIR, "Pé Direito")
    }

    for lado, (id_osc, nome_amigavel) in mapeamento_lados.items():
        if lado not in dados_imu_dict:
            continue
        if len(dados_osc_dict[id_osc]["t_unix"]) < 20:
            continue

        # Interpolação SolarXR IMU
        imu = dados_imu_dict[lado]
        t_imu = np.array(imu["t_unix"])
        ax_i = np.interp(t_comum, t_imu, imu["ax"])
        ay_i = np.interp(t_comum, t_imu, imu["ay"])
        az_i = np.interp(t_comum, t_imu, imu["az"])

        # Interpolação OSC Posição
        osc = dados_osc_dict[id_osc]
        t_osc = np.array(osc["t_unix"])
        x_osc_i = np.interp(t_comum, t_osc, osc["x"])
        y_osc_i = np.interp(t_comum, t_osc, osc["y"])
        z_osc_i = np.interp(t_comum, t_osc, osc["z"])

        # 1. Processamento ZUPT (Inercial)
        res_zupt = processar_zupt_pe(t_relativo, dt, ax_i, ay_i, az_i)
        if not res_zupt or not res_zupt["passadas"]:
            continue

        # 2. Processamento OSC (Posição)
        res_osc = processar_osc_correspondente(t_relativo, dt, x_osc_i, y_osc_i, z_osc_i, res_zupt["passadas"])

        comparacoes[lado] = {
            "nome": nome_amigavel,
            "t_rel": t_relativo,
            "zupt": res_zupt,
            "osc": res_osc
        }

    return comparacoes


# ----------------- RELATÓRIO E EXPORTAÇÃO -----------------
def exibir_relatorio_e_salvar(comparacoes):
    print("\n" + "=" * 98)
    print("      COMPARAÇÃO DE MARCHA: INERCIAL 3D (ZUPT) vs CINEMÁTICA DE POSIÇÃO (OSC)")
    print("=" * 98)

    linhas_csv = []

    for lado, comp in comparacoes.items():
        nome = comp["nome"]
        passadas = comp["zupt"]["passadas"]
        metricas_osc = comp["osc"]["metricas"]

        print(f"\n▶ {nome} ({len(passadas)} passadas sincronizadas):")
        print(f"{'#':<4} {'Tempo(s)':<9} {'Comp ZUPT(m)':<14} {'Comp OSC(m)':<14} {'ΔComp(cm)':<12} | {'Alt ZUPT(cm)':<14} {'Alt OSC(cm)':<14} {'ΔAlt(cm)'}")
        print("-" * 98)

        comps_z, comps_o = [], []
        alts_z, alts_o = [], []

        for i, (pz, po) in enumerate(zip(passadas, metricas_osc)):
            c_z = pz["comp_zupt_m"]
            c_o = po["comp_osc_m"]
            delta_c = (c_z - c_o) * 100.0

            a_z = pz["alt_zupt_cm"]
            a_o = po["alt_osc_cm"]
            delta_a = a_z - a_o

            comps_z.append(c_z)
            comps_o.append(c_o)
            alts_z.append(a_z)
            alts_o.append(a_o)

            print(
                f"{i+1:<4} {pz['t_inicio']:<9.2f} "
                f"{c_z:<14.3f} {c_o:<14.3f} {delta_c:+10.1f} cm | "
                f"{a_z:<14.1f} {a_o:<14.1f} {delta_a:+10.1f} cm"
            )

            linhas_csv.append([
                lado, i + 1, f"{pz['t_inicio']:.3f}", f"{pz['duracao_s']:.3f}",
                f"{c_z:.3f}", f"{c_o:.3f}", f"{delta_c:.2f}",
                f"{a_z:.2f}", f"{a_o:.2f}", f"{delta_a:.2f}"
            ])

        print("-" * 98)
        print(f"  MÉDIAS {nome}:")
        print(f"  • Comprimento: ZUPT = {np.mean(comps_z):.3f} m | OSC = {np.mean(comps_o):.3f} m (Diferença média: {np.mean(comps_z) - np.mean(comps_o):+.3f} m)")
        print(f"  • Altura/Clearance: ZUPT = {np.mean(alts_z):.1f} cm | OSC = {np.mean(alts_o):.1f} cm (Diferença média: {np.mean(alts_z) - np.mean(alts_o):+.1f} cm)")

    print("=" * 98)

    # Gravação no CSV
    with open(ARQUIVO_COMPARACAO_CSV, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Membro", "Passo", "Tempo_s", "Duracao_s",
            "Comp_ZUPT_m", "Comp_OSC_m", "Delta_Comp_cm",
            "Alt_ZUPT_cm", "Alt_OSC_cm", "Delta_Alt_cm"
        ])
        for linha in linhas_csv:
            escritor.writerow(linha)

    print(f"\nResultados comparativos detalhados exportados para '{ARQUIVO_COMPARACAO_CSV}'.")


# ----------------- VISUALIZAÇÃO GRÁFICA COMPARATIVA -----------------
def plotar_comparacao_grafica(comparacoes):
    lados_ativos = list(comparacoes.keys())
    if not lados_ativos:
        return

    n_cols = len(lados_ativos)
    fig, axes = plt.subplots(4, n_cols, figsize=(8 * n_cols, 12), sharex="col")
    if n_cols == 1:
        axes = np.expand_dims(axes, axis=1)

    for col, lado in enumerate(lados_ativos):
        comp = comparacoes[lado]
        t = comp["t_rel"]
        res_z = comp["zupt"]
        res_o = comp["osc"]
        passadas = res_z["passadas"]
        metricas_o = res_o["metricas"]

        # Painel 1: Aceleração linear com Heel-Strikes marcados
        ax1 = axes[0, col]
        ax1.plot(t, res_z["acc_mag"], color="#1f77b4", label="Aceleração Linear (m/s²)", alpha=0.8)
        t_impactos = [p["t_impacto"] for p in passadas]
        amps = [res_z["acc_mag"][int(tp / 0.01)] for tp in t_impactos]
        ax1.plot(t_impactos, amps, "rv", markersize=7, label="Heel-Strike (Impacto)")
        ax1.set_title(f"1. Aceleração IMU e Heel-Strikes — {comp['nome']}", fontweight="bold", fontsize=10)
        ax1.set_ylabel("Aceleração (m/s²)")
        ax1.legend(loc="upper right", fontsize=8)
        ax1.grid(True, alpha=0.3)

        # Painel 2: Velocidade integrada com ZUPT
        ax2 = axes[1, col]
        ax2.plot(t, res_z["vel_total"], color="teal", label="Velocidade ZUPT (m/s)", linewidth=1.3)
        ax2.axhline(0, color="gray", linestyle=":")
        ax2.set_title(f"2. Velocidade do Pé Corrigida com ZUPT", fontweight="bold", fontsize=10)
        ax2.set_ylabel("Velocidade (m/s)")
        ax2.legend(loc="upper right", fontsize=8)
        ax2.grid(True, alpha=0.3)

        # Painel 3: Comparação de Altura/Elevação Vertical (ZUPT vs OSC)
        ax3 = axes[2, col]
        ax3.plot(t, res_z["pos_y_total"] * 100.0, color="darkorange", label="Altura ZUPT (cm)", linewidth=1.5)
        ax3.plot(t, res_o["elev_osc"] * 100.0, color="purple", linestyle="--", label="Altura OSC (cm)", alpha=0.85)
        ax3.set_title(f"3. Elevação Vertical do Pé: ZUPT vs OSC (Clearance)", fontweight="bold", fontsize=10)
        ax3.set_ylabel("Altura (cm)")
        ax3.legend(loc="upper right", fontsize=8)
        ax3.grid(True, alpha=0.3)

        # Painel 4: Gráfico de Barras Comparativo Passo a Passo
        ax4 = axes[3, col]
        idx_p = np.arange(1, len(passadas) + 1)
        largura = 0.35
        comps_z = [p["comp_zupt_m"] for p in passadas]
        comps_o = [m["comp_osc_m"] for m in metricas_o]

        ax4.bar(idx_p - largura/2, comps_z, width=largura, color="steelblue", label="Comp. ZUPT (m)")
        ax4.bar(idx_p + largura/2, comps_o, width=largura, color="mediumpurple", label="Comp. OSC (m)")
        ax4.set_xlabel("Número da Passada")
        ax4.set_ylabel("Comprimento (m)")
        ax4.set_title(f"4. Comparativo de Comprimento por Passada", fontweight="bold", fontsize=10)
        ax4.set_xticks(idx_p)
        ax4.legend(loc="upper right", fontsize=8)
        ax4.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def main():
    parser = argparse.ArgumentParser(
        description="Captura e Comparação Simultânea de Marcha: ZUPT (IMU SolarXR) vs OSC (Posição)"
    )
    parser.add_argument("--prep", type=int, default=5, help="Tempo de preparação em segundos (padrão: 5)")
    parser.add_argument("--duracao", type=int, default=0, help="Tempo de caminhada em segundos (0 = até Ctrl+C)")
    parser.add_argument("--nome", type=str, default=None, help="Nome da sessão de captura")
    args = parser.parse_args()

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_sessao = args.nome if args.nome else f"sessao_zupt_osc_{timestamp_str}"
    caminho_solarxr = os.path.join("slimevr_client_raw", "captures", pasta_sessao)

    print("=" * 70)
    print("   SISTEMA DE COMPARAÇÃO SIMULTÂNEA: ZUPT (IMU) vs POSIÇÃO (OSC)")
    print("=" * 70)
    print(f"Porta 21110 (SolarXR) : Acelerações lineares e Heel-Strikes das IMUs")
    print(f"Porta 9000 (OSC UDP)  : Posições tridimensionais dos pés ({ID_OSC_ESQ} e {ID_OSC_DIR})")
    print(f"Tempo de Preparação   : {args.prep}s")
    dur_str = f"{args.duracao}s" if args.duracao > 0 else "Livre (encerra no Ctrl+C)"
    print(f"Duração da Gravação   : {dur_str}")
    print("=" * 70)

    # 1. Inicia o servidor OSC local na porta 9000
    disp = Dispatcher()
    disp.set_default_handler(osc_handler)
    try:
        servidor_osc = ThreadingOSCUDPServer((IP_OSC, PORTA_OSC), disp)
        thread_osc = threading.Thread(target=rodar_servidor_osc, args=(servidor_osc,), daemon=True)
        thread_osc.start()
        print("[OK] Servidor OSC escutando na porta 9000.")
    except Exception as e:
        print(f"[Aviso] Erro ao abrir servidor OSC na porta 9000: {e}")
        servidor_osc = None

    # 2. Contagem regressiva de preparação
    if args.prep > 0:
        print("\n[ PREPARAÇÃO ] Posicione-se no local inicial da caminhada:")
        for t_rest in range(args.prep, 0, -1):
            print(f"\r  >>> Começando em {t_rest} segundo(s)...   ", end="", flush=True)
            time.sleep(1.0)

    print("\n\n" + "#" * 70)
    print("  >>> GRAVANDO! PODE CAMINHAR NORMALMENTE... <<<")
    if args.duracao == 0:
        print("  >>> Pressione 'Ctrl + C' quando terminar a caminhada. <<<")
    print("#" * 70 + "\n")

    # 3. Dispara o cliente SolarXR Node.js em subprocesso
    cmd_node = [
        "node",
        os.path.join("slimevr_client_raw", "src", "cli.mjs"),
        "capture",
        "--seconds", str(args.duracao),
        "--out", caminho_solarxr
    ]

    try:
        subprocess.run(cmd_node)
    except KeyboardInterrupt:
        print("\n\nCaptura interrompida pelo usuário (Ctrl + C). Finalizando buffers...")
        time.sleep(0.5)

    if servidor_osc:
        servidor_osc.shutdown()

    # 4. Processa os dados
    arquivo_samples = os.path.join(caminho_solarxr, "samples.csv")
    if not os.path.exists(arquivo_samples):
        print(f"[Erro] Arquivo de dados SolarXR não encontrado em '{arquivo_samples}'.")
        return

    print("\nSincronizando amostras da IMU com os pacotes OSC...")
    with lock_osc:
        copia_osc = {
            ID_OSC_ESQ: dict(dados_osc[ID_OSC_ESQ]),
            ID_OSC_DIR: dict(dados_osc[ID_OSC_DIR])
        }

    dados_imu = carregar_dados_solarxr(arquivo_samples)

    if not dados_imu:
        print("[Erro] Nenhuma IMU de pé válida encontrada nos dados do SolarXR.")
        return

    n_osc_esq = len(copia_osc[ID_OSC_ESQ]["t_unix"])
    n_osc_dir = len(copia_osc[ID_OSC_DIR]["t_unix"])
    print(f"Amostras coletadas -> SolarXR: {list(dados_imu.keys())} | OSC: Esq={n_osc_esq}, Dir={n_osc_dir}")

    if n_osc_esq < 15 and n_osc_dir < 15:
        print("[Aviso] Poucos pacotes OSC recebidos na porta 9000. Verifique se o envio de posição OSC está ligado no SlimeVR.")

    comparacoes = comparar_membros(dados_imu, copia_osc)
    if not comparacoes:
        print("[Aviso] Não foi possível encontrar passadas suficientes para comparar.")
        return

    exibir_relatorio_e_salvar(comparacoes)
    plotar_comparacao_grafica(comparacoes)


if __name__ == "__main__":
    main()

