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

ID_OSC_ESQ = "2"
ID_OSC_DIR = "3"

ARQUIVO_SAIDA_CSV = "comparacao_altura_detalhada.csv"
INTERVALO_FEEDBACK = 0.12  # Terminal atualiza ~8 vezes por segundo
# ==============================================================

dados_osc_live = {
    ID_OSC_ESQ: {"t_unix": [], "y": []},
    ID_OSC_DIR: {"t_unix": [], "y": []}
}
lock_osc = threading.Lock()
y_atual_live = {ID_OSC_ESQ: None, ID_OSC_DIR: None}
ultimo_feedback = 0


# ----------------- RECEPÇÃO DE DADOS OSC (PORTA 9000) -----------------
def osc_handler(address, *args):
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5 or partes[4] != "position":
        return

    tracker_id = partes[3]
    if tracker_id in (ID_OSC_ESQ, ID_OSC_DIR) and len(args) >= 2:
        y = float(args[1])
        agora = time.time()
        with lock_osc:
            dados_osc_live[tracker_id]["t_unix"].append(agora)
            dados_osc_live[tracker_id]["y"].append(y)
            y_atual_live[tracker_id] = y


def rodar_servidor_osc(server):
    try:
        server.serve_forever()
    except Exception:
        pass


# ----------------- CARREGAMENTO SOLARXR (SAMPLES.CSV) -----------------
def carregar_solarxr_altura(caminho_csv):
    """Carrega acelerações verticais e quaternions das IMUs dos pés."""
    if not os.path.exists(caminho_csv):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_csv}")

    dados_imu = {}
    with open(caminho_csv, mode="r", encoding="utf-8") as f:
        leitor = csv.DictReader(f)
        for linha in leitor:
            nome = linha.get("body_part_name") or ""
            if "FOOT" not in nome and "LOWER_LEG" not in nome:
                continue

            lado = "ESQ" if "LEFT" in nome else ("DIR" if "RIGHT" in nome else None)
            if not lado:
                continue

            tipo = "FOOT" if "FOOT" in nome else "LEG"
            chave = f"{lado}_{tipo}"

            if chave not in dados_imu:
                dados_imu[chave] = {
                    "lado": lado,
                    "t_unix": [],
                    "ay": [], "ax": [], "az": [],
                    "qx": [], "qy": [], "qz": [], "qw": []
                }

            try:
                t_unix = float(linha["received_unix_ms"]) / 1000.0
                ay = float(linha["linear_acc_y_ms2"])
                ax = float(linha["linear_acc_x_ms2"])
                az = float(linha["linear_acc_z_ms2"])
                qx = float(linha.get("rotation_x") or 0.0)
                qy = float(linha.get("rotation_y") or 0.0)
                qz = float(linha.get("rotation_z") or 0.0)
                qw = float(linha.get("rotation_w") or 1.0)
            except (ValueError, KeyError, TypeError):
                continue

            dados_imu[chave]["t_unix"].append(t_unix)
            dados_imu[chave]["ay"].append(ay)
            dados_imu[chave]["ax"].append(ax)
            dados_imu[chave]["az"].append(az)
            dados_imu[chave]["qx"].append(qx)
            dados_imu[chave]["qy"].append(qy)
            dados_imu[chave]["qz"].append(qz)
            dados_imu[chave]["qw"].append(qw)

    dados_finais = {}
    for lado in ("ESQ", "DIR"):
        cf = f"{lado}_FOOT"
        cl = f"{lado}_LEG"
        if cf in dados_imu and len(dados_imu[cf]["t_unix"]) > 30:
            dados_finais[lado] = dados_imu[cf]
        elif cl in dados_imu and len(dados_imu[cl]["t_unix"]) > 30:
            dados_finais[lado] = dados_imu[cl]

    return dados_finais


def quaternion_para_pitch(qx, qy, qz, qw):
    """Calcula a inclinação angular do pé (pitch) em graus."""
    sinp = 2.0 * (qw * qx - qy * qz)
    sinp = np.clip(sinp, -1.0, 1.0)
    return np.degrees(np.arcsin(sinp))


