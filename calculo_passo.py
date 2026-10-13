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
PORTA_OSC = 9000     # Trackers OSC do VRChat (Pés, Quadril, Coxas, Peito)
PORTA_VMC = 39539    # Captura Virtual de Movimentos - VMC (Cabeça / Neck)
IP = "127.0.0.1"

# Identificação dos trackers no OSC
TRACKER_QUADRIL = "1"
TORNOZELO_ESQ = "2"
TORNOZELO_DIR = "3"
TRACKER_COXA_ESQ = "4"
TRACKER_COXA_DIR = "5"
TRACKER_PEITO = "6"
OSSO_CABECA = "neck"         # No SlimeVR VMC, a inclinação da cabeça atua no osso 'Neck'

INVERTER_PITCH_CABECA = True    # True para tracker na nuca / trás da cabeça
INVERTER_PITCH_TRONCO = True    # True para inverter frente/trás do peito

# Parâmetros biomecânicos e filtros
FREQUENCIA_CORTE_POS = 5.0   # Hz para suavização das trajetórias espaciais
FREQUENCIA_CORTE_ACC = 12.0  # Hz para sinal de aceleração das IMUs
ORDEM_FILTRO = 2

# Detecção de Heel-Strike via Acelerômetro SolarXR
LIMIAR_IMPACTO_ACC = 5.0      # m/s² mínimo no acelerômetro para considerar impacto mecânico
PROEMINENCIA_IMPACTO = 1.8    # m/s² de destaque do pico de impacto
DISTANCIA_MINIMA_PASSO = 0.25 # metros mínimos entre os pés no instante do impacto
LIMIAR_MAD = 4.5              # Z-score modificado para detecção de outliers

# Limiares de classificação postural
LIMIAR_QUADRIL_EM_PE = 0.65   # metros acima do chão (se > 0.65m = Em Pé, se <= 0.65m = Sentado)
LIMIAR_COXA_SENTADA = -45.0   # graus de pitch da coxa (se coxa < -45° = reforço de Sentado)
LIMIAR_ACC_MOVIMENTO = 1.6    # m/s² para diferenciar Parado de Caminhando/Ativo
LIMIAR_VAR_DIST_MOV = 0.035   # metros de desvio da distância entre os pés

# Limiares de classificação do olhar (cabeça)
LIMIAR_CIMA = 15.0
LIMIAR_BAIXO = -15.0
LIMIAR_MUITO_BAIXO = -35.0

# Arquivos de saída
os.makedirs("dados", exist_ok=True)
NOME_ARQUIVO_PASSOS = os.path.join("dados", "resultado_completo_marcha.csv")
NOME_ARQUIVO_BRUTO = os.path.join("dados", "dados_brutos_sessao.csv")
INTERVALO_FEEDBACK = 0.20     # Atualização do terminal 5 vezes por segundo
# ==============================================================

