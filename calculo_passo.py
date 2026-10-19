import sys

# Garante suporte UTF-8 imediato no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

print("\n" + "=" * 75, flush=True)
print("  🚀 INICIANDO SLIMEVR DATA RECEIVER & GAIT ANALYSIS", flush=True)
print("  📡 Abrindo portas OSC/VMC e conectando SolarXR...", flush=True)
print("=" * 75 + "\n", flush=True)

import csv
import os
import time
import math
import subprocess
import threading
import numpy as np
import cadeia_pernas
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

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

# ==================== MODO CLÍNICO (PARKINSON) ====================
# Ajustado para alta sensibilidade: detecta toques suaves (flat-foot / antepé)
# e passos curtos (microbasia de 10 a 25 cm) típicos de Doença de Parkinson
MODO_CLINICO_PARKINSON = True

if MODO_CLINICO_PARKINSON:
    LIMIAR_IMPACTO_ACC = 1.8      # m/s² (sensível a contatos suaves sem choque forte de calcanhar)
    PROEMINENCIA_IMPACTO = 0.5    # m/s² (destaque do pico de impacto)
    DISTANCIA_MINIMA_PASSO = 0.10 # metros mínimos (10 cm - captura passos curtos e microbasia)
    DIST_MIN_MESMO_PE_S = 0.65    # segundos mínimos entre passos do mesmo pé (evita Toe-Off e foot-flat)
    DIST_MIN_PASSOS_GERAL_S = 0.32 # segundos mínimos entre passos alternados (cadência máx ~180 ppm)
else:
    LIMIAR_IMPACTO_ACC = 4.5      # m/s² (adultos saudáveis com heel-strike vigoroso)
    PROEMINENCIA_IMPACTO = 1.5    # m/s²
    DISTANCIA_MINIMA_PASSO = 0.25 # metros mínimos (25 cm)
    DIST_MIN_MESMO_PE_S = 0.75    # segundos mínimos entre passos do mesmo pé
    DIST_MIN_PASSOS_GERAL_S = 0.35 # segundos mínimos entre passos alternados
LIMIAR_MAD = 4.5                  # Z-score modificado para detecção de outliers

# Compensação de atraso de fase do filtro SlimeVR OSC vs SolarXR (Seção 4 do relatório)
TAU_SYNC_OSC = 0.025          # 25 ms de compensação do smoothing/slerp do servidor SlimeVR

# Limiares de classificação postural
LIMIAR_QUADRIL_EM_PE = 0.65   # metros acima do chão (se > 0.65m = Em Pé, se <= 0.65m = Sentado)
LIMIAR_COXA_SENTADA = -45.0   # graus de pitch da coxa (se coxa < -45° = reforço de Sentado)
LIMIAR_ACC_MOVIMENTO = 1.6    # m/s² para diferenciar Parado de Caminhando/Ativo
LIMIAR_VAR_DIST_MOV = 0.035   # metros de desvio da distância entre os pés

# Limiares de classificação do olhar (cabeça)
LIMIAR_CIMA = 15.0
LIMIAR_BAIXO = -15.0
LIMIAR_MUITO_BAIXO = -35.0

# Calibração inicial estática recomendada (Seção 5)
TEMPO_PREPARACAO_S = 5.0      # 5 segundos de contagem regressiva para se posicionar
TEMPO_CALIB_ESTATICA_S = 3.0  # 3 segundos de amostragem estática com ambos os pés no chão

# True = altura da pelve congelada na calibração; False = pelve acompanha o tracker em tempo real
PELVE_FIXA = True

# Como medir a altura do pé:
#   "osc"      = diferença de altura entre os pés no OSC (sobe junto com o tronco, mas os dois pés sobem igual)
#   "cadeia"   = cadeia de transformações das pernas (coxa e canela do SolarXR, sem tronco)
#   "mescla"   = média das duas
#   "pipeline" = OSC com o tronco reto; conforme o tronco inclina, passa para a cadeia.
#                Cada método recebe o seu próprio ganho na calibração da caixa.
# A aceleração das IMUs continua servindo só para o tempo dos passos (heel-strike).
MODO_ALTURA = "pipeline"
TRONCO_RETO_GRAUS = 10.0       # até aqui só OSC (modo "pipeline")
TRONCO_INCLINADO_GRAUS = 30.0  # a partir daqui só cadeia; entre os dois, transição linear

# Calibração de ganho por pé: pisar numa caixa de altura conhecida com cada pé
CALIBRAR_CAIXA = True
ALTURA_CAIXA_CM = 6.7
TEMPO_CAIXA_S = 3.0      # segundos de amostragem com o pé na caixa

# Roteiro guiado: cada etapa tem um tempo fixo e o programa avisa a troca.
# Ao terminar, a gravação encerra sozinha (Ctrl + C continua funcionando).
# (nome da etapa, segundos, instrução)
ROTEIRO_GUIADO = True
ROTEIRO = [
    ("ir_inicio",           15, "Vá até o início da caminhada e fique parado"),
    ("caminhada_reta",      15, "CAMINHE normalmente, com o tronco RETO"),
    ("voltar_inicio",       15, "Volte ao início e prepare-se para caminhar inclinado"),
    ("caminhada_inclinada", 15, "CAMINHE com o tronco INCLINADO para a frente (mais de 30°)"),
    ("ir_caixa",            15, "Vá até a caixa e fique com os dois pés no chão, tronco reto"),
    ("caixa_reto_ESQ",       5, "Pé ESQUERDO em cima da caixa, tronco RETO"),
    ("chao_1",               4, "Pé de volta ao chão"),
    ("caixa_reto_DIR",       5, "Pé DIREITO em cima da caixa, tronco RETO"),
    ("chao_2",               4, "Pé de volta ao chão"),
    ("inclinar",             5, "INCLINE o tronco para a frente (mais de 30°) e mantenha"),
    ("caixa_incl_ESQ",       5, "Pé ESQUERDO em cima da caixa, tronco INCLINADO"),
    ("chao_3",               4, "Pé de volta ao chão, continue inclinado"),
    ("caixa_incl_DIR",       5, "Pé DIREITO em cima da caixa, tronco INCLINADO"),
    ("chao_4",               4, "Pé de volta ao chão. Pode endireitar o tronco"),
]

# Arquivos de saída
os.makedirs("dados", exist_ok=True)
NOME_ARQUIVO_PASSOS = os.path.join("dados", "resultado_completo_marcha.csv")
NOME_ARQUIVO_BRUTO = os.path.join("dados", "dados_brutos_sessao.csv")
NOME_ARQUIVO_ENTRADA = os.path.join("dados", "dados_brutos_entrada.json")
NOME_ARQUIVO_FASES = os.path.join("dados", "fases_sessao.csv")
INTERVALO_FEEDBACK = 0.20     # Atualização do terminal 5 vezes por segundo
# ==============================================================

