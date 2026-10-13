import csv
import sys
import os
import time
import math
import subprocess
import threading
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Garante suporte UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
# Portas de comunicação do SlimeVR
PORTA_OSC = 9000     # Trackers OSC do VRChat (Tornozelos 2 e 3 + Peito 6)
PORTA_VMC = 39539    # Captura Virtual de Movimentos - VMC (Cabeça)
IP = "127.0.0.1"

# Identificação dos trackers no OSC
TORNOZELO_ESQ = "2"
TORNOZELO_DIR = "3"
TRACKER_PEITO = "6"
OSSO_CABECA = "neck"         # No SlimeVR VMC, a inclinação da cabeça atua no osso 'Neck'
INVERTER_PITCH_CABECA = True    # True para tracker na nuca / trás da cabeça
INVERTER_PITCH_TRONCO = True    # True para inverter frente/trás do peito

# Parâmetros biomecânicos e filtros
FREQUENCIA_CORTE_POS = 5.0   # Hz para suavização da distância horizontal
FREQUENCIA_CORTE_ACC = 12.0  # Hz para sinal de aceleração das IMUs
ORDEM_FILTRO = 2

# Detecção de Heel-Strike via Acelerômetro SolarXR
LIMIAR_IMPACTO_ACC = 5.0     # m/s² mínimo no acelerômetro para considerar impacto mecânico
PROEMINENCIA_IMPACTO = 1.8   # m/s² de destaque do pico de impacto em relação ao entorno
DISTANCIA_MINIMA_PASSO = 0.25 # metros mínimos entre os pés no instante do impacto
LIMIAR_MAD = 4.5             # Z-score modificado para detecção de outliers

# Limiares de classificação do olhar (cabeça)
LIMIAR_CIMA = 15.0
LIMIAR_BAIXO = -15.0
LIMIAR_MUITO_BAIXO = -35.0

# Arquivos de saída
os.makedirs("dados", exist_ok=True)
NOME_ARQUIVO_PASSOS = os.path.join("dados", "resultado_completo_marcha.csv")
NOME_ARQUIVO_BRUTO = os.path.join("dados", "dados_brutos_sessao.csv")
INTERVALO_FEEDBACK = 0.25    # Atualização do terminal 4 vezes por segundo
# ==============================================================

# Estruturas de dados em memória
dados = {
    TORNOZELO_ESQ: {"t": [], "x": [], "y": [], "z": []},
    TORNOZELO_DIR: {"t": [], "x": [], "y": [], "z": []},
    TRACKER_PEITO: {"t": [], "pitch": []},
    "cabeca": {"t": [], "pitch": [], "yaw": [], "roll": []}
}

# Dados diretos do acelerômetro físico das IMUs (SolarXR)
dados_imu = {
    "ESQ": {"t": [], "ax": [], "ay": [], "az": [], "acc": []},
    "DIR": {"t": [], "ax": [], "ay": [], "az": [], "acc": []}
}

tempo_inicial = None
ultimo_feedback = 0
lock = threading.Lock()
rodando = True

# Posições e rotações em tempo real para feedback
pos_atual = {TORNOZELO_ESQ: None, TORNOZELO_DIR: None}
min_y_visto = {TORNOZELO_ESQ: float("inf"), TORNOZELO_DIR: float("inf")}
rot_peito_atual = None
rot_cabeca_atual = None
ossos_vmc_detectados = set()
ultimo_impacto_str = "Aguardando..."


# ----------------- CONVERSÕES CINEMÁTICAS -----------------
def calcular_inclinacao_tronco(rotacao):
    """Calcula a inclinação do peito/tronco (pitch) em graus."""
    if len(rotacao) == 4:
        x, y, z, w = rotacao
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))
        pitch = math.degrees(math.asin(sinp))
    elif len(rotacao) == 3:
        pitch = float(rotacao[0])
        if pitch > 180: pitch -= 360
    else:
        pitch = 0.0

    if INVERTER_PITCH_TRONCO:
        pitch = -pitch
    return pitch


def converter_quaternion_cabeca(x, y, z, w):
    """Calcula a inclinação vertical da cabeça (pitch) a partir do quaternion VMC."""
    sinp = 2.0 * (w * x - y * z)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.degrees(math.asin(sinp))

    siny_cosp = 2.0 * (w * y - z * x)
    cosy_cosp = 1.0 - 2.0 * (x * x + y * y)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

    sinr_cosp = 2.0 * (w * z + x * y)
    cosr_cosp = 1.0 - 2.0 * (y * y + z * z)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    if INVERTER_PITCH_CABECA:
        pitch = -pitch
    return pitch, yaw, roll