# Estruturas de dados em memória
dados = {
    TRACKER_QUADRIL: {"t": [], "x": [], "y": [], "z": [], "pitch": []},
    TORNOZELO_ESQ: {"t": [], "x": [], "y": [], "z": []},
    TORNOZELO_DIR: {"t": [], "x": [], "y": [], "z": []},
    TRACKER_COXA_ESQ: {"t": [], "pitch": []},
    TRACKER_COXA_DIR: {"t": [], "pitch": []},
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

# Posições e rotações instantâneas
pos_atual = {
    TRACKER_QUADRIL: None,
    TORNOZELO_ESQ: None,
    TORNOZELO_DIR: None
}
rot_atual = {
    TRACKER_QUADRIL: None,
    TRACKER_COXA_ESQ: None,
    TRACKER_COXA_DIR: None,
    TRACKER_PEITO: None,
    "cabeca": None
}

# Calibração inicial de chão
chao_base_calibrado = None
amostras_calib_y = []
CALIB_DURACAO_S = 1.5

ossos_vmc_detectados = set()
ultimo_impacto_str = "Aguardando..."


# ----------------- CONVERSÕES CINEMÁTICAS -----------------
def extrair_pitch_rotacao(rotacao, inverter=False):
    """Calcula o ângulo de pitch (inclinação frente/trás) em graus."""
    if len(rotacao) == 4:
        x, y, z, w = rotacao
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))
        pitch = math.degrees(math.asin(sinp))
    elif len(rotacao) == 3:
        pitch = float(rotacao[0])
        if pitch > 180:
            pitch -= 360
    else:
        pitch = 0.0

    if inverter:
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
    """Recebe trackers na porta 9000 (Tornozelos, Quadril, Coxas, Peito)."""
    global tempo_inicial, chao_base_calibrado

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
            if tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR, TRACKER_QUADRIL):
                dados[tracker_id]["t"].append(t_rel)
                dados[tracker_id]["x"].append(x)
                dados[tracker_id]["y"].append(y)
                dados[tracker_id]["z"].append(z)
                pos_atual[tracker_id] = (x, y, z)

                # Calibração inicial do chão (primeiros 1.5s com pés no chão)
                if t_rel <= CALIB_DURACAO_S and tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR):
                    amostras_calib_y.append(y)
                    if len(amostras_calib_y) >= 15:
                        chao_base_calibrado = float(np.percentile(amostras_calib_y, 10))

        elif tipo == "rotation" and len(args) >= 3:
            if tracker_id == TRACKER_PEITO:
                pitch = extrair_pitch_rotacao(args, inverter=INVERTER_PITCH_TRONCO)
                dados[TRACKER_PEITO]["t"].append(t_rel)
                dados[TRACKER_PEITO]["pitch"].append(pitch)
                rot_atual[TRACKER_PEITO] = pitch
            elif tracker_id == TRACKER_QUADRIL:
                pitch = extrair_pitch_rotacao(args)
                dados[TRACKER_QUADRIL]["pitch"].append(pitch)
                rot_atual[TRACKER_QUADRIL] = pitch
            elif tracker_id in (TRACKER_COXA_ESQ, TRACKER_COXA_DIR):
                pitch = extrair_pitch_rotacao(args)
                dados[tracker_id]["t"].append(t_rel)
                dados[tracker_id]["pitch"].append(pitch)
                rot_atual[tracker_id] = pitch


def vmc_handler_39539(address, *args):
    """Recebe dados da cabeça via VMC na porta 39539."""
    global tempo_inicial

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
        rot_atual["cabeca"] = pitch


def thread_leitor_solarxr(proc):
    """Lê aceleração linear direta das IMUs dos pés via SolarXR."""
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


