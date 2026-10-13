import sys
import os
import csv
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
PORTA_OSC = 9000      # Trackers VRChat OSC (Tornozelos 2/3, Quadril 1, Peito 6)
PORTA_VMC = 39539     # VMC Protocol (Cabeça/Neck)
IP = "127.0.0.1"

# Identificadores no OSC
ID_QUADRIL = "1"
ID_TORNOZELO_ESQ = "2"
ID_TORNOZELO_DIR = "3"
ID_PEITO = "6"
OSSO_CABECA = "neck"

ARQUIVO_SAIDA_CSV = os.path.join("dados", "todos_dados_brutos.csv")
TAXA_DISPLAY_HZ = 5.0
# ==============================================================

lock = threading.Lock()
tempo_inicial = None
rodando = True

# Contadores de pacotes
contagem_pacotes = {
    "OSC_ESQ": 0,
    "OSC_DIR": 0,
    "OSC_HIP": 0,
    "OSC_CHEST": 0,
    "VMC_HEAD": 0,
    "IMU_ESQ": 0,
    "IMU_DIR": 0
}

# Buffers de dados
dados_osc = {
    "ESQ": {"t": [], "x": [], "y": [], "z": []},
    "DIR": {"t": [], "x": [], "y": [], "z": []},
    "HIP": {"t": [], "x": [], "y": [], "z": [], "pitch": []},
    "CHEST": {"t": [], "x": [], "y": [], "z": [], "pitch": []}
}

dados_vmc = {
    "HEAD": {"t": [], "pitch": [], "yaw": [], "roll": []}
}

dados_imu = {
    "ESQ": {"t": [], "ax": [], "ay": [], "az": [], "mag": [], "pitch": []},
    "DIR": {"t": [], "ax": [], "ay": [], "az": [], "mag": [], "pitch": []}
}


# ----------------- AUXILIARES CINEMÁTICOS -----------------
def calcular_pitch_euler(rot):
    if len(rot) == 4:
        x, y, z, w = rot
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))
        return -math.degrees(math.asin(sinp))
    elif len(rot) == 3:
        p = float(rot[0])
        if p > 180: p -= 360
        return -p
    return 0.0


def converter_quaternion_cabeca(x, y, z, w):
    sinp = 2.0 * (w * x - y * z)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = -math.degrees(math.asin(sinp))  # Nuca invertida

    siny_cosp = 2.0 * (w * y - z * x)
    cosy_cosp = 1.0 - 2.0 * (x * x + y * y)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

    sinr_cosp = 2.0 * (w * z + x * y)
    cosr_cosp = 1.0 - 2.0 * (y * y + z * z)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))
    return pitch, yaw, roll


# ----------------- RECEPÇÃO OSC (PORTA 9000) -----------------
def osc_handler(address, *args):
    global tempo_inicial
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5:
        return

    tid = partes[3]
    tipo = partes[4]
    agora = time.time()

    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora
        t_rel = agora - tempo_inicial

        if tipo == "position" and len(args) >= 3:
            x, y, z = float(args[0]), float(args[1]), float(args[2])
            if tid == ID_TORNOZELO_ESQ:
                dados_osc["ESQ"]["t"].append(t_rel)
                dados_osc["ESQ"]["x"].append(x)
                dados_osc["ESQ"]["y"].append(y)
                dados_osc["ESQ"]["z"].append(z)
                contagem_pacotes["OSC_ESQ"] += 1
            elif tid == ID_TORNOZELO_DIR:
                dados_osc["DIR"]["t"].append(t_rel)
                dados_osc["DIR"]["x"].append(x)
                dados_osc["DIR"]["y"].append(y)
                dados_osc["DIR"]["z"].append(z)
                contagem_pacotes["OSC_DIR"] += 1
            elif tid == ID_QUADRIL:
                dados_osc["HIP"]["t"].append(t_rel)
                dados_osc["HIP"]["x"].append(x)
                dados_osc["HIP"]["y"].append(y)
                dados_osc["HIP"]["z"].append(z)
                contagem_pacotes["OSC_HIP"] += 1
            elif tid == ID_PEITO:
                dados_osc["CHEST"]["t"].append(t_rel)
                dados_osc["CHEST"]["x"].append(x)
                dados_osc["CHEST"]["y"].append(y)
                dados_osc["CHEST"]["z"].append(z)
                contagem_pacotes["OSC_CHEST"] += 1

        elif tipo == "rotation" and len(args) >= 3:
            p = calcular_pitch_euler(args)
            if tid == ID_QUADRIL:
                dados_osc["HIP"]["pitch"].append(p)
            elif tid == ID_PEITO:
                dados_osc["CHEST"]["pitch"].append(p)


