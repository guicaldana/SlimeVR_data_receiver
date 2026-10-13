import sys
import os
import csv
import time
import math
import threading
import numpy as np
import matplotlib.pyplot as plt
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Garante suporte UTF-8 no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
PORTA_OSC = 9000
PORTA_VMC = 39539
IP = "127.0.0.1"

# Identificadores de Trackers no OSC
ID_QUADRIL = "1"     # Hip / Waist
ID_TORNOZELO_ESQ = "2"
ID_TORNOZELO_DIR = "3"
ID_PEITO = "6"       # Chest

TAXA_DISPLAY_HZ = 10.0
TEMPO_CALIB_INICIAL = 2.0  # Primeiros 2 segundos para tara inicial
ARQUIVO_SAIDA_CSV = os.path.join("dados", "teste_altura_referencias.csv")
# ==============================================================

lock = threading.Lock()
tempo_inicial = None
rodando = True
calibrado = False

# Dados brutos instantâneos (Y)
y_atual = {
    "ESQ": None,
    "DIR": None,
    "HIP": None,
    "CHEST": None
}
rot_pitch = {
    "HIP": 0.0,
    "CHEST": 0.0
}

# Referências de Tara / Calibração inicial (pés no chão)
y_base = {
    "ESQ": None,
    "DIR": None,
    "HIP": None,
    "CHEST": None
}
diff_pes_base = 0.0
dist_hip_esq_base = 0.0
dist_hip_dir_base = 0.0

# Histórico para geração de gráficos
historico = {
    "t": [],
    "esq_estatico": [],
    "esq_dif": [],
    "esq_hip": [],
    "dir_estatico": [],
    "dir_dif": [],
    "dir_hip": [],
    "y_esq_raw": [],
    "y_dir_raw": [],
    "y_hip_raw": [],
    "pitch_hip": [],
    "pitch_chest": []
}


# ----------------- RECEPÇÃO DE DADOS OSC (PORTA 9000) -----------------
def calcular_pitch(rotacao):
    if len(rotacao) == 4:
        x, y, z, w = rotacao
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))
        return math.degrees(math.asin(sinp))
    elif len(rotacao) == 3:
        p = float(rotacao[0])
        return p - 360 if p > 180 else p
    return 0.0


def osc_handler(address, *args):
    global tempo_inicial, calibrado
    global diff_pes_base, dist_hip_esq_base, dist_hip_dir_base

    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5:
        return

    tracker_id = partes[3]
    tipo = partes[4]
    agora = time.time()

    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora
        t_rel = agora - tempo_inicial

        # 1. Posições Y
        if tipo == "position" and len(args) >= 2:
            y = float(args[1])
            if tracker_id == ID_TORNOZELO_ESQ:
                y_atual["ESQ"] = y
            elif tracker_id == ID_TORNOZELO_DIR:
                y_atual["DIR"] = y
            elif tracker_id == ID_QUADRIL:
                y_atual["HIP"] = y
            elif tracker_id == ID_PEITO:
                y_atual["CHEST"] = y

        # 2. Rotações (Pitch do Tronco e Quadril)
        elif tipo == "rotation" and len(args) >= 3:
            p = calcular_pitch(args)
            if tracker_id == ID_QUADRIL:
                rot_pitch["HIP"] = p
            elif tracker_id == ID_PEITO:
                rot_pitch["CHEST"] = p

        # 3. Processamento e Calibração Inicial (Tara de 2.0s com pés no chão)
        if t_rel < TEMPO_CALIB_INICIAL:
            for k in ("ESQ", "DIR", "HIP", "CHEST"):
                if y_atual[k] is not None:
                    if y_base[k] is None:
                        y_base[k] = y_atual[k]
                    else:
                        y_base[k] = 0.85 * y_base[k] + 0.15 * y_atual[k]

            if y_base["ESQ"] is not None and y_base["DIR"] is not None:
                diff_pes_base = y_base["ESQ"] - y_base["DIR"]
            if y_base["HIP"] is not None and y_base["ESQ"] is not None:
                dist_hip_esq_base = y_base["HIP"] - y_base["ESQ"]
            if y_base["HIP"] is not None and y_base["DIR"] is not None:
                dist_hip_dir_base = y_base["HIP"] - y_base["DIR"]
        else:
            calibrado = True


# ----------------- RECEPÇÃO VMC OPCIONAL (PORTA 39539) -----------------
def vmc_handler(address, *args):
    """Captura posições/rotações caso SlimeVR envie via protocolo VMC."""
    if address != "/VMC/Ext/Bone/Pos" or len(args) < 8:
        return
    bone = str(args[0]).lower()
    py = float(args[2])
    with lock:
        if bone == "hips" and y_atual["HIP"] is None:
            y_atual["HIP"] = py


