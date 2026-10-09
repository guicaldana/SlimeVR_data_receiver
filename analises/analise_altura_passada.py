import csv
import sys
import os
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
IP_OSC = "127.0.0.1"

ID_OSC_ESQ = "2"
ID_OSC_DIR = "3"

ARQUIVO_SAIDA_ALTURA = "resultado_altura_passada.csv"
INTERVALO_FEEDBACK = 0.10  # 10 vezes por segundo no terminal
# ==============================================================

dados_osc = {
    ID_OSC_ESQ: {"t": [], "y": []},
    ID_OSC_DIR: {"t": [], "y": []}
}
lock_osc = threading.Lock()
y_atual = {ID_OSC_ESQ: None, ID_OSC_DIR: None}
y_solo_ref = {ID_OSC_ESQ: None, ID_OSC_DIR: None}
ultimo_feedback = 0
tempo_inicial = None


def osc_handler(address, *args):
    global tempo_inicial
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
            if tempo_inicial is None:
                tempo_inicial = agora
            t_rel = agora - tempo_inicial
            dados_osc[tracker_id]["t"].append(t_rel)
            dados_osc[tracker_id]["y"].append(y)
            y_atual[tracker_id] = y

            # Calibração contínua do piso
            if y_solo_ref[tracker_id] is None or y < y_solo_ref[tracker_id]:
                y_solo_ref[tracker_id] = y


def mostrar_feedback_ao_vivo():
    global ultimo_feedback
    agora = time.time()
    if agora - ultimo_feedback < INTERVALO_FEEDBACK:
        return
    ultimo_feedback = agora

    with lock_osc:
        y_e = y_atual[ID_OSC_ESQ]
        y_d = y_atual[ID_OSC_DIR]
        solo_e = y_solo_ref[ID_OSC_ESQ]
        solo_d = y_solo_ref[ID_OSC_DIR]
        t0 = tempo_inicial

    if y_e is None or y_d is None or solo_e is None or solo_d is None:
        print("\r  [Aguardando pacotes OSC dos pés (trackers 2 e 3)...] ", end="", flush=True)
        return

    tempo = agora - t0 if t0 else 0
    # Elevação atual em cm
    elev_e_cm = max(0.0, (y_e - solo_e) * 100.0)
    elev_d_cm = max(0.0, (y_d - solo_d) * 100.0)

    # Barras gráficas (escala até 30 cm)
    bar_e = "#" * int(min(elev_e_cm / 30.0, 1.0) * 10)
    bar_d = "#" * int(min(elev_d_cm / 30.0, 1.0) * 10)

    print(
        f"\r⏱ {tempo:4.1f}s | "
        f"Pé ESQ: {elev_e_cm:4.1f} cm [{bar_e:<10}] | "
        f"Pé DIR: {elev_d_cm:4.1f} cm [{bar_d:<10}]  (Ctrl+C p/ gráfico)",
        end="", flush=True
    )


# ----------------- PROCESSAMENTO DE ALTURA -----------------
def processar_alturas_detalhadas(t_arr, y_arr, rotulo_membro):
    """Calcula a trajetória vertical, linha de base e picos de elevação (clearance)."""
    dt = np.mean(np.diff(t_arr)) if len(t_arr) > 1 else 0.01
    taxa = 1.0 / dt if dt > 0 else 100.0

    # Filtro passa-baixa Butterworth para eliminar ruídos de alta frequência
    b, a = butter(2, 5.0 / (taxa / 2.0), btype="low")
    y_filt = filtfilt(b, a, y_arr)

    # Linha do solo estimada (percentil 5 da curva de altura)
    chao_y = np.percentile(y_filt, 5)

    # Elevação instantânea em centímetros
    elevacao_cm = np.maximum(0.0, (y_filt - chao_y) * 100.0)

    # Identificação de cada pico de passada (altura máxima)
    distancia_min_pontos = int(0.40 * taxa)  # Mínimo 400ms entre passos
    picos_idx, props = find_peaks(
        elevacao_cm,
        height=3.0,          # Mínimo 3 cm para considerar uma elevação de passo
        distance=distancia_min_pontos,
        prominence=2.0       # Destaque de 2 cm em relação aos vales
    )

    passadas = []
    for i, idx in enumerate(picos_idx):
        passadas.append({
            "passo": i + 1,
            "tempo_s": float(t_arr[idx]),
            "altura_cm": float(elevacao_cm[idx]),
            "idx": idx
        })

    return {
        "membro": rotulo_membro,
        "t": t_arr,
        "elevacao_cm": elevacao_cm,
        "picos_idx": picos_idx,
        "passadas": passadas,
        "chao_y": chao_y
    }


