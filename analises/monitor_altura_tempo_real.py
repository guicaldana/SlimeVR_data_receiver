import sys
import os
import time
import math
import subprocess
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
IP_OSC = "127.0.0.1"

ID_OSC_ESQ = "2"
ID_OSC_DIR = "3"
TAXA_DISPLAY_HZ = 10.0  # Atualizações por segundo no terminal
# ==============================================================

# Estados em tempo real
lock = threading.Lock()
tempo_inicial = None
rodando = True

# Estado OSC
osc_y = {"ESQ": None, "DIR": None}
solo_base = {"ESQ": None, "DIR": None}
diff_base = 0.0
calibrado = False
osc_altura_cm = {"ESQ": 0.0, "DIR": 0.0}
osc_diff_cm = 0.0

# Histórico OSC para gráficos
historico_osc = {
    "ESQ": {"t": [], "altura_cm": []},
    "DIR": {"t": [], "altura_cm": []}
}

# Estado IMU SolarXR
imu_vy = {"ESQ": 0.0, "DIR": 0.0}
imu_pos_y = {"ESQ": 0.0, "DIR": 0.0}
imu_altura_cm = {"ESQ": 0.0, "DIR": 0.0}
imu_pitch = {"ESQ": 0.0, "DIR": 0.0}
imu_em_voo = {"ESQ": False, "DIR": False}
ultimo_t_imu = {"ESQ": None, "DIR": None}
repouso_contagem = {"ESQ": 0, "DIR": 0}
pico_voo_atual = {"ESQ": 0.0, "DIR": 0.0}

# Histórico IMU para gráficos
historico_imu = {
    "ESQ": {"t": [], "altura_cm": [], "pitch": []},
    "DIR": {"t": [], "altura_cm": [], "pitch": []}
}

# Picos registrados passada a passada
passadas_registradas = {
    "ESQ": [],  # [{ t, alt_imu, alt_osc }]
    "DIR": []
}


# ----------------- RECEPÇÃO OSC (PORTA 9000) -----------------
def osc_handler(address, *args):
    global tempo_inicial, calibrado, diff_base
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5 or partes[4] != "position":
        return

    tracker_id = partes[3]
    lado = "ESQ" if tracker_id == ID_OSC_ESQ else ("DIR" if tracker_id == ID_OSC_DIR else None)
    if not lado or len(args) < 2:
        return

    y = float(args[1])
    agora = time.time()

    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora
        t_rel = agora - tempo_inicial

        osc_y[lado] = y

        # Calibração estática do solo nos primeiros 2 segundos
        if t_rel < 2.0:
            if solo_base[lado] is None:
                solo_base[lado] = y
            else:
                solo_base[lado] = 0.85 * solo_base[lado] + 0.15 * y

            if solo_base["ESQ"] is not None and solo_base["DIR"] is not None:
                diff_base = solo_base["ESQ"] - solo_base["DIR"]
            alt_osc = 0.0
        else:
            calibrado = True
            # Altura bruta em relação ao solo inicial de cada pé
            ref_solo = solo_base[lado] if solo_base[lado] is not None else y
            alt_osc = max(0.0, (y - ref_solo) * 100.0)

            # Diferença direta entre os dois pés (cancela inclinações do tronco)
            if osc_y["ESQ"] is not None and osc_y["DIR"] is not None:
                global osc_diff_cm
                diff_atual = (osc_y["ESQ"] - osc_y["DIR"]) - diff_base
                osc_diff_cm = diff_atual * 100.0

        osc_altura_cm[lado] = alt_osc

        historico_osc[lado]["t"].append(t_rel)
        historico_osc[lado]["altura_cm"].append(alt_osc)