def classificar_olhar(pitch):
    if pitch > LIMIAR_CIMA:
        return "CIMA [^]", "Olhar p/ Cima"
    elif pitch >= LIMIAR_BAIXO:
        return "NEUTRO [-]", "Olhar Frente"
    elif pitch >= LIMIAR_MUITO_BAIXO:
        return "BAIXO [v]", "Olhar p/ Baixo"
    else:
        return "MUITO BAIXO [!!]", "Olhar Chão"


def classificar_postura_tronco(angulo):
    if abs(angulo) < 5.0:
        return "Ereto [OK]"
    elif angulo < -5.0:
        return "Inclinado Frente"
    else:
        return "Inclinado Trás"


# ----------------- RECEPÇÃO DE DADOS (OSC, VMC & SOLARXR) -----------------
def osc_handler_9000(address, *args):
    """Recebe dados dos tornozelos e peito na porta 9000."""
    global tempo_inicial, rot_peito_atual

    if not address.startswith("/tracking/trackers/"):
        return

    partes = address.split("/")
    if len(partes) < 5:
        return

    tracker_id = partes[3]
    tipo = partes[4]
    tempo_atual = time.time()

    with lock:
        if tempo_inicial is None:
            tempo_inicial = tempo_atual
        t_rel = tempo_atual - tempo_inicial

        if tipo == "position" and len(args) == 3:
            x, y, z = args
            if tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR):
                dados[tracker_id]["t"].append(t_rel)
                dados[tracker_id]["x"].append(x)
                dados[tracker_id]["y"].append(y)
                dados[tracker_id]["z"].append(z)
                pos_atual[tracker_id] = (x, y, z)
                min_y_visto[tracker_id] = min(min_y_visto[tracker_id], y)

        elif tipo == "rotation" and len(args) >= 3:
            if tracker_id == TRACKER_PEITO:
                pitch = calcular_inclinacao_tronco(args)
                dados[TRACKER_PEITO]["t"].append(t_rel)
                dados[TRACKER_PEITO]["pitch"].append(pitch)
                rot_peito_atual = pitch


def vmc_handler_39539(address, *args):
    """Recebe dados da cabeça via VMC na porta 39539."""
    global tempo_inicial, rot_cabeca_atual

    if address != "/VMC/Ext/Bone/Pos" or len(args) < 8:
        return

    bone_name = str(args[0])
    with lock:
        ossos_vmc_detectados.add(bone_name)

        alvo = OSSO_CABECA.lower()
        if alvo in ("neck", "head"):
            if bone_name.lower() not in ("neck", "head"):
                return
            if bone_name.lower() == "head" and "Neck" in ossos_vmc_detectados:
                return
        elif bone_name.lower() != alvo:
            return

        tempo_atual = time.time()
        if tempo_inicial is None:
            tempo_inicial = tempo_atual
        t_rel = tempo_atual - tempo_inicial

        qx, qy, qz, qw = args[4], args[5], args[6], args[7]
        pitch, yaw, roll = converter_quaternion_cabeca(qx, qy, qz, qw)

        dados["cabeca"]["t"].append(t_rel)
        dados["cabeca"]["pitch"].append(pitch)
        dados["cabeca"]["yaw"].append(yaw)
        dados["cabeca"]["roll"].append(roll)
        rot_cabeca_atual = pitch


def thread_leitor_solarxr(proc):
    """Lê os dados físicos do acelerômetro das IMUs via SolarXR (Node.js bridge)."""
    global tempo_inicial, ultimo_impacto_str
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
            dados_imu[lado]["t"].append(t_rel)
            dados_imu[lado]["ax"].append(ax)
            dados_imu[lado]["ay"].append(ay)
            dados_imu[lado]["az"].append(az)
            dados_imu[lado]["acc"].append(acc_mag)

            if acc_mag >= LIMIAR_IMPACTO_ACC:
                ultimo_impacto_str = f"Pé {lado}: {acc_mag:.1f} m/s²"