# ----------------- CÁLCULO DE ALTURA IMU (ZUPT REFINADO) -----------------
def processar_altura_imu_zupt(t, dt, ay, ax, az):
    """Calcula a trajetória vertical do pé integrando estritamente entre Toe-Off e Heel-Strike."""
    acc_mag = np.sqrt(ax**2 + ay**2 + az**2)

    # Detecção de impactos de pouso (Heel-Strikes)
    picos_impacto, _ = find_peaks(
        acc_mag,
        height=6.0,
        distance=int(0.40 / dt),
        prominence=3.0
    )

    if len(picos_impacto) < 2:
        return None

    passadas = []
    altura_continua_cm = np.zeros_like(t)
    vel_y_continua = np.zeros_like(t)

    for i in range(len(picos_impacto) - 1):
        idx_hs1 = picos_impacto[i]
        idx_hs2 = picos_impacto[i + 1]

        # Procuramos o momento exato em que o pé decola (Toe-Off) entre hs1 e hs2
        # No Toe-Off, a aceleração vertical começa a subir após a fase de repouso
        janela = slice(idx_hs1, idx_hs2)
        ay_janela = ay[janela]
        acc_janela = acc_mag[janela]

        # O repouso ocorre logo após o hs1. O Toe-Off é o início do aumento de aceleração
        idx_rel_subida = 0
        for k in range(int(0.15 / dt), len(ay_janela) - 5):
            if acc_janela[k] > 2.5 and ay_janela[k] > 0.8:
                idx_rel_subida = k
                break

        idx_toe_off = idx_hs1 + idx_rel_subida
        if idx_hs2 - idx_toe_off < 5:
            continue

        # Integração estrita na fase de voo (Toe-Off -> Heel-Strike)
        ay_voo = ay[idx_toe_off:idx_hs2]
        n_pts = len(ay_voo)

        # 1ª Integração: aceleração vertical -> velocidade vertical
        vy = np.cumsum(ay_voo) * dt

        # Correção ZUPT na velocidade: força vy = 0 no início (decolagem) e fim (pouso)
        vy_corr = vy - np.linspace(0, vy[-1], n_pts)

        # 2ª Integração: velocidade vertical corrigida -> elevação vertical
        y_voo = np.cumsum(vy_corr) * dt

        # Correção final: altura parte de 0 no solo e retorna a 0 no solo
        perfil_y = (y_voo - np.linspace(0, y_voo[-1], n_pts)) * 100.0  # cm
        altura_max = float(np.max(perfil_y))

        altura_continua_cm[idx_toe_off:idx_hs2] = np.maximum(0.0, perfil_y)
        vel_y_continua[idx_toe_off:idx_hs2] = vy_corr

        passadas.append({
            "passo": i + 1,
            "t_inicio": float(t[idx_toe_off]),
            "t_pico": float(t[idx_toe_off + np.argmax(perfil_y)]),
            "t_pouso": float(t[idx_hs2]),
            "altura_imu_cm": altura_max,
            "idx_toe_off": idx_toe_off,
            "idx_hs2": idx_hs2
        })

    return {
        "passadas": passadas,
        "altura_cm": altura_continua_cm,
        "vel_y": vel_y_continua,
        "acc_mag": acc_mag
    }


# ----------------- CÁLCULO DE ALTURA OSC (ESTABILIZADO) -----------------
def processar_altura_osc_estabilizada(t, dt, y_osc, passadas_imu):
    """Calcula a altura vertical do OSC calibrando o chão local a cada passada."""
    # Filtro passa-baixa no sinal de posição vertical
    b, a = butter(2, 6.0 / (50.0), btype="low")
    y_filt = filtfilt(b, a, y_osc)

    altura_osc_continua_cm = np.zeros_like(t)
    resultados = []

    for p in passadas_imu:
        i1 = p["idx_toe_off"]
        i2 = p["idx_hs2"]

        # Janela da passada estendida para encontrar a referência de solo antes da decolagem
        i_base_inicio = max(0, i1 - int(0.20 / dt))
        solo_local = np.min(y_filt[i_base_inicio:i2])

        # Elevação em cm em relação ao solo local daquela passada
        elev_passada = np.maximum(0.0, (y_filt[i1:i2] - solo_local) * 100.0)
        alt_max_osc = float(np.max(elev_passada))

        altura_osc_continua_cm[i1:i2] = elev_passada
        resultados.append({
            "altura_osc_cm": alt_max_osc
        })

    return {
        "altura_cm": altura_osc_continua_cm,
        "metricas": resultados
    }