# ----------------- FILTRO ONE EURO (SEÇÃO 5) -----------------
class OneEuroFilter:
    """
    One Euro Filter (Casiez et al., CHI 2012)
    Filtro adaptativo com latência quase nula durante movimentos rápidos
    e alta atenuação de tremor/ruído de repouso (4 a 6 Hz típico de Parkinson).
    """
    def __init__(self, t0=0.0, x0=0.0, min_cutoff=1.0, beta=0.007, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_prev = float(x0)
        self.dx_prev = 0.0
        self.t_prev = float(t0)

    def filter(self, t, x):
        dt = t - self.t_prev
        if dt <= 1e-5:
            return self.x_prev

        a_d = self._smoothing_factor(dt, self.d_cutoff)
        dx = (x - self.x_prev) / dt
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._smoothing_factor(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self.x_prev

        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        return x_hat

    def _smoothing_factor(self, dt, cutoff):
        r = 2.0 * math.pi * cutoff * dt
        return r / (r + 1.0)


# Estruturas de dados em memória
dados = {
    TRACKER_QUADRIL: {"t": [], "x": [], "y": [], "z": [], "pitch": [], "yaw": []},
    TORNOZELO_ESQ: {"t": [], "x": [], "y": [], "z": []},
    TORNOZELO_DIR: {"t": [], "x": [], "y": [], "z": []},
    TRACKER_COXA_ESQ: {"t": [], "x": [], "y": [], "z": [], "pitch": []},
    TRACKER_COXA_DIR: {"t": [], "x": [], "y": [], "z": [], "pitch": []},
    TRACKER_PEITO: {"t": [], "pitch": []},
    "cabeca": {"t": [], "pitch": [], "yaw": [], "roll": []}
}

# Dados diretos do acelerômetro físico das IMUs (SolarXR)
dados_imu = {
    "ESQ": {"t": [], "ax": [], "ay": [], "az": [], "acc": []},
    "DIR": {"t": [], "ax": [], "ay": [], "az": [], "acc": []}
}

# Orientação das coxas e canelas (SolarXR) para a cadeia de transformações
dados_quat = {seg: {"t": [], "q": []} for seg in cadeia_pernas.SEGMENTOS}
quat_atual = {seg: None for seg in cadeia_pernas.SEGMENTOS}
cadeia = None                    # CadeiaPernas calibrada (None = só OSC)

tempo_inicial = None
ultimo_feedback = 0
lock = threading.Lock()
rodando = True

# Posições e rotações instantâneas
pos_atual = {
    TRACKER_QUADRIL: None,
    TORNOZELO_ESQ: None,
    TORNOZELO_DIR: None,
    TRACKER_COXA_ESQ: None,
    TRACKER_COXA_DIR: None
}
rot_atual = {
    TRACKER_QUADRIL: (0.0, 0.0), # (pitch, yaw)
    TRACKER_COXA_ESQ: None,
    TRACKER_COXA_DIR: None,
    TRACKER_PEITO: None,
    "cabeca": None
}

# Parâmetros de calibração estática dos pés e chão
calibrado = False
dist_qp_e_ref = 0.0
dist_qp_d_ref = 0.0
offset_calib_pernas = 0.0       # Equaliza a distância pélvica de ambos os pés no chão
chao_esq_ref = 0.0
chao_dir_ref = 0.0
yq_ref = 0.0                     # Altura da pelve (Y) no instante da calibração
pitch_tronco_ref = 0.0           # Inclinação natural do peito na calibração (graus)
fases_sessao = []                # (etapa, t0, t1) do roteiro guiado, no tempo da gravação
etapa_atual = ""                 # texto curto mostrado na linha de feedback
roteiro_terminou = False
serie_altura = {}                # Série completa da altura (preenchida em calcular_sinais_completos)
ganho_esq = 1.0                  # Fator de escala da altura do pé E (calibrado na caixa)
ganho_dir = 1.0                  # Fator de escala da altura do pé D (calibrado na caixa)
# No modo "pipeline", cada método tem o seu ganho por pé: {"osc": (E, D), "cadeia": (E, D)}
ganhos_metodo = {"osc": (1.0, 1.0), "cadeia": (1.0, 1.0)}
L_coxa_pe_e_ref = 0.0          # Comprimento de repouso Coxa-Pé Esquerdo (desacoplamento pélvico)
L_coxa_pe_d_ref = 0.0          # Comprimento de repouso Coxa-Pé Direito (desacoplamento pélvico)
limiar_ruido_chao = 1.0         # cm (zona morta contra tremor e ruído dos sensores no chão)
delta_cal_esq = 0.0
delta_cal_dir = 0.0
chao_base_calibrado = None

# Instâncias dos Filtros One Euro para suavização em tempo real
euro_dist = OneEuroFilter(min_cutoff=0.8, beta=0.005)
euro_alt_e = OneEuroFilter(min_cutoff=1.0, beta=0.007)
euro_alt_d = OneEuroFilter(min_cutoff=1.0, beta=0.007)

ossos_vmc_detectados = set()
ultimo_impacto_str = "Aguardando..."


# ----------------- CONVERSÕES CINEMÁTICAS -----------------
def extrair_pitch_e_yaw(rotacao, inverter_pitch=False):
    """Extrai os ângulos de pitch e yaw em graus a partir de Euler ou Quaternion."""
    if len(rotacao) == 4:
        x, y, z, w = rotacao
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))
        pitch = math.degrees(math.asin(sinp))

        siny_cosp = 2.0 * (w * y - z * x)
        cosy_cosp = 1.0 - 2.0 * (x * x + y * y)
        yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    elif len(rotacao) >= 3:
        pitch = float(rotacao[0])
        if pitch > 180: pitch -= 360
        yaw = float(rotacao[1])
        if yaw > 180: yaw -= 360
    else:
        pitch, yaw = 0.0, 0.0

    if inverter_pitch:
        pitch = -pitch
    return pitch, yaw


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
    global tempo_inicial, delta_cal_esq, delta_cal_dir, chao_base_calibrado

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
            if tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR, TRACKER_QUADRIL, TRACKER_COXA_ESQ, TRACKER_COXA_DIR):
                dados[tracker_id]["t"].append(t_rel)
                dados[tracker_id]["x"].append(x)
                dados[tracker_id]["y"].append(y)
                dados[tracker_id]["z"].append(z)
                pos_atual[tracker_id] = (x, y, z)

        elif tipo == "rotation" and len(args) >= 3:
            if tracker_id == TRACKER_PEITO:
                pitch, _ = extrair_pitch_e_yaw(args, inverter_pitch=INVERTER_PITCH_TRONCO)
                dados[TRACKER_PEITO]["t"].append(t_rel)
                dados[TRACKER_PEITO]["pitch"].append(pitch)
                rot_atual[TRACKER_PEITO] = pitch
            elif tracker_id == TRACKER_QUADRIL:
                pitch, yaw = extrair_pitch_e_yaw(args)
                dados[TRACKER_QUADRIL]["pitch"].append(pitch)
                dados[TRACKER_QUADRIL]["yaw"].append(yaw)
                rot_atual[TRACKER_QUADRIL] = (pitch, yaw)
            elif tracker_id in (TRACKER_COXA_ESQ, TRACKER_COXA_DIR):
                pitch, _ = extrair_pitch_e_yaw(args)
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
    """Lê aceleração linear direta das IMUs dos pés via SolarXR (com pitch do cadarço)."""
    global tempo_inicial, ultimo_impacto_str
    for linha in proc.stdout:
        if not rodando:
            break
        linha_str = linha.strip()
        if linha_str.startswith("Q "):
            partes = linha_str.split()
            if len(partes) >= 6 and partes[1] in quat_atual:
                try:
                    q = tuple(float(v) for v in partes[2:6])
                except ValueError:
                    continue
                agora = time.time()
                with lock:
                    if tempo_inicial is None:
                        tempo_inicial = agora
                    dados_quat[partes[1]]["t"].append(agora - tempo_inicial)
                    dados_quat[partes[1]]["q"].append(q)
                    quat_atual[partes[1]] = q
            continue
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
            pitch_cadarco = float(partes[5])
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
                ultimo_impacto_str = f"Pé {lado}: {acc_mag:.1f} m/s² (Pitch:{pitch_cadarco:+3.0f}°)"


# ----------------- PROJEÇÃO VETORIAL ANTEROPOSTERIOR (SEÇÃO 3) -----------------
def calcular_projecao_anteroposterior(p_esq, p_dir, rot_quadril, pos_quadril=None):
    """
    Projeção Vetorial da Distância entre os Pés (Eq. da Seção 3 da Nota Técnica):
    D_passo(t) = (P_esq(t) - P_dir(t)) · F_quadril_XZ(t)
    
    Elimina a contaminação da largura lateral dos pés (comum no Parkinson).
    A curva cruza exatamente o zero durante o cruzamento das pernas (mid-stance).
    Positivo = Pé Esquerdo à frente | Negativo = Pé Direito à frente.
    """
    dx = p_esq[0] - p_dir[0]
    dz = p_esq[2] - p_dir[2]

    # Vetor unitário frontal horizontal do quadril F_quadril_XZ
    yaw_deg = rot_quadril[1] if rot_quadril else 0.0
    psi = math.radians(yaw_deg)
    fx = -math.sin(psi)
    fz = -math.cos(psi)

    # Normaliza
    fn = math.sqrt(fx**2 + fz**2)
    if fn > 0:
        fx /= fn
        fz /= fn
    else:
        fx, fz = 0.0, -1.0

    # Projeção escalar sobre o vetor de avanço
    d_ap = dx * fx + dz * fz

    # Largura da base de suporte lateral desacoplada
    largura_lateral = abs(dx * fz - dz * fx)

    return d_ap, largura_lateral


# ----------------- CLASSIFICAÇÃO POSTURAL E DE MOVIMENTO -----------------
def classificar_estado_usuario(p_esq, p_dir, p_quad, rot_coxa_e, rot_coxa_d, a_esq, a_dir, var_dist):
    """
    Classifica Postura e Movimento com Nível Dinâmico do Solo e Referencial Pelvino.
    """
    # Nível Dinâmico do Solo equalizado com a calibração estática
    if calibrado:
        chao_instantaneo = (chao_esq_ref + chao_dir_ref) / 2.0
    else:
        chao_instantaneo = min(p_esq[1], p_dir[1])

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
        altura_quad_cm = dist_quadril_chao * 100.0
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


# ----------------- FEEDBACK EM TEMPO REAL COM ONE EURO FILTER -----------------
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
        p_coxa_e = pos_atual[TRACKER_COXA_ESQ]
        p_coxa_d = pos_atual[TRACKER_COXA_DIR]
        r_quad = rot_atual[TRACKER_QUADRIL]
        r_coxa_e = rot_atual[TRACKER_COXA_ESQ]
        r_coxa_d = rot_atual[TRACKER_COXA_DIR]
        p_peito = rot_atual[TRACKER_PEITO]
        quats = dict(quat_atual)
        t_init = tempo_inicial
        ult_imp = ultimo_impacto_str

        a_e = dados_imu["ESQ"]["acc"][-1] if len(dados_imu["ESQ"]["acc"]) > 0 else 0.0
        a_d = dados_imu["DIR"]["acc"][-1] if len(dados_imu["DIR"]["acc"]) > 0 else 0.0

        n_pts = min(len(dados[TORNOZELO_ESQ]["x"]), len(dados[TORNOZELO_DIR]["x"]))
        var_dist = 0.0
        if n_pts >= 8:
            dx = np.array(dados[TORNOZELO_DIR]["x"][-8:]) - np.array(dados[TORNOZELO_ESQ]["x"][-8:])
            dz = np.array(dados[TORNOZELO_DIR]["z"][-8:]) - np.array(dados[TORNOZELO_ESQ]["z"][-8:])
            var_dist = float(np.std(np.sqrt(dx**2 + dz**2)))

    if p_esq is None or p_dir is None:
        esperando = []
        if p_esq is None: esperando.append(f"Pé ESQ({TORNOZELO_ESQ})")
        if p_dir is None: esperando.append(f"Pé DIR({TORNOZELO_DIR})")
        print(f"\r  Aguardando trackers dos pés: {', '.join(esperando)}...", end="", flush=True)
        return

    tempo = agora - t_init if t_init else 0

    # 1. Projeção Vetorial Anteroposterior (Seção 3)
    d_ap, larg_lat = calcular_projecao_anteroposterior(p_esq, p_dir, r_quad, p_quad)
    d_ap_filtrada = euro_dist.filter(tempo, d_ap)

    # 2. Altura de cada pé acima do outro (OSC, cadeia das pernas ou mescla: MODO_ALTURA)
    clearance_e_raw, clearance_d_raw = _clearance_bruto_cm(p_esq, p_dir, p_quad, quats, p_peito)

    # Ganho por pé calibrado com a caixa de altura conhecida
    clearance_e_raw *= ganho_esq
    clearance_d_raw *= ganho_dir

    tornoz_e_raw = clearance_e_raw
    tornoz_d_raw = clearance_d_raw

    # Zona morta de solo calibrada
    if clearance_e_raw < limiar_ruido_chao: clearance_e_raw = 0.0
    if clearance_d_raw < limiar_ruido_chao: clearance_d_raw = 0.0
    if tornoz_e_raw < limiar_ruido_chao: tornoz_e_raw = 0.0
    if tornoz_d_raw < limiar_ruido_chao: tornoz_d_raw = 0.0

    clearance_e_cm = euro_alt_e.filter(tempo, clearance_e_raw)
    clearance_d_cm = euro_alt_d.filter(tempo, clearance_d_raw)
    if clearance_e_cm < 0.2: clearance_e_cm = 0.0
    if clearance_d_cm < 0.2: clearance_d_cm = 0.0

    postura, movimento, _, alt_quad_cm = classificar_estado_usuario(
        p_esq, p_dir, p_quad, r_coxa_e, r_coxa_d, a_e, a_d, var_dist
    )

    # Identifica perna na frente via projeção vetorial
    if abs(d_ap_filtrada) < 0.05:
        str_passo = f"Cruzt: {d_ap_filtrada:+.2f}m"
    elif d_ap_filtrada > 0:
        str_passo = f"EsqAnt: {d_ap_filtrada:+.2f}m"
    else:
        str_passo = f"DirAnt: {-d_ap_filtrada:+.2f}m"

    str_calib = " [CALIBRADO]" if calibrado else ""

    str_estado = f"[{postura} | {movimento}]"
    str_alt = f"Solado: E={clearance_e_cm:3.1f}cm D={clearance_d_cm:3.1f}cm"
    str_base = f"Base:{larg_lat*100:2.0f}cm"
    str_quad = f" | Quad:{alt_quad_cm:3.0f}cm" if alt_quad_cm is not None else ""
    str_trc = f" | Trc:{p_peito:+4.0f}°" if p_peito is not None else ""
    str_imu = f" | IMU:{ult_imp}" if (a_e > 0 or a_d > 0) else ""

    if etapa_atual:
        str_calib = f"{str_calib} | {etapa_atual}"
    print(f"\r  ⏱ {tempo:4.1f}s | {str_estado:<21} | {str_passo} ({str_base}) | {str_alt}{str_quad}{str_trc}{str_imu}{str_calib}  ", end="", flush=True)