# ----------------- CLASSIFICAÇÃO POSTURAL E DE MOVIMENTO -----------------
def classificar_estado_usuario(p_esq, p_dir, p_quad, rot_coxa_e, rot_coxa_d, a_esq, a_dir, var_dist):
    """
    Classifica de forma unificada:
    1. Postura: EM PÉ vs SENTADO
    2. Movimento: PARADO vs CAMINHANDO / ATIVO
    3. Cota do chão dinâmico (imune à inclinação do tronco)
    """
    # Piso instantâneo: âncora no pé que está apoiado no chão
    chao_instantaneo = min(p_esq[1], p_dir[1])
    if chao_base_calibrado is not None:
        chao_instantaneo = min(chao_instantaneo, chao_base_calibrado)

    # 1. Postura (EM PÉ vs SENTADO)
    if p_quad is not None:
        dist_quadril_chao = p_quad[1] - chao_instantaneo
        pitch_coxas = []
        if rot_coxa_e is not None: pitch_coxas.append(rot_coxa_e)
        if rot_coxa_d is not None: pitch_coxas.append(rot_coxa_d)
        media_coxa = np.mean(pitch_coxas) if pitch_coxas else None

        if dist_quadril_chao >= LIMIAR_QUADRIL_EM_PE and (media_coxa is None or media_coxa > LIMIAR_COXA_SENTADA):
            postura = "EM PÉ"
        else:
            postura = "SENTADO"
        altura_quad_cm = dist_quadril_chao * 100
    else:
        postura = "EM PÉ"
        altura_quad_cm = None

    # 2. Movimento (PARADO vs CAMINHANDO / ATIVO)
    max_acc = max(a_esq, a_dir)
    if max_acc >= LIMIAR_ACC_MOVIMENTO or var_dist >= LIMIAR_VAR_DIST_MOV:
        movimento = "CAMINHANDO" if postura == "EM PÉ" else "ATIVO"
    else:
        movimento = "PARADO"

    return postura, movimento, chao_instantaneo, altura_quad_cm


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
        p_quad = pos_atual[TRACKER_QUADRIL]
        r_coxa_e = rot_atual[TRACKER_COXA_ESQ]
        r_coxa_d = rot_atual[TRACKER_COXA_DIR]
        p_peito = rot_atual[TRACKER_PEITO]
        p_cabeca = rot_atual["cabeca"]
        t_init = tempo_inicial
        ult_imp = ultimo_impacto_str

        # Aceleração recente das IMUs (últimas 3 amostras)
        a_e = dados_imu["ESQ"]["acc"][-1] if len(dados_imu["ESQ"]["acc"]) > 0 else 0.0
        a_d = dados_imu["DIR"]["acc"][-1] if len(dados_imu["DIR"]["acc"]) > 0 else 0.0

        # Variabilidade recente da distância
        n_pts = min(len(dados[TORNOZELO_ESQ]["x"]), len(dados[TORNOZELO_DIR]["x"]))
        var_dist = 0.0
        if n_pts >= 8:
            dx = np.array(dados[TORNOZELO_DIR]["x"][-8:]) - np.array(dados[TORNOZELO_ESQ]["x"][-8:])
            dz = np.array(dados[TORNOZELO_DIR]["z"][-8:]) - np.array(dados[TORNOZELO_ESQ]["z"][-8:])
            d_recentes = np.sqrt(dx**2 + dz**2)
            var_dist = float(np.std(d_recentes))

    if p_esq is None or p_dir is None:
        esperando = []
        if p_esq is None: esperando.append(f"Pé ESQ({TORNOZELO_ESQ})")
        if p_dir is None: esperando.append(f"Pé DIR({TORNOZELO_DIR})")
        print(f"\r  Aguardando trackers dos pés: {', '.join(esperando)}...", end="", flush=True)
        return

    tempo = agora - t_init if t_init else 0
    dist_horizontal = math.sqrt((p_dir[0] - p_esq[0])**2 + (p_dir[2] - p_esq[2])**2)

    postura, movimento, chao_inst, alt_quad_cm = classificar_estado_usuario(
        p_esq, p_dir, p_quad, r_coxa_e, r_coxa_d, a_e, a_d, var_dist
    )

    # Altura REAL dos pés (Imune à inclinação do corpo por âncora de apoio)
    alt_e_cm = max(0.0, (p_esq[1] - chao_inst) * 100)
    alt_d_cm = max(0.0, (p_dir[1] - chao_inst) * 100)

    # Papel instantâneo de cada pé
    if alt_e_cm < 1.8 and alt_d_cm < 1.8:
        papel_e, papel_d = "Apoio", "Apoio"
    elif alt_e_cm <= alt_d_cm:
        papel_e, papel_d = "Apoio", "Balanço"
    else:
        papel_e, papel_d = "Balanço", "Apoio"

    # Badges visuais
    str_estado = f"[{postura} | {movimento}]"
    str_pes = f"Pés: {dist_horizontal:.3f}m"
    str_alt = f"Alt: E={alt_e_cm:4.1f}cm({papel_e[0]}) D={alt_d_cm:4.1f}cm({papel_d[0]})"
    str_quad = f" | Quad:{alt_quad_cm:3.0f}cm" if alt_quad_cm is not None else ""
    str_trc = f" | Trc:{p_peito:+4.0f}°" if p_peito is not None else ""
    str_imu = f" | IMU:{ult_imp}" if (a_e > 0 or a_d > 0) else ""

    print(f"\r  ⏱ {tempo:4.1f}s | {str_estado:<21} | {str_pes} | {str_alt}{str_quad}{str_trc}{str_imu}  ", end="", flush=True)


# ----------------- PROCESSAMENTO DE SINAIS E HEEL-STRIKE -----------------
def aplicar_filtro(sinal, frequencia_corte, taxa_amostragem):
    nyquist = taxa_amostragem / 2.0
    freq_norm = frequencia_corte / nyquist
    if freq_norm >= 1.0 or freq_norm <= 0:
        return sinal
    b, a = butter(ORDEM_FILTRO, freq_norm, btype='low')
    return filtfilt(b, a, sinal)