# ----------------- RECEPÇÃO VMC (PORTA 39539) -----------------
def vmc_handler(address, *args):
    global tempo_inicial
    if address != "/VMC/Ext/Bone/Pos" or len(args) < 8:
        return
    bone = str(args[0]).lower()
    if bone not in ("neck", "head"):
        return

    agora = time.time()
    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora
        t_rel = agora - tempo_inicial

        qx, qy, qz, qw = args[4], args[5], args[6], args[7]
        pitch, yaw, roll = converter_quaternion_cabeca(qx, qy, qz, qw)
        dados_vmc["HEAD"]["t"].append(t_rel)
        dados_vmc["HEAD"]["pitch"].append(pitch)
        dados_vmc["HEAD"]["yaw"].append(yaw)
        dados_vmc["HEAD"]["roll"].append(roll)
        contagem_pacotes["VMC_HEAD"] += 1


# ----------------- RECEPÇÃO SOLARXR (NODE.JS BRIDGE) -----------------
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
        lado = partes[1]
        if lado not in ("ESQ", "DIR"):
            continue

        try:
            ax = float(partes[2])
            ay = float(partes[3])
            az = float(partes[4])
            pitch = float(partes[5])
        except ValueError:
            continue

        agora = time.time()
        with lock:
            if tempo_inicial is None:
                tempo_inicial = agora
            t_rel = agora - tempo_inicial

            mag = math.sqrt(ax**2 + ay**2 + az**2)
            dados_imu[lado]["t"].append(t_rel)
            dados_imu[lado]["ax"].append(ax)
            dados_imu[lado]["ay"].append(ay)
            dados_imu[lado]["az"].append(az)
            dados_imu[lado]["mag"].append(mag)
            dados_imu[lado]["pitch"].append(pitch)
            contagem_pacotes[f"IMU_{lado}"] += 1


# ----------------- PAINEL DO TERMINAL -----------------
def renderizar_terminal():
    intervalo = 1.0 / TAXA_DISPLAY_HZ
    while rodando:
        with lock:
            t0 = tempo_inicial
            cnt = dict(contagem_pacotes)
        agora = time.time()
        t_rel = (agora - t0) if t0 else 0.0

        linha = (
            f"\r⏱ {t_rel:4.1f}s | "
            f"OSC Pés: [E={cnt['OSC_ESQ']} D={cnt['OSC_DIR']}] | "
            f"Hip: {cnt['OSC_HIP']} | "
            f"Peito: {cnt['OSC_CHEST']} | "
            f"VMC Cabeça: {cnt['VMC_HEAD']} | "
            f"IMU SolarXR: [E={cnt['IMU_ESQ']} D={cnt['IMU_DIR']}]  "
        )
        sys.stdout.write(linha)
        sys.stdout.flush()
        time.sleep(intervalo)