# ----------------- PAINEL VISUAL AO VIVO NO TERMINAL -----------------
def renderizar_painel():
    intervalo = 1.0 / TAXA_DISPLAY_HZ
    while rodando:
        with lock:
            t0 = tempo_inicial
            is_calib = calibrado
            ye, yd = y_atual["ESQ"], y_atual["DIR"]
            yhip = y_atual["HIP"]
            phip, ptrc = rot_pitch["HIP"], rot_pitch["CHEST"]
            ybe, ybd = y_base["ESQ"], y_base["DIR"]
            yb_hip = y_base["HIP"]
            diff_base = diff_pes_base
            d_he_base = dist_hip_esq_base
            d_hd_base = dist_hip_dir_base

        agora = time.time()
        t_rel = (agora - t0) if t0 else 0.0

        if not is_calib:
            linha = f"\r⏳ Calibrando ponto zero ({t_rel:3.1f}s / {TEMPO_CALIB_INICIAL:.1f}s)... Mantenha os pés no chão!   "
            sys.stdout.write(linha)
            sys.stdout.flush()
            time.sleep(intervalo)
            continue

        if ye is None or yd is None:
            sys.stdout.write("\rAguardando trackers dos pés (2 e 3)...                           ")
            sys.stdout.flush()
            time.sleep(intervalo)
            continue

        # --- CÁLCULO DAS 3 REFERÊNCIAS ---
        # 1. Chão Estático: (Y - Y_solo_inicial)
        h_est_e = (ye - (ybe if ybe is not None else ye)) * 100.0
        h_est_d = (yd - (ybd if ybd is not None else yd)) * 100.0

        # 2. Diferencial Pé-com-Pé: (Y_e - Y_d - diff_base)
        h_dif_e = ((ye - yd) - diff_base) * 100.0
        h_dif_d = -h_dif_e

        # 3. Referência pelo Quadril: encurtamento da distância vertical Pé -> Quadril
        if yhip is not None and d_he_base > 0:
            dist_he_atual = yhip - ye
            h_hip_e = (d_he_base - dist_he_atual) * 100.0
        else:
            h_hip_e = 0.0

        if yhip is not None and d_hd_base > 0:
            dist_hd_atual = yhip - yd
            h_hip_d = (d_hd_base - dist_hd_atual) * 100.0
        else:
            h_hip_d = 0.0

        # Salva histórico
        with lock:
            historico["t"].append(t_rel)
            historico["esq_estatico"].append(h_est_e)
            historico["esq_dif"].append(h_dif_e)
            historico["esq_hip"].append(h_hip_e)
            historico["dir_estatico"].append(h_est_d)
            historico["dir_dif"].append(h_dif_d)
            historico["dir_hip"].append(h_hip_d)
            historico["y_esq_raw"].append(ye)
            historico["y_dir_raw"].append(yd)
            historico["y_hip_raw"].append(yhip if yhip is not None else 0.0)
            historico["pitch_hip"].append(phip)
            historico["pitch_chest"].append(ptrc)

        # Barra gráfica (0 a 15 cm)
        def barra(val):
            cheios = int(min(max(val / 15.0, 0.0), 1.0) * 6)
            return "#" * cheios + "-" * (6 - cheios)

        bar_e = barra(max(h_est_e, h_hip_e, h_dif_e))
        bar_d = barra(max(h_est_d, h_hip_d, h_dif_d))

        hip_str = f"Hip(1): {phip:+4.1f}°" if yhip is not None else "Hip(1): N/A"
        linha = (
            f"\r⏱ {t_rel:4.1f}s | "
            f"ESQ: [Chão:{h_est_e:+4.1f}|Dif:{h_dif_e:+4.1f}|Hip:{h_hip_e:+4.1f}]cm [{bar_e}] "
            f"|| DIR: [Chão:{h_est_d:+4.1f}|Dif:{h_dif_d:+4.1f}|Hip:{h_hip_d:+4.1f}]cm [{bar_d}] "
            f"| {hip_str} | Peito: {ptrc:+4.1f}° "
        )
        sys.stdout.write(linha)
        sys.stdout.flush()
        time.sleep(intervalo)


