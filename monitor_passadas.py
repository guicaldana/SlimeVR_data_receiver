import sys
import os
import csv
import time
import math
import subprocess
import threading
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
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

# Identificadores no OSC
ID_OSC_ESQ = "2"
ID_OSC_DIR = "3"

# Parâmetros biomecânicos de detecção
LIMIAR_SWING_ACC = 2.2      # m/s² para início do balanço
LIMIAR_HEEL_STRIKE = 6.0    # m/s² para detecção de impacto
DURACAO_MIN_SWING = 0.18    # segundos mínimos de balanço para evitar ruído
DISTANCIA_MIN_PASSO = 0.10  # metros mínimos entre os pés para considerar passada
TAXA_DISPLAY_HZ = 10.0      # Atualizações por segundo no terminal
ARQUIVO_SAIDA_CSV = os.path.join("dados", "resultado_passadas_sincronizadas.csv")
# ==============================================================

lock = threading.Lock()
tempo_inicial = None
rodando = True

# Estado OSC
pos_osc = {
    "ESQ": {"x": None, "y": None, "z": None},
    "DIR": {"x": None, "y": None, "z": None}
}
distancia_atual_m = 0.0

# Histórico contínuo para gráficos
historico_distancia = {"t": [], "dist_m": []}
historico_imu = {
    "ESQ": {"t": [], "acc": []},
    "DIR": {"t": [], "acc": []}
}

# Estado da IMU (máquina de estados do passo)
estado_gait = {
    "ESQ": "STANCE",
    "DIR": "STANCE"
}
t_inicio_swing = {"ESQ": None, "DIR": None}
pico_acc_swing = {"ESQ": 0.0, "DIR": 0.0}
repouso_contagem = {"ESQ": 0, "DIR": 0}

# Registro das passadas detectadas
passadas = []  # [{ tempo, lado, comprimento_m, comprimento_cm, impacto_ms2, duracao_swing_s }]
ultimo_passo_str = "Nenhum ainda"


# ----------------- RECEPÇÃO OSC (PORTA 9000) -----------------
def osc_handler(address, *args):
    global tempo_inicial, distancia_atual_m
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5 or partes[4] != "position":
        return

    tracker_id = partes[3]
    lado = "ESQ" if tracker_id == ID_OSC_ESQ else ("DIR" if tracker_id == ID_OSC_DIR else None)
    if not lado or len(args) < 3:
        return

    x, y, z = float(args[0]), float(args[1]), float(args[2])
    agora = time.time()

    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora
        t_rel = agora - tempo_inicial

        pos_osc[lado]["x"] = x
        pos_osc[lado]["y"] = y
        pos_osc[lado]["z"] = z

        pe_e = pos_osc["ESQ"]
        pe_d = pos_osc["DIR"]

        # Calcula a distância euclidiana horizontal (plano X-Z) entre os pés
        if pe_e["x"] is not None and pe_d["x"] is not None:
            dx = pe_e["x"] - pe_d["x"]
            dz = pe_e["z"] - pe_d["z"]
            dist = math.sqrt(dx**2 + dz**2)
            distancia_atual_m = dist

            historico_distancia["t"].append(t_rel)
            historico_distancia["dist_m"].append(dist)