# ----------------- SALVAMENTO E PLOTAGEM DE TODOS OS DADOS -----------------
def salvar_e_plotar_todos_os_dados():
    print("\n\n" + "=" * 80)
    print("      COMPILAÇÃO E VISUALIZAÇÃO GERAL DE TODOS OS DADOS")
    print("=" * 80)

    # 1. Salva CSV consolidado
    os.makedirs("dados", exist_ok=True)
    with open(ARQUIVO_SAIDA_CSV, mode="w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Fonte", "Tracker_Parte", "Tempo_s",
            "Pos_X", "Pos_Y", "Pos_Z",
            "Acc_X", "Acc_Y", "Acc_Z", "Acc_Mag",
            "Pitch_deg", "Yaw_deg", "Roll_deg"
        ])

        # OSC
        for lado in ("ESQ", "DIR"):
            d = dados_osc[lado]
            for i in range(len(d["t"])):
                escritor.writerow([
                    "OSC", f"FOOT_{lado}", f"{d['t'][i]:.4f}",
                    f"{d['x'][i]:.4f}", f"{d['y'][i]:.4f}", f"{d['z'][i]:.4f}",
                    "", "", "", "", "", "", ""
                ])

        for parte, key in [("HIP", "HIP"), ("CHEST", "CHEST")]:
            d = dados_osc[key]
            for i in range(len(d["t"])):
                p = d["pitch"][i] if i < len(d["pitch"]) else ""
                escritor.writerow([
                    "OSC", parte, f"{d['t'][i]:.4f}",
                    f"{d['x'][i]:.4f}", f"{d['y'][i]:.4f}", f"{d['z'][i]:.4f}",
                    "", "", "", "", p, "", ""
                ])

        # VMC Cabeça
        d = dados_vmc["HEAD"]
        for i in range(len(d["t"])):
            escritor.writerow([
                "VMC", "HEAD", f"{d['t'][i]:.4f}",
                "", "", "", "", "", "", "",
                f"{d['pitch'][i]:.2f}", f"{d['yaw'][i]:.2f}", f"{d['roll'][i]:.2f}"
            ])

        # SolarXR IMU
        for lado in ("ESQ", "DIR"):
            d = dados_imu[lado]
            for i in range(len(d["t"])):
                escritor.writerow([
                    "SolarXR_IMU", f"FOOT_{lado}", f"{d['t'][i]:.4f}",
                    "", "", "",
                    f"{d['ax'][i]:.4f}", f"{d['ay'][i]:.4f}", f"{d['az'][i]:.4f}", f"{d['mag'][i]:.4f}",
                    f"{d['pitch'][i]:.2f}", "", ""
                ])

    print(f"[OK] Todos os dados salvos em: {ARQUIVO_SAIDA_CSV}")

    # ================= GERAR GRÁFICOS MULTIPAINÉIS =================
    fig, axes = plt.subplots(5, 1, figsize=(15, 12), sharex=True)

    # --- PAINEL 1: Posições Verticais (Eixo Y - OSC) ---
    ax_y = axes[0]
    if len(dados_osc["ESQ"]["t"]) > 0:
        ax_y.plot(dados_osc["ESQ"]["t"], dados_osc["ESQ"]["y"], color="darkorange", label="Y Pé Esquerdo (m)")
    if len(dados_osc["DIR"]["t"]) > 0:
        ax_y.plot(dados_osc["DIR"]["t"], dados_osc["DIR"]["y"], color="green", label="Y Pé Direito (m)")
    if len(dados_osc["HIP"]["t"]) > 0:
        ax_y.plot(dados_osc["HIP"]["t"], dados_osc["HIP"]["y"], color="purple", label="Y Quadril / Hip (m)", linewidth=1.5)
    if len(dados_osc["CHEST"]["t"]) > 0:
        ax_y.plot(dados_osc["CHEST"]["t"], dados_osc["CHEST"]["y"], color="royalblue", label="Y Peito / Chest (m)")
    ax_y.set_ylabel("Posição Y (m)")
    ax_y.set_title("1. Posições Verticais Y de Todas as Articulações (VRChat OSC)", fontweight="bold")
    ax_y.legend(loc="upper right", fontsize=8, ncol=4)
    ax_y.grid(True, alpha=0.3)

    # --- PAINEL 2: Distância Horizontal entre os Tornozelos (Plano X-Z) ---
    ax_dist = axes[1]
    te, xe, ze = np.array(dados_osc["ESQ"]["t"]), np.array(dados_osc["ESQ"]["x"]), np.array(dados_osc["ESQ"]["z"])
    td, xd, zd = np.array(dados_osc["DIR"]["t"]), np.array(dados_osc["DIR"]["x"]), np.array(dados_osc["DIR"]["z"])
    if len(te) > 5 and len(td) > 5:
        t_min = max(te[0], td[0])
        t_max = min(te[-1], td[-1])
        if t_max > t_min:
            t_grid = np.linspace(t_min, t_max, 500)
            xe_i, ze_i = np.interp(t_grid, te, xe), np.interp(t_grid, te, ze)
            xd_i, zd_i = np.interp(t_grid, td, xd), np.interp(t_grid, td, zd)
            dist_hz = np.sqrt((xd_i - xe_i)**2 + (zd_i - ze_i)**2)
            ax_dist.plot(t_grid, dist_hz, color="black", label="Separação Horizontal dos Pés (m)", linewidth=1.4)
            ax_dist.fill_between(t_grid, 0, dist_hz, color="lightblue", alpha=0.3)
    ax_dist.set_ylabel("Distância (m)")
    ax_dist.set_title("2. Separação Horizontal entre Tornozelos (Plano X-Z)", fontweight="bold")
    ax_dist.legend(loc="upper right", fontsize=8)
    ax_dist.grid(True, alpha=0.3)

    # --- PAINEL 3: Aceleração Resultante das IMUs (Magnitude - SolarXR) ---
    ax_acc = axes[2]
    if len(dados_imu["ESQ"]["t"]) > 0:
        ax_acc.plot(dados_imu["ESQ"]["t"], dados_imu["ESQ"]["mag"], color="darkorange", alpha=0.75, label="Magnitude Acc Pé ESQ (m/s²)")
    if len(dados_imu["DIR"]["t"]) > 0:
        ax_acc.plot(dados_imu["DIR"]["t"], dados_imu["DIR"]["mag"], color="green", alpha=0.75, label="Magnitude Acc Pé DIR (m/s²)")
    ax_acc.axhline(5.0, color="red", linestyle=":", alpha=0.6, label="Limiar Impacto Heel-Strike (5.0 m/s²)")
    ax_acc.set_ylabel("Acc (m/s²)")
    ax_acc.set_title("3. Aceleração Linear Resultante das IMUs dos Pés (SolarXR)", fontweight="bold")
    ax_acc.legend(loc="upper right", fontsize=8)
    ax_acc.grid(True, alpha=0.3)

    # --- PAINEL 4: Componentes de Aceleração Vertical e Anterior (Ay e Az - SolarXR) ---
    ax_comp = axes[3]
    if len(dados_imu["ESQ"]["t"]) > 0:
        ax_comp.plot(dados_imu["ESQ"]["t"], dados_imu["ESQ"]["ay"], color="coral", linestyle="--", label="Ay Vertical Pé ESQ (m/s²)")
        ax_comp.plot(dados_imu["ESQ"]["t"], dados_imu["ESQ"]["az"], color="firebrick", alpha=0.7, label="Az Anterior Pé ESQ (m/s²)")
    if len(dados_imu["DIR"]["t"]) > 0:
        ax_comp.plot(dados_imu["DIR"]["t"], dados_imu["DIR"]["ay"], color="limegreen", linestyle="--", label="Ay Vertical Pé DIR (m/s²)")
        ax_comp.plot(dados_imu["DIR"]["t"], dados_imu["DIR"]["az"], color="darkgreen", alpha=0.7, label="Az Anterior Pé DIR (m/s²)")
    ax_comp.set_ylabel("Acc (m/s²)")
    ax_comp.set_title("4. Componentes Direcionais da Aceleração (Ay Vertical vs Az Anterior)", fontweight="bold")
    ax_comp.legend(loc="upper right", fontsize=8, ncol=4)
    ax_comp.grid(True, alpha=0.3)

    # --- PAINEL 5: Rotações Angulares (Pitch / Inclinação de Tronco, Cabeça e Quadril) ---
    ax_rot = axes[4]
    if len(dados_osc["CHEST"]["t"]) > 0 and len(dados_osc["CHEST"]["pitch"]) > 0:
        ax_rot.plot(dados_osc["CHEST"]["t"][:len(dados_osc["CHEST"]["pitch"])], dados_osc["CHEST"]["pitch"], color="crimson", label="Peito / Tronco (Pitch °)")
    if len(dados_osc["HIP"]["t"]) > 0 and len(dados_osc["HIP"]["pitch"]) > 0:
        ax_rot.plot(dados_osc["HIP"]["t"][:len(dados_osc["HIP"]["pitch"])], dados_osc["HIP"]["pitch"], color="purple", linewidth=1.5, label="Quadril / Hip (Pitch °)")
    if len(dados_vmc["HEAD"]["t"]) > 0:
        ax_rot.plot(dados_vmc["HEAD"]["t"], dados_vmc["HEAD"]["pitch"], color="black", linewidth=1.2, label="Cabeça / Olhar (Pitch °)")
    ax_rot.axhline(0, color="gray", linestyle=":", alpha=0.5)
    ax_rot.set_ylabel("Ângulo (°)")
    ax_rot.set_xlabel("Tempo da Sessão (s)")
    ax_rot.set_title("5. Inclinações Posturais (Pitch de Peito, Quadril e Cabeça)", fontweight="bold")
    ax_rot.legend(loc="upper right", fontsize=8)
    ax_rot.grid(True, alpha=0.3)

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
    print("   GRAVADOR E VISUALIZADOR MULTISSENSORIAL COMPLETO (SLIMEVR + SOLARXR)")
    print("=" * 80)
    print("• Captura simultaneamente:")
    print("  - Porta 9000 (OSC)     : Pés (2, 3), Quadril (1) e Peito (6)")
    print("  - Porta 39539 (VMC)    : Cabeça / Olhar")
    print("  - Porta 21110 (SolarXR): IMUs físicas acelerômetro 3D de ambos os pés")
    print("• Mova-se, caminhe ou teste posturas livremente.")
    print("• Pressione 'Ctrl + C' para encerrar a gravação e plotar todos os dados.")
    print("=" * 80)

    # Inicia servidores
    t_osc = threading.Thread(target=rodar_servidor, args=(PORTA_OSC, osc_handler), daemon=True)
    t_vmc = threading.Thread(target=rodar_servidor, args=(PORTA_VMC, vmc_handler), daemon=True)
    t_osc.start()
    t_vmc.start()

    # Inicia stream SolarXR
    script_node = os.path.join("slimevr_client_raw", "src", "stream.mjs")
    proc_node = None
    if os.path.exists(script_node):
        try:
            proc_node = subprocess.Popen(
                ["node", script_node],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            t_solar = threading.Thread(target=thread_leitor_solarxr, args=(proc_node,), daemon=True)
            t_solar.start()
            print("[OK] SolarXR conectado na porta 21110.")
        except Exception as e:
            print(f"[Aviso] Falha ao iniciar SolarXR: {e}")

    t_disp = threading.Thread(target=renderizar_terminal, daemon=True)
    t_disp.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        rodando = False
        print("\n\nEncerrando gravação e abrindo gráficos...")
        if proc_node:
            try:
                proc_node.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        salvar_e_plotar_todos_os_dados()


if __name__ == "__main__":
    main()