# ----------------- PROCESSAMENTO DE SINAIS E SÉRIES TEMPORAIS -----------------
def aplicar_filtro(sinal, frequencia_corte, taxa_amostragem):
    from scipy.signal import butter, filtfilt
    nyquist = taxa_amostragem / 2.0
    freq_norm = frequencia_corte / nyquist
    if freq_norm >= 1.0 or freq_norm <= 0:
        return sinal
    b, a = butter(2, freq_norm, btype='low')
    return filtfilt(b, a, sinal)


def alturas_cadeia_serie(t_comum, taxa_amostragem):
    """Série da altura (cm) de cada pé acima do outro pela cadeia das pernas, ou (None, None)."""
    if cadeia is None or MODO_ALTURA == "osc":
        return None, None
    series = {}
    for seg in cadeia_pernas.SEGMENTOS:
        d = dados_quat[seg]
        if len(d["t"]) < 10:
            return None, None
        series[seg] = (np.array(d["t"]), np.array(d["q"]))
    dif = cadeia.dif_vertical(cadeia_pernas.quats_em(series, t_comum))
    dif = aplicar_filtro(dif, 5.0, taxa_amostragem)
    e, d = cadeia_pernas.CadeiaPernas.alturas_pes(dif)
    return e * 100.0, d * 100.0


def calcular_sinais_completos(d_esq, d_dir, d_quad, d_coxa_e=None, d_coxa_d=None):
    """
    Gera as séries temporais determinísticas com Projeção Vetorial Anteroposterior (Seção 3),
    Referencial Pelvino Imune à Inclinação (Eq. 1.3) e Largura da Base de Suporte.
    """
    t_esq = np.array(d_esq["t"])
    x_esq, y_esq, z_esq = np.array(d_esq["x"]), np.array(d_esq["y"]), np.array(d_esq["z"])

    t_dir = np.array(d_dir["t"])
    x_dir, y_dir, z_dir = np.array(d_dir["x"]), np.array(d_dir["y"]), np.array(d_dir["z"])

    t_inicio = max(t_esq[0], t_dir[0])
    t_fim = min(t_esq[-1], t_dir[-1])
    dt = 0.01  # Base uniforme determinística a 100 Hz (Seção 5)
    taxa_amostragem = 100.0

    t_comum = np.arange(t_inicio, t_fim, dt)
    x_esq_i, z_esq_i = np.interp(t_comum, t_esq, x_esq), np.interp(t_comum, t_esq, z_esq)
    x_dir_i, z_dir_i = np.interp(t_comum, t_dir, x_dir), np.interp(t_comum, t_dir, z_dir)

    y_esq_filt = aplicar_filtro(np.interp(t_comum, t_esq, y_esq), 5.0, taxa_amostragem)
    y_dir_filt = aplicar_filtro(np.interp(t_comum, t_dir, y_dir), 5.0, taxa_amostragem)

    # Normalização independente de solo calibrado para cada pé (elimina assimetrias estruturais)
    y_e_norm = y_esq_filt - chao_esq_ref
    y_d_norm = y_dir_filt - chao_dir_ref
    piso_apoio = np.minimum(y_e_norm, y_d_norm)

    # 1. Projeção Vetorial Anteroposterior com o Vetor de Direção do Quadril (Seção 3)
    dx = x_esq_i - x_dir_i
    dz = z_esq_i - z_dir_i

    tem_quad = len(d_quad["t"]) > 10
    if tem_quad and "yaw" in d_quad and len(d_quad["yaw"]) > 10:
        t_q = np.array(d_quad["t"])
        yaw_q = np.array(d_quad["yaw"])
        yaw_i = np.interp(t_comum, t_q, yaw_q)
        rad_yaw = np.radians(yaw_i)
        fx = -np.sin(rad_yaw)
        fz = -np.cos(rad_yaw)
    else:
        # Fallback cinemático pelo vetor de deslocamento do centro dos pés
        cx = (x_esq_i + x_dir_i) / 2.0
        cz = (z_esq_i + z_dir_i) / 2.0
        vx = np.gradient(cx, dt)
        vz = np.gradient(cz, dt)
        v_mag = np.sqrt(vx**2 + vz**2)
        v_mag[v_mag < 0.05] = 1.0
        fx = vx / v_mag
        fz = vz / v_mag

    f_norm = np.sqrt(fx**2 + fz**2)
    f_norm[f_norm == 0] = 1.0
    fx /= f_norm
    fz /= f_norm

    # D_passo(t) projeta exclusivamente no eixo anteroposterior
    d_passo_ap = dx * fx + dz * fz
    d_passo_ap_filt = aplicar_filtro(d_passo_ap, 4.0, taxa_amostragem)

    # Largura da base de suporte lateral (Step Width)
    largura_base_m = np.abs(dx * fz - dz * fx)

    # 2. Piso Dinâmico de Apoio e Referencial Pelvino Fixo
    if tem_quad:
        t_quad = np.array(d_quad["t"])
        y_quad = np.array(d_quad["y"])
        y_quad_filt = aplicar_filtro(np.interp(t_comum, t_quad, y_quad), 5.0, taxa_amostragem)
        chao_medio = (chao_esq_ref + chao_dir_ref) / 2.0 if calibrado else np.minimum(y_esq_filt, y_dir_filt)
        alt_quadril = (y_quad_filt - chao_medio) * 100.0
        posturas = np.where(alt_quadril >= (LIMIAR_QUADRIL_EM_PE * 100), "EM PÉ", "SENTADO")

        if calibrado and dist_qp_e_ref > 0 and dist_qp_d_ref > 0:
            # Pelve como altura de referência fixa: imune a inclinações de cabeça e tronco
            yq_uso = yq_ref if PELVE_FIXA else y_quad_filt
            h_e = dist_qp_e_ref - (yq_uso - y_esq_filt)
            h_d = dist_qp_d_ref - (yq_uso - y_dir_filt)
            piso_apoio = np.minimum(h_e, h_d)
            clearance_esq = np.maximum(0.0, h_e - piso_apoio) * 100.0
            clearance_dir = np.maximum(0.0, h_d - piso_apoio) * 100.0
        else:
            piso_apoio = np.minimum(y_e_norm, y_d_norm)
            clearance_esq = np.maximum(0.0, y_e_norm - piso_apoio) * 100.0
            clearance_dir = np.maximum(0.0, y_d_norm - piso_apoio) * 100.0

        elev_tornoz_esq = clearance_esq
        elev_tornoz_dir = clearance_dir
    else:
        alt_quadril = np.full_like(t_comum, 90.0)
        posturas = np.full_like(t_comum, "EM PÉ", dtype=object)
        piso_apoio = np.minimum(y_e_norm, y_d_norm)
        clearance_esq = np.maximum(0.0, y_e_norm - piso_apoio) * 100.0
        clearance_dir = np.maximum(0.0, y_d_norm - piso_apoio) * 100.0
        elev_tornoz_esq = clearance_esq
        elev_tornoz_dir = clearance_dir

    # Cadeia das pernas (SolarXR) e mescla com o OSC, conforme MODO_ALTURA
    cad_e, cad_d = alturas_cadeia_serie(t_comum, taxa_amostragem)
    pitch_serie = None
    d_peito = dados[TRACKER_PEITO]
    if len(d_peito["t"]) > 10:
        pitch_serie = np.interp(t_comum, np.array(d_peito["t"]), np.array(d_peito["pitch"]))
    osc_e_bruto, osc_d_bruto = clearance_esq.copy(), clearance_dir.copy()
    clearance_esq, clearance_dir = combinar_alturas(clearance_esq, clearance_dir, cad_e, cad_d, pitch_serie)
    serie_altura.clear()
    serie_altura.update({
        "t": t_comum, "osc_esq_cm": osc_e_bruto, "osc_dir_cm": osc_d_bruto,
        "cadeia_esq_cm": cad_e, "cadeia_dir_cm": cad_d, "tronco_graus": pitch_serie,
        "peso_cadeia": (peso_cadeia(pitch_serie) * np.ones_like(t_comum)
                        if MODO_ALTURA == "pipeline" and cad_e is not None else None),
    })

    # Ganho por pé calibrado com a caixa de altura conhecida
    clearance_esq = clearance_esq * ganho_esq
    clearance_dir = clearance_dir * ganho_dir
    elev_tornoz_esq = clearance_esq.copy()
    elev_tornoz_dir = clearance_dir.copy()

    # Aplica zona morta de estabilização contra ruído e tremor no solo
    clearance_esq[clearance_esq < limiar_ruido_chao] = 0.0
    clearance_dir[clearance_dir < limiar_ruido_chao] = 0.0
    serie_altura["altura_esq_cm"] = clearance_esq
    serie_altura["altura_dir_cm"] = clearance_dir
    elev_tornoz_esq[elev_tornoz_esq < limiar_ruido_chao] = 0.0
    elev_tornoz_dir[elev_tornoz_dir < limiar_ruido_chao] = 0.0

    return t_comum, d_passo_ap_filt, largura_base_m, clearance_esq, clearance_dir, elev_tornoz_esq, elev_tornoz_dir, alt_quadril, posturas, taxa_amostragem