def exibir_relatorio_altura(res_esq, res_dir):
    print("\n\n" + "=" * 80)
    print("      RELATÓRIO DEDICADO: ALTURA DA PASSADA E FOOT CLEARANCE")
    print("=" * 80)

    alturas_e = [p["altura_cm"] for p in res_esq["passadas"]]
    alturas_d = [p["altura_cm"] for p in res_dir["passadas"]]

    media_e = np.mean(alturas_e) if alturas_e else 0.0
    desvio_e = np.std(alturas_e) if alturas_e else 0.0
    media_d = np.mean(alturas_d) if alturas_d else 0.0
    desvio_d = np.std(alturas_d) if alturas_d else 0.0

    print(f"\n▶ PÉ ESQUERDO ({len(alturas_e)} passadas detectadas):")
    print(f"  • Altura Média:   {media_e:5.1f} cm (± {desvio_e:.1f} cm)")
    if alturas_e:
        print(f"  • Mínima/Máxima:  {np.min(alturas_e):.1f} cm / {np.max(alturas_e):.1f} cm")

    print(f"\n▶ PÉ DIREITO ({len(alturas_d)} passadas detectadas):")
    print(f"  • Altura Média:   {media_d:5.1f} cm (± {desvio_d:.1f} cm)")
    if alturas_d:
        print(f"  • Mínima/Máxima:  {np.min(alturas_d):.1f} cm / {np.max(alturas_d):.1f} cm")

    if media_e > 0 and media_d > 0:
        simetria = (min(media_e, media_d) / max(media_e, media_d)) * 100.0
        diferenca_cm = abs(media_e - media_d)
        print(f"\n▶ SIMETRIA VERTICAL ENTRE OS DOIS PÉS:")
        print(f"  • Índice de Simetria: {simetria:5.1f}%")
        print(f"  • Diferença Média:    {diferenca_cm:5.1f} cm")
        if simetria >= 90.0:
            print("  • Diagnóstico:        Passada Altamente Simétrica [OK]")
        elif simetria >= 80.0:
            print("  • Diagnóstico:        Leve Assimetria [Aceitável]")
        else:
            print("  • Diagnóstico:        Assimetria Relevante [Atenção]")

    print("\n--- Tabela Passo a Passo das Alturas Máximas ---")
    print(f"{'#':<4} | {'Pé Esquerdo: Tempo / Altura':<30} | {'Pé Direito: Tempo / Altura':<30}")
    print("-" * 70)

    max_p = max(len(res_esq["passadas"]), len(res_dir["passadas"]))
    linhas_csv = []

    for i in range(max_p):
        p_e = res_esq["passadas"][i] if i < len(res_esq["passadas"]) else None
        p_d = res_dir["passadas"][i] if i < len(res_dir["passadas"]) else None

        str_e = f"{p_e['tempo_s']:5.2f}s  ->  {p_e['altura_cm']:5.1f} cm" if p_e else "---"
        str_d = f"{p_d['tempo_s']:5.2f}s  ->  {p_d['altura_cm']:5.1f} cm" if p_d else "---"
        print(f"{i+1:<4} | {str_e:<30} | {str_d:<30}")

        linhas_csv.append([
            i + 1,
            f"{p_e['tempo_s']:.2f}" if p_e else "",
            f"{p_e['altura_cm']:.1f}" if p_e else "",
            f"{p_d['tempo_s']:.2f}" if p_d else "",
            f"{p_d['altura_cm']:.1f}" if p_d else "",
        ])

    print("=" * 80)

    # Salva CSV
    with open(ARQUIVO_SAIDA_ALTURA, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["Passo", "Tempo_Esq_s", "Altura_Esq_cm", "Tempo_Dir_s", "Altura_Dir_cm"])
        for linha in linhas_csv:
            escritor.writerow(linha)
    print(f"Resultados detalhados de altura salvos em '{ARQUIVO_SAIDA_ALTURA}'.")