# ----------------- FEEDBACK EM TEMPO REAL -----------------
def mostrar_feedback():
    global ultimo_feedback
    agora = time.time()

    if agora - ultimo_feedback < INTERVALO_FEEDBACK:
        return
    ultimo_feedback = agora

    with lock:
        p_esq = pos_atual[TORNOZELO_ESQ]
        p_dir = pos_atual[TORNOZELO_DIR]
        p_peito = rot_peito_atual
        p_cabeca = rot_cabeca_atual
        t_init = tempo_inicial
        ult_imp = ultimo_impacto_str
        n_imu_e = len(dados_imu["ESQ"]["t"])
        n_imu_d = len(dados_imu["DIR"]["t"])

    if p_esq is None or p_dir is None:
        esperando = []
        if p_esq is None: esperando.append(f"Tornozelo ESQ({TORNOZELO_ESQ})")
        if p_dir is None: esperando.append(f"Tornozelo DIR({TORNOZELO_DIR})")
        print(f"\r  Aguardando trackers: {', '.join(esperando)}...", end="", flush=True)
        return

    tempo = agora - t_init if t_init else 0
    dist = math.sqrt((p_dir[0] - p_esq[0])**2 + (p_dir[2] - p_esq[2])**2)

    barra_tamanho = int(min(dist / 1.0, 1.0) * 10)
    barra = "#" * barra_tamanho + "-" * (10 - barra_tamanho)

    str_peito = f" | Trc: {p_peito:+4.1f}°" if p_peito is not None else ""
    str_cabeca = ""
    if p_cabeca is not None:
        badge, _ = classificar_olhar(p_cabeca)
        str_cabeca = f" | Olhar: {p_cabeca:+4.1f}° {badge}"

    # Elevação instantânea baseada no pé de apoio
    chao_ao_vivo = min(p_esq[1], p_dir[1])
    alt_e_cm = max(0.0, (p_esq[1] - chao_ao_vivo) * 100)
    alt_d_cm = max(0.0, (p_dir[1] - chao_ao_vivo) * 100)
    str_alt = f" | Alt: E={alt_e_cm:3.0f}cm D={alt_d_cm:3.0f}cm"
    str_imu = f" | IMU: {ult_imp}" if (n_imu_e > 0 or n_imu_d > 0) else " | IMU: conectando..."

    print(f"\r  ⏱ {tempo:4.1f}s | Pés: {dist:.3f}m [{barra}]{str_alt}{str_imu}{str_peito}{str_cabeca}  ", end="", flush=True)


# ----------------- PROCESSAMENTO DE SINAIS E HEEL-STRIKE -----------------
def aplicar_filtro(sinal, frequencia_corte, taxa_amostragem):
    nyquist = taxa_amostragem / 2.0
    freq_norm = frequencia_corte / nyquist
    if freq_norm >= 1.0 or freq_norm <= 0:
        return sinal
    b, a = butter(ORDEM_FILTRO, freq_norm, btype='low')
    return filtfilt(b, a, sinal)


def calcular_sinais_tornozelos(dados_esq, dados_dir):
    t_esq = np.array(dados_esq["t"])
    x_esq, y_esq, z_esq = np.array(dados_esq["x"]), np.array(dados_esq["y"]), np.array(dados_esq["z"])

    t_dir = np.array(dados_dir["t"])
    x_dir, y_dir, z_dir = np.array(dados_dir["x"]), np.array(dados_dir["y"]), np.array(dados_dir["z"])

    t_inicio = max(t_esq[0], t_dir[0])
    t_fim = min(t_esq[-1], t_dir[-1])
    dt = 0.01  # Base de tempo uniforme a 100 Hz
    taxa_amostragem = 100.0

    t_comum = np.arange(t_inicio, t_fim, dt)
    x_esq_i, z_esq_i = np.interp(t_comum, t_esq, x_esq), np.interp(t_comum, t_esq, z_esq)
    x_dir_i, z_dir_i = np.interp(t_comum, t_dir, x_dir), np.interp(t_comum, t_dir, z_dir)

    dist = np.sqrt((x_dir_i - x_esq_i)**2 + (z_dir_i - z_esq_i)**2)

    y_esq_filt = aplicar_filtro(np.interp(t_comum, t_esq, y_esq), FREQUENCIA_CORTE_POS, taxa_amostragem)
    y_dir_filt = aplicar_filtro(np.interp(t_comum, t_dir, y_dir), FREQUENCIA_CORTE_POS, taxa_amostragem)

    # Abordagem de Chão Dinâmico do Pé de Apoio:
    # 1. Calibração inicial do desvio estático entre os dois pés (primeiros 1.5s com pés no chão)
    mask_calib = t_comum <= min(t_comum[0] + 1.5, t_comum[-1])
    delta_calib = np.mean(y_esq_filt[mask_calib]) - np.mean(y_dir_filt[mask_calib]) if np.any(mask_calib) else 0.0

    # 2. O chão verdadeiro em cada instante é o pé apoiado (mínimo vertical compensado)
    y_esq_corr = y_esq_filt - delta_calib
    chao_dinamico = np.minimum(y_esq_corr, y_dir_filt)

    elev_esq = np.maximum(0.0, y_esq_corr - chao_dinamico)
    elev_dir = np.maximum(0.0, y_dir_filt - chao_dinamico)

    return t_comum, dist, elev_esq, elev_dir, taxa_amostragem


