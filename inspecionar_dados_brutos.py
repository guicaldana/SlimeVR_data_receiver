import sys
import os
import json
import time
import math
import subprocess
import threading
from collections import defaultdict
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Garante suporte UTF-8 no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
PORTA_OSC = 9000      # VRChat OSC (Tornozelos 2/3, Quadril 1, Peito 6)
PORTA_VMC = 39539     # VMC Protocol (Ossos, Cabeça)
IP = "127.0.0.1"

INTERVALO_ATUALIZACAO = 0.3  # Atualiza o painel a cada 300 ms
ARQUIVO_AMOSTRA_JSON = os.path.join("dados", "amostra_dados_crus.json")
# ==============================================================

lock = threading.Lock()
tempo_inicial = None
rodando = True

# Estruturas para armazenar o estado mais recente de cada sensor
# Formato: { identificador: { 'dados_crus': ..., 'timestamp': ..., 'contagem': ..., 'taxa_hz': ... } }
estado_osc = defaultdict(lambda: {"pos": None, "rot": None, "ts": 0, "contagem": 0, "hz": 0.0})
estado_vmc = defaultdict(lambda: {"pos": None, "quat": None, "pitch": None, "ts": 0, "contagem": 0, "hz": 0.0})
estado_imu = defaultdict(lambda: {"ax": 0.0, "ay": 0.0, "az": 0.0, "mag": 0.0, "pitch": 0.0, "ts": 0, "contagem": 0, "hz": 0.0})

# Amostras salvas para inspeção detalhada
amostras_capturadas = {
    "OSC_porta_9000": [],
    "VMC_porta_39539": [],
    "SolarXR_porta_21110": []
}


# ----------------- RECEPÇÃO PORTA 9000 (VRChat OSC) -----------------
def osc_handler(address, *args):
    global tempo_inicial
    agora = time.time()
    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora

        partes = address.split("/")
        if len(partes) >= 5 and partes[1] == "tracking" and partes[2] == "trackers":
            t_id = partes[3]
            tipo = partes[4]

            estado = estado_osc[t_id]
            estado["contagem"] += 1
            dt = agora - estado["ts"] if estado["ts"] > 0 else 1.0
            if dt > 0:
                estado["hz"] = 0.85 * estado["hz"] + 0.15 * (1.0 / dt) if estado["hz"] > 0 else (1.0 / dt)
            estado["ts"] = agora

            valores_crus = [round(v, 4) if isinstance(v, float) else v for v in args]

            if tipo == "position":
                estado["pos"] = valores_crus
            elif tipo == "rotation":
                estado["rot"] = valores_crus

            # Guarda amostra nos primeiros 5 segundos
            if len(amostras_capturadas["OSC_porta_9000"]) < 30:
                amostras_capturadas["OSC_porta_9000"].append({
                    "tempo_relativo_s": round(agora - tempo_inicial, 3),
                    "endereco_osc": address,
                    "tracker_id": t_id,
                    "tipo": tipo,
                    "argumentos_crus": list(args)
                })


# ----------------- RECEPÇÃO PORTA 39539 (VMC Protocol) -----------------
def vmc_handler(address, *args):
    global tempo_inicial
    if address != "/VMC/Ext/Bone/Pos" or len(args) < 8:
        return
    agora = time.time()
    bone_name = str(args[0])

    with lock:
        if tempo_inicial is None:
            tempo_inicial = agora

        estado = estado_vmc[bone_name]
        estado["contagem"] += 1
        dt = agora - estado["ts"] if estado["ts"] > 0 else 1.0
        if dt > 0:
            estado["hz"] = 0.85 * estado["hz"] + 0.15 * (1.0 / dt) if estado["hz"] > 0 else (1.0 / dt)
        estado["ts"] = agora

        px, py, pz = round(float(args[1]), 4), round(float(args[2]), 4), round(float(args[3]), 4)
        qx, qy, qz, qw = float(args[4]), float(args[5]), float(args[6]), float(args[7])

        # Calcula pitch da cabeça
        sinp = 2.0 * (qw * qx - qy * qz)
        sinp = max(-1.0, min(1.0, sinp))
        pitch = -math.degrees(math.asin(sinp))

        estado["pos"] = [px, py, pz]
        estado["quat"] = [round(qx, 3), round(qy, 3), round(qz, 3), round(qw, 3)]
        estado["pitch"] = round(pitch, 1)

        if len(amostras_capturadas["VMC_porta_39539"]) < 30:
            amostras_capturadas["VMC_porta_39539"].append({
                "tempo_relativo_s": round(agora - tempo_inicial, 3),
                "endereco": address,
                "bone_name": bone_name,
                "pos_x_y_z": [px, py, pz],
                "quat_x_y_z_w": [qx, qy, qz, qw],
                "pitch_graus": round(pitch, 2)
            })