def calcular_sinais_completos(d_esq, d_dir, d_quad):
    """
    Sincroniza os sinais a 100 Hz e extrai as alturas reais imunes à inclinação
    e as séries temporais de postura e movimento.
    """
    t_esq = np.array(d_esq["t"])
    x_esq, y_esq, z_esq = np.array(d_esq["x"]), np.array(d_esq["y"]), np.array(d_esq["z"])

    t_dir = np.array(d_dir["t"])
    x_dir, y_dir, z_dir = np.array(d_dir["x"]), np.array(d_dir["y"]), np.array(d_dir["z"])

    t_inicio = max(t_esq[0], t_dir[0])
    t_fim = min(t_esq[-1], t_dir[-1])
    dt = 0.01  # Base uniforme a 100 Hz
    taxa_amostragem = 100.0

    t_comum = np.arange(t_inicio, t_fim, dt)
    x_esq_i, z_esq_i = np.interp(t_comum, t_esq, x_esq), np.interp(t_comum, t_esq, z_esq)
    x_dir_i, z_dir_i = np.interp(t_comum, t_dir, x_dir), np.interp(t_comum, t_dir, z_dir)

    dist = np.sqrt((x_dir_i - x_esq_i)**2 + (z_dir_i - z_esq_i)**2)

    y_esq_filt = aplicar_filtro(np.interp(t_comum, t_esq, y_esq), FREQUENCIA_CORTE_POS, taxa_amostragem)
    y_dir_filt = aplicar_filtro(np.interp(t_comum, t_dir, y_dir), FREQUENCIA_CORTE_POS, taxa_amostragem)

    # 1. Calibração inicial de chão (primeiros 1.5s)
    mask_calib = t_comum <= min(t_comum[0] + CALIB_DURACAO_S, t_comum[-1])
    piso_base = min(np.min(y_esq_filt[mask_calib]), np.min(y_dir_filt[mask_calib])) if np.any(mask_calib) else min(y_esq_filt[0], y_dir_filt[0])

    # 2. Piso Dinâmico de Apoio (Pé de apoio instantâneo como cota zero física)
    chao_dinamico = np.minimum(np.minimum(y_esq_filt, y_dir_filt), piso_base)

    # Alturas reais em cm (totalmente imunes à inclinação do tronco)
    elev_esq = np.maximum(0.0, y_esq_filt - chao_dinamico) * 100.0
    elev_dir = np.maximum(0.0, y_dir_filt - chao_dinamico) * 100.0

    # 3. Trajetória do Quadril (se houver)
    tem_quad = len(d_quad["t"]) > 10
    if tem_quad:
        t_quad = np.array(d_quad["t"])
        y_quad = np.array(d_quad["y"])
        y_quad_filt = aplicar_filtro(np.interp(t_comum, t_quad, y_quad), FREQUENCIA_CORTE_POS, taxa_amostragem)
        alt_quadril = (y_quad_filt - chao_dinamico) * 100.0
        posturas = np.where(alt_quadril >= (LIMIAR_QUADRIL_EM_PE * 100), "EM PÉ", "SENTADO")
    else:
        alt_quadril = np.full_like(t_comum, 90.0)
        posturas = np.full_like(t_comum, "EM PÉ", dtype=object)

    return t_comum, dist, elev_esq, elev_dir, alt_quadril, posturas, taxa_amostragem