def detectar_passos_por_impacto_imu(t_comum, dist_sinal, taxa_amostragem):
    """
    Detecta o timing exato de cada Heel-Strike a partir dos picos de aceleração
    física das IMUs do SolarXR nos pés ESQ e DIR.
    """
    eventos_impacto = []

    # Processa cada pé separadamente se houver dados da IMU
    for lado in ("ESQ", "DIR"):
        t_imu = np.array(dados_imu[lado]["t"])
        acc_imu = np.array(dados_imu[lado]["acc"])

        if len(t_imu) < 30:
            continue

        # Interpola aceleração para a base de tempo comum de 100 Hz
        acc_interp = np.interp(t_comum, t_imu, acc_imu)
        acc_filtrada = aplicar_filtro(acc_interp, FREQUENCIA_CORTE_ACC, taxa_amostragem)

        # Distância mínima entre passos do MESMO pé: ~0.45s (45 amostras a 100 Hz)
        dist_min_pts = int(taxa_amostragem * 0.45)

        picos, props = find_peaks(
            acc_filtrada,
            height=LIMIAR_IMPACTO_ACC,
            distance=dist_min_pts,
            prominence=PROEMINENCIA_IMPACTO
        )

        for p_idx in picos:
            t_impacto = t_comum[p_idx]
            mag_impacto = acc_filtrada[p_idx]
            dist_no_impacto = dist_sinal[p_idx]

            # Filtra eventos onde os pés não estavam separados (ex: tremor parado)
            if dist_no_impacto >= DISTANCIA_MINIMA_PASSO:
                eventos_impacto.append({
                    "tempo": t_impacto,
                    "pe": lado,
                    "impacto_ms2": mag_impacto,
                    "idx": p_idx,
                    "comprimento_m": dist_no_impacto
                })

    # Se não houver dados de IMU suficientes, recorre ao método de picos geométricos
    if len(eventos_impacto) < 2:
        print("\n[Aviso] Dados de impacto do acelerômetro insuficientes. Usando detecção geométrica clássica...")
        intervalo_min = int(taxa_amostragem * 0.3)
        dist_filt = aplicar_filtro(dist_sinal, FREQUENCIA_CORTE_POS, taxa_amostragem)
        picos_geom, _ = find_peaks(
            dist_filt,
            height=DISTANCIA_MINIMA_PASSO,
            distance=intervalo_min,
            prominence=0.08
        )
        tempos = t_comum[picos_geom]
        comprimentos = dist_filt[picos_geom]
        impactos = [0.0] * len(tempos)
        pes_estimados = ["?"] * len(tempos)
        return tempos, comprimentos, impactos, pes_estimados

    # Ordena cronologicamente os eventos de ambos os pés
    eventos_impacto.sort(key=lambda ev: ev["tempo"])

    # Remove eventos duplicados excessivamente próximos (< 0.20s entre passos alternados)
    eventos_filtrados = []
    for ev in eventos_impacto:
        if not eventos_filtrados:
            eventos_filtrados.append(ev)
        else:
            if (ev["tempo"] - eventos_filtrados[-1]["tempo"]) >= 0.20:
                eventos_filtrados.append(ev)

    tempos = np.array([ev["tempo"] for ev in eventos_filtrados])
    comprimentos = np.array([ev["comprimento_m"] for ev in eventos_filtrados])
    impactos = np.array([ev["impacto_ms2"] for ev in eventos_filtrados])
    pes = np.array([ev["pe"] for ev in eventos_filtrados])

    return tempos, comprimentos, impactos, pes


def calcular_alturas_passos(t, tempos_impactos, pes_impactos, elevacao_esq, elevacao_dir):
    """Calcula a altura máxima (clearance) atingida na fase de balanço que antecede o impacto."""
    alturas = []
    for i, (t_imp, pe) in enumerate(zip(tempos_impactos, pes_impactos)):
        t_inicio = tempos_impactos[i - 1] if i > 0 else max(t[0], t_imp - 0.7)
        mask = (t >= t_inicio) & (t <= t_imp)
        if np.any(mask):
            if pe == "DIR":
                alturas.append(np.max(elevacao_dir[mask]))
            elif pe == "ESQ":
                alturas.append(np.max(elevacao_esq[mask]))
            else:
                alturas.append(max(np.max(elevacao_esq[mask]), np.max(elevacao_dir[mask])))
        else:
            alturas.append(0.0)
    return np.array(alturas)


def remover_outliers(valores, limiar=LIMIAR_MAD):
    if len(valores) < 4:
        return np.ones(len(valores), dtype=bool)
    mediana = np.median(valores)
    desvios = np.abs(valores - mediana)
    mad = np.median(desvios)
    if mad == 0:
        mad = np.mean(desvios)
        if mad == 0:
            return np.ones(len(valores), dtype=bool)
    z_mod = 0.6745 * desvios / mad
    return z_mod <= limiar