# ----------------- COMPARAÇÃO INTEGRADA DE ALTURA -----------------
def comparar_alturas(dados_solarxr, dados_osc):
    t_min = max(
        max(dados_solarxr[l]["t_unix"][0] for l in dados_solarxr),
        max(dados_osc[id_o]["t_unix"][0] for id_o in (ID_OSC_ESQ, ID_OSC_DIR) if len(dados_osc[id_o]["t_unix"]) > 5)
    )
    t_max = min(
        min(dados_solarxr[l]["t_unix"][-1] for l in dados_solarxr),
        min(dados_osc[id_o]["t_unix"][-1] for id_o in (ID_OSC_ESQ, ID_OSC_DIR) if len(dados_osc[id_o]["t_unix"]) > 5)
    )

    if t_max - t_min < 2.0:
        raise ValueError("Tempo comum insuficiente para sincronização de altura.")

    dt = 0.01
    t_comum = np.arange(t_min, t_max, dt)
    t_rel = t_comum - t_min

    resultados = {}
    mapeamento = {"ESQ": (ID_OSC_ESQ, "Pé Esquerdo"), "DIR": (ID_OSC_DIR, "Pé Direito")}

    for lado, (id_osc, nome_membro) in mapeamento.items():
        if lado not in dados_solarxr or len(dados_osc[id_osc]["t_unix"]) < 20:
            continue

        imu = dados_solarxr[lado]
        t_imu = np.array(imu["t_unix"])
        ay_i = np.interp(t_comum, t_imu, imu["ay"])
        ax_i = np.interp(t_comum, t_imu, imu["ax"])
        az_i = np.interp(t_comum, t_imu, imu["az"])
        qx_i = np.interp(t_comum, t_imu, imu["qx"])
        qy_i = np.interp(t_comum, t_imu, imu["qy"])
        qz_i = np.interp(t_comum, t_imu, imu["qz"])
        qw_i = np.interp(t_comum, t_imu, imu["qw"])
        pitch = quaternion_para_pitch(qx_i, qy_i, qz_i, qw_i)

        osc = dados_osc[id_osc]
        t_osc = np.array(osc["t_unix"])
        y_osc_i = np.interp(t_comum, t_osc, osc["y"])

        res_imu = processar_altura_imu_zupt(t_rel, dt, ay_i, ax_i, az_i)
        if not res_imu or not res_imu["passadas"]:
            continue

        res_osc = processar_altura_osc_estabilizada(t_rel, dt, y_osc_i, res_imu["passadas"])

        resultados[lado] = {
            "nome": nome_membro,
            "t_rel": t_rel,
            "res_imu": res_imu,
            "res_osc": res_osc,
            "pitch": pitch
        }

    return resultados