# ----------------- THREAD DO STREAM SOLARXR (NODE.JS) -----------------
def thread_leitor_solarxr(proc):
    global tempo_inicial, ultimo_passo_str
    for linha in proc.stdout:
        if not rodando:
            break
        linha_str = linha.strip()
        if not linha_str.startswith("D "):
            continue

        partes = linha_str.split()
        if len(partes) < 7:
            continue

        lado = partes[1]
        if lado not in ("ESQ", "DIR"):
            continue

        try:
            ax = float(partes[2])
            ay = float(partes[3])
            az = float(partes[4])
        except ValueError:
            continue

        agora = time.time()
        with lock:
            if tempo_inicial is None:
                tempo_inicial = agora
            t_rel = agora - tempo_inicial

            acc_mag = math.sqrt(ax**2 + ay**2 + az**2)
            historico_imu[lado]["t"].append(t_rel)
            historico_imu[lado]["acc"].append(acc_mag)

            # --- MÁQUINA DE ESTADOS DO GAIT (ZUPT / HEEL-STRIKE) ---
            if estado_gait[lado] == "STANCE":
                # Detecta início do balanço (Swing)
                if acc_mag > LIMIAR_SWING_ACC:
                    estado_gait[lado] = "SWING"
                    t_inicio_swing[lado] = t_rel
                    pico_acc_swing[lado] = acc_mag

            elif estado_gait[lado] == "SWING":
                pico_acc_swing[lado] = max(pico_acc_swing[lado], acc_mag)
                duracao_swing = t_rel - (t_inicio_swing[lado] or t_rel)

                # HEEL-STRIKE: impacto mecânico após balanço fisiológico
                if acc_mag >= LIMIAR_HEEL_STRIKE and duracao_swing >= DURACAO_MIN_SWING:
                    # Captura a distância horizontal dos pés no momento exato do impacto!
                    dist_momento = distancia_atual_m

                    if dist_momento >= DISTANCIA_MIN_PASSO:
                        passadas.append({
                            "tempo": t_rel,
                            "lado": lado,
                            "comprimento_m": dist_momento,
                            "comprimento_cm": dist_momento * 100.0,
                            "impacto_ms2": acc_mag,
                            "duracao_swing_s": duracao_swing
                        })
                        ultimo_passo_str = f"Pé {lado}: {dist_momento*100.0:.1f} cm (Impacto: {acc_mag:.1f} m/s²)"

                    estado_gait[lado] = "STANCE"
                    t_inicio_swing[lado] = None
                    pico_acc_swing[lado] = 0.0

                # Pouso suave (sem pico brusco): se o pé entrar em repouso após voo
                elif acc_mag < 1.6 and duracao_swing >= DURACAO_MIN_SWING:
                    dist_momento = distancia_atual_m
                    if dist_momento >= DISTANCIA_MIN_PASSO:
                        passadas.append({
                            "tempo": t_rel,
                            "lado": lado,
                            "comprimento_m": dist_momento,
                            "comprimento_cm": dist_momento * 100.0,
                            "impacto_ms2": pico_acc_swing[lado],
                            "duracao_swing_s": duracao_swing
                        })
                        ultimo_passo_str = f"Pé {lado}: {dist_momento*100.0:.1f} cm (Pouso Suave)"

                    estado_gait[lado] = "STANCE"
                    t_inicio_swing[lado] = None
                    pico_acc_swing[lado] = 0.0


# ----------------- PAINEL AO VIVO NO TERMINAL -----------------
def renderizar_painel():
    intervalo = 1.0 / TAXA_DISPLAY_HZ
    while rodando:
        with lock:
            t0 = tempo_inicial
            dist = distancia_atual_m
            total_passos = len(passadas)
            ult = ultimo_passo_str

        agora = time.time()
        t_decorrido = (agora - t0) if t0 else 0.0

        # Barra gráfica da distância entre os pés (0 a 1.0 metro)
        cheios = int(min(max(dist / 0.85, 0.0), 1.0) * 10)
        barra_dist = "#" * cheios + "-" * (10 - cheios)

        passos_e = sum(1 for p in passadas if p["lado"] == "ESQ")
        passos_d = sum(1 for p in passadas if p["lado"] == "DIR")
        media_cm = np.mean([p["comprimento_cm"] for p in passadas]) if passadas else 0.0

        linha = (
            f"\r⏱ {t_decorrido:4.1f}s | "
            f"Dist. Pés: {dist:4.2f}m [{barra_dist}] | "
            f"Passos: {total_passos:2d} (E={passos_e}, D={passos_d}) | "
            f"Média: {media_cm:4.1f}cm | "
            f"Último: {ult:<36s}"
        )
        sys.stdout.write(linha)
        sys.stdout.flush()
        time.sleep(intervalo)