# ----------------- THREAD DO STREAM SOLARXR (NODE.JS) -----------------
def thread_leitor_solarxr(proc):
    global tempo_inicial
    for linha in proc.stdout:
        if not rodando:
            break
        linha_str = linha.strip()
        if not linha_str.startswith("D "):
            continue

        partes = linha_str.split()
        if len(partes) < 7:
            continue

        lado = partes[1]  # 'ESQ' ou 'DIR'
        if lado not in ("ESQ", "DIR"):
            continue

        try:
            ax = float(partes[2])
            ay = float(partes[3])
            az = float(partes[4])
            pitch = float(partes[5])
            t_ms = float(partes[6]) / 1000.0
        except ValueError:
            continue

        agora = time.time()
        with lock:
            if tempo_inicial is None:
                tempo_inicial = agora
            t_rel = agora - tempo_inicial

            t_ant = ultimo_t_imu[lado]
            dt = (t_ms - t_ant) if t_ant is not None else 0.01
            ultimo_t_imu[lado] = t_ms
            if dt <= 0 or dt > 0.1:
                dt = 0.01

            acc_mag = math.sqrt(ax**2 + ay**2 + az**2)
            imu_pitch[lado] = pitch

            # Detecção de Fases: Stance (Apoio no solo) vs Swing (Balanço no ar)
            if acc_mag < 1.6:
                repouso_contagem[lado] += 1
            else:
                repouso_contagem[lado] = 0

            # 1. Pouso suave ou repouso consolidado no chão (ZUPT ativo na IMU)
            if repouso_contagem[lado] >= 4:
                if imu_em_voo[lado] and pico_voo_atual[lado] > 2.0:
                    passadas_registradas[lado].append({
                        "tempo": t_rel,
                        "alt_imu": pico_voo_atual[lado],
                        "alt_osc": osc_altura_cm[lado]
                    })
                    pico_voo_atual[lado] = 0.0

                imu_em_voo[lado] = False
                imu_vy[lado] = 0.0
                imu_pos_y[lado] = 0.0
                alt_cm_imu = 0.0

            # 2. Início do movimento / elevação do pé
            elif acc_mag > 2.2:
                imu_em_voo[lado] = True

            # 3. Durante o voo / movimento, integra aceleração vertical
            if imu_em_voo[lado]:
                imu_vy[lado] = (imu_vy[lado] + ay * dt) * 0.99
                imu_pos_y[lado] = max(0.0, imu_pos_y[lado] + imu_vy[lado] * dt)
                alt_cm_imu = imu_pos_y[lado] * 100.0
                pico_voo_atual[lado] = max(pico_voo_atual[lado], alt_cm_imu)

                # Impacto mecânico brusco de calcanhar (Heel-Strike)
                if acc_mag > 7.5 and pico_voo_atual[lado] > 2.5:
                    passadas_registradas[lado].append({
                        "tempo": t_rel,
                        "alt_imu": pico_voo_atual[lado],
                        "alt_osc": osc_altura_cm[lado]
                    })
                    pico_voo_atual[lado] = 0.0
                    imu_em_voo[lado] = False
                    imu_vy[lado] = 0.0
                    imu_pos_y[lado] = 0.0
                    alt_cm_imu = 0.0
            else:
                alt_cm_imu = 0.0

            imu_altura_cm[lado] = alt_cm_imu
            historico_imu[lado]["t"].append(t_rel)
            historico_imu[lado]["altura_cm"].append(alt_cm_imu)
            historico_imu[lado]["pitch"].append(pitch)


# ----------------- PAINEL VISUAL AO VIVO NO TERMINAL -----------------
def renderizar_painel_ao_vivo():
    intervalo = 1.0 / TAXA_DISPLAY_HZ
    while rodando:
        with lock:
            t0 = tempo_inicial
            is_calib = calibrado
            h_imu_e = imu_altura_cm["ESQ"]
            h_osc_e = osc_altura_cm["ESQ"]
            h_imu_d = imu_altura_cm["DIR"]
            h_osc_d = osc_altura_cm["DIR"]
            diff_rel = osc_diff_cm
            passos_e = len(passadas_registradas["ESQ"])
            passos_d = len(passadas_registradas["DIR"])

        agora = time.time()
        t_decorrido = (agora - t0) if t0 else 0.0

        if not is_calib:
            linha = f"\r⏳ Calibrando ponto zero do chão ({t_decorrido:3.1f}s / 2.0s)... Mantenha os pés no chão!   "
        else:
            # Barras gráficas (0 a 20 cm)
            def criar_barra(valor_cm, tam=7):
                cheios = int(min(max(valor_cm / 20.0, 0.0), 1.0) * tam)
                return "#" * cheios + "-" * (tam - cheios)

            bar_osc_e = criar_barra(h_osc_e)
            bar_osc_d = criar_barra(h_osc_d)

            linha = (
                f"\r⏱ {t_decorrido:4.1f}s | "
                f"ESQ: [OSC {h_osc_e:4.1f}cm ({bar_osc_e}) | IMU {h_imu_e:4.1f}cm] "
                f"|| DIR: [OSC {h_osc_d:4.1f}cm ({bar_osc_d}) | IMU {h_imu_d:4.1f}cm] "
                f"| DIF: {diff_rel:+4.1f}cm "
            )
        sys.stdout.write(linha)
        sys.stdout.flush()
        time.sleep(intervalo)