# ----------------- RECEPÇÃO PORTA 21110 (SolarXR IMU Bridge) -----------------
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
            unix_ms = int(partes[6])
        except ValueError:
            continue

        agora = time.time()
        with lock:
            if tempo_inicial is None:
                tempo_inicial = agora

            estado = estado_imu[lado]
            estado["contagem"] += 1
            dt = agora - estado["ts"] if estado["ts"] > 0 else 1.0
            if dt > 0:
                estado["hz"] = 0.85 * estado["hz"] + 0.15 * (1.0 / dt) if estado["hz"] > 0 else (1.0 / dt)
            estado["ts"] = agora

            mag = math.sqrt(ax**2 + ay**2 + az**2)
            estado["ax"] = ax
            estado["ay"] = ay
            estado["az"] = az
            estado["mag"] = mag
            estado["pitch"] = pitch

            if len(amostras_capturadas["SolarXR_porta_21110"]) < 30:
                amostras_capturadas["SolarXR_porta_21110"].append({
                    "tempo_relativo_s": round(agora - tempo_inicial, 3),
                    "lado": lado,
                    "aceleracao_linear_ms2": {"ax": ax, "ay": ay, "az": az, "magnitude": round(mag, 4)},
                    "pitch_graus": pitch,
                    "unix_ms_hardware": unix_ms
                })