# ----------------- RELATÓRIO E VISUALIZAÇÃO UNIFICADA -----------------
def gerar_relatorio_e_graficos(t_passo, dist, elev_esq, elev_dir,
                              tempos_impactos, dist_impactos, impactos_ms2, alturas_passos, pes_passos, mascara_validos):
    print("\n\n" + "=" * 80)
    print("      RELATÓRIO UNIFICADO: MARCHA SINCRONIZADA VIA HEEL-STRIKE (SOLARXR)")
    print("=" * 80)

    n_total = len(dist_impactos)
    n_outliers = np.sum(~mascara_validos)
    passos_validos = dist_impactos[mascara_validos]
    tempos_validos = tempos_impactos[mascara_validos]
    alturas_validas = alturas_passos[mascara_validos]
    pes_validos = pes_passos[mascara_validos]
    impactos_validos = impactos_ms2[mascara_validos]

    duracao = t_passo[-1]
    print(f"Duração da sessão: {duracao:.1f} s")
    print(f"Passos totais:     {n_total} (Válidos: {len(passos_validos)} | Outliers: {n_outliers} via MAD)")

    # 1. Postura do Tronco
    tem_peito = len(dados[TRACKER_PEITO]["t"]) >= 10
    tronco_nos_passos = []
    if tem_peito:
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        tronco_nos_passos = np.interp(tempos_impactos, t_peito, pitch_peito)
        med_tronco = np.mean(pitch_peito)
        std_tronco = np.std(pitch_peito)
        print(f"\n--- Postura do Tronco (Peito - Tracker {TRACKER_PEITO}) ---")
        print(f"  Inclinação média:    {med_tronco:+.2f}° ({classificar_postura_tronco(med_tronco)})")
        print(f"  Estabilidade (±DP):  {std_tronco:.2f}°")
    else:
        tronco_nos_passos = [0.0] * n_total
        print("\n[Aviso] Dados do tracker de peito insuficientes.")

    # 2. Cabeça e Atenção Visual
    tem_cabeca = len(dados["cabeca"]["t"]) >= 10
    cabeca_nos_passos = []
    if tem_cabeca:
        t_cabeca = np.array(dados["cabeca"]["t"])
        pitch_cabeca = np.array(dados["cabeca"]["pitch"])
        cabeca_nos_passos = np.interp(tempos_impactos, t_cabeca, pitch_cabeca)

        dt_c = np.mean(np.diff(t_cabeca)) if len(t_cabeca) > 1 else 0.02
        t_neutro = np.sum((pitch_cabeca >= LIMIAR_BAIXO) & (pitch_cabeca <= LIMIAR_CIMA)) * dt_c
        t_baixo = np.sum((pitch_cabeca < LIMIAR_BAIXO) & (pitch_cabeca >= LIMIAR_MUITO_BAIXO)) * dt_c
        t_chao = np.sum(pitch_cabeca < LIMIAR_MUITO_BAIXO) * dt_c
        t_cima = np.sum(pitch_cabeca > LIMIAR_CIMA) * dt_c

        pct_neutro = (t_neutro / duracao) * 100 if duracao > 0 else 0
        pct_baixo = (t_baixo / duracao) * 100 if duracao > 0 else 0
        pct_chao = (t_chao / duracao) * 100 if duracao > 0 else 0
        pct_cima = (t_cima / duracao) * 100 if duracao > 0 else 0

        print(f"\n--- Atenção Visual e Orientação da Cabeça (VMC Head/Neck) ---")
        print(f"  Inclinação média:    {np.mean(pitch_cabeca):+.1f}° (± {np.std(pitch_cabeca):.1f}°)")
        print(f"  Atenção para Frente  [-15° a +15°]: {pct_neutro:5.1f}% ({t_neutro:.1f}s)")
        print(f"  Olhar para Baixo     [-35° a -15°]: {pct_baixo:5.1f}% ({t_baixo:.1f}s)")
        print(f"  Olhar no Chão / Pés  [< -35°]:      {pct_chao:5.1f}% ({t_chao:.1f}s)")
        print(f"  Olhar para Cima      [> +15°]:      {pct_cima:5.1f}% ({t_cima:.1f}s)")
    else:
        cabeca_nos_passos = [0.0] * n_total
        print("\n[Aviso] Dados da cabeça (VMC) insuficientes.")

    # 3. Parâmetros Espaciais da Marcha
    print(f"\n--- Parâmetros Espaciais da Marcha (Sincronizados no Heel-Strike) ---")
    if len(passos_validos) > 0:
        media_passo = np.mean(passos_validos)
        desvio_passo = np.std(passos_validos)
        media_alt = np.mean(alturas_validas) * 100
        desvio_alt = np.std(alturas_validas) * 100

        print(f"  Comprimento médio:   {media_passo:.3f} m (± {desvio_passo:.3f} m)")
        print(f"  Altura média (lift): {media_alt:.1f} cm (± {desvio_alt:.1f} cm)")

        mask_esq = pes_validos == "ESQ"
        mask_dir = pes_validos == "DIR"
        n_esq = np.sum(mask_esq)
        n_dir = np.sum(mask_dir)
        d_e = np.mean(passos_validos[mask_esq]) if n_esq > 0 else 0.0
        d_d = np.mean(passos_validos[mask_dir]) if n_dir > 0 else 0.0
        alt_e = np.mean(alturas_validas[mask_esq]) * 100 if n_esq > 0 else 0.0
        alt_d = np.mean(alturas_validas[mask_dir]) * 100 if n_dir > 0 else 0.0

        print(f"  Passos Pé ESQ ({n_esq}): {d_e:.3f} m | Altura: {alt_e:.1f} cm")
        print(f"  Passos Pé DIR ({n_dir}): {d_d:.3f} m | Altura: {alt_d:.1f} cm")
        if d_e > 0 and d_d > 0:
            simetria = (1.0 - abs(d_e - d_d) / ((d_e + d_d) / 2.0)) * 100.0
            print(f"  Índice de Simetria do Passo: {simetria:.1f}%")

        if len(tempos_validos) >= 2:
            cadencia = 60.0 / np.mean(np.diff(tempos_validos))
            print(f"  Cadência estimada:   {cadencia:.1f} passos/min")
    else:
        media_passo = 0.0

    # 4. Tabela Integrada
    print("\n--- Tabela Integrada Passo a Passo (Timing Definido pelo Heel-Strike da IMU) ---")
    print(f"{'#':<4} {'Tempo(s)':<9} {'Pé':<5} {'Impacto(m/s²)':<15} {'Comprim.(m)':<13} {'Altura(cm)':<12} {'Tronco':<10} {'Cabeça':<10} {'Olhar':<14} {'Status'}")
    print("-" * 105)
    for i, (t_p, d_p, imp_p, alt_p, pe_p, tr_p, cb_p) in enumerate(zip(
        tempos_impactos, dist_impactos, impactos_ms2, alturas_passos, pes_passos, tronco_nos_passos, cabeca_nos_passos
    )):
        valido = mascara_validos[i]
        status = "Válido" if valido else "⚠ Outlier"
        _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
        tr_str = f"{tr_p:+.1f}°" if tem_peito else "N/A"
        cb_str = f"{cb_p:+.1f}°" if tem_cabeca else "N/A"
        print(f"{i+1:<4} {t_p:<9.2f} {pe_p:<5} {imp_p:<15.1f} {d_p:<13.3f} {alt_p*100:<12.1f} {tr_str:<10} {cb_str:<10} {olhar_lbl:<14} {status}")
    print("=" * 80)

    # 5. Salva CSV Unificado
    with open(NOME_ARQUIVO_PASSOS, mode='w', newline='', encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Passo", "Tempo_HeelStrike_s", "Pe_Impacto", "Impacto_IMU_ms2", "Comprimento_m",
            "Altura_Passada_cm", "Tronco_graus", "Cabeca_Pitch_graus", "Estado_Olhar", "Outlier"
        ])
        for i, (t_p, d_p, imp_p, alt_p, pe_p, tr_p, cb_p) in enumerate(zip(
            tempos_impactos, dist_impactos, impactos_ms2, alturas_passos, pes_passos, tronco_nos_passos, cabeca_nos_passos
        )):
            _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
            escritor.writerow([
                i+1, f"{t_p:.3f}", pe_p, f"{imp_p:.2f}", f"{d_p:.3f}", f"{alt_p*100:.2f}",
                f"{tr_p:.2f}", f"{cb_p:.2f}", olhar_lbl, "NAO" if mascara_validos[i] else "SIM"
            ])
    print(f"\nResultados consolidados salvos em '{NOME_ARQUIVO_PASSOS}'.")

    # ================= GRÁFICOS INTEGRADOS (COM PAINEL SOLARXR) =================
    tem_imu_plot = len(dados_imu["ESQ"]["t"]) > 20 or len(dados_imu["DIR"]["t"]) > 20
    painéis_extras = (1 if tem_imu_plot else 0) + (1 if tem_peito else 0) + (1 if tem_cabeca else 0)
    total_subplots = 3 + painéis_extras
    fig, axes = plt.subplots(total_subplots, 1, figsize=(14, 2.6 * total_subplots), sharex=False)

    idx_ax = 0

    # Painel 1: Distância entre os tornozelos com os instantes exatos de Heel-Strike
    ax_dist = axes[idx_ax]
    ax_dist.plot(t_passo, dist, color='steelblue', label='Distância Horizontal OSC (m)', linewidth=1.4)
    # Plota marcadores de impacto nos instantes exatos
    for i in range(len(tempos_impactos)):
        t_hs = tempos_impactos[i]
        d_hs = dist_impactos[i]
        pe_hs = pes_passos[i]
        val = mascara_validos[i]
        cor = "green" if pe_hs == "DIR" else "darkorange"
        marcador = "v" if val else "x"
        ax_dist.scatter(t_hs, d_hs, color=cor, s=80, zorder=5, marker=marcador,
                        label=f"Heel-Strike {pe_hs}" if f"Heel-Strike {pe_hs}" not in ax_dist.get_legend_handles_labels()[1] else "")
        if val:
            ax_dist.annotate(f"{d_hs:.2f}m", (t_hs, d_hs + 0.03), fontsize=8, ha="center", fontweight="bold")

    if len(passos_validos) > 0:
        ax_dist.axhline(media_passo, color='black', linestyle='--', alpha=0.6, label=f'Média: {media_passo:.3f} m')
    ax_dist.set_ylabel('Distância (m)')
    ax_dist.set_title('1. Comprimento da Passada (Sincronizado no Exato Momento do Heel-Strike da IMU)', fontweight="bold")
    ax_dist.legend(loc='upper right', fontsize=8)
    ax_dist.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 2: Acelerações das IMUs dos Pés (SolarXR Impactos)
    if tem_imu_plot:
        ax_imu = axes[idx_ax]
        t_ie = np.array(dados_imu["ESQ"]["t"])
        a_ie = np.array(dados_imu["ESQ"]["acc"])
        t_id = np.array(dados_imu["DIR"]["t"])
        a_id = np.array(dados_imu["DIR"]["acc"])

        if len(t_ie) > 0:
            ax_imu.plot(t_ie, a_ie, color='darkorange', alpha=0.75, label='Aceleração IMU Pé Esquerdo (m/s²)')
        if len(t_id) > 0:
            ax_imu.plot(t_id, a_id, color='green', alpha=0.75, label='Aceleração IMU Pé Direito (m/s²)')

        ax_imu.axhline(LIMIAR_IMPACTO_ACC, color='red', linestyle=':', label=f'Limiar Impacto ({LIMIAR_IMPACTO_ACC} m/s²)')
        ax_imu.set_ylabel('Acc (m/s²)')
        ax_imu.set_title('2. Aceleração Física das IMUs (SolarXR) — Detecção Precisa dos Impactos de Calcanhar', fontweight="bold")
        ax_imu.legend(loc='upper right', fontsize=8)
        ax_imu.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 3: Elevação Vertical dos Pés (Clearance)
    ax_alt = axes[idx_ax]
    ax_alt.plot(t_passo, elev_esq * 100, color='dodgerblue', label='Elevação Pé ESQ (cm)')
    ax_alt.plot(t_passo, elev_dir * 100, color='darkorange', label='Elevação Pé DIR (cm)')
    ax_alt.set_ylabel('Elevação (cm)')
    ax_alt.set_title('3. Altura da Passada (Clearance Vertical dos Tornozelos no Balanço)', fontweight="bold")
    ax_alt.legend(loc='upper right', fontsize=8)
    ax_alt.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 4: Tronco (se houver)
    if tem_peito:
        ax_tr = axes[idx_ax]
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        ax_tr.plot(t_peito, pitch_peito, color='purple', label='Inclinação do Tronco (Pitch)')
        ax_tr.axhline(0, color='gray', linestyle=':', label='Vertical (0°)')
        ax_tr.set_ylabel('Tronco (°)')
        ax_tr.set_title('4. Postura e Estabilidade do Tronco', fontweight="bold")
        ax_tr.legend(loc='upper right', fontsize=8)
        ax_tr.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 5: Cabeça / Olhar (se houver)
    if tem_cabeca:
        ax_cb = axes[idx_ax]
        t_cb = np.array(dados["cabeca"]["t"])
        pitch_cb = np.array(dados["cabeca"]["pitch"])
        ax_cb.plot(t_cb, pitch_cb, color='black', linewidth=1.2, label='Olhar / Cabeça (Pitch)')
        ax_cb.axhline(0, color='gray', linestyle=':', label='Linha Neutra (0°)')
        ax_cb.axhspan(LIMIAR_CIMA, 60, color='dodgerblue', alpha=0.12, label='Olhar p/ Cima')
        ax_cb.axhspan(LIMIAR_BAIXO, LIMIAR_CIMA, color='mediumseagreen', alpha=0.15, label='Neutro (Frente)')
        ax_cb.axhspan(LIMIAR_MUITO_BAIXO, LIMIAR_BAIXO, color='gold', alpha=0.18, label='Olhar p/ Baixo')
        ax_cb.axhspan(-90, LIMIAR_MUITO_BAIXO, color='tomato', alpha=0.20, label='Olhar no Chão')
        ax_cb.set_ylabel('Cabeça (°)')
        ax_cb.set_title('5. Direção da Atenção Visual (Inclinação da Cabeça)', fontweight="bold")
        ax_cb.legend(loc='upper right', fontsize=8, ncol=3)
        ax_cb.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 6: Gráfico de Barras Passo a Passo
    ax_bar = axes[idx_ax]
    x_indices = np.arange(1, n_total + 1)
    largura = 0.38
    ax_bar.bar(x_indices - largura/2, dist_impactos, width=largura, color='steelblue', label='Comprimento (m)')
    ax_bar.set_ylabel('Comprimento (m)', color='steelblue')
    ax_bar.tick_params(axis='y', labelcolor='steelblue')
    ax_bar.set_xlabel('Número da Passada')
    ax_bar.set_title('6. Relação Comprimento (m) vs Altura (cm) por Passada', fontweight="bold")
    ax_bar.set_xticks(x_indices)

    ax_bar_twin = ax_bar.twinx()
    ax_bar_twin.bar(x_indices + largura/2, alturas_passos * 100, width=largura, color='coral', label='Altura (cm)')
    ax_bar_twin.set_ylabel('Altura (cm)', color='coral')
    ax_bar_twin.tick_params(axis='y', labelcolor='coral')
    ax_bar.grid(True, alpha=0.3, axis='x')

    plt.tight_layout()
    plt.show()