def detectar_passos_por_impacto_imu(t_comum, dist_sinal, taxa_amostragem):
    """
    Detecta o timing exato de cada Heel-Strike a partir dos impactos das IMUs.
    """
    eventos_impacto = []

    for lado in ("ESQ", "DIR"):
        t_imu = np.array(dados_imu[lado]["t"])
        acc_imu = np.array(dados_imu[lado]["acc"])

        if len(t_imu) < 30:
            continue

        acc_interp = np.interp(t_comum, t_imu, acc_imu)
        acc_filtrada = aplicar_filtro(acc_interp, FREQUENCIA_CORTE_ACC, taxa_amostragem)

        # Distância mínima entre passos do mesmo pé: ~0.45s
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

            if dist_no_impacto >= DISTANCIA_MINIMA_PASSO:
                eventos_impacto.append({
                    "tempo": t_impacto,
                    "pe": lado,
                    "impacto_ms2": mag_impacto,
                    "idx": p_idx,
                    "comprimento_m": dist_no_impacto
                })

    if len(eventos_impacto) < 2:
        print("\n[Aviso] Dados de impacto SolarXR insuficientes. Usando detecção geométrica clássica...")
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

    eventos_impacto.sort(key=lambda ev: ev["tempo"])

    # Remove eventos duplicados excessivamente próximos (< 0.20s)
    eventos_filtrados = []
    for ev in eventos_impacto:
        if not eventos_filtrados or (ev["tempo"] - eventos_filtrados[-1]["tempo"]) >= 0.20:
            eventos_filtrados.append(ev)

    tempos = np.array([ev["tempo"] for ev in eventos_filtrados])
    comprimentos = np.array([ev["comprimento_m"] for ev in eventos_filtrados])
    impactos = np.array([ev["impacto_ms2"] for ev in eventos_filtrados])
    pes = np.array([ev["pe"] for ev in eventos_filtrados])

    return tempos, comprimentos, impactos, pes