# ----------------- RENDERIZAÇÃO DO PAINEL EM TEMPO REAL -----------------
def renderizar_painel():
    nomes_conhecidos = {
        "1": "Quadril / Hips",
        "2": "Pé/Tornozelo Esquerdo",
        "3": "Pé/Tornozelo Direito",
        "4": "Coxa Esquerda",
        "5": "Coxa Direita",
        "6": "Peito / Chest",
        "7": "Canela Esquerda",
        "8": "Canela Direita"
    }

    while rodando:
        with lock:
            t0 = tempo_inicial
            osc_snap = {k: dict(v) for k, v in estado_osc.items()}
            vmc_snap = {k: dict(v) for k, v in estado_vmc.items()}
            imu_snap = {k: dict(v) for k, v in estado_imu.items()}

        agora = time.time()
        t_decorrido = (agora - t0) if t0 else 0.0

        # Monta a tela limpa
        linhas = []
        linhas.append("\033[H\033[J")  # ANSI clear screen
        linhas.append("=" * 95)
        linhas.append(f"     INSPETOR DE DADOS BRUTOS AO VIVO — SLIMEVR / SOLARXR (⏱ {t_decorrido:4.1f}s)")
        linhas.append("=" * 95)

        # 1. TABELA OSC (Porta 9000)
        linhas.append("\n[1] CANAL VRChat OSC (Porta 9000 UDP) — Cinemática Virtual:")
        linhas.append("-" * 95)
        linhas.append(f"{'ID':<4} {'Nome Provável':<24} {'Posição [X, Y, Z] (metros)':<32} {'Rotação (graus/quat)':<22} {'Taxa':<8}")
        linhas.append("-" * 95)

        if not osc_snap:
            linhas.append("  (Nenhum pacote OSC recebido na porta 9000 até o momento)")
        else:
            for tid in sorted(osc_snap.keys()):
                info = osc_snap[tid]
                nome = nomes_conhecidos.get(tid, f"Tracker {tid}")
                pos_str = str(info["pos"]) if info["pos"] else "---"
                rot_str = str(info["rot"]) if info["rot"] else "---"
                linhas.append(f"{tid:<4} {nome:<24} {pos_str:<32} {rot_str:<22} {info['hz']:4.0f} Hz")

        # 2. TABELA SOLARXR (Porta 21110)
        linhas.append("\n[2] CANAL SolarXR Raw IMUs (Porta 21110 WebSocket) — Acelerômetro Físico Direto:")
        linhas.append("-" * 95)
        linhas.append(f"{'Lado':<6} {'Ax (m/s²)':<12} {'Ay (m/s²)':<12} {'Az (m/s²)':<12} {'Resultante (Mag)':<18} {'Pitch (°)':<12} {'Taxa':<8}")
        linhas.append("-" * 95)

        if not imu_snap:
            linhas.append("  (Aguardando conexão SolarXR ou pacotes das IMUs)")
        else:
            for lado in sorted(imu_snap.keys()):
                info = imu_snap[lado]
                linhas.append(
                    f"{lado:<6} {info['ax']:+10.4f}  {info['ay']:+10.4f}  {info['az']:+10.4f}  "
                    f"{info['mag']:10.4f} m/s²    {info['pitch']:+8.2f}°    {info['hz']:4.0f} Hz"
                )

        # 3. TABELA VMC (Porta 39539)
        linhas.append("\n[3] CANAL VMC Protocol (Porta 39539 UDP) — Ossos e Captura de Movimento:")
        linhas.append("-" * 95)
        linhas.append(f"{'Osso':<16} {'Posição [X, Y, Z] (m)':<30} {'Quaternion [qx, qy, qz, qw]':<32} {'Pitch (°)':<10} {'Taxa':<8}")
        linhas.append("-" * 95)

        if not vmc_snap:
            linhas.append("  (Nenhum pacote VMC recebido na porta 39539)")
        else:
            for bone in sorted(vmc_snap.keys()):
                info = vmc_snap[bone]
                pos_str = str(info["pos"]) if info["pos"] else "---"
                quat_str = str(info["quat"]) if info["quat"] else "---"
                p_str = f"{info['pitch']:+.1f}°" if info["pitch"] is not None else "---"
                linhas.append(f"{bone:<16} {pos_str:<30} {quat_str:<32} {p_str:<10} {info['hz']:4.0f} Hz")

        linhas.append("-" * 95)
        linhas.append("• Pressione 'Ctrl + C' para encerrar e salvar uma amostra dos dados crus em JSON.\n")

        sys.stdout.write("\n".join(linhas))
        sys.stdout.flush()
        time.sleep(INTERVALO_ATUALIZACAO)


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
    # 1. Inicia servidores UDP
    t_osc = threading.Thread(target=rodar_servidor, args=(PORTA_OSC, osc_handler), daemon=True)
    t_vmc = threading.Thread(target=rodar_servidor, args=(PORTA_VMC, vmc_handler), daemon=True)
    t_osc.start()
    t_vmc.start()

    # 2. Inicia streaming SolarXR
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
        except Exception as e:
            print(f"[Aviso] Falha ao iniciar SolarXR: {e}")

    # 3. Thread de visualização do painel
    t_disp = threading.Thread(target=renderizar_painel, daemon=True)
    t_disp.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        rodando = False
        if proc_node:
            try:
                proc_node.terminate()
            except Exception:
                pass
        time.sleep(0.3)

        print("\n\n" + "=" * 80)
        print("          INSPEÇÃO FINALIZADA — AMOSTRAS DE DADOS SALVAS")
        print("=" * 80)

        os.makedirs("dados", exist_ok=True)
        with open(ARQUIVO_AMOSTRA_JSON, mode="w", encoding="utf-8") as f:
            json.dump(amostras_capturadas, f, indent=2, ensure_ascii=False)

        print(f"[OK] Amostras exatas com tipos e valores gravadas em:")
        print(f"     -> {ARQUIVO_AMOSTRA_JSON}")
        print("\nVocê pode abrir esse arquivo JSON para analisar a estrutura exata de cada pacote!")
        print("=" * 80)


if __name__ == "__main__":
    main()