def plotar_graficos_altura(res_esq, res_dir):
    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=False)

    t_e, elev_e = res_esq["t"], res_esq["elevacao_cm"]
    t_d, elev_d = res_dir["t"], res_dir["elevacao_cm"]

    # Painel 1: Trajetórias Verticais Contínuas Sincronizadas
    ax1 = axes[0]
    ax1.plot(t_e, elev_e, color="royalblue", label="Elevação Pé ESQ (cm)", linewidth=1.5)
    ax1.plot(t_d, elev_d, color="darkorange", label="Elevação Pé DIR (cm)", linewidth=1.5)

    # Marcadores de ápice
    for p in res_esq["passadas"]:
        ax1.plot(p["tempo_s"], p["altura_cm"], "bo", markersize=6)
    for p in res_dir["passadas"]:
        ax1.plot(p["tempo_s"], p["altura_cm"], "o", color="darkorange", markersize=6)

    ax1.set_ylabel("Elevação (cm)")
    ax1.set_title("1. Trajetória Contínua de Elevação Vertical dos Pés (Foot Clearance)", fontweight="bold")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Painel 2: Curvas Separadas com Linha Média de Altura
    ax2 = axes[1]
    alturas_e = [p["altura_cm"] for p in res_esq["passadas"]]
    alturas_d = [p["altura_cm"] for p in res_dir["passadas"]]

    if alturas_e:
        med_e = np.mean(alturas_e)
        ax2.axhline(med_e, color="royalblue", linestyle="--", label=f"Média ESQ: {med_e:.1f} cm")
    if alturas_d:
        med_d = np.mean(alturas_d)
        ax2.axhline(med_d, color="darkorange", linestyle="--", label=f"Média DIR: {med_d:.1f} cm")

    ax2.plot(t_e, elev_e, color="royalblue", alpha=0.7)
    ax2.plot(t_d, elev_d, color="darkorange", alpha=0.7)
    ax2.set_ylabel("Elevação (cm)")
    ax2.set_title("2. Altura Média e Estabilidade de Levantamento de Cada Pé", fontweight="bold")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.3)

    # Painel 3: Gráfico de Barras Comparativo Passo a Passo
    ax3 = axes[2]
    max_p = max(len(alturas_e), len(alturas_d))
    indices = np.arange(1, max_p + 1)
    largura = 0.38

    vals_e = alturas_e + [0.0] * (max_p - len(alturas_e))
    vals_d = alturas_d + [0.0] * (max_p - len(alturas_d))

    ax3.bar(indices - largura/2, vals_e, width=largura, color="royalblue", label="Altura Pé ESQ (cm)")
    ax3.bar(indices + largura/2, vals_d, width=largura, color="darkorange", label="Altura Pé DIR (cm)")

    ax3.set_xlabel("Número da Passada")
    ax3.set_ylabel("Altura Máxima (cm)")
    ax3.set_title("3. Comparação de Altura Passo a Passo (Pé Esquerdo vs Pé Direito)", fontweight="bold")
    ax3.set_xticks(indices)
    ax3.legend(loc="upper right", fontsize=8)
    ax3.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def main():
    parser = argparse.ArgumentParser(description="Analisador Dedicado de Altura da Passada e Foot Clearance (SlimeVR)")
    parser.add_argument("--captura", "-c", type=str, default=None, help="Caminho de uma pasta com samples.csv do SolarXR para reanálise offline")
    args = parser.parse_args()

    print("=" * 70)
    print("      ANALISADOR DEDICADO DE ALTURA DA PASSADA (FOOT CLEARANCE)")
    print("=" * 70)
    print("Focado exclusivamente na elevação vertical de cada pé em centímetros.")
    print("Pressione 'Ctrl + C' para finalizar a caminhada e ver os gráficos.")
    print("=" * 70)

    disp = Dispatcher()
    disp.set_default_handler(osc_handler)
    try:
        server = ThreadingOSCUDPServer((IP_OSC, PORTA_OSC), disp)
        t_srv = threading.Thread(target=server.serve_forever, daemon=True)
        t_srv.start()
    except Exception as e:
        print(f"[Erro] Falha ao abrir porta OSC 9000: {e}")
        return

    try:
        while True:
            mostrar_feedback_ao_vivo()
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n\nCaptura finalizada! Processando trajetória de elevação dos pés...")

        with lock_osc:
            t_e = np.array(dados_osc[ID_OSC_ESQ]["t"])
            y_e = np.array(dados_osc[ID_OSC_ESQ]["y"])
            t_d = np.array(dados_osc[ID_OSC_DIR]["t"])
            y_d = np.array(dados_osc[ID_OSC_DIR]["y"])

        if len(t_e) < 20 or len(t_d) < 20:
            print("[Aviso] Poucos dados coletados. Caminhe por mais tempo para gerar o relatório.")
            return

        # Sincroniza em base de tempo comum
        t_ini = max(t_e[0], t_d[0])
        t_fim = min(t_e[-1], t_d[-1])
        dt = 0.01
        t_comum = np.arange(t_ini, t_fim, dt)

        y_e_i = np.interp(t_comum, t_e, y_e)
        y_d_i = np.interp(t_comum, t_d, y_d)
        t_rel = t_comum - t_ini

        res_esq = processar_alturas_detalhadas(t_rel, y_e_i, "Pé Esquerdo")
        res_dir = processar_alturas_detalhadas(t_rel, y_d_i, "Pé Direito")

        exibir_relatorio_altura(res_esq, res_dir)
        plotar_graficos_altura(res_esq, res_dir)


if __name__ == "__main__":
    main()