# ----------------- DETECTOR MULTIMODAL DE PASSOS (SEÇÃO 4) -----------------
def detectar_passos_multimodal(t_comum, d_passo_ap, taxa_amostragem):
    """
    Detector Multimodal com Compensação de Fase OSC vs SolarXR (Seção 4 da Nota Técnica):
    1. Spikes de aceleração e Jerk nas IMUs dos cadarços (100 Hz).
    2. Compensação de atraso de fase de 25 ms do filtro esférico (slerp) do servidor SlimeVR.
    3. Fusão com inversão de derivada e ZUPT para passos suaves e marcha festinante de Parkinson.
    """
    from scipy.signal import find_peaks
    eventos_impacto = []

    # 1. Picos de Aceleração e Jerk (da/dt) das IMUs
    for lado in ("ESQ", "DIR"):
        t_imu = np.array(dados_imu[lado]["t"])
        acc_imu = np.array(dados_imu[lado]["acc"])

        if len(t_imu) < 30:
            continue

        acc_interp = np.interp(t_comum, t_imu, acc_imu)
        acc_filtrada = aplicar_filtro(acc_interp, 12.0, taxa_amostragem)

        # Cálculo do Jerk (derivada temporal da aceleração)
        jerk = np.gradient(acc_filtrada, 1.0 / taxa_amostragem)

        dist_min_pts = int(taxa_amostragem * DIST_MIN_MESMO_PE_S)

        picos, _ = find_peaks(
            acc_filtrada,
            height=LIMIAR_IMPACTO_ACC,
            distance=dist_min_pts,
            prominence=PROEMINENCIA_IMPACTO
        )

        for p_idx in picos:
            t_impacto_imu = t_comum[p_idx]
            mag_impacto = acc_filtrada[p_idx]

            # Aplica compensação de atraso de fase do filtro OSC (Seção 4: tau_sync = 25 ms)
            t_osc_alinhado = min(t_impacto_imu + TAU_SYNC_OSC, t_comum[-1])
            idx_osc = int(round((t_osc_alinhado - t_comum[0]) * taxa_amostragem))
            idx_osc = max(0, min(len(t_comum) - 1, idx_osc))

            val_passo = d_passo_ap[idx_osc]
            comp_passo = abs(val_passo)

            # Validação fisiológica da direção do passo (Polaridade Anteroposterior):
            # No contato inicial (Heel-Strike), o pé de ataque DEVE estar à frente do quadril/outro pé.
            # Se o impacto ocorreu com o pé posicionado atrás, trata-se da impulsão (Toe-Off), e não de um novo passo.
            if lado == "ESQ" and val_passo < -0.02:
                continue
            if lado == "DIR" and val_passo > 0.02:
                continue

            if comp_passo >= DISTANCIA_MINIMA_PASSO:
                eventos_impacto.append({
                    "tempo": t_impacto_imu,
                    "tempo_osc": t_osc_alinhado,
                    "pe": lado,
                    "impacto_ms2": mag_impacto,
                    "idx": idx_osc,
                    "comprimento_m": comp_passo,
                    "projecao_ap": val_passo
                })

    # 2. Resgate Multimodal por Inversão Cinemática (ZUPT / Extensão Máxima)
    # Detecta cristas positivas (Pé Esquerdo à frente) e vales negativos (Pé Direito à frente)
    intervalo_min = int(taxa_amostragem * DIST_MIN_MESMO_PE_S)
    picos_pos, _ = find_peaks(d_passo_ap, height=DISTANCIA_MINIMA_PASSO, distance=intervalo_min, prominence=0.06)
    vales_neg, _ = find_peaks(-d_passo_ap, height=DISTANCIA_MINIMA_PASSO, distance=intervalo_min, prominence=0.06)

    for g_idx in list(picos_pos) + list(vales_neg):
        t_g = t_comum[g_idx]
        val_g = d_passo_ap[g_idx]
        comp_g = abs(val_g)
        pe_estimado = "ESQ" if val_g > 0 else "DIR"

        tem_impacto_proximo = any(abs(ev["tempo"] - t_g) <= 0.22 for ev in eventos_impacto)
        if not tem_impacto_proximo:
            eventos_impacto.append({
                "tempo": t_g,
                "tempo_osc": t_g,
                "pe": f"{pe_estimado}*",  # * indica detectado por fusão cinemática suave
                "impacto_ms2": 0.0,
                "idx": g_idx,
                "comprimento_m": comp_g,
                "projecao_ap": val_g
            })

    if len(eventos_impacto) == 0:
        return np.array([]), np.array([]), np.array([]), np.array([]), np.array([])

    eventos_impacto.sort(key=lambda ev: ev["tempo"])

    # Filtro de coerência fisiológica e eliminação de rebotes (Toe-off / vibração do cadarço)
    eventos_filtrados = []
    for ev in eventos_impacto:
        if not eventos_filtrados:
            eventos_filtrados.append(ev)
            continue

        ult = eventos_filtrados[-1]
        dt = ev["tempo"] - ult["tempo"]
        pe_atual = ev["pe"].replace("*", "")
        pe_ult = ult["pe"].replace("*", "")

        # Picos sucessivos do mesmo pé em intervalo < DIST_MIN_MESMO_PE_S (rebote de cadarço / foot-flat)
        if pe_atual == pe_ult and dt < DIST_MIN_MESMO_PE_S:
            # Preserva o impacto de maior magnitude dentro do mesmo ciclo de passada
            if ev["impacto_ms2"] > ult["impacto_ms2"]:
                eventos_filtrados[-1] = ev
            continue

        # Passos alternados em intervalo biologicamente impossível (< DIST_MIN_PASSOS_GERAL_S)
        if pe_atual != pe_ult and dt < DIST_MIN_PASSOS_GERAL_S:
            continue

        eventos_filtrados.append(ev)

    tempos = np.array([ev["tempo"] for ev in eventos_filtrados])
    comprimentos = np.array([ev["comprimento_m"] for ev in eventos_filtrados])
    impactos = np.array([ev["impacto_ms2"] for ev in eventos_filtrados])
    pes = np.array([ev["pe"] for ev in eventos_filtrados])
    projecoes = np.array([ev["projecao_ap"] for ev in eventos_filtrados])

    return tempos, comprimentos, impactos, pes, projecoes