# ----------------- RELATÓRIO E GRÁFICOS AO FINAL -----------------
def salvar_e_plotar_resultados():
    print("\n\n" + "=" * 80)
    print("      RELATÓRIO DE COMPRIMENTO DE PASSADA: HEEL-STRIKE (IMU) + OSC")
    print("=" * 80)

    if not passadas:
        print("Nenhuma passada detectada durante a sessão.")
        print("=" * 80)
        return

    # Salva CSV
    os.makedirs("dados", exist_ok=True)
    with open(ARQUIVO_SAIDA_CSV, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(
            f,
            fieldnames=["passo_num", "tempo_s", "lado", "comprimento_m", "comprimento_cm", "impacto_ms2", "duracao_swing_s"]
        )
        escritor.writeheader()
        for idx, p in enumerate(passadas, 1):
            escritor.writerow({
                "passo_num": idx,
                "tempo_s": f"{p['tempo']:.3f}",
                "lado": p["lado"],
                "comprimento_m": f"{p['comprimento_m']:.4f}",
                "comprimento_cm": f"{p['comprimento_cm']:.1f}",
                "impacto_ms2": f"{p['impacto_ms2']:.2f}",
                "duracao_swing_s": f"{p['duracao_swing_s']:.3f}"
            })
    print(f"[OK] Dados salvos em: {ARQUIVO_SAIDA_CSV}")

    # Estatísticas
    passos_e = [p["comprimento_cm"] for p in passadas if p["lado"] == "ESQ"]
    passos_d = [p["comprimento_cm"] for p in passadas if p["lado"] == "DIR"]
    todos_passos = [p["comprimento_cm"] for p in passadas]

    print(f"\n▶ Total de Passadas: {len(passadas)}")
    print(f"  • Média Geral:      {np.mean(todos_passos):.1f} cm (± {np.std(todos_passos):.1f} cm)")
    if passos_e:
        print(f"  • Pé Esquerdo ({len(passos_e)}): {np.mean(passos_e):.1f} cm (± {np.std(passos_e):.1f} cm)")
    if passos_d:
        print(f"  • Pé Direito  ({len(passos_d)}): {np.mean(passos_d):.1f} cm (± {np.std(passos_d):.1f} cm)")

    if passos_e and passos_d:
        me, md = np.mean(passos_e), np.mean(passos_d)
        simetria = (1.0 - abs(me - md) / ((me + md) / 2.0)) * 100.0
        print(f"  • Índice de Simetria (E vs D): {simetria:.1f}%")

    print("\n" + "-" * 80)
    print(f"{'#':<4} {'Tempo (s)':<10} {'Lado':<6} {'Comprimento (cm)':<18} {'Impacto (m/s²)':<16} {'Balanço (s)':<12}")
    print("-" * 80)
    for idx, p in enumerate(passadas, 1):
        print(f"{idx:<4} {p['tempo']:<10.2f} {p['lado']:<6} {p['comprimento_cm']:<18.1f} {p['impacto_ms2']:<16.1f} {p['duracao_swing_s']:<12.3f}")
    print("=" * 80)

    # Gráficos
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)

    # 1. Distância entre os pés com os eventos de Heel-Strike
    t_dist = np.array(historico_distancia["t"])
    d_dist = np.array(historico_distancia["dist_m"]) * 100.0
    if len(t_dist) > 0:
        ax1.plot(t_dist, d_dist, color="#1f77b4", linewidth=1.5, label="Distância Horizontal entre os Pés (cm)")

    # Marca os Heel-Strikes
    for p in passadas:
        cor = "green" if p["lado"] == "DIR" else "darkorange"
        ax1.scatter(
            p["tempo"], p["comprimento_cm"],
            color=cor, s=90, zorder=5, edgecolors="black",
            label=f"Heel-Strike {p['lado']}" if f"Heel-Strike {p['lado']}" not in ax1.get_legend_handles_labels()[1] else ""
        )
        ax1.annotate(
            f"{p['comprimento_cm']:.1f}cm",
            (p["tempo"], p["comprimento_cm"] + 2.0),
            fontsize=8, fontweight="bold", ha="center"
        )

    ax1.set_title("Comprimento da Passada Sincronizado (Heel-Strike via IMU + Distância OSC)", fontweight="bold")
    ax1.set_ylabel("Distância / Passo (cm)")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper right")

    # 2. Sinais de Aceleração das IMUs (Impactos de Calcanhar)
    t_e = np.array(historico_imu["ESQ"]["t"])
    a_e = np.array(historico_imu["ESQ"]["acc"])
    t_d = np.array(historico_imu["DIR"]["t"])
    a_d = np.array(historico_imu["DIR"]["acc"])

    if len(t_e) > 0:
        ax2.plot(t_e, a_e, color="darkorange", alpha=0.7, label="Aceleração IMU Pé Esquerdo (m/s²)")
    if len(t_d) > 0:
        ax2.plot(t_d, a_d, color="green", alpha=0.7, label="Aceleração IMU Pé Direito (m/s²)")

    ax2.axhline(LIMIAR_HEEL_STRIKE, color="red", linestyle="--", alpha=0.5, label=f"Limiar de Impacto ({LIMIAR_HEEL_STRIKE} m/s²)")
    ax2.set_title("Detecção Inercial dos Impactos de Calcanhar (Heel-Strike)", fontweight="bold")
    ax2.set_xlabel("Tempo (s)")
    ax2.set_ylabel("Magnitude Acc (m/s²)")
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right")

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def main():
    global rodando
    print("=" * 80)
    print("   ESTIMADOR DE COMPRIMENTO DE PASSADA: HEEL-STRIKE (IMU) + OSC")
    print("=" * 80)
    print("• Sincroniza o instante exato do toque no chão (IMU ZUPT) com a distância dos pés.")
    print("• Imune a inclinações de tronco, postura ou altura da cabeça.")
    print("• Pressione 'Ctrl + C' para encerrar a sessão e abrir o relatório/gráficos.")
    print("=" * 80)

    # 1. Inicia o servidor OSC na porta 9000
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

    # 2. Inicia o streaming SolarXR (Node.js)
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

    t_solar = threading.Thread(target=thread_leitor_solarxr, args=(proc_node,), daemon=True)
    t_solar.start()

    t_disp = threading.Thread(target=renderizar_painel, daemon=True)
    t_disp.start()

    print("\n>>> PRONTO! Caminhe normalmente... <<<\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        rodando = False
        print("\n\nEncerrando sessão e compilando dados...")
        proc_node.terminate()
        server_osc.shutdown()
        time.sleep(0.5)
        salvar_e_plotar_resultados()


if __name__ == "__main__":
    main()