# ----------------- RELATÓRIO E PLOTS -----------------
def exibir_relatorio_altura(resultados):
    print("\n" + "=" * 90)
    print("      COMPARAÇÃO DE ALTURA DA PASSADA: IMU (ZUPT REFINADO) vs OSC (ESTABILIZADO)")
    print("=" * 90)

    linhas_csv = []
    for lado, r in resultados.items():
        nome = r["nome"]
        passadas = r["res_imu"]["passadas"]
        metricas_osc = r["res_osc"]["metricas"]

        print(f"\n▶ {nome} ({len(passadas)} passadas sincronizadas):")
        print(f"{'#':<4} {'Tempo(s)':<9} {'Altura IMU(cm)':<18} {'Altura OSC(cm)':<18} {'Diferença Δ(cm)'}")
        print("-" * 75)

        alts_imu, alts_osc = [], []
        for i, (pi, po) in enumerate(zip(passadas, metricas_osc)):
            a_i = pi["altura_imu_cm"]
            a_o = po["altura_osc_cm"]
            delta = a_i - a_o
            alts_imu.append(a_i)
            alts_osc.append(a_o)

            print(f"{i+1:<4} {pi['t_inicio']:<9.2f} {a_i:<18.1f} {a_o:<18.1f} {delta:+14.1f} cm")
            linhas_csv.append([lado, i + 1, f"{pi['t_inicio']:.2f}", f"{a_i:.1f}", f"{a_o:.1f}", f"{delta:.1f}"])

        print("-" * 75)
        print(f"  MÉDIAS {nome}:")
        print(f"  • Altura Média IMU (ZUPT): {np.mean(alts_imu):.1f} cm (± {np.std(alts_imu):.1f} cm)")
        print(f"  • Altura Média OSC:        {np.mean(alts_osc):.1f} cm (± {np.std(alts_osc):.1f} cm)")
        print(f"  • Discrepância Média:      {np.mean(alts_imu) - np.mean(alts_osc):+.1f} cm")

    print("=" * 90)

    with open(ARQUIVO_SAIDA_CSV, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["Membro", "Passo", "Tempo_s", "Altura_IMU_cm", "Altura_OSC_cm", "Delta_cm"])
        for linha in linhas_csv:
            escritor.writerow(linha)
    print(f"\nResultados comparativos de altura exportados para '{ARQUIVO_SAIDA_CSV}'.")