def calcular_alturas_passos(t, tempos_impactos, pes_impactos, elevacao_esq_cm, elevacao_dir_cm):
    """Calcula a altura máxima (clearance em cm) atingida no balanço anterior ao impacto."""
    alturas = []
    for i, (t_imp, pe) in enumerate(zip(tempos_impactos, pes_impactos)):
        t_inicio = tempos_impactos[i - 1] if i > 0 else max(t[0], t_imp - 0.7)
        mask = (t >= t_inicio) & (t <= t_imp)
        if np.any(mask):
            if "DIR" in pe:
                alturas.append(np.max(elevacao_dir_cm[mask]))
            elif "ESQ" in pe:
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
def gerar_relatorio_e_graficos(t_passo, d_passo_ap, larg_base_m,
                              clear_esq_cm, clear_dir_cm, torn_esq_cm, torn_dir_cm,
                              alt_quadril_cm, posturas,
                              tempos_impactos, dist_impactos, impactos_ms2,
                              clearances_passos_cm, tornozelos_passos_cm,
                              pes_passos, projecoes_ap, mascara_validos):
    print("\n\n" + "=" * 90)
    print("      RELATÓRIO CLÍNICO & BIOMECÂNICO: ARQUITETURA DE FUSÃO SOLARXR + SLIMEVR")
    print("=" * 90)

    n_total = len(dist_impactos)
    n_outliers = np.sum(~mascara_validos)
    passos_validos = dist_impactos[mascara_validos]
    tempos_validos = tempos_impactos[mascara_validos]
    clearances_validos = clearances_passos_cm[mascara_validos]
    tornozelos_validos = tornozelos_passos_cm[mascara_validos]
    pes_validos = pes_passos[mascara_validos]

    duracao = t_passo[-1]
    print(f"Duração da sessão:       {duracao:.1f} s")
    print(f"Passos detectados:       {n_total} (Válidos: {len(passos_validos)} | Outliers MAD: {n_outliers})")

    # 1. Estatística Postural
    dt_amostra = np.mean(np.diff(t_passo)) if len(t_passo) > 1 else 0.01
    tempo_em_pe = np.sum(posturas == "EM PÉ") * dt_amostra
    tempo_sentado = np.sum(posturas == "SENTADO") * dt_amostra
    pct_em_pe = (tempo_em_pe / duracao) * 100 if duracao > 0 else 0
    pct_sentado = (tempo_sentado / duracao) * 100 if duracao > 0 else 0

    print(f"\n--- 1. Classificação Postural (Referencial Pelvino - Eq. 1.3) ---")
    print(f"  Tempo Em Pé:   {tempo_em_pe:5.1f} s ({pct_em_pe:5.1f}%)")
    print(f"  Tempo Sentado: {tempo_sentado:5.1f} s ({pct_sentado:5.1f}%)")
    if np.any(alt_quadril_cm > 0):
        print(f"  Altura Média Quadril-Solo: {np.mean(alt_quadril_cm):.1f} cm (± {np.std(alt_quadril_cm):.1f} cm)")

    # 2. Tronco e Cabeça
    tem_peito = len(dados[TRACKER_PEITO]["t"]) >= 10
    tronco_nos_passos = []
    if tem_peito:
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        tronco_nos_passos = np.interp(tempos_impactos, t_peito, pitch_peito)
        med_tronco = np.mean(pitch_peito)
        print(f"\n--- 2. Postura do Tronco (Peito - Tracker {TRACKER_PEITO}) ---")
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
        print(f"\n--- 3. Atenção Visual e Orientação da Cabeça (VMC) ---")
        print(f"  Inclinação média: {np.mean(pitch_cabeca):+.1f}° | Olhar Frente: {pct_neutro:.1f}% do tempo")
    else:
        cabeca_nos_passos = [0.0] * n_total

    # 3. Parâmetros Espaciais da Marcha
    print(f"\n--- 4. Parâmetros Espaciais (Projeção Vetorial Anteroposterior - Seção 3) ---")
    if len(passos_validos) > 0:
        media_passo = np.mean(passos_validos)
        desvio_passo = np.std(passos_validos)
        media_clear = np.mean(clearances_validos)
        desvio_clear = np.std(clearances_validos)
        media_torn = np.mean(tornozelos_validos)
        desvio_torn = np.std(tornozelos_validos)
        media_base = np.mean(larg_base_m) * 100.0

        print(f"  Comprimento médio da passada: {media_passo:.3f} m (± {desvio_passo:.3f} m)")
        print(f"  Largura média da base (X):    {media_base:.1f} cm (Base de suporte lateral)")
        print(f"  Clearance Real do Solado:     {media_clear:.1f} cm (± {desvio_clear:.1f} cm) [Folga solo-sapato: 1.5 - 4.5 cm]")
        print(f"  Elevação Tornozelo / Flexão:  {media_torn:.1f} cm (± {desvio_torn:.1f} cm) [Encurtamento joelho: 10 - 18 cm]")

        mask_esq = np.array(["ESQ" in p for p in pes_validos])
        mask_dir = np.array(["DIR" in p for p in pes_validos])
        n_esq = np.sum(mask_esq)
        n_dir = np.sum(mask_dir)
        d_e = np.mean(passos_validos[mask_esq]) if n_esq > 0 else 0.0
        d_d = np.mean(passos_validos[mask_dir]) if n_dir > 0 else 0.0
        cl_e = np.mean(clearances_validos[mask_esq]) if n_esq > 0 else 0.0
        cl_d = np.mean(clearances_validos[mask_dir]) if n_dir > 0 else 0.0
        tn_e = np.mean(tornozelos_validos[mask_esq]) if n_esq > 0 else 0.0
        tn_d = np.mean(tornozelos_validos[mask_dir]) if n_dir > 0 else 0.0

        print(f"  Pé ESQ ({n_esq} passos): {d_e:.3f} m | Solado: {cl_e:.1f} cm | Tornozelo: {tn_e:.1f} cm")
        print(f"  Pé DIR ({n_dir} passos): {d_d:.3f} m | Solado: {cl_d:.1f} cm | Tornozelo: {tn_d:.1f} cm")
        if d_e > 0 and d_d > 0:
            simetria = (1.0 - abs(d_e - d_d) / ((d_e + d_d) / 2.0)) * 100.0
            print(f"  Índice de Simetria do Passo:  {simetria:.1f}%")

        if len(tempos_validos) >= 2:
            cadencia = 60.0 / np.mean(np.diff(tempos_validos))
            print(f"  Cadência estimada:            {cadencia:.1f} passos/min")

        # 4. Biomarcadores Específicos para Doença de Parkinson (Seção 5 da Nota Técnica)
        cv_passo = (desvio_passo / media_passo) * 100.0 if media_passo > 0 else 0.0
        n_microbasia = np.sum(passos_validos < 0.30)
        pct_microbasia = (n_microbasia / len(passos_validos)) * 100.0
        n_baixo_clearance = np.sum(clearances_validos < 2.0)
        pct_baixo_clearance = (n_baixo_clearance / len(clearances_validos)) * 100.0

        print(f"\n--- 5. Avaliação Clínica para Doença de Parkinson (Biomarcadores de Risco) ---")
        if cv_passo > 10.0:
            alerta_cv = "⚠ ALTO (Elevado Risco de Quedas)"
        elif cv_passo >= 6.0:
            alerta_cv = "⚡ MODERADO (Atenção ao Equilíbrio)"
        else:
            alerta_cv = "✔ BAIXO (Padrão Regular e Estável)"
        print(f"  Variabilidade do Passo (CV): {cv_passo:5.1f}% [{alerta_cv}]")
        print(f"  Taxa de Microbasia (< 30cm): {n_microbasia}/{len(passos_validos)} ({pct_microbasia:.1f}%)" + (" ⚠ Indicativo de passos curtos" if pct_microbasia > 30 else ""))
        print(f"  Risco de Arraste (Solado < 2.0cm): {n_baixo_clearance}/{len(clearances_validos)} ({pct_baixo_clearance:.1f}%)" + (" ⚠ Risco de tropeço / Shuffling" if pct_baixo_clearance > 25 else " ✔ Clearance adequado"))
    else:
        media_passo = 0.0

    # 5. Tabela Integrada
    print("\n--- Tabela Integrada Passo a Passo (Sincronia Heel-Strike SolarXR + Projeção Anteroposterior) ---")
    print(f"{'#':<4} {'Tempo(s)':<9} {'Pé':<6} {'Impacto(m/s²)':<15} {'Comprim.(m)':<13} {'Solado(cm)':<12} {'Tornoz(cm)':<12} {'Tronco':<10} {'Olhar':<14} {'Status'}")
    print("-" * 110)
    for i, (t_p, d_p, imp_p, cl_p, tn_p, pe_p, tr_p, cb_p) in enumerate(zip(
        tempos_impactos, dist_impactos, impactos_ms2, clearances_passos_cm, tornozelos_passos_cm, pes_passos, tronco_nos_passos, cabeca_nos_passos
    )):
        valido = mascara_validos[i]
        status = "Válido" if valido else "⚠ Outlier"
        _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
        tr_str = f"{tr_p:+.1f}°" if tem_peito else "N/A"
        print(f"{i+1:<4} {t_p:<9.2f} {pe_p:<6} {imp_p:<15.1f} {d_p:<13.3f} {cl_p:<12.1f} {tn_p:<12.1f} {tr_str:<10} {olhar_lbl:<14} {status}")
    print("=" * 110)

    # 6. Salva CSV Unificado
    with open(NOME_ARQUIVO_PASSOS, mode='w', newline='', encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow([
            "Passo", "Tempo_HeelStrike_s", "Pe_Impacto", "Impacto_IMU_ms2", "Comprimento_m",
            "Clearance_Solado_cm", "Elev_Tornozelo_cm", "Tronco_graus", "Cabeca_Pitch_graus", "Estado_Olhar", "Outlier"
        ])
        for i, (t_p, d_p, imp_p, cl_p, tn_p, pe_p, tr_p, cb_p) in enumerate(zip(
            tempos_impactos, dist_impactos, impactos_ms2, clearances_passos_cm, tornozelos_passos_cm, pes_passos, tronco_nos_passos, cabeca_nos_passos
        )):
            _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
            escritor.writerow([
                i+1, f"{t_p:.3f}", pe_p, f"{imp_p:.2f}", f"{d_p:.3f}", f"{cl_p:.2f}", f"{tn_p:.2f}",
                f"{tr_p:.2f}", f"{cb_p:.2f}", olhar_lbl, "NAO" if mascara_validos[i] else "SIM"
            ])
    print(f"\nResultados consolidados salvos em '{NOME_ARQUIVO_PASSOS}'.")

    # ================= GRÁFICOS INTEGRADOS =================
    import matplotlib.pyplot as plt
    tem_imu_plot = len(dados_imu["ESQ"]["t"]) > 20 or len(dados_imu["DIR"]["t"]) > 20
    painéis_extras = (1 if tem_imu_plot else 0) + (1 if tem_peito else 0) + (1 if tem_cabeca else 0) + 1
    total_subplots = 3 + painéis_extras
    fig, axes = plt.subplots(total_subplots, 1, figsize=(14, 2.5 * total_subplots), sharex=False)

    idx_ax = 0

    # Painel 1: Projeção Vetorial Anteroposterior (cruzando o zero!)
    ax_dist = axes[idx_ax]
    ax_dist.plot(t_passo, d_passo_ap, color='steelblue', label='Projeção Vetorial Anteroposterior D_passo(t)', linewidth=1.5)
    ax_dist.axhline(0, color='gray', linestyle=':', label='Cruzamento das Pernas (Mid-Stance = 0m)')

    for i in range(len(tempos_impactos)):
        t_hs = tempos_impactos[i]
        d_hs = dist_impactos[i]
        proj_hs = projecoes_ap[i]
        pe_hs = pes_passos[i]
        val = mascara_validos[i]
        cor = "green" if "DIR" in pe_hs else "darkorange"
        marcador = "v" if val else "x"
        ax_dist.scatter(t_hs, proj_hs, color=cor, s=80, zorder=5, marker=marcador)
        if val:
            ax_dist.annotate(f"{d_hs:.2f}m", (t_hs, proj_hs + (0.04 if proj_hs >= 0 else -0.06)),
                            fontsize=8, ha="center", fontweight="bold", color=cor)

    ax_dist.set_ylabel('Avanço (m)')
    ax_dist.set_title('1. Projeção Vetorial Anteroposterior (D_passo cruzando zero no Mid-Stance — Seção 3)', fontweight="bold")
    ax_dist.legend(loc='upper right', fontsize=8)
    ax_dist.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 2: Acelerações das IMUs e Sincronia de Impacto
    if tem_imu_plot:
        ax_imu = axes[idx_ax]
        t_ie = np.array(dados_imu["ESQ"]["t"])
        a_ie = np.array(dados_imu["ESQ"]["acc"])
        t_id = np.array(dados_imu["DIR"]["t"])
        a_id = np.array(dados_imu["DIR"]["acc"])

        if len(t_ie) > 0:
            ax_imu.plot(t_ie, a_ie, color='darkorange', alpha=0.75, label='Aceleração Cadarço ESQ (m/s²)')
        if len(t_id) > 0:
            ax_imu.plot(t_id, a_id, color='green', alpha=0.75, label='Aceleração Cadarço DIR (m/s²)')

        ax_imu.axhline(LIMIAR_IMPACTO_ACC, color='red', linestyle=':', label=f'Limiar Sensível Parkinson ({LIMIAR_IMPACTO_ACC} m/s²)')
        ax_imu.set_ylabel('Acc (m/s²)')
        ax_imu.set_title('2. Aceleração Física das IMUs no Cadarço (SolarXR) — Timing de Impacto Mecânico', fontweight="bold")
        ax_imu.legend(loc='upper right', fontsize=8)
        ax_imu.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 3: Altura Real dos Pés (Folga do Solado vs Elevação do Tornozelo)
    ax_alt = axes[idx_ax]
    ax_alt.plot(t_passo, clear_esq_cm, color='dodgerblue', label='Folga Solado ESQ (cm) [Clearance Real]', linewidth=1.8)
    ax_alt.plot(t_passo, clear_dir_cm, color='darkorange', label='Folga Solado DIR (cm) [Clearance Real]', linewidth=1.8)
    ax_alt.plot(t_passo, torn_esq_cm, color='dodgerblue', linestyle='--', alpha=0.45, label='Elevação Tornozelo ESQ (cm)')
    ax_alt.plot(t_passo, torn_dir_cm, color='darkorange', linestyle='--', alpha=0.45, label='Elevação Tornozelo DIR (cm)')
    ax_alt.axhspan(0, 2.0, color='tomato', alpha=0.15, label='Zona Risco de Tropeço (Solado < 2.0 cm)')
    ax_alt.set_ylabel('Altura (cm)')
    ax_alt.set_title('3. Folga Real do Solado ao Solo (Linhas Sólidas) vs Elevação Articular do Tornozelo (Tracejadas)', fontweight="bold")
    ax_alt.legend(loc='upper right', fontsize=8)
    ax_alt.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 4: Quadril e Postura
    ax_quad = axes[idx_ax]
    ax_quad.plot(t_passo, alt_quadril_cm, color='teal', label='Altura Quadril ao Solo (cm)', linewidth=1.5)
    ax_quad.axhline(LIMIAR_QUADRIL_EM_PE * 100, color='red', linestyle='--', label=f'Limiar Sentado/Em Pé ({LIMIAR_QUADRIL_EM_PE*100:.0f} cm)')
    ax_quad.fill_between(t_passo, 0, LIMIAR_QUADRIL_EM_PE * 100, color='orange', alpha=0.10, label='Zona Sentado')
    ax_quad.fill_between(t_passo, LIMIAR_QUADRIL_EM_PE * 100, 120, color='lightgreen', alpha=0.10, label='Zona Em Pé')
    ax_quad.set_ylabel('Quadril (cm)')
    ax_quad.set_title('4. Altura do Quadril e Classificação Postural (Sentado vs Em Pé)', fontweight="bold")
    ax_quad.legend(loc='upper right', fontsize=8, ncol=2)
    ax_quad.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 5: Tronco
    if tem_peito:
        ax_tr = axes[idx_ax]
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        ax_tr.plot(t_peito, pitch_peito, color='purple', label='Inclinação Tronco (Pitch)')
        ax_tr.axhline(0, color='gray', linestyle=':', label='Vertical (0°)')
        ax_tr.set_ylabel('Tronco (°)')
        ax_tr.set_title('5. Postura do Tronco (Avaliação de Camptocormia)', fontweight="bold")
        ax_tr.legend(loc='upper right', fontsize=8)
        ax_tr.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 6: Cabeça / Olhar
    if tem_cabeca:
        ax_cb = axes[idx_ax]
        t_cb = np.array(dados["cabeca"]["t"])
        pitch_cb = np.array(dados["cabeca"]["pitch"])
        ax_cb.plot(t_cb, pitch_cb, color='black', linewidth=1.2, label='Olhar / Cabeça (Pitch)')
        ax_cb.axhline(0, color='gray', linestyle=':')
        ax_cb.axhspan(LIMIAR_BAIXO, LIMIAR_CIMA, color='mediumseagreen', alpha=0.15, label='Frente')
        ax_cb.axhspan(-90, LIMIAR_MUITO_BAIXO, color='tomato', alpha=0.20, label='Chão')
        ax_cb.set_ylabel('Cabeça (°)')
        ax_cb.set_title('6. Atenção Visual (Inclinação da Cabeça)', fontweight="bold")
        ax_cb.legend(loc='upper right', fontsize=8)
        ax_cb.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 7: Barras Passo a Passo
    ax_bar = axes[idx_ax]
    x_indices = np.arange(1, n_total + 1)
    largura = 0.38
    ax_bar.bar(x_indices - largura/2, dist_impactos, width=largura, color='steelblue', label='Comprimento (m)')
    ax_bar.axhline(0.30, color='goldenrod', linestyle='--', alpha=0.8, label='Limiar Microbasia (30 cm)')
    ax_bar.set_ylabel('Comprimento (m)', color='steelblue')
    ax_bar.set_xlabel('Número do Passo')
    ax_bar.set_title('7. Comprimento de Passo vs Altura Real com Biomarcadores de Parkinson', fontweight="bold")
    ax_bar.set_xticks(x_indices)

    ax_bar_twin = ax_bar.twinx()
    ax_bar_twin.bar(x_indices + largura/2, clearances_passos_cm, width=largura, color='coral', label='Clearance Solado (cm)')
    ax_bar_twin.set_ylabel('Clearance Solado (cm)', color='coral')
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


def _clearance_cadeia_cm(quats):
    """Altura (cm) de cada pé acima do outro pela cadeia das pernas, ou None se indisponível."""
    if cadeia is None or any(quats.get(seg) is None for seg in cadeia_pernas.SEGMENTOS):
        return None
    dif = cadeia.dif_vertical({seg: np.array([quats[seg]]) for seg in cadeia_pernas.SEGMENTOS})
    e, d = cadeia_pernas.CadeiaPernas.alturas_pes(dif)
    return float(e[0]) * 100.0, float(d[0]) * 100.0


def peso_cadeia(pitch_tronco):
    """Peso da cadeia no modo "pipeline": 0 com o tronco reto, 1 com o tronco inclinado."""
    if pitch_tronco is None:
        return 0.0
    faixa = max(1e-6, TRONCO_INCLINADO_GRAUS - TRONCO_RETO_GRAUS)
    inclinacao = np.abs(np.asarray(pitch_tronco, dtype=float) - pitch_tronco_ref)
    return np.clip((inclinacao - TRONCO_RETO_GRAUS) / faixa, 0.0, 1.0)


def combinar_alturas(osc_e, osc_d, cad_e, cad_d, pitch_tronco=None):
    """Junta as alturas do OSC e da cadeia conforme MODO_ALTURA (aceita escalares ou arrays)."""
    if cad_e is None or MODO_ALTURA == "osc":
        if MODO_ALTURA == "pipeline":
            g = ganhos_metodo["osc"]
            return osc_e * g[0], osc_d * g[1]
        return osc_e, osc_d
    if MODO_ALTURA == "cadeia":
        return cad_e, cad_d
    if MODO_ALTURA == "pipeline":
        go, gc = ganhos_metodo["osc"], ganhos_metodo["cadeia"]
        w = peso_cadeia(pitch_tronco)
        return ((1.0 - w) * osc_e * go[0] + w * cad_e * gc[0],
                (1.0 - w) * osc_d * go[1] + w * cad_d * gc[1])
    return (osc_e + cad_e) / 2.0, (osc_d + cad_d) / 2.0


def _clearance_bruto_cm(p_esq, p_dir, p_quad, quats=None, pitch_tronco=None):
    """Folga bruta (cm) de cada pé no modo MODO_ALTURA (sem o ganho global da caixa)."""
    osc_e, osc_d = _clearance_osc_cm(p_esq, p_dir, p_quad)
    cad = _clearance_cadeia_cm(quats if quats is not None else quat_atual)
    if cad is None:
        return combinar_alturas(osc_e, osc_d, None, None, pitch_tronco)
    return combinar_alturas(osc_e, osc_d, cad[0], cad[1], pitch_tronco)


def _clearance_osc_cm(p_esq, p_dir, p_quad):
    """Folga bruta (cm) de cada pé pelo OSC (diferença entre os pés)."""
    if p_quad is not None and calibrado and dist_qp_e_ref > 0 and dist_qp_d_ref > 0:
        yq_uso = yq_ref if PELVE_FIXA else p_quad[1]
        h_e = dist_qp_e_ref - (yq_uso - p_esq[1])
        h_d = dist_qp_d_ref - (yq_uso - p_dir[1])
    else:
        h_e = p_esq[1] - chao_esq_ref
        h_d = p_dir[1] - chao_dir_ref
    piso = min(h_e, h_d)
    return max(0.0, (h_e - piso) * 100.0), max(0.0, (h_d - piso) * 100.0)


def calibrar_ganho_caixa():
    """Pede para pisar na caixa com cada pé e calcula o fator de escala por pé."""
    global ganho_esq, ganho_dir, ganhos_metodo
    print("\n" + "=" * 90)
    print(f"   📦 ETAPA 2: CALIBRAÇÃO DE ALTURA COM CAIXA DE {ALTURA_CAIXA_CM:.1f} CM")
    print("=" * 90)

    resultados = {}
    for lado, idx in (("ESQUERDO", 0), ("DIREITO", 1)):
        print(f"\n👉 Coloque o pé {lado} em cima da caixa (o outro pé fica no chão) e fique parado.")
        for cont in (5, 4, 3, 2, 1):
            print(f"    Amostragem em {cont}s...   ", end="\r", flush=True)
            time.sleep(1.0)
        print("    >>> MEDINDO... fique imóvel! <<<           ", flush=True)

        amostras = []
        amostras_osc, amostras_cad = [], []
        t_fim = time.time() + TEMPO_CAIXA_S
        while time.time() < t_fim and rodando:
            with lock:
                pe = pos_atual[TORNOZELO_ESQ]
                pd = pos_atual[TORNOZELO_DIR]
                pq = pos_atual[TRACKER_QUADRIL]
                qs = dict(quat_atual)
            if pe is not None and pd is not None:
                amostras.append(_clearance_bruto_cm(pe, pd, pq, qs)[idx])
                amostras_osc.append(_clearance_osc_cm(pe, pd, pq)[idx])
                cad = _clearance_cadeia_cm(qs)
                if cad is not None:
                    amostras_cad.append(cad[idx])
            time.sleep(0.01)

        if MODO_ALTURA == "pipeline":
            # Ganho próprio de cada método; o ganho global fica em 1
            for metodo, amo in (("osc", amostras_osc), ("cadeia", amostras_cad)):
                med = float(np.median(amo)) if amo else 0.0
                g = max(0.4, min(2.5, ALTURA_CAIXA_CM / med)) if med >= 2.0 else 1.0
                atual = list(ganhos_metodo[metodo])
                atual[idx] = g
                ganhos_metodo[metodo] = tuple(atual)
                print(f"    Pé {lado} ({metodo}): medido {med:.1f} cm -> ganho {g:.3f}")
            resultados[idx] = 1.0
            print("    Volte o pé para o chão.")
            time.sleep(2.0)
            continue

        medido = float(np.median(amostras)) if amostras else 0.0
        if medido < 2.0:
            print(f"    [Aviso] Pé {lado}: só {medido:.1f} cm medidos. Ganho mantido em 1.0.")
            ganho = 1.0
        else:
            ganho = max(0.4, min(2.5, ALTURA_CAIXA_CM / medido))
            print(f"    Pé {lado}: medido {medido:.1f} cm -> ganho {ganho:.3f}")
        resultados[idx] = ganho
        print("    Volte o pé para o chão.")
        time.sleep(2.0)

    ganho_esq, ganho_dir = resultados[0], resultados[1]
    print(f"\n  [✓] Ganhos: ESQ={ganho_esq:.3f} | DIR={ganho_dir:.3f}")
    print("=" * 90)


def calibrar_cadeia(amostras_pos, amostras_quat):
    """Monta a cadeia das pernas com as orientações e posições do período parado."""
    if MODO_ALTURA == "osc":
        return None
    faltando = [seg for seg, qs in amostras_quat.items() if len(qs) < 10]
    if faltando:
        print(f"  [Aviso] Sem orientação SolarXR de {', '.join(faltando)}: altura só pelo OSC.")
        return None
    comp = None
    if all(len(v) >= 10 for v in amostras_pos.values()):
        med = {k: np.median(np.array(v), axis=0) for k, v in amostras_pos.items()}
        comp = cadeia_pernas.comprimentos_de_osc(med["quad"], med["joelho_e"], med["joelho_d"], med["pe_e"], med["pe_d"])
    nova = cadeia_pernas.CadeiaPernas(comp)
    nova.calibrar(amostras_quat)
    c = nova.comprimentos
    print(f"      • Cadeia pernas  : coxa E {c['LEFT_UPPER_LEG']*100:4.1f} / canela E {c['LEFT_LOWER_LEG']*100:4.1f} | "
          f"coxa D {c['RIGHT_UPPER_LEG']*100:4.1f} / canela D {c['RIGHT_LOWER_LEG']*100:4.1f} cm (modo: {MODO_ALTURA})")
    return nova


def executar_calibracao_inicial():
    """
    Executa a calibração estática inicial com os dois pés apoiados no chão.
    Calcula as referências pélvicas, equaliza o offset estrutural dos cadarços
    e define a zona morta para que ambos os pés marquem estritamente 0.0 cm no solo.
    """
    global dist_qp_e_ref, dist_qp_d_ref, offset_calib_pernas
    global chao_esq_ref, chao_dir_ref, limiar_ruido_chao, calibrado
    global L_coxa_pe_e_ref, L_coxa_pe_d_ref, yq_ref
    global tempo_inicial, cadeia, pitch_tronco_ref

    print("\n" + "=" * 90)
    print("   🎯 ETAPA 1: CALIBRAÇÃO ESTÁTICA DOS PÉS E CHÃO (NIVELAMENTO EM 0.0 CM)")
    print("=" * 90)
    print("   1. Fique em pé na sua postura natural, com os DOIS PÉS apoiados no chão.")
    print("   2. Não levante os pés durante a calibração para registrar a referência exata do solo.")
    print("-" * 90)

    # 1. Aguarda trackers essenciais (Pés e Quadril)
    print("[*] Aguardando sinal dos trackers de Pés (2, 3) e Quadril (1)...", end="", flush=True)
    while rodando:
        with lock:
            pe = pos_atual[TORNOZELO_ESQ]
            pd = pos_atual[TORNOZELO_DIR]
            pq = pos_atual[TRACKER_QUADRIL]
        if pe is not None and pd is not None and pq is not None:
            break
        time.sleep(0.1)
    print(" [CONECTADOS!]\n", flush=True)

    # 2. Contagem para posicionamento do usuário (não-bloqueante)
    try:
        import msvcrt
        tem_msvcrt = True
    except ImportError:
        tem_msvcrt = False

    print("👉 Posicione-se com ambos os pés no chão.")
    print("   A calibração iniciará em 5 segundos (ou pressione [ENTER] para iniciar agora):\n")
    t_limite = time.time() + TEMPO_PREPARACAO_S
    while time.time() < t_limite and rodando:
        restante = int(math.ceil(t_limite - time.time()))
        print(f"   ⏳ Iniciando em {restante}s... (pressione [ENTER] para acelerar)   ", end="\r", flush=True)
        if tem_msvcrt and msvcrt.kbhit():
            msvcrt.getch()
            break
        time.sleep(0.1)

    print("\n\n[*] Calibrando em 3 segundos... Fique parado com os dois pés no chão!")
    for cont in (3, 2, 1):
        print(f"    Iniciando amostragem em {cont}s...", end="\r", flush=True)
        time.sleep(1.0)
    print("    >>> COLETANDO REFERÊNCIA BASAL... Fique imóvel! <<<               ", flush=True)

    # Coleta 3 segundos de amostras estáticas
    amostras_ye = []
    amostras_yd = []
    amostras_yq = []
    amostras_de = []
    amostras_dd = []
    amostras_lce = []
    amostras_lcd = []
    amostras_pos = {"quad": [], "pe_e": [], "pe_d": [], "joelho_e": [], "joelho_d": []}
    amostras_quat = {seg: [] for seg in cadeia_pernas.SEGMENTOS}

    t_fim = time.time() + TEMPO_CALIB_ESTATICA_S
    while time.time() < t_fim and rodando:
        with lock:
            pe = pos_atual[TORNOZELO_ESQ]
            pd = pos_atual[TORNOZELO_DIR]
            pq = pos_atual[TRACKER_QUADRIL]
            pce = pos_atual.get(TRACKER_COXA_ESQ)
            pcd = pos_atual.get(TRACKER_COXA_DIR)
            qs = dict(quat_atual)
        if pe is not None and pd is not None and pq is not None:
            amostras_ye.append(pe[1])
            amostras_yd.append(pd[1])
            amostras_yq.append(pq[1])
            amostras_de.append(pq[1] - pe[1])
            amostras_dd.append(pq[1] - pd[1])
            if pce is not None and pcd is not None:
                amostras_lce.append(pce[1] - pe[1])
                amostras_lcd.append(pcd[1] - pd[1])
                amostras_pos["quad"].append(pq)
                amostras_pos["pe_e"].append(pe)
                amostras_pos["pe_d"].append(pd)
                amostras_pos["joelho_e"].append(pce)
                amostras_pos["joelho_d"].append(pcd)
            for seg, q in qs.items():
                if q is not None:
                    amostras_quat[seg].append(q)
        time.sleep(0.01)

    if len(amostras_de) >= 20 and len(amostras_dd) >= 20:
        dist_qp_e_ref = float(np.median(amostras_de))
        dist_qp_d_ref = float(np.median(amostras_dd))
        chao_esq_ref = float(np.median(amostras_ye))
        chao_dir_ref = float(np.median(amostras_yd))
        yq_ref = float(np.median(amostras_yq))
        offset_calib_pernas = 0.0

        if len(amostras_lce) >= 20 and len(amostras_lcd) >= 20:
            L_coxa_pe_e_ref = float(np.median(amostras_lce))
            L_coxa_pe_d_ref = float(np.median(amostras_lcd))

        # Ruído basal (desvio padrão)
        sigma_e = float(np.std(amostras_ye)) * 100.0
        sigma_d = float(np.std(amostras_yd)) * 100.0
        limiar_ruido_chao = max(1.0, 3.0 * max(sigma_e, sigma_d))

        calibrado = True
        cadeia = calibrar_cadeia(amostras_pos, amostras_quat)
        # Postura natural do tronco: o pipeline mede a inclinação a partir dela
        tp, pp = dados[TRACKER_PEITO]["t"], dados[TRACKER_PEITO]["pitch"]
        if len(tp) >= 10:
            tp, pp = np.array(tp), np.array(pp)
            pitch_tronco_ref = float(np.median(pp[tp >= tp[-1] - 3.0]))
            print(f"      • Tronco natural : {pitch_tronco_ref:+.1f}° (referência da inclinação)")
        print("=" * 90)
        print("  [✓] CALIBRAÇÃO CONCLUÍDA COM SUCESSO!")
        print(f"      • Ref. Solo E    : {chao_esq_ref:+.3f} m  |  D : {chao_dir_ref:+.3f} m (ambos nivelados a 0.0 cm)")
        if L_coxa_pe_e_ref > 0 and L_coxa_pe_d_ref > 0:
            print(f"      • Comp. Coxa-Pé E: {L_coxa_pe_e_ref*100:5.1f} cm  |  D : {L_coxa_pe_d_ref*100:5.1f} cm (Desacoplamento Pélvico ATIVO)")
        print(f"      • Ref. Pélvica E : {dist_qp_e_ref*100:5.1f} cm  |  D : {dist_qp_d_ref*100:5.1f} cm")
        print(f"      • Zona Morta Solo: {limiar_ruido_chao:5.1f} cm (anti-ruído/tremor)")
        print("=" * 90)
    else:
        print("\n[Aviso] Poucas amostras na calibração. Mantendo limiares padrão.")

    if calibrado and CALIBRAR_CAIXA:
        calibrar_ganho_caixa()

    # 3. Zera os dados anteriores para que o teste de marcha comece limpo a partir de agora
    with lock:
        for t_id in dados:
            dados[t_id] = {k: [] for k in dados[t_id]}
        for l in dados_imu:
            dados_imu[l] = {k: [] for k in dados_imu[l]}
        for seg in dados_quat:
            dados_quat[seg] = {"t": [], "q": []}
        tempo_inicial = time.time()


def _aviso_sonoro(vezes=1):
    try:
        import winsound
        for _ in range(vezes):
            winsound.Beep(880, 180)
    except Exception:
        print("\a" * vezes, end="", flush=True)


def executar_roteiro():
    """Conduz as etapas do ROTEIRO com tempo fixo e guarda o início e o fim de cada uma."""
    global etapa_atual, roteiro_terminou
    total = sum(seg for _, seg, _ in ROTEIRO)
    print(f"\n   📋 Roteiro guiado: {len(ROTEIRO)} etapas, {total:.0f} s no total.\n")
    for i, (nome, segundos, instrucao) in enumerate(ROTEIRO):
        if not rodando:
            return
        proxima = ROTEIRO[i + 1][2] if i + 1 < len(ROTEIRO) else "fim da gravação"
        print(f"\n\n   ▶ [{i + 1}/{len(ROTEIRO)}] {instrucao}  ({segundos:.0f} s)")
        print(f"     depois: {proxima}\n")
        _aviso_sonoro(2 if nome.startswith(("caminhada", "caixa", "inclinar")) else 1)
        t0 = time.time() - tempo_inicial
        fim = time.time() + segundos
        while rodando and time.time() < fim:
            etapa_atual = f"{nome} {fim - time.time():4.1f}s"
            time.sleep(0.05)
        fases_sessao.append((nome, t0, time.time() - tempo_inicial))
    etapa_atual = ""
    _aviso_sonoro(3)
    print("\n\n   ✅ Roteiro concluído. Encerrando a gravação...")
    roteiro_terminou = True


def salvar_fases():
    if not fases_sessao:
        return
    try:
        with open(NOME_ARQUIVO_FASES, mode="w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["etapa", "t0", "t1"])
            for nome, t0, t1 in fases_sessao:
                w.writerow([nome, f"{t0:.3f}", f"{t1:.3f}"])
        print(f"Etapas do roteiro salvas em {NOME_ARQUIVO_FASES}")
    except Exception as e:
        print(f"Não foi possível salvar as etapas: {e}")


def salvar_dados_brutos():
    """Salva a altura de cada pé ao longo do tempo (CSV) e as entradas brutas (JSON)."""
    import json
    try:
        colunas = ["t", "altura_esq_cm", "altura_dir_cm", "osc_esq_cm", "osc_dir_cm",
                   "cadeia_esq_cm", "cadeia_dir_cm", "tronco_graus", "peso_cadeia"]
        n = len(serie_altura.get("t", []))
        with open(NOME_ARQUIVO_BRUTO, mode="w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(colunas)
            for i in range(n):
                linha = []
                for c in colunas:
                    v = serie_altura.get(c)
                    linha.append("" if v is None else f"{float(v[i]):.4f}")
                w.writerow(linha)
        entrada = {
            "modo_altura": MODO_ALTURA,
            "ganho_esq": ganho_esq, "ganho_dir": ganho_dir,
            "ganhos_metodo": {k: list(v) for k, v in ganhos_metodo.items()},
            "fases": [list(f) for f in fases_sessao],
            "calibracao": {
                "dist_qp_e_ref": dist_qp_e_ref, "dist_qp_d_ref": dist_qp_d_ref, "yq_ref": yq_ref,
                "chao_esq_ref": chao_esq_ref, "chao_dir_ref": chao_dir_ref,
                "limiar_ruido_chao": limiar_ruido_chao, "pitch_tronco_ref": pitch_tronco_ref,
                "cadeia_comprimentos": cadeia.comprimentos if cadeia is not None else None,
                "cadeia_vetor_local": ({k: list(map(float, v)) for k, v in cadeia.vetor_local.items()}
                                       if cadeia is not None else None),
            },
            "trackers": {tid: {k: list(map(float, v)) for k, v in d.items()} for tid, d in dados.items()},
            "imu": {lado: {k: list(map(float, v)) for k, v in d.items()} for lado, d in dados_imu.items()},
            "quats": {seg: {"t": list(map(float, d["t"])), "q": [list(map(float, q)) for q in d["q"]]}
                      for seg, d in dados_quat.items()},
        }
        with open(NOME_ARQUIVO_ENTRADA, "w", encoding="utf-8") as f:
            json.dump(entrada, f)
        print(f"Série completa salva em {NOME_ARQUIVO_BRUTO} e entradas em {NOME_ARQUIVO_ENTRADA}")
    except Exception as e:
        print(f"Não foi possível salvar os dados brutos: {e}")


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

    print("=" * 90)
    print("   SISTEMA UNIFICADO DE MARCHA (SOLARXR IMU + OSC) — ARQUITETURA ARGON")
    print("=" * 90)
    print(f"Porta 9000 (VRChat OSC) : Pés ({TORNOZELO_ESQ}, {TORNOZELO_DIR}), Quadril ({TRACKER_QUADRIL}), Peito ({TRACKER_PEITO})")
    print(f"Porta 39539 (VMC)       : Cabeça ({OSSO_CABECA.upper()} - Nuca)")
    print(f"Porta 21110 (SolarXR)   : Aceleração direta das IMUs dos cadarços (Heel-Strike Timing)")
    print("-" * 90)

    # 3. Rotina de Calibração Estática Inicial
    executar_calibracao_inicial()

    print("\n" + "=" * 90)
    if ROTEIRO_GUIADO:
        print("   🚀 GRAVAÇÃO INICIADA! Siga as etapas que vão aparecer na tela.")
    else:
        print("   🚀 GRAVAÇÃO INICIADA! Pode caminhar normalmente.")
    print("   Pressione 'Ctrl + C' a qualquer momento para finalizar e abrir os relatórios e gráficos.")
    print("=" * 90 + "\n")

    if ROTEIRO_GUIADO:
        threading.Thread(target=executar_roteiro, daemon=True).start()

    try:
        while not roteiro_terminou:
            mostrar_feedback()
            time.sleep(0.005)
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        rodando = False
        print("\n\n[*] Captura concluída! Sincronizando sinais biométricos e calculando métricas...", flush=True)

        if proc_node:
            try:
                proc_node.terminate()
            except Exception:
                pass

        d_esq = dados[TORNOZELO_ESQ]
        d_dir = dados[TORNOZELO_DIR]
        d_quad = dados[TRACKER_QUADRIL]
        d_coxa_e = dados[TRACKER_COXA_ESQ]
        d_coxa_d = dados[TRACKER_COXA_DIR]

        if len(d_esq["t"]) < 20 or len(d_dir["t"]) < 20:
            print("Dados insuficientes de passos para análise. Caminhe por mais tempo.")
            return

        t_passo, d_passo_ap, larg_base_m, clear_esq_cm, clear_dir_cm, torn_esq_cm, torn_dir_cm, alt_quadril_cm, posturas, taxa = calcular_sinais_completos(
            d_esq, d_dir, d_quad, d_coxa_e, d_coxa_d
        )

        salvar_fases()
        salvar_dados_brutos()

        tempos_impactos, dist_impactos, impactos_ms2, pes_passos, projecoes_ap = detectar_passos_multimodal(
            t_passo, d_passo_ap, taxa
        )

        if len(dist_impactos) == 0:
            print("Nenhum passo com a amplitude configurada foi detectado.")
            return

        clearances_passos_cm = calcular_alturas_passos(t_passo, tempos_impactos, pes_passos, clear_esq_cm, clear_dir_cm)
        tornozelos_passos_cm = calcular_alturas_passos(t_passo, tempos_impactos, pes_passos, torn_esq_cm, torn_dir_cm)
        mascara_validos = remover_outliers(dist_impactos)

        gerar_relatorio_e_graficos(
            t_passo, d_passo_ap, larg_base_m,
            clear_esq_cm, clear_dir_cm, torn_esq_cm, torn_dir_cm,
            alt_quadril_cm, posturas,
            tempos_impactos, dist_impactos, impactos_ms2,
            clearances_passos_cm, tornozelos_passos_cm,
            pes_passos, projecoes_ap, mascara_validos
        )


if __name__ == "__main__":
    main()