# ----------------- RELATÓRIO E GRÁFICOS AO FINAL -----------------
def plotar_comparacao():
    print("\n\n" + "=" * 80)
    print("      RELATÓRIO COMPARATIVO DE REFERÊNCIAS DE ALTURA DOS PÉS")
    print("=" * 80)

    t = np.array(historico["t"])
    if len(t) < 10:
        print("Dados insuficientes para gerar gráficos.")
        return

    # Salva CSV
    os.makedirs("dados", exist_ok=True)
    with open(ARQUIVO_SAIDA_CSV, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Tempo_s",
            "ESQ_ChaoEstatico_cm", "ESQ_Diferencial_cm", "ESQ_ReferenciaQuadril_cm",
            "DIR_ChaoEstatico_cm", "DIR_Diferencial_cm", "DIR_ReferenciaQuadril_cm",
            "Pitch_Quadril_deg", "Pitch_Peito_deg"
        ])
        for i in range(len(t)):
            escritor.writerow([
                f"{t[i]:.3f}",
                f"{historico['esq_estatico'][i]:.2f}", f"{historico['esq_dif'][i]:.2f}", f"{historico['esq_hip'][i]:.2f}",
                f"{historico['dir_estatico'][i]:.2f}", f"{historico['dir_dif'][i]:.2f}", f"{historico['dir_hip'][i]:.2f}",
                f"{historico['pitch_hip'][i]:.2f}", f"{historico['pitch_chest'][i]:.2f}"
            ])
    print(f"[OK] Dados gravados em: {ARQUIVO_SAIDA_CSV}")

    # Gráficos
    fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=True)

    # Painel 1: Pé Esquerdo nas 3 Referências
    axes[0].plot(t, historico["esq_estatico"], label="1. Chão Estático Inicial (cm)", color="gray", linestyle="--")
    axes[0].plot(t, historico["esq_dif"], label="2. Diferencial Pé-com-Pé (cm)", color="darkorange", linewidth=1.5)
    axes[0].plot(t, historico["esq_hip"], label="3. Referência pelo Quadril (cm)", color="dodgerblue", linewidth=1.5)
    axes[0].axhline(0, color="black", alpha=0.3)
    axes[0].set_ylabel("Altura (cm)")
    axes[0].set_title("Pé Esquerdo: Comparação das 3 Referências de Altura", fontweight="bold")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].grid(True, alpha=0.3)

    # Painel 2: Pé Direito nas 3 Referências
    axes[1].plot(t, historico["dir_estatico"], label="1. Chão Estático Inicial (cm)", color="gray", linestyle="--")
    axes[1].plot(t, historico["dir_dif"], label="2. Diferencial Pé-com-Pé (cm)", color="darkorange", linewidth=1.5)
    axes[1].plot(t, historico["dir_hip"], label="3. Referência pelo Quadril (cm)", color="dodgerblue", linewidth=1.5)
    axes[1].axhline(0, color="black", alpha=0.3)
    axes[1].set_ylabel("Altura (cm)")
    axes[1].set_title("Pé Direito: Comparação das 3 Referências de Altura", fontweight="bold")
    axes[1].legend(loc="upper right", fontsize=8)
    axes[1].grid(True, alpha=0.3)

    # Painel 3: Posições Verticais Brutas Y no Mundo Virtual
    axes[2].plot(t, historico["y_esq_raw"], label="Y Pé Esquerdo (m)", color="orange")
    axes[2].plot(t, historico["y_dir_raw"], label="Y Pé Direito (m)", color="green")
    if np.any(np.array(historico["y_hip_raw"]) != 0):
        axes[2].plot(t, historico["y_hip_raw"], label="Y Quadril / Hip (m)", color="purple")
    axes[2].set_ylabel("Y Bruto (m)")
    axes[2].set_title("Coordenadas Verticais Brutas (OSC VRChat)", fontweight="bold")
    axes[2].legend(loc="upper right", fontsize=8)
    axes[2].grid(True, alpha=0.3)

    # Painel 4: Estabilidade Angular (Quadril vs Peito)
    axes[3].plot(t, historico["pitch_chest"], label="Inclinação do Peito (Pitch °)", color="crimson")
    axes[3].plot(t, historico["pitch_hip"], label="Inclinação do Quadril (Pitch °)", color="purple", linewidth=1.5)
    axes[3].axhline(0, color="black", alpha=0.3)
    axes[3].set_ylabel("Ângulo (°)")
    axes[3].set_xlabel("Tempo (s)")
    axes[3].set_title("Comparação de Estabilidade: Inclinação do Peito vs Quadril", fontweight="bold")
    axes[3].legend(loc="upper right", fontsize=8)
    axes[3].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def rodar_servidor(porta, handler):
    disp = Dispatcher()
    disp.set_default_handler(handler)
    try:
        server = ThreadingOSCUDPServer((IP, porta), disp)
        server.serve_forever()
    except Exception as e:
        print(f"[Erro] Falha ao abrir porta {porta}: {e}")


def main():
    global rodando
    print("=" * 80)
    print("   AVALIADOR DE ALTURA DO PÉ E ESTUDO DE REFERÊNCIA NO QUADRIL (HIP)")
    print("=" * 80)
    print("• Compara em tempo real 3 abordagens de medição de altura:")
    print("  1. Chão Estático Inicial (Tara de 2.0s)")
    print("  2. Diferencial Pé-com-Pé (Pé na caixa vs Pé no chão)")
    print("  3. Referência no Quadril (Tracker 1 / Hip)")
    print("• Pressione 'Ctrl + C' quando quiser encerrar e abrir os gráficos comparativos.")
    print("=" * 80)

    t_osc = threading.Thread(target=rodar_servidor, args=(PORTA_OSC, osc_handler), daemon=True)
    t_vmc = threading.Thread(target=rodar_servidor, args=(PORTA_VMC, vmc_handler), daemon=True)
    t_osc.start()
    t_vmc.start()

    t_disp = threading.Thread(target=renderizar_painel, daemon=True)
    t_disp.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        rodando = False
        print("\n\nEncerrando teste e gerando gráficos comparativos...")
        time.sleep(0.5)
        plotar_comparacao()


if __name__ == "__main__":
    main()