# ----------------- EXECUÇÃO PRINCIPAL -----------------
def rodar_servidor(porta, handler):
    disp = Dispatcher()
    disp.set_default_handler(handler)
    try:
        server = ThreadingOSCUDPServer((IP, porta), disp)
        server.serve_forever()
    except OSError as e:
        print(f"\n[AVISO] Erro na porta {porta}: {e}")


def main():
    global rodando
    # 1. Servidores OSC (Porta 9000) e VMC (Porta 39539)
    t_osc = threading.Thread(target=rodar_servidor, args=(PORTA_OSC, osc_handler_9000), daemon=True)
    t_vmc = threading.Thread(target=rodar_servidor, args=(PORTA_VMC, vmc_handler_39539), daemon=True)
    t_osc.start()
    t_vmc.start()

    # 2. Conexão SolarXR (Porta 21110) via subprocesso Node.js
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
            print("[OK] Conexão SolarXR ativa na porta 21110.")
        except Exception as e:
            print(f"[Aviso] Falha ao iniciar stream SolarXR: {e}")

    print("=" * 80)
    print("   SISTEMA UNIFICADO DE MARCHA: HEEL-STRIKE (SOLARXR IMU) + OSC")
    print("=" * 80)
    print(f"Porta 9000 (VRChat OSC) : Tornozelos ({TORNOZELO_ESQ}, {TORNOZELO_DIR}) e Peito ({TRACKER_PEITO})")
    print(f"Porta 39539 (VMC)       : Cabeça ({OSSO_CABECA.upper()} - Nuca)")
    print(f"Porta 21110 (SolarXR)   : Aceleração direta das IMUs dos pés (Heel-Strike Timing)")
    print("-" * 80)
    print("-> Caminhe normalmente pelo ambiente.")
    print("-> Pressione 'Ctrl + C' para finalizar e abrir os relatórios e gráficos.\n")

    try:
        while True:
            mostrar_feedback()
            time.sleep(0.005)
    except KeyboardInterrupt:
        rodando = False
        print("\n\nCaptura concluída! Sincronizando sinais biométricos e impactos...")

        if proc_node:
            try:
                proc_node.terminate()
            except Exception:
                pass

        d_esq = dados[TORNOZELO_ESQ]
        d_dir = dados[TORNOZELO_DIR]

        if len(d_esq["t"]) < 20 or len(d_dir["t"]) < 20:
            print("Dados insuficientes de passos para análise. Caminhe por mais tempo.")
            return

        t_passo, dist, elev_esq, elev_dir, taxa = calcular_sinais_tornozelos(d_esq, d_dir)

        # Detecta o timing de cada Heel-Strike a partir dos impactos da IMU (SolarXR)
        tempos_impactos, dist_impactos, impactos_ms2, pes_passos = detectar_passos_por_impacto_imu(
            t_passo, dist, taxa
        )

        if len(dist_impactos) == 0:
            print("Nenhum passo com a amplitude configurada foi detectado.")
            return

        alturas_passos = calcular_alturas_passos(t_passo, tempos_impactos, pes_passos, elev_esq, elev_dir)
        mascara_validos = remover_outliers(dist_impactos)

        gerar_relatorio_e_graficos(
            t_passo, dist, elev_esq, elev_dir,
            tempos_impactos, dist_impactos, impactos_ms2, alturas_passos, pes_passos, mascara_validos
        )


if __name__ == "__main__":
    main()