# ----------------- GRÁFICOS AO FINAL DA SESSÃO -----------------
def plotar_resultados():
    print("\n\n" + "=" * 75)
    print("      RESUMO DA COMPARAÇÃO DE ALTURA (TEMPO REAL)")
    print("=" * 75)

    for lado, nome in [("ESQ", "Pé Esquerdo"), ("DIR", "Pé Direito")]:
        passos = passadas_registradas[lado]
        if passos:
            alts_i = [p["alt_imu"] for p in passos]
            alts_o = [p["alt_osc"] for p in passos]
            print(f"\n▶ {nome} ({len(passos)} passadas registradas):")
            print(f"  • Média IMU (ZUPT): {np.mean(alts_i):.1f} cm (± {np.std(alts_i):.1f} cm)")
            print(f"  • Média OSC:        {np.mean(alts_o):.1f} cm (± {np.std(alts_o):.1f} cm)")
            print(f"  • Discrepância:     {np.mean(alts_i) - np.mean(alts_o):+.1f} cm")
        else:
            print(f"\n▶ {nome}: Nenhuma passada completa detectada.")

    print("=" * 75)

    # Gráficos
    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex="col")

    for col, (lado, nome) in enumerate([("ESQ", "Pé Esquerdo"), ("DIR", "Pé Direito")]):
        # Painel Superior: Curvas de Altura Contínuas Sobrepostas
        ax_alt = axes[0, col]
        t_i = np.array(historico_imu[lado]["t"])
        h_i = np.array(historico_imu[lado]["altura_cm"])
        t_o = np.array(historico_osc[lado]["t"])
        h_o = np.array(historico_osc[lado]["altura_cm"])

        if len(t_i) > 0:
            ax_alt.plot(t_i, h_i, color="#1f77b4", label="Altura IMU ZUPT (cm)", linewidth=1.5)
        if len(t_o) > 0:
            ax_alt.plot(t_o, h_o, color="darkorange", linestyle="--", label="Altura OSC (cm)", alpha=0.85)

        ax_alt.set_title(f"Altura em Tempo Real — {nome}", fontweight="bold")
        ax_alt.set_ylabel("Elevação (cm)")
        ax_alt.legend(loc="upper right", fontsize=8)
        ax_alt.grid(True, alpha=0.3)

        # Painel Inferior: Inclinação Angular da IMU (Pitch)
        ax_pitch = axes[1, col]
        p_i = np.array(historico_imu[lado]["pitch"])
        if len(t_i) > 0 and len(p_i) > 0:
            ax_pitch.plot(t_i, p_i, color="purple", label="Pitch da IMU (graus)", linewidth=1.2)
        ax_pitch.set_title(f"Inclinação Angular (Flexão) — {nome}", fontweight="bold")
        ax_pitch.set_xlabel("Tempo (s)")
        ax_pitch.set_ylabel("Pitch (°)")
        ax_pitch.legend(loc="upper right", fontsize=8)
        ax_pitch.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def main():
    global rodando
    print("=" * 75)
    print("   MONITOR DE ALTURA EM TEMPO REAL: IMU (SOLARXR) vs OSC")
    print("=" * 75)
    print("• Exibe simultaneamente a elevação de cada pé pelas DUAS fontes.")
    print("• Pressione 'Ctrl + C' quando quiser encerrar e ver os gráficos.")
    print("=" * 75)

    # 1. Inicia o servidor OSC
    disp = Dispatcher()
    disp.set_default_handler(osc_handler)
    try:
        server_osc = ThreadingOSCUDPServer((IP_OSC, PORTA_OSC), disp)
        t_osc = threading.Thread(target=server_osc.serve_forever, daemon=True)
        t_osc.start()
        print("[OK] Servidor OSC ativo na porta 9000.")
    except Exception as e:
        print(f"[Erro] Falha ao abrir OSC na porta 9000: {e}")
        return

    # 2. Inicia o streaming do SolarXR em subprocesso Node.js
    script_node = os.path.join("slimevr_client_raw", "src", "stream.mjs")
    try:
        proc_node = subprocess.Popen(
            ["node", script_node],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        print("[OK] Conexão SolarXR ativa na porta 21110.")
    except Exception as e:
        print(f"[Erro] Falha ao iniciar stream.mjs: {e}")
        server_osc.shutdown()
        return

    # Thread que consome o stdout do Node.js
    t_solar = threading.Thread(target=thread_leitor_solarxr, args=(proc_node,), daemon=True)
    t_solar.start()

    # Thread que renderiza o painel visual no terminal
    t_disp = threading.Thread(target=renderizar_painel_ao_vivo, daemon=True)
    t_disp.start()

    print("\n>>> MONITORANDO AO VIVO! Caminhe normalmente... <<<\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        rodando = False
        print("\n\nEncerrando monitoramento em tempo real...")
        proc_node.terminate()
        server_osc.shutdown()
        time.sleep(0.5)
        plotar_resultados()


if __name__ == "__main__":
    main()