def plotar_graficos_comparacao_altura(resultados):
    lados = list(resultados.keys())
    if not lados:
        return

    n_cols = len(lados)
    fig, axes = plt.subplots(4, n_cols, figsize=(8 * n_cols, 12), sharex="col")
    if n_cols == 1:
        axes = np.expand_dims(axes, axis=1)

    for col, lado in enumerate(lados):
        r = resultados[lado]
        t = r["t_rel"]
        res_i = r["res_imu"]
        res_o = r["res_osc"]
        passadas = res_i["passadas"]
        metricas_o = res_o["metricas"]

        # Painel 1: Trajetórias de Altura Sobrepostas (IMU vs OSC)
        ax1 = axes[0, col]
        ax1.plot(t, res_i["altura_cm"], color="#1f77b4", label="Altura IMU ZUPT (cm)", linewidth=1.6)
        ax1.plot(t, res_o["altura_cm"], color="darkorange", linestyle="--", label="Altura OSC Estabilizada (cm)", alpha=0.85)
        for p in passadas:
            ax1.plot(p["t_pico"], p["altura_imu_cm"], "bo", markersize=5)
        ax1.set_title(f"1. Trajetória de Elevação Vertical — {r['nome']}", fontweight="bold", fontsize=10)
        ax1.set_ylabel("Altura (cm)")
        ax1.legend(loc="upper right", fontsize=8)
        ax1.grid(True, alpha=0.3)

        # Painel 2: Velocidade Vertical Integrada (m/s)
        ax2 = axes[1, col]
        ax2.plot(t, res_i["vel_y"], color="teal", label="Velocidade Vertical Vy (m/s)", linewidth=1.2)
        ax2.axhline(0, color="gray", linestyle=":")
        ax2.set_title("2. Velocidade Vertical da Passada (Subida e Descida)", fontweight="bold", fontsize=10)
        ax2.set_ylabel("Velocidade (m/s)")
        ax2.legend(loc="upper right", fontsize=8)
        ax2.grid(True, alpha=0.3)

        # Painel 3: Inclinação Angular do Pé (Pitch)
        ax3 = axes[2, col]
        ax3.plot(t, r["pitch"], color="purple", label="Inclinação do Pé (Pitch °)", linewidth=1.2)
        ax3.set_title("3. Flexão / Rotação Angular do Calçado (Dorsiflexão)", fontweight="bold", fontsize=10)
        ax3.set_ylabel("Pitch (°)")
        ax3.legend(loc="upper right", fontsize=8)
        ax3.grid(True, alpha=0.3)

        # Painel 4: Gráfico de Barras Passo a Passo
        ax4 = axes[3, col]
        idx_p = np.arange(1, len(passadas) + 1)
        largura = 0.36
        alts_i = [p["altura_imu_cm"] for p in passadas]
        alts_o = [m["altura_osc_cm"] for m in metricas_o]

        ax4.bar(idx_p - largura/2, alts_i, width=largura, color="#1f77b4", label="IMU ZUPT (cm)")
        ax4.bar(idx_p + largura/2, alts_o, width=largura, color="darkorange", label="OSC (cm)")
        ax4.set_xlabel("Número da Passada")
        ax4.set_ylabel("Altura Máxima (cm)")
        ax4.set_title("4. Altura Máxima por Passada: IMU vs OSC", fontweight="bold", fontsize=10)
        ax4.set_xticks(idx_p)
        ax4.legend(loc="upper right", fontsize=8)
        ax4.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def main():
    parser = argparse.ArgumentParser(description="Comparador Dedicado de Altura da Passada: IMU SolarXR vs OSC")
    parser.add_argument("--captura", "-c", type=str, default=None, help="Pasta de sessão existente em slimevr_client_raw/captures/")
    parser.add_argument("--prep", type=int, default=5, help="Tempo de preparação ao vivo (segundos)")
    parser.add_argument("--duracao", type=int, default=0, help="Duração ao vivo (0 = até Ctrl+C)")
    args = parser.parse_args()

    # Se uma captura específica foi solicitada ou se queremos carregar a última
    if args.captura:
        caminho_solarxr = args.captura
        pasta_sessao = os.path.basename(caminho_solarxr)
    else:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        pasta_sessao = f"sessao_altura_{timestamp_str}"
        caminho_solarxr = os.path.join("slimevr_client_raw", "captures", pasta_sessao)

        print("=" * 70)
        print("   COMPARAÇÃO DE ALTURA DA PASSADA: IMU (SOLARXR) vs OSC (ESTABILIZADO)")
        print("=" * 70)
        print("Gravando simultaneamente da IMU do pé e do OSC...")
        print("Pressione 'Ctrl + C' quando terminar a caminhada.")
        print("=" * 70)

        disp = Dispatcher()
        disp.set_default_handler(osc_handler)
        try:
            servidor_osc = ThreadingOSCUDPServer((IP_OSC, PORTA_OSC), disp)
            thread_osc = threading.Thread(target=rodar_servidor_osc, args=(servidor_osc,), daemon=True)
            thread_osc.start()
        except Exception as e:
            print(f"[Erro] Falha ao abrir servidor OSC: {e}")
            servidor_osc = None

        if args.prep > 0:
            print("\n[ PREPARAÇÃO ] Posicione-se no local inicial:")
            for t_r in range(args.prep, 0, -1):
                print(f"\r  >>> Iniciando em {t_r} segundo(s)...   ", end="", flush=True)
                time.sleep(1.0)

        print("\n\n" + "#" * 70)
        print("  >>> PODE CAMINHAR! GRAVANDO DADOS DE ALTURA... <<<")
        print("  >>> Pressione 'Ctrl + C' para ver a comparação de altura. <<<")
        print("#" * 70 + "\n")

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
            print("\n\nCaptura encerrada pelo usuário (Ctrl + C). Sincronizando dados...")
            time.sleep(0.5)

        if servidor_osc:
            servidor_osc.shutdown()

    arquivo_samples = os.path.join(caminho_solarxr, "samples.csv")
    if not os.path.exists(arquivo_samples):
        print(f"[Erro] Arquivo samples.csv não encontrado em '{arquivo_samples}'.")
        return

    with lock_osc:
        copia_osc = {
            ID_OSC_ESQ: dict(dados_osc_live[ID_OSC_ESQ]),
            ID_OSC_DIR: dict(dados_osc_live[ID_OSC_DIR])
        }

    dados_imu = carregar_solarxr_altura(arquivo_samples)
    if not dados_imu:
        print("[Erro] Nenhum dado de IMU de pé encontrado.")
        return

    # Se rodou ao vivo e tem dados OSC
    if len(copia_osc[ID_OSC_ESQ]["t_unix"]) > 10:
        comparacoes = comparar_alturas(dados_imu, copia_osc)
        if comparacoes:
            exibir_relatorio_altura(comparacoes)
            plotar_graficos_comparacao_altura(comparacoes)
    else:
        print("\n[Aviso] Dados OSC insuficientes na memória nesta execução.")


if __name__ == "__main__":
    main()