def calcular_alturas_passos(t, tempos_impactos, pes_impactos, elevacao_esq_cm, elevacao_dir_cm):
    """Calcula a altura máxima (clearance em cm) atingida no balanço anterior ao impacto."""
    alturas = []
    for i, (t_imp, pe) in enumerate(zip(tempos_impactos, pes_impactos)):
        t_inicio = tempos_impactos[i - 1] if i > 0 else max(t[0], t_imp - 0.7)
        mask = (t >= t_inicio) & (t <= t_imp)
        if np.any(mask):
            if pe == "DIR":
                alturas.append(np.max(elevacao_dir_cm[mask]))
            elif pe == "ESQ":
                alturas.append(np.max(elevacao_esq_cm[mask]))
            else:
                alturas.append(max(np.max(elevacao_esq_cm[mask]), np.max(elevacao_dir_cm[mask])))
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
def gerar_relatorio_e_graficos(t_passo, dist, elev_esq_cm, elev_dir_cm, alt_quadril_cm, posturas,
                              tempos_impactos, dist_impactos, impactos_ms2, alturas_passos_cm, pes_passos, mascara_validos):
    print("\n\n" + "=" * 85)
    print("      RELATÓRIO UNIFICADO: MARCHA E POSTURA COM ALTURA REAL DOS PÉS")
    print("=" * 85)

    n_total = len(dist_impactos)
    n_outliers = np.sum(~mascara_validos)
    passos_validos = dist_impactos[mascara_validos]
    tempos_validos = tempos_impactos[mascara_validos]
    alturas_validas = alturas_passos_cm[mascara_validos]
    pes_validos = pes_passos[mascara_validos]

    duracao = t_passo[-1]
    print(f"Duração total da sessão: {duracao:.1f} s")
    print(f"Passos detectados:       {n_total} (Válidos: {len(passos_validos)} | Outliers: {n_outliers} via MAD)")

    # 1. Estatística Postural (Sentado vs Em Pé)
    dt_amostra = np.mean(np.diff(t_passo)) if len(t_passo) > 1 else 0.01
    tempo_em_pe = np.sum(posturas == "EM PÉ") * dt_amostra
    tempo_sentado = np.sum(posturas == "SENTADO") * dt_amostra
    pct_em_pe = (tempo_em_pe / duracao) * 100 if duracao > 0 else 0
    pct_sentado = (tempo_sentado / duracao) * 100 if duracao > 0 else 0

    print(f"\n--- Classificação Postural (Baseada no Quadril e Coxas) ---")
    print(f"  Tempo Em Pé:   {tempo_em_pe:5.1f} s ({pct_em_pe:5.1f}%)")
    print(f"  Tempo Sentado: {tempo_sentado:5.1f} s ({pct_sentado:5.1f}%)")
    if np.any(alt_quadril_cm > 0):
        print(f"  Altura média do Quadril ao Solo: {np.mean(alt_quadril_cm):.1f} cm (± {np.std(alt_quadril_cm):.1f} cm)")

    # 2. Tronco e Cabeça
    tem_peito = len(dados[TRACKER_PEITO]["t"]) >= 10
    tronco_nos_passos = []
    if tem_peito:
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        tronco_nos_passos = np.interp(tempos_impactos, t_peito, pitch_peito)
        med_tronco = np.mean(pitch_peito)
        print(f"\n--- Postura do Tronco (Peito - Tracker {TRACKER_PEITO}) ---")
        print(f"  Inclinação média: {med_tronco:+.2f}° ({classificar_postura_tronco(med_tronco)})")
    else:
        tronco_nos_passos = [0.0] * n_total

    tem_cabeca = len(dados["cabeca"]["t"]) >= 10
    cabeca_nos_passos = []
    if tem_cabeca:
        t_cabeca = np.array(dados["cabeca"]["t"])
        pitch_cabeca = np.array(dados["cabeca"]["pitch"])
        cabeca_nos_passos = np.interp(tempos_impactos, t_cabeca, pitch_cabeca)
        dt_c = np.mean(np.diff(t_cabeca)) if len(t_cabeca) > 1 else 0.02
        t_neutro = np.sum((pitch_cabeca >= LIMIAR_BAIXO) & (pitch_cabeca <= LIMIAR_CIMA)) * dt_c
        pct_neutro = (t_neutro / duracao) * 100 if duracao > 0 else 0
        print(f"\n--- Atenção Visual e Cabeça (VMC) ---")
        print(f"  Inclinação média: {np.mean(pitch_cabeca):+.1f}° | Olhar Frente: {pct_neutro:.1f}% do tempo")
    else:
        cabeca_nos_passos = [0.0] * n_total

    # 3. Parâmetros Espaciais da Marcha
    print(f"\n--- Parâmetros Espaciais da Marcha (Sincronizados no Heel-Strike) ---")
    if len(passos_validos) > 0:
        media_passo = np.mean(passos_validos)
        desvio_passo = np.std(passos_validos)
        media_alt = np.mean(alturas_validas)
        desvio_alt = np.std(alturas_validas)

        print(f"  Comprimento médio da passada: {media_passo:.3f} m (± {desvio_passo:.3f} m)")
        print(f"  Altura máxima da passada:     {media_alt:.1f} cm (± {desvio_alt:.1f} cm)")

        mask_esq = pes_validos == "ESQ"
        mask_dir = pes_validos == "DIR"
        n_esq = np.sum(mask_esq)
        n_dir = np.sum(mask_dir)
        d_e = np.mean(passos_validos[mask_esq]) if n_esq > 0 else 0.0
        d_d = np.mean(passos_validos[mask_dir]) if n_dir > 0 else 0.0
        alt_e = np.mean(alturas_validas[mask_esq]) if n_esq > 0 else 0.0
        alt_d = np.mean(alturas_validas[mask_dir]) if n_dir > 0 else 0.0

        print(f"  Pé ESQ ({n_esq} passos): {d_e:.3f} m | Altura Real: {alt_e:.1f} cm")
        print(f"  Pé DIR ({n_dir} passos): {d_d:.3f} m | Altura Real: {alt_d:.1f} cm")
        if d_e > 0 and d_d > 0:
            simetria = (1.0 - abs(d_e - d_d) / ((d_e + d_d) / 2.0)) * 100.0
            print(f"  Índice de Simetria do Passo:  {simetria:.1f}%")

        if len(tempos_validos) >= 2:
            cadencia = 60.0 / np.mean(np.diff(tempos_validos))
            print(f"  Cadência estimada:            {cadencia:.1f} passos/min")
    else:
        media_passo = 0.0

    # 4. Tabela Integrada
    print("\n--- Tabela Integrada Passo a Passo (Timing Definido pelo Heel-Strike SolarXR) ---")
    print(f"{'#':<4} {'Tempo(s)':<9} {'Pé':<5} {'Impacto(m/s²)':<15} {'Comprim.(m)':<13} {'Alt.Real(cm)':<14} {'Tronco':<10} {'Olhar':<14} {'Status'}")
    print("-" * 95)
    for i, (t_p, d_p, imp_p, alt_p, pe_p, tr_p, cb_p) in enumerate(zip(
        tempos_impactos, dist_impactos, impactos_ms2, alturas_passos_cm, pes_passos, tronco_nos_passos, cabeca_nos_passos
    )):
        valido = mascara_validos[i]
        status = "Válido" if valido else "⚠ Outlier"
        _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
        tr_str = f"{tr_p:+.1f}°" if tem_peito else "N/A"
        print(f"{i+1:<4} {t_p:<9.2f} {pe_p:<5} {imp_p:<15.1f} {d_p:<13.3f} {alt_p:<14.1f} {tr_str:<10} {olhar_lbl:<14} {status}")
    print("=" * 85)

    # 5. Salva CSV Unificado
    with open(NOME_ARQUIVO_PASSOS, mode='w', newline='', encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Passo", "Tempo_HeelStrike_s", "Pe_Impacto", "Impacto_IMU_ms2", "Comprimento_m",
            "Altura_Real_cm", "Tronco_graus", "Cabeca_Pitch_graus", "Estado_Olhar", "Outlier"
        ])
        for i, (t_p, d_p, imp_p, alt_p, pe_p, tr_p, cb_p) in enumerate(zip(
            tempos_impactos, dist_impactos, impactos_ms2, alturas_passos_cm, pes_passos, tronco_nos_passos, cabeca_nos_passos
        )):
            _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
            escritor.writerow([
                i+1, f"{t_p:.3f}", pe_p, f"{imp_p:.2f}", f"{d_p:.3f}", f"{alt_p:.2f}",
                f"{tr_p:.2f}", f"{cb_p:.2f}", olhar_lbl, "NAO" if mascara_validos[i] else "SIM"
            ])
    print(f"\nResultados consolidados salvos em '{NOME_ARQUIVO_PASSOS}'.")

    # ================= GRÁFICOS INTEGRADOS =================
    tem_imu_plot = len(dados_imu["ESQ"]["t"]) > 20 or len(dados_imu["DIR"]["t"]) > 20
    painéis_extras = (1 if tem_imu_plot else 0) + (1 if tem_peito else 0) + (1 if tem_cabeca else 0) + 1  # +1 para o Quadril/Postura
    total_subplots = 3 + painéis_extras
    fig, axes = plt.subplots(total_subplots, 1, figsize=(14, 2.5 * total_subplots), sharex=False)

    idx_ax = 0

    # Painel 1: Distância entre os tornozelos com os instantes exatos de Heel-Strike
    ax_dist = axes[idx_ax]
    ax_dist.plot(t_passo, dist, color='steelblue', label='Distância Horizontal OSC (m)', linewidth=1.4)
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

    # Painel 3: Altura Real dos Pés (Imune à Inclinação)
    ax_alt = axes[idx_ax]
    ax_alt.plot(t_passo, elev_esq_cm, color='dodgerblue', label='Elevação Real Pé ESQ (cm)')
    ax_alt.plot(t_passo, elev_dir_cm, color='darkorange', label='Elevação Real Pé DIR (cm)')
    ax_alt.set_ylabel('Elevação (cm)')
    ax_alt.set_title('3. Altura Real dos Pés (Piso Dinâmico de Apoio — Imune à Inclinação do Tronco)', fontweight="bold")
    ax_alt.legend(loc='upper right', fontsize=8)
    ax_alt.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 4: Quadril e Classificação Postural (Sentado vs Em Pé)
    ax_quad = axes[idx_ax]
    ax_quad.plot(t_passo, alt_quadril_cm, color='teal', label='Altura do Quadril ao Solo (cm)', linewidth=1.5)
    ax_quad.axhline(LIMIAR_QUADRIL_EM_PE * 100, color='red', linestyle='--', label=f'Limiar Sentado/Em Pé ({LIMIAR_QUADRIL_EM_PE*100:.0f} cm)')
    ax_quad.fill_between(t_passo, 0, LIMIAR_QUADRIL_EM_PE * 100, color='orange', alpha=0.10, label='Zona Sentado')
    ax_quad.fill_between(t_passo, LIMIAR_QUADRIL_EM_PE * 100, 120, color='lightgreen', alpha=0.10, label='Zona Em Pé')
    ax_quad.set_ylabel('Quadril (cm)')
    ax_quad.set_title('4. Altura do Quadril e Classificação Postural (Sentado vs Em Pé)', fontweight="bold")
    ax_quad.legend(loc='upper right', fontsize=8, ncol=2)
    ax_quad.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 5: Tronco (se houver)
    if tem_peito:
        ax_tr = axes[idx_ax]
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        ax_tr.plot(t_peito, pitch_peito, color='purple', label='Inclinação do Tronco (Pitch)')
        ax_tr.axhline(0, color='gray', linestyle=':', label='Vertical (0°)')
        ax_tr.set_ylabel('Tronco (°)')
        ax_tr.set_title('5. Postura e Estabilidade do Tronco', fontweight="bold")
        ax_tr.legend(loc='upper right', fontsize=8)
        ax_tr.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 6: Cabeça / Olhar (se houver)
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
        ax_cb.set_title('6. Direção da Atenção Visual (Inclinação da Cabeça)', fontweight="bold")
        ax_cb.legend(loc='upper right', fontsize=8, ncol=3)
        ax_cb.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 7: Gráfico de Barras Passo a Passo
    ax_bar = axes[idx_ax]
    x_indices = np.arange(1, n_total + 1)
    largura = 0.38
    ax_bar.bar(x_indices - largura/2, dist_impactos, width=largura, color='steelblue', label='Comprimento (m)')
    ax_bar.set_ylabel('Comprimento (m)', color='steelblue')
    ax_bar.tick_params(axis='y', labelcolor='steelblue')
    ax_bar.set_xlabel('Número da Passada')
    ax_bar.set_title('7. Relação Comprimento (m) vs Altura Real (cm) por Passada', fontweight="bold")
    ax_bar.set_xticks(x_indices)

    ax_bar_twin = ax_bar.twinx()
    ax_bar_twin.bar(x_indices + largura/2, alturas_passos_cm, width=largura, color='coral', label='Altura Real (cm)')
    ax_bar_twin.set_ylabel('Altura Real (cm)', color='coral')
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

    print("=" * 85)
    print("   SISTEMA UNIFICADO DE MARCHA E POSTURA COM ALTURA REAL DOS PÉS")
    print("=" * 85)
    print(f"Porta 9000 (VRChat OSC) : Pés ({TORNOZELO_ESQ}, {TORNOZELO_DIR}), Quadril ({TRACKER_QUADRIL}), Peito ({TRACKER_PEITO})")
    print(f"Porta 39539 (VMC)       : Cabeça ({OSSO_CABECA.upper()} - Nuca)")
    print(f"Porta 21110 (SolarXR)   : Aceleração física das IMUs (Heel-Strike Timing)")
    print("-" * 85)
    print("-> Fique em repouso nos primeiros 1.5s para calibração automática do piso.")
    print("-> Caminhe ou realize o teste (em pé ou sentado).")
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
        d_quad = dados[TRACKER_QUADRIL]

        if len(d_esq["t"]) < 20 or len(d_dir["t"]) < 20:
            print("Dados insuficientes de passos para análise. Caminhe por mais tempo.")
            return

        t_passo, dist, elev_esq_cm, elev_dir_cm, alt_quadril_cm, posturas, taxa = calcular_sinais_completos(
            d_esq, d_dir, d_quad
        )

        tempos_impactos, dist_impactos, impactos_ms2, pes_passos = detectar_passos_por_impacto_imu(
            t_passo, dist, taxa
        )

        if len(dist_impactos) == 0:
            print("Nenhum passo com a amplitude configurada foi detectado.")
            return

        alturas_passos_cm = calcular_alturas_passos(t_passo, tempos_impactos, pes_passos, elev_esq_cm, elev_dir_cm)
        mascara_validos = remover_outliers(dist_impactos)

        gerar_relatorio_e_graficos(
            t_passo, dist, elev_esq_cm, elev_dir_cm, alt_quadril_cm, posturas,
            tempos_impactos, dist_impactos, impactos_ms2, alturas_passos_cm, pes_passos, mascara_validos
        )


if __name__ == "__main__":
    main()
