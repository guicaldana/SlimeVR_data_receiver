"""
Piloto: coleta única por participante com os três circuitos de marcas e alturas conhecidas.

Baseado no calculo_passo.py (mesmos cálculos), com o protocolo do piloto:
  1. nome e altura do participante e as alturas dos objetos de teste;
  2. calibração (caixa de 7 cm);
  3. uma caminhada em cada circuito A, B e C (mesmo corredor), com o tronco reto,
     em ordem sorteada para cada participante;
  4. pé E e pé D sobre cada objeto de teste, com o tronco reto.
Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>/.

    uv run python piloto.py
"""
import sys

# Garante suporte UTF-8 imediato no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

print("\n" + "=" * 75, flush=True)
print("  🚀 PILOTO — SLIMEVR DATA RECEIVER & GAIT ANALYSIS", flush=True)
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
import marcas_chao
import re
import participante as cadastro
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
CORRIGIR_SENTIDO_AP = True   # confere pelo balanço dos pés se o eixo AP aponta para a frente
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
# Na análise final, a escala da cadeia é ajustada para medir a mesma altura de passo
# que o OSC na caminhada com o tronco reto (etapa "caminhada_reta" do roteiro).
CADEIA_ESCALA_NA_MARCHA = False  # 20/10: desligado (o ganho da caixa acertou a caixa reta e inclinada)

# Calibração de ganho por pé: pisar numa caixa de altura conhecida com cada pé
CALIBRAR_CAIXA = True
ALTURA_CAIXA_CM = 7.0    # caixa de calibração (antes de 23/10 o valor usado era 6,7)
# Alturas da calibração (cm). Com uma altura, o ganho é um fator só (ALTURA_CAIXA_CM).
# Com duas ou mais, cada método e cada pé ganha uma curva que passa pelo chão (0, 0) e
# pelos pontos medidos, corrigindo erros que crescem com a altura (experimento_altura.py).
ALTURAS_CALIBRACAO_CM = [ALTURA_CAIXA_CM]
TEMPO_CAIXA_S = 3.0      # segundos de amostragem com o pé na caixa
TEMPO_TRANSICAO_CALIB_S = 3   # calibração: segundos para subir e para descer o pé da caixa
TEMPO_VIRAR_CAIXA_S = 10      # calibração com várias alturas: segundos para virar a caixa

# Roteiro guiado: cada etapa tem um tempo fixo e o programa avisa a troca.
# Ao terminar, a gravação encerra sozinha (Ctrl + C continua funcionando).
ROTEIRO_GUIADO = True
# Três circuitos, cada um com um comprimento de passo fixo e 18 pegadas (marcas_chao.py).
TEMPO_CAMINHADA_S = {"A": 25, "B": 25, "C": 25}
TEMPO_VOLTA_S = 20
# Uma coleta só: os três circuitos ficam no mesmo corredor (uma cor de fita para cada).
# Uma caminhada com o tronco reto em cada circuito.
CIRCUITOS_COLETA = ["A", "B", "C"]
SORTEAR_ORDEM_CIRCUITOS = True      # ordem sorteada por participante, para evitar viés de ordem
ORDEM_CIRCUITOS = list(CIRCUITOS_COLETA)
# Alturas conhecidas de teste (cm), na ordem do roteiro. None = perguntar no terminal.
# As três alturas ainda serão definidas (entre 15 e 25 cm); todas são digitadas no início da coleta.
ALTURAS_TESTE_PADRAO_CM = [None, None, None]
FAIXA_ALTURAS_TESTE_CM = (15.0, 25.0)   # fora dela o programa pede confirmação
ALTURAS_TESTE_CM = []               # definidas no início da coleta
# Experimento: "completo" (circuitos + objetos) ou "altura" (só objetos, ver experimento_altura.py)
EXPERIMENTO = "completo"
REPETICOES_ALTURA = 3               # no experimento de altura: subidas de cada pé em cada objeto
SORTEAR_ORDEM_OBJETOS = True        # no experimento de altura: ordem dos objetos sorteada na coleta
ORDEM_OBJETOS = []                  # índices (1, 2, 3) na ordem do roteiro; vazio = 1, 2, 3
PES_ALTURA = ["ESQ", "DIR"]         # no experimento de altura: pés que sobem nos objetos
TEMPO_TRANSICAO_S = 3               # no experimento de altura: tempo para subir e para descer o pé


def montar_roteiro(alturas_cm=None):
    """Lista de (nome da etapa, segundos, instrução) da coleta."""
    r = []
    for c in (ORDEM_CIRCUITOS if EXPERIMENTO != "altura" else []):
        r += [
            (f"ir_inicio_{c}", TEMPO_VOLTA_S, f"Vá até o início do CIRCUITO {c}, pé D na marca 0"),
            (f"caminhada_reta_{c}", TEMPO_CAMINHADA_S[c],
             f"CIRCUITO {c}: CAMINHE pisando com o calcanhar nas MARCAS, tronco RETO; no fim junte os pés"),
        ]
    alturas = list(alturas_cm or [ALTURA_CAIXA_CM])
    ordem = [j for j in ORDEM_OBJETOS if 1 <= j <= len(alturas)] or list(range(1, len(alturas) + 1))
    reps = REPETICOES_ALTURA if EXPERIMENTO == "altura" else 1
    for j in ordem:
        obj = f"objeto de {alturas[j - 1]:.1f} cm"
        r.append((f"ir_objeto_{j}", 15, f"Vá até o {obj} e fique com os dois pés no chão, tronco reto"))
        pes = PES_ALTURA if EXPERIMENTO == "altura" else ["ESQ", "DIR"]
        nome_pe = {"ESQ": "ESQUERDO", "DIR": "DIREITO"}
        for k in range(1, reps + 1):
            vez = f" ({k}ª vez)" if reps > 1 else ""
            for pe in pes:
                if EXPERIMENTO == "altura" and TEMPO_TRANSICAO_S > 0:
                    # Etapas só para mover o pé; a medida usa as etapas parado em cima e no chão
                    r += [
                        (f"subir_{pe}_{j}", TEMPO_TRANSICAO_S, f"SUBA o pé {nome_pe[pe]} no {obj}{vez}"),
                        (f"caixa_reto_{pe}_{j}", 7, f"Pé {nome_pe[pe]} PARADO em cima do {obj}{vez}"),
                        (f"descer_{pe}_{j}", TEMPO_TRANSICAO_S, "DESÇA o pé para o chão"),
                        (f"chao_reto_{pe}_{j}", 6, "Pé PARADO no chão"),
                    ]
                    continue
                r += [
                    (f"caixa_reto_{pe}_{j}", 7, f"Pé {nome_pe[pe]} em cima do {obj}{vez}"),
                    (f"chao_reto_{pe}_{j}",  6, "Pé de volta ao chão"),
                ]
    return r


def circuito_da_etapa(nome):
    """'caminhada_reta_B' -> 'B'; etapas sem circuito -> circuito ativo do marcas_chao."""
    fim = nome.rsplit("_", 1)[-1]
    return fim if fim in marcas_chao.CIRCUITOS else marcas_chao.CIRCUITO_ATIVO


def rotulo_etapa(nome):
    tronco = "tronco reto" if "reta" in nome else "tronco inclinado" if "inclinada" in nome else ""
    fim = nome.rsplit("_", 1)[-1]
    if fim in marcas_chao.CIRCUITOS:
        return f"Circuito {fim} ({marcas_chao.DESCRICAO.get(fim, '')}), {tronco}"
    return {"marcha": "Marcha"}.get(nome, f"Caminhada, {tronco}" if tronco else nome)


ROTEIRO = montar_roteiro()

# Arquivos de saída
os.makedirs("dados", exist_ok=True)
NOME_ARQUIVO_PASSOS = os.path.join("dados", "resultado_completo_marcha.csv")
NOME_ARQUIVO_BRUTO = os.path.join("dados", "dados_brutos_sessao.csv")
NOME_ARQUIVO_ENTRADA = os.path.join("dados", "dados_brutos_entrada.json")
NOME_ARQUIVO_FASES = os.path.join("dados", "fases_sessao.csv")
NOME_ARQUIVO_RELATORIO = os.path.join("dados", "relatorio.txt")
# Antes do experimento o programa pede nome e altura do participante e grava tudo
# em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>/ (ver participante.py).
PEDIR_PARTICIPANTE = True
# Comprimento do passo: máximo da distância anteroposterior entre os pés em cada
# platô (o pé da frente fica à frente até o outro passar). O contato do calcanhar
# (aceleração) só marca o tempo do passo. False volta ao valor no instante do contato.
COMPRIMENTO_PELO_PLATO = True
PLATO_PERCENTIL = 100         # 100 = máximo do platô; 80 deixa o valor um pouco menor
PLATO_LIMIAR_M = 0.05         # abaixo disso os pés estão lado a lado (fora do platô)
PLATO_DURACAO_MIN_S = 0.15
PLATO_JUNTAR_S = 0.5          # junta platôs do mesmo pé separados por menos que isso
MOSTRAR_GRAFICOS = True       # abre as janelas dos gráficos no fim (os PNG são salvos de qualquer jeito)
PASTA_COLETA = "dados"
PARTICIPANTE = None           # {"nome": ..., "altura_cm": ...}
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
ganhos_cadeia_caixa = []         # ganho da cadeia na caixa, antes do ajuste pela marcha
# Curvas de calibração com várias alturas: {"osc": [curva_E, curva_D], "cadeia": [...]},
# cada curva é uma lista [(medido_cm, real_cm), ...] começando em (0, 0). Vazio = usa ganhos_metodo.
curvas_metodo = {}


def aplicar_calibracao(valor, metodo, idx):
    """Altura corrigida (cm) de um pé por um método: curva de calibração ou ganho único."""
    curva = (curvas_metodo.get(metodo) or [None, None])[idx]
    if not curva or len(curva) < 2:
        return valor * ganhos_metodo[metodo][idx]
    xs = np.array([c[0] for c in curva], dtype=float)
    ys = np.array([c[1] for c in curva], dtype=float)
    v = np.asarray(valor, dtype=float)
    out = np.interp(v, xs, ys)
    # acima do último ponto, segue a inclinação do último trecho
    incl = float(np.clip((ys[-1] - ys[-2]) / max(1e-6, xs[-1] - xs[-2]), 0.4, 2.5))
    out = np.where(v > xs[-1], ys[-1] + (v - xs[-1]) * incl, out)
    return float(out) if np.ndim(out) == 0 else out
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
        if not rodando:  # captura encerrada: não mexe nos dados durante a análise
            return
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
        if not rodando:  # captura encerrada: não mexe nos dados durante a análise
            return
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
                    if not rodando:  # captura encerrada: não mexe nos dados durante a análise
                        return
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
            if not rodando:  # captura encerrada: não mexe nos dados durante a análise
                return
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


def _picos_balanco(x, minimo=1.5, fim=0.5):
    """Altura máxima de cada balanço (trecho em que o pé sai do chão)."""
    picos, i, n = [], 0, len(x)
    while i < n:
        if x[i] > minimo:
            j = i
            while j < n and x[j] > fim:
                j += 1
            picos.append(float(np.max(x[i:j])))
            i = j
        else:
            i += 1
    return picos


def escalar_cadeia_pela_marcha(t, osc_e, osc_d, cad_e, cad_d, pitch_serie):
    """Ajusta o ganho da cadeia para que, na caminhada com o tronco reto, ela meça
    a mesma altura de passo que o OSC (já com o ganho da caixa). Assim a troca do
    pipeline não muda a escala da medida no meio da caminhada."""
    janela = None
    for nome, t0, t1 in fases_sessao:
        if nome.startswith("caminhada_reta"):
            j = (t >= t0) & (t <= t1)
            janela = j if janela is None else (janela | j)
    if janela is None:
        # Sem roteiro: usa todos os trechos com o tronco reto
        if pitch_serie is None:
            return
        janela = peso_cadeia(pitch_serie) <= 0.0
    go = ganhos_metodo["osc"]
    novos = list(ganhos_metodo["cadeia"])
    for i, (o, c) in enumerate(((osc_e, cad_e), (osc_d, cad_d))):
        po = _picos_balanco(o[janela] * go[i])
        pc = _picos_balanco(c[janela])
        if len(po) < 3 or len(pc) < 3 or np.median(pc) <= 0:
            continue
        novos[i] = float(np.clip(np.median(po) / np.median(pc), 0.2, 3.0))
    print(f"      • Ganho da cadeia na marcha: E {novos[0]:.2f} | D {novos[1]:.2f} "
          f"(caixa: E {ganhos_metodo['cadeia'][0]:.2f} | D {ganhos_metodo['cadeia'][1]:.2f})")
    if not ganhos_cadeia_caixa:
        ganhos_cadeia_caixa[:] = list(ganhos_metodo["cadeia"])
    ganhos_metodo["cadeia"] = tuple(novos)


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

    # Confere o sentido do eixo anteroposterior pelos próprios dados: enquanto o pé
    # ESQ está no ar, ele avança em relação ao pé D, então (E - D) tem que crescer.
    # Se crescer ao contrário, o eixo do quadril aponta para trás e o sinal é invertido.
    if CORRIGIR_SENTIDO_AP:
        dif_alt = y_e_norm - y_d_norm
        no_ar = np.abs(dif_alt) > 0.02
        if np.count_nonzero(no_ar) > 50:
            sentido = float(np.sum(np.gradient(d_passo_ap_filt)[no_ar] * dif_alt[no_ar]))
            if sentido < 0:
                d_passo_ap_filt = -d_passo_ap_filt
                print("      • Eixo anteroposterior invertido (o quadril apontava para trás); sinal corrigido.")

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
    if MODO_ALTURA == "pipeline" and CADEIA_ESCALA_NA_MARCHA and cad_e is not None:
        escalar_cadeia_pela_marcha(t_comum, osc_e_bruto, osc_d_bruto, cad_e, cad_d, pitch_serie)
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

        # Distância curta aqui: o impulso (toe-off) do mesmo pé pode ser maior que o
        # heel-strike e, com a distância de um ciclo, o esconderia. A polaridade AP abaixo
        # descarta o impulso, e o filtro de coerência depois garante um passo por ciclo.
        dist_min_pts = int(taxa_amostragem * 0.20)

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


def platos_anteroposteriores(t, d_ap):
    """Platôs da distância anteroposterior entre os pés.

    d_ap > 0: pé ESQ à frente; d_ap < 0: pé DIR à frente. Cada platô vai de uma
    passagem por zero (|d_ap| < PLATO_LIMIAR_M) à seguinte. Platôs seguidos do
    mesmo pé com pausa curta são juntados. Retorna lista de dicts
    {t0, t1, pe, comprimento_m, t_max}."""
    t = np.asarray(t); d = np.asarray(d_ap)
    sinal = np.sign(d)
    sinal[np.abs(d) < PLATO_LIMIAR_M] = 0
    platos, i, n = [], 0, len(d)
    while i < n:
        if sinal[i] == 0:
            i += 1
            continue
        j = i
        while j < n and sinal[j] == sinal[i]:
            j += 1
        if t[j - 1] - t[i] >= PLATO_DURACAO_MIN_S:
            pe = "ESQ" if sinal[i] > 0 else "DIR"
            if platos and platos[-1]["pe"] == pe and t[i] - platos[-1]["t1"] < PLATO_JUNTAR_S:
                platos[-1]["t1"] = float(t[j - 1])
                platos[-1]["fim"] = j
            else:
                platos.append({"t0": float(t[i]), "t1": float(t[j - 1]), "pe": pe, "ini": i, "fim": j})
        i = j
    for p in platos:
        trecho = np.abs(d[p["ini"]:p["fim"]])
        p["comprimento_m"] = float(np.percentile(trecho, PLATO_PERCENTIL))
        p["t_max"] = float(t[p["ini"] + int(np.argmax(trecho))])
    return platos


def comprimento_pelo_plato(t, d_ap, tempos, comprimentos, impactos, pes, projecoes):
    """Troca o comprimento de cada passo pelo valor do platô em que ele caiu.

    - O pé do passo passa a ser o do platô (o que ficou à frente).
    - Duas detecções no mesmo platô viram um passo só (fica a primeira).
    - Um platô sem detecção vira um passo marcado com '*' (sem impacto da IMU),
      no instante em que o pé atinge o máximo.
    - Detecções fora de qualquer platô (pés lado a lado) são descartadas."""
    platos = platos_anteroposteriores(t, d_ap)
    if not platos:
        return tempos, comprimentos, impactos, pes, projecoes
    t = np.asarray(t); d = np.asarray(d_ap)
    usados = {}
    for k, t_k in enumerate(tempos):
        dentro = [i for i, p in enumerate(platos) if p["t0"] <= t_k <= p["t1"]]
        if not dentro:
            perto = [i for i, p in enumerate(platos) if p["t0"] - 0.3 <= t_k <= p["t1"] + 0.3
                     and p["pe"] in str(pes[k])]
            dentro = sorted(perto, key=lambda i: min(abs(t_k - platos[i]["t0"]), abs(t_k - platos[i]["t1"])))
        if dentro and dentro[0] not in usados:
            usados[dentro[0]] = k
    novos = []
    for i, p in enumerate(platos):
        sinal = 1.0 if p["pe"] == "ESQ" else -1.0
        if i in usados:
            k = usados[i]
            imu = "*" not in str(pes[k])
            novos.append((float(tempos[k]), p["comprimento_m"], float(impactos[k]),
                          p["pe"] + ("" if imu else "*"), sinal * p["comprimento_m"]))
        else:
            novos.append((p["t_max"], p["comprimento_m"], 0.0, p["pe"] + "*", sinal * p["comprimento_m"]))
    novos.sort(key=lambda x: x[0])
    tt, cc, ii, pp, jj = zip(*novos)
    return np.array(tt), np.array(cc), np.array(ii), np.array(pp), np.array(jj)


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
                              pes_passos, projecoes_ap, mascara_validos, reais_m=None):
    print("\n\n" + "=" * 90)
    print("      RELATÓRIO CLÍNICO & BIOMECÂNICO: ARQUITETURA DE FUSÃO SOLARXR + SLIMEVR")
    print("=" * 90)

    n_total = len(dist_impactos)
    n_outliers = np.sum(~mascara_validos)
    # Com o roteiro guiado, as estatísticas da marcha usam só os passos das caminhadas
    etapas_passos = [etapa_no_instante(t_p) for t_p in tempos_impactos]
    na_marcha = np.array([e.startswith("caminhada") for e in etapas_passos])
    if not np.any(na_marcha):
        na_marcha = np.ones(n_total, dtype=bool)
    sel = mascara_validos & na_marcha
    passos_validos = dist_impactos[sel]
    tempos_validos = tempos_impactos[sel]
    clearances_validos = clearances_passos_cm[sel]
    tornozelos_validos = tornozelos_passos_cm[sel]
    pes_validos = pes_passos[sel]

    duracao = t_passo[-1]
    print(f"Duração da sessão:       {duracao:.1f} s")
    print(f"Passos detectados:       {n_total} (Outliers MAD: {n_outliers})")
    if not np.all(na_marcha):
        print(f"Passos nas caminhadas:   {int(np.sum(na_marcha))} (válidos: {len(passos_validos)}); as estatísticas abaixo usam só esses")

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

    # 3. Parâmetros Espaciais da Marcha
    if PARTICIPANTE:
        print(f"\n--- Participante: {PARTICIPANTE['nome']} | altura {PARTICIPANTE['altura_cm']:.1f} cm"
              f" | ordem dos circuitos {' -> '.join(ORDEM_CIRCUITOS)} ---")
    print(f"\n--- 3. Parâmetros Espaciais (Projeção Vetorial Anteroposterior - Seção 3) ---")
    if len(passos_validos) > 0:
        media_passo = np.mean(passos_validos)
        desvio_passo = np.std(passos_validos)
        media_clear = np.mean(clearances_validos)
        desvio_clear = np.std(clearances_validos)
        media_torn = np.mean(tornozelos_validos)
        desvio_torn = np.std(tornozelos_validos)
        media_base = np.mean(larg_base_m) * 100.0

        print(f"  Comprimento médio da passada: {media_passo:.3f} m (± {desvio_passo:.3f} m)")
        if PARTICIPANTE:
            print(f"  Comprimento / altura:         {media_passo * 100 / PARTICIPANTE['altura_cm']:.2f}")
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

        # Cadência só entre passos seguidos da mesma caminhada (não conta a pausa entre elas)
        etapas_sel = np.array(etapas_passos)[sel]
        intervalos = [b - a for a, b, ea, eb in zip(tempos_validos[:-1], tempos_validos[1:], etapas_sel[:-1], etapas_sel[1:])
                      if ea == eb]
        if intervalos:
            cadencia = 60.0 / np.mean(intervalos)
            print(f"  Cadência estimada:            {cadencia:.1f} passos/min")

        # Por caminhada (os circuitos têm comprimentos diferentes, então a média geral mistura todos)
        print("\n  Por caminhada (comprimento | altura do passo):")
        for nome_c in [n for n, _, _ in fases_sessao if n.startswith("caminhada")]:
            sel_c = sel & np.array([e == nome_c for e in etapas_passos])
            if not np.any(sel_c):
                print(f"    {rotulo_etapa(nome_c)}: nenhum passo")
                continue
            v = dist_impactos[sel_c] * 100
            h = clearances_passos_cm[sel_c]
            cv_c = v.std() / v.mean() * 100 if v.mean() > 0 else 0.0
            print(f"    {rotulo_etapa(nome_c)}: {v.mean():.1f} ± {v.std():.1f} cm (CV {cv_c:.0f}%, {len(v)} passos)"
                  f" | {h.mean():.1f} ± {h.std():.1f} cm")

        # 4. Biomarcadores Específicos para Doença de Parkinson (Seção 5 da Nota Técnica)
        cv_passo = (desvio_passo / media_passo) * 100.0 if media_passo > 0 else 0.0
        n_microbasia = np.sum(passos_validos < 0.30)
        pct_microbasia = (n_microbasia / len(passos_validos)) * 100.0
        n_baixo_clearance = np.sum(clearances_validos < 2.0)
        pct_baixo_clearance = (n_baixo_clearance / len(clearances_validos)) * 100.0

        print(f"\n--- 4. Avaliação Clínica para Doença de Parkinson (Biomarcadores de Risco) ---")
        if cv_passo > 10.0:
            alerta_cv = "⚠ ALTO (Elevado Risco de Quedas)"
        elif cv_passo >= 6.0:
            alerta_cv = "⚡ MODERADO (Atenção ao Equilíbrio)"
        else:
            alerta_cv = "✔ BAIXO (Padrão Regular e Estável)"
        print(f"  Variabilidade do Passo (CV): {cv_passo:5.1f}% [{alerta_cv}]")
        print("  (o CV e a microbasia desta seção juntam todos os circuitos; o CV de cada caminhada está na seção 3)")
        print(f"  Taxa de Microbasia (< 30cm): {n_microbasia}/{len(passos_validos)} ({pct_microbasia:.1f}%)" + (" ⚠ Indicativo de passos curtos" if pct_microbasia > 30 else ""))
        print(f"  Risco de Arraste (Solado < 2.0cm): {n_baixo_clearance}/{len(clearances_validos)} ({pct_baixo_clearance:.1f}%)" + (" ⚠ Risco de tropeço / Shuffling" if pct_baixo_clearance > 25 else " ✔ Clearance adequado"))
    else:
        media_passo = 0.0

    # 5. Tabela Integrada
    print("\n--- Tabela Integrada Passo a Passo (Sincronia Heel-Strike SolarXR + Projeção Anteroposterior) ---")
    print(f"{'#':<4} {'Tempo(s)':<9} {'Pé':<6} {'Etapa':<20} {'Impacto(m/s²)':<15} {'Comprim.(m)':<13} {'Solado(cm)':<12} {'Tornoz(cm)':<12} {'Tronco':<10} {'Status'}")
    print("-" * 110)
    for i, (t_p, d_p, imp_p, cl_p, tn_p, pe_p, tr_p) in enumerate(zip(
        tempos_impactos, dist_impactos, impactos_ms2, clearances_passos_cm, tornozelos_passos_cm, pes_passos, tronco_nos_passos
    )):
        valido = mascara_validos[i]
        status = "Válido" if valido else "⚠ Outlier"
        tr_str = f"{tr_p:+.1f}°" if tem_peito else "N/A"
        print(f"{i+1:<4} {t_p:<9.2f} {pe_p:<6} {etapas_passos[i]:<20} {imp_p:<15.1f} {d_p:<13.3f} {cl_p:<12.1f} {tn_p:<12.1f} {tr_str:<10} {status}")
    print("=" * 110)

    # 6. Salva CSV Unificado
    with open(NOME_ARQUIVO_PASSOS, mode='w', newline='', encoding="utf-8") as f:
        escritor = csv.writer(f)
        nome_p = PARTICIPANTE["nome"] if PARTICIPANTE else ""
        altura_p = PARTICIPANTE["altura_cm"] if PARTICIPANTE else None
        reais = reais_m or {}
        escritor.writerow([
            "Participante", "Altura_participante_cm", "Circuito", "Passo", "Etapa", "Tempo_HeelStrike_s", "Pe_Impacto",
            "Impacto_IMU_ms2", "Comprimento_m", "Comprimento_por_altura", "Comprimento_real_marcas_m",
            "Clearance_Solado_cm", "Elev_Tornozelo_cm", "Tronco_graus", "Outlier"
        ])
        for i, (t_p, d_p, imp_p, cl_p, tn_p, pe_p, tr_p) in enumerate(zip(
            tempos_impactos, dist_impactos, impactos_ms2, clearances_passos_cm, tornozelos_passos_cm, pes_passos, tronco_nos_passos
        )):
            escritor.writerow([
                nome_p, f"{altura_p:.1f}" if altura_p else "", circuito_da_etapa(etapas_passos[i]) if etapas_passos[i].startswith("caminhada") else "", i+1, etapas_passos[i], f"{t_p:.3f}", pe_p,
                f"{imp_p:.2f}", f"{d_p:.3f}", f"{d_p * 100 / altura_p:.3f}" if altura_p else "",
                f"{reais[i]:.3f}" if i in reais else "",
                f"{cl_p:.2f}", f"{tn_p:.2f}", f"{tr_p:.2f}", "NAO" if mascara_validos[i] else "SIM"
            ])
    print(f"\nResultados consolidados salvos em '{NOME_ARQUIVO_PASSOS}'.")

    # ================= GRÁFICOS INTEGRADOS =================
    import matplotlib.pyplot as plt
    tem_imu_plot = len(dados_imu["ESQ"]["t"]) > 20 or len(dados_imu["DIR"]["t"]) > 20
    painéis_extras = (1 if tem_imu_plot else 0) + (1 if tem_peito else 0) + 1
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

    # Painel 7: Barras Passo a Passo
    ax_bar = axes[idx_ax]
    x_indices = np.arange(1, n_total + 1)
    largura = 0.38
    ax_bar.bar(x_indices - largura/2, dist_impactos, width=largura, color='steelblue', label='Comprimento (m)')
    ax_bar.axhline(0.30, color='goldenrod', linestyle='--', alpha=0.8, label='Limiar Microbasia (30 cm)')
    ax_bar.set_ylabel('Comprimento (m)', color='steelblue')
    ax_bar.set_xlabel('Número do Passo')
    ax_bar.set_title('6. Comprimento de Passo vs Altura Real com Biomarcadores de Parkinson', fontweight="bold")
    ax_bar.set_xticks(x_indices)

    ax_bar_twin = ax_bar.twinx()
    ax_bar_twin.bar(x_indices + largura/2, clearances_passos_cm, width=largura, color='coral', label='Clearance Solado (cm)')
    ax_bar_twin.set_ylabel('Clearance Solado (cm)', color='coral')
    ax_bar.grid(True, alpha=0.3, axis='x')

    if PARTICIPANTE:
        fig.suptitle(f"{PARTICIPANTE['nome']} · altura {PARTICIPANTE['altura_cm']:.0f} cm", fontweight="bold")
    plt.tight_layout()
    salvar_figura(fig, "painel_completo.png")

    graficos_marcha(t_passo, clear_esq_cm, clear_dir_cm, tempos_impactos, dist_impactos,
                    clearances_passos_cm, pes_passos, mascara_validos, etapas_passos, reais)


def salvar_figura(fig, nome):
    arq = os.path.join(PASTA_COLETA, nome)
    try:
        fig.savefig(arq, dpi=130)
        print(f"Gráfico salvo em {arq}")
    except Exception as e:
        print(f"Não foi possível salvar {arq}: {e}")


def etapa_no_instante(t):
    """Nome da etapa do roteiro em que o instante t caiu ('' sem roteiro).

    Um passo até 1 s depois do fim de uma caminhada ainda conta para ela
    (o último passo costuma fechar logo depois do aviso)."""
    for nome, t0, t1 in fases_sessao:
        if t0 <= t <= t1:
            return nome
    for nome, t0, t1 in fases_sessao:
        if nome.startswith("caminhada") and t1 < t <= t1 + 1.0:
            return nome
    return ""


def _grupos_marcha(etapas_passos):
    """Índices dos passos de cada caminhada; sem roteiro, todos os passos num grupo só."""
    nomes = []
    for n, _, _ in fases_sessao:
        if n.startswith("caminhada") and n not in nomes:
            nomes.append(n)
    if not nomes:
        return [("marcha", list(range(len(etapas_passos))))]
    return [(n, [i for i, e in enumerate(etapas_passos) if e == n]) for n in nomes]


def graficos_marcha(t_passo, clear_esq_cm, clear_dir_cm, tempos_impactos, dist_impactos,
                    clearances_passos_cm, pes_passos, mascara_validos, etapas_passos, reais):
    """Gráficos de comprimento e altura do passo, só com os passos das caminhadas."""
    import matplotlib.pyplot as plt
    grupos = _grupos_marcha(etapas_passos)
    cor_pe = {"ESQ": "darkorange", "DIR": "green"}
    titulo_p = (f"{PARTICIPANTE['nome']} · altura {PARTICIPANTE['altura_cm']:.0f} cm"
                if PARTICIPANTE else "")

    def barras(ax, idx, valores, unidade, fator, ref=None):
        x = np.arange(1, len(idx) + 1)
        for k, i in enumerate(idx):
            lado = "DIR" if "DIR" in pes_passos[i] else "ESQ"
            ok = bool(mascara_validos[i])
            ax.bar(x[k], valores[i] * fator, color=cor_pe[lado], alpha=0.9 if ok else 0.3,
                   hatch=None if ok else "//", edgecolor="k" if not ok else None, linewidth=0.5)
            ax.text(x[k], valores[i] * fator, f"{valores[i] * fator:.0f}", ha="center", va="bottom", fontsize=8)
            if ref is not None and i in ref:
                ax.plot([x[k] - 0.4, x[k] + 0.4], [ref[i] * fator] * 2, color="k", lw=2)
        ax.set_xticks(x, [f"{k}\n{'D' if 'DIR' in pes_passos[i] else 'E'}" for k, i in zip(x, idx)])
        ax.set_ylabel(unidade)
        topo = max([valores[i] * fator for i in idx] + [ref[i] * fator for i in idx if ref and i in ref] + [1.0])
        ax.set_ylim(0, topo * 1.3)
        ax.grid(True, alpha=0.3, axis="y")

    def resumo(idx, valores, fator, un):
        v = np.array([valores[i] for i in idx if mascara_validos[i]]) * fator
        if len(v) == 0:
            return "sem passos válidos"
        txt = f"média {v.mean():.1f} ± {v.std():.1f} {un} ({len(v)} passos)"
        for lado, ch in (("ESQ", "E"), ("DIR", "D")):
            vl = np.array([valores[i] for i in idx if mascara_validos[i] and lado in pes_passos[i]]) * fator
            if len(vl):
                txt += f" | {ch} {vl.mean():.1f}"
        return txt

    # Comprimento do passo
    fig, axes = plt.subplots(len(grupos), 1, figsize=(10, 3.4 * len(grupos)), squeeze=False)
    for ax, (nome, idx) in zip(axes[:, 0], grupos):
        if not idx:
            ax.set_title(f"{rotulo_etapa(nome)}: nenhum passo detectado"); ax.axis("off"); continue
        barras(ax, idx, dist_impactos, "Comprimento (cm)", 100.0, reais)
        txt = resumo(idx, dist_impactos, 100.0, "cm")
        if PARTICIPANTE:
            v = [dist_impactos[i] for i in idx if mascara_validos[i]]
            if v:
                txt += f" | comprimento/altura {np.mean(v) * 100 / PARTICIPANTE['altura_cm']:.2f}"
        ax.set_title(f"{rotulo_etapa(nome)}: {txt}", fontsize=10)
    axes[-1, 0].set_xlabel("Passo (pé que pisou)")
    axes[0, 0].plot([], [], color="darkorange", lw=6, label="Pé E")
    axes[0, 0].plot([], [], color="green", lw=6, label="Pé D")
    if reais:
        axes[0, 0].plot([], [], color="k", lw=2, label="Marca no chão")
    axes[0, 0].legend(fontsize=8, loc="upper right", ncol=3)
    fig.suptitle("Comprimento do passo" + (f" · {titulo_p}" if titulo_p else ""), fontweight="bold")
    fig.tight_layout()
    salvar_figura(fig, "comprimento_passo.png")

    # Altura do passo: série no tempo + barras por passo
    fig, axes = plt.subplots(len(grupos), 2, figsize=(13, 3.4 * len(grupos)), squeeze=False,
                             gridspec_kw={"width_ratios": [1.6, 1]})
    for (ax_t, ax_b), (nome, idx) in zip(axes, grupos):
        fases_g = [(a, b) for n, a, b in fases_sessao if n == nome]
        if fases_g:
            m = np.zeros(len(t_passo), dtype=bool)
            for a, b in fases_g:
                m |= (t_passo >= a) & (t_passo <= b + 1.0)
        else:
            m = np.ones(len(t_passo), dtype=bool)
        # fora das caminhadas a linha fica em branco
        ax_t.plot(t_passo, np.where(m, clear_esq_cm, np.nan), color="darkorange", lw=1.3, label="Pé E")
        ax_t.plot(t_passo, np.where(m, clear_dir_cm, np.nan), color="green", lw=1.3, label="Pé D")
        if np.any(m):
            ax_t.set_xlim(t_passo[m][0] - 1, t_passo[m][-1] + 1)
        for i in idx:
            ax_t.axvline(tempos_impactos[i], color="gray", lw=0.6, ls=":")
        ax_t.set_ylabel("Altura do pé (cm)")
        ax_t.set_xlabel("Tempo (s)")
        ax_t.set_title(rotulo_etapa(nome), fontsize=10)
        ax_t.legend(fontsize=8, loc="upper right")
        ax_t.grid(True, alpha=0.3)
        if idx:
            barras(ax_b, idx, clearances_passos_cm, "Altura máxima (cm)", 1.0)
            ax_b.set_title(resumo(idx, clearances_passos_cm, 1.0, "cm"), fontsize=9)
            ax_b.set_xlabel("Passo (pé que pisou)")
        else:
            ax_b.axis("off")
    fig.suptitle("Altura do passo" + (f" · {titulo_p}" if titulo_p else ""), fontweight="bold")
    fig.tight_layout()
    salvar_figura(fig, "altura_passo.png")


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
            return aplicar_calibracao(osc_e, "osc", 0), aplicar_calibracao(osc_d, "osc", 1)
        return osc_e, osc_d
    if MODO_ALTURA == "cadeia":
        return cad_e, cad_d
    if MODO_ALTURA == "pipeline":
        w = peso_cadeia(pitch_tronco)
        return ((1.0 - w) * aplicar_calibracao(osc_e, "osc", 0) + w * aplicar_calibracao(cad_e, "cadeia", 0),
                (1.0 - w) * aplicar_calibracao(osc_d, "osc", 1) + w * aplicar_calibracao(cad_d, "cadeia", 1))
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


def _contagem(texto, segundos):
    """Mostra uma contagem regressiva de segundos inteiros na mesma linha."""
    for cont in range(int(segundos), 0, -1):
        print(f"    {texto} {cont}s...   ", end="\r", flush=True)
        time.sleep(1.0)
    print(" " * 60, end="\r")


def _medir_pe_na_caixa(lado, idx, altura_cm):
    """Pede um pé sobre a caixa e devolve as medianas (pipeline, osc, cadeia) brutas."""
    print(f"\n👉 SUBA o pé {lado} na caixa de {altura_cm:.1f} cm (o outro pé fica no chão).")
    _aviso_sonoro(1)
    _contagem("Subindo o pé:", max(TEMPO_TRANSICAO_CALIB_S, 1))
    print("    Pé em cima: fique PARADO.")
    _contagem("Amostragem em", 2)
    print("    >>> MEDINDO... fique imóvel! <<<           ", flush=True)
    amostras, amostras_osc, amostras_cad = [], [], []
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
    med = lambda a: float(np.median(a)) if a else 0.0
    print("    DESÇA o pé para o chão.")
    _aviso_sonoro(1)
    _contagem("Descendo o pé:", max(TEMPO_TRANSICAO_CALIB_S, 1))
    _contagem("Pé parado no chão:", 2)
    return med(amostras), med(amostras_osc), med(amostras_cad)


def calibrar_ganho_caixa():
    """Pede para pisar na caixa com cada pé (em uma ou mais alturas) e calcula a correção por pé."""
    global ganho_esq, ganho_dir, ganhos_metodo, curvas_metodo
    alturas = list(ALTURAS_CALIBRACAO_CM or [ALTURA_CAIXA_CM])
    print("\n" + "=" * 90)
    print("   📦 ETAPA 2: CALIBRAÇÃO DE ALTURA COM A CAIXA: "
          + " e ".join(f"{h:.1f} cm" for h in alturas))
    print("=" * 90)

    if MODO_ALTURA == "pipeline":
        pontos = {m: [[(0.0, 0.0)], [(0.0, 0.0)]] for m in ("osc", "cadeia")}
        for k, h in enumerate(alturas):
            if len(alturas) > 1:
                print(f"\n   --- Caixa de {h:.1f} cm ---")
                if k > 0:
                    print(f"    VIRE a caixa para ficar com {h:.1f} cm de altura.")
                    _aviso_sonoro(1)
                    _contagem("Virando a caixa:", TEMPO_VIRAR_CAIXA_S)
            for lado, idx in (("ESQUERDO", 0), ("DIREITO", 1)):
                _, m_osc, m_cad = _medir_pe_na_caixa(lado, idx, h)
                for metodo, med in (("osc", m_osc), ("cadeia", m_cad)):
                    if med >= 2.0:
                        pontos[metodo][idx].append((med, h))
                    print(f"    Pé {lado} ({metodo}): medido {med:.1f} cm (real {h:.1f})")
        # Ganho único (compatível com as coletas antigas): pela primeira altura
        for metodo in ("osc", "cadeia"):
            g = []
            for idx in (0, 1):
                pts = sorted(pontos[metodo][idx])
                g.append(max(0.4, min(2.5, pts[1][1] / pts[1][0])) if len(pts) > 1 else 1.0)
            ganhos_metodo[metodo] = tuple(g)
        if len(alturas) > 1:
            def _curva(pts):
                # Ordena pela altura real e descarta pontos em que o medido não cresce
                saida = [(0.0, 0.0)]
                for med, real in sorted(pts[1:], key=lambda p: p[1]):
                    if med > saida[-1][0] + 0.5:
                        saida.append((med, real))
                    else:
                        print(f"    [Aviso] Ponto {med:.1f}->{real:.1f} cm ignorado: a medida não cresceu com a altura.")
                return saida
            curvas_metodo = {m: [_curva(pontos[m][0]), _curva(pontos[m][1])] for m in pontos}
            for m in curvas_metodo:
                for idx, lado in ((0, "E"), (1, "D")):
                    txt = ", ".join(f"{a:.1f}->{b:.1f}" for a, b in curvas_metodo[m][idx][1:])
                    print(f"    Curva {m} pé {lado}: {txt}")
        ganho_esq, ganho_dir = 1.0, 1.0
        print("=" * 90)
        return

    resultados = {}
    for lado, idx in (("ESQUERDO", 0), ("DIREITO", 1)):
        medido, _, _ = _medir_pe_na_caixa(lado, idx, ALTURA_CAIXA_CM)
        if medido < 2.0:
            print(f"    [Aviso] Pé {lado}: só {medido:.1f} cm medidos. Ganho mantido em 1.0.")
            ganho = 1.0
        else:
            ganho = max(0.4, min(2.5, ALTURA_CAIXA_CM / medido))
            print(f"    Pé {lado}: medido {medido:.1f} cm -> ganho {ganho:.3f}")
        resultados[idx] = ganho

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
    """Bipe na troca de etapa. No Windows tenta o som do sistema e o Beep; nos outros, o sino do terminal."""
    try:
        import winsound
        try:
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            pass
        for _ in range(vezes):
            winsound.Beep(1000, 200)
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
            falta = fim - time.time()
            if falta <= 3.0 and i + 1 < len(ROTEIRO):
                etapa_atual = f"{nome} {falta:4.1f}s >> PRÓXIMA: {ROTEIRO[i + 1][0]} em {int(falta) + 1}"
            else:
                etapa_atual = f"{nome} {falta:4.1f}s"
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


def comparar_com_marcas(tempos, pes, comprimentos):
    """Compara o comprimento de cada passo das caminhadas com as marcas do chão.

    Retorna {índice do passo: comprimento real em m} dos passos que casaram."""
    reais = {}
    for nome, t0, t1 in fases_sessao:
        if not nome.startswith("caminhada"):
            continue
        m = (np.asarray(tempos) >= t0) & (np.asarray(tempos) <= t1 + 1.0)
        indices = np.flatnonzero(m)
        circ = circuito_da_etapa(nome)
        casados = marcas_chao.casar_com_marcas(np.asarray(pes)[m], np.asarray(comprimentos)[m], np.asarray(tempos)[m],
                                               percurso=marcas_chao.CIRCUITOS[circ])
        for c in casados:
            k = int(indices[np.argmin(np.abs(np.asarray(tempos)[m] - c["t"]))])
            reais[k] = c["real_m"]
        print(f"\n  📏 Comprimento do passo x marcas no chão ({rotulo_etapa(nome)}):")
        print(marcas_chao.resumo_texto(casados, percurso=marcas_chao.CIRCUITOS[circ]))
    return reais


def _etapa_altura(nome):
    """'caixa_reto_ESQ_2' -> ('reto', 'ESQ', 2); 'caixa_incl_DIR' -> ('inclinado', 'DIR', None)."""
    m = re.match(r"^caixa_(reto|incl)_(ESQ|DIR)(?:_(\d+))?$", nome)
    if not m:
        return None
    return ("reto" if m.group(1) == "reto" else "inclinado", m.group(2), int(m.group(3)) if m.group(3) else None)


def avaliar_altura_conhecida():
    """Compara a altura medida com o pé sobre cada objeto de teste com a altura real."""
    s = serie_altura
    if len(s.get("t", [])) == 0:
        return
    t = np.asarray(s["t"])
    he, hd = np.asarray(s["altura_esq_cm"]), np.asarray(s["altura_dir_cm"])
    tronco = s.get("tronco_graus")
    # Cada método separado, já com a sua calibração (para comparar OSC e cadeia)
    por_metodo = {}
    for metodo, ce, cd in (("osc", "osc_esq_cm", "osc_dir_cm"), ("cadeia", "cadeia_esq_cm", "cadeia_dir_cm")):
        if s.get(ce) is not None and s.get(cd) is not None:
            por_metodo[metodo] = (np.asarray(aplicar_calibracao(np.asarray(s[ce], dtype=float), metodo, 0)),
                                  np.asarray(aplicar_calibracao(np.asarray(s[cd], dtype=float), metodo, 1)))
    extra = {}
    linhas = []
    for nome, t0, t1 in fases_sessao:
        info = _etapa_altura(nome)
        if info is None:
            continue
        cond, pe, j = info
        ref = ALTURAS_TESTE_CM[j - 1] if j and j <= len(ALTURAS_TESTE_CM) else ALTURA_CAIXA_CM
        rep = 1 + sum(1 for l in linhas if l[0] == ref and l[2] == pe)
        m = (t > t0 + 2.0) & (t < t1 - 0.5)
        if not np.any(m):
            continue
        dif = float(np.median((hd - he)[m]))
        medido = -dif if pe == "ESQ" else dif
        tr = float(np.median(np.abs(np.asarray(tronco)[m] - pitch_tronco_ref))) if tronco is not None else float("nan")
        linhas.append((ref, cond, pe, medido, medido - ref, tr, rep))
        for metodo, (me, md) in por_metodo.items():
            dm = float(np.median((md - me)[m]))
            extra[(len(linhas) - 1, metodo)] = -dm if pe == "ESQ" else dm
    if not linhas:
        return []
    print("\n  📐 Alturas conhecidas")
    for ref, cond, pe, medido, erro, tr, rep in linhas:
        print(f"    objeto {ref:5.1f} cm | {rep}ª vez | tronco {tr:3.0f}° | pé {pe}: {medido:5.1f} cm  (erro {erro:+.1f} cm)")
    print(f"    erro absoluto médio: {np.mean([abs(l[4]) for l in linhas]):.1f} cm")
    refs = sorted({l[0] for l in linhas})
    if any(sum(1 for l in linhas if l[0] == r) > 2 for r in refs):
        print("\n    Resumo por objeto e pé (média ± desvio das repetições):")
        for r in refs:
            for pe in ("ESQ", "DIR"):
                v = [l[3] for l in linhas if l[0] == r and l[2] == pe]
                if v:
                    print(f"    objeto {r:5.1f} cm | pé {pe}: {np.mean(v):5.1f} ± {np.std(v):.1f} cm"
                          f"  (erro médio {np.mean(v) - r:+.1f} cm, {len(v)} vezes)")
    if por_metodo:
        print("\n    Comparação dos métodos (cada um com a sua calibração), erro médio por objeto:")
        for r in refs:
            txt = []
            for metodo in por_metodo:
                v = [extra[(i, metodo)] for i, l in enumerate(linhas) if l[0] == r and (i, metodo) in extra]
                if v:
                    txt.append(f"{metodo} {np.mean(v):5.1f} cm ({np.mean(v) - r:+.1f})")
            print(f"    objeto {r:5.1f} cm | " + " | ".join(txt))
        for metodo in por_metodo:
            v = [abs(extra[(i, metodo)] - l[0]) for i, l in enumerate(linhas) if (i, metodo) in extra]
            if v:
                print(f"    erro absoluto médio pelo {metodo}: {np.mean(v):.1f} cm")
        if curvas_metodo:
            print("    (calibração com a caixa em " + " e ".join(f"{h:.1f}" for h in ALTURAS_CALIBRACAO_CM) + " cm)")
    arq = os.path.join(PASTA_COLETA, "altura_conhecida.csv")
    with open(arq, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        metodos = list(por_metodo)
        w.writerow(["Participante", "Altura_real_cm", "Repeticao", "Pe", "Tronco_graus", "Altura_medida_cm", "Erro_cm"]
                   + [f"Altura_{m}_cm" for m in metodos])
        nome = PARTICIPANTE["nome"] if PARTICIPANTE else ""
        for i, (ref, cond, pe, medido, erro, tr, rep) in enumerate(linhas):
            w.writerow([nome, f"{ref:.2f}", rep, pe, f"{tr:.1f}", f"{medido:.2f}", f"{erro:.2f}"]
                       + [f"{extra[(i, m)]:.2f}" if (i, m) in extra else "" for m in metodos])
    return linhas


def grafico_altura_conhecida(linhas):
    """altura_objetos.png: altura medida em cada subida contra a altura real de cada objeto."""
    import matplotlib.pyplot as plt
    if not linhas:
        return
    refs = sorted({l[0] for l in linhas})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    cores = {"ESQ": "tab:orange", "DIR": "tab:green"}
    for i, r in enumerate(refs):
        for pe, dx in (("ESQ", -0.12), ("DIR", 0.12)):
            v = [l[3] for l in linhas if l[0] == r and l[2] == pe]
            ax1.scatter([i + dx] * len(v), v, color=cores[pe], s=36, zorder=3,
                        label=f"pé {pe}" if i == 0 else None)
        ax1.plot([i - 0.35, i + 0.35], [r, r], color="k", lw=2, zorder=2,
                 label="altura real" if i == 0 else None)
    ax1.set_xticks(range(len(refs)))
    ax1.set_xticklabels([f"{r:.1f} cm" for r in refs])
    ax1.set_xlabel("Objeto")
    ax1.set_ylabel("Altura medida (cm)")
    ax1.set_title("Altura medida em cada subida")
    ax1.set_ylim(bottom=0)
    ax1.legend(frameon=False)
    for pe in ("ESQ", "DIR"):
        e = [l[4] for l in linhas if l[2] == pe]
        x = [l[0] for l in linhas if l[2] == pe]
        ax2.scatter(x, e, color=cores[pe], s=36, label=f"pé {pe}")
    ax2.axhline(0, color="k", lw=1)
    ax2.set_xlabel("Altura real (cm)")
    ax2.set_ylabel("Erro (medido − real, cm)")
    ax2.set_title(f"Erro absoluto médio: {np.mean([abs(l[4]) for l in linhas]):.1f} cm")
    ax2.legend(frameon=False)
    if PARTICIPANTE:
        fig.suptitle(f"{PARTICIPANTE['nome']} — experimento de altura")
    fig.tight_layout()
    salvar_figura(fig, "altura_objetos.png")
    if MOSTRAR_GRAFICOS:
        plt.show()
    plt.close(fig)


def sortear_ordem_objetos(gerador=None):
    """Sorteia a ordem dos objetos de teste e refaz o roteiro (experimento de altura)."""
    global ORDEM_OBJETOS, ROTEIRO
    import random
    g = gerador or random.SystemRandom()
    n = len(ALTURAS_TESTE_CM)
    ORDEM_OBJETOS = g.sample(range(1, n + 1), n)
    ROTEIRO = montar_roteiro(ALTURAS_TESTE_CM or None)
    return ORDEM_OBJETOS


def sortear_ordem_circuitos(gerador=None):
    """Sorteia a ordem dos circuitos desta coleta e refaz o roteiro."""
    global ORDEM_CIRCUITOS, ROTEIRO
    import random
    g = gerador or random.SystemRandom()
    ORDEM_CIRCUITOS = g.sample(CIRCUITOS_COLETA, len(CIRCUITOS_COLETA))
    ROTEIRO = montar_roteiro(ALTURAS_TESTE_CM or None)
    return ORDEM_CIRCUITOS


def definir_alturas_teste(alturas_cm):
    """Guarda as alturas dos objetos de teste e refaz o roteiro com elas."""
    global ALTURAS_TESTE_CM, ROTEIRO
    ALTURAS_TESTE_CM = list(alturas_cm)
    ROTEIRO = montar_roteiro(ALTURAS_TESTE_CM)


def definir_pasta_saida(pasta):
    """Faz todos os arquivos da coleta irem para a pasta do participante."""
    global PASTA_COLETA, NOME_ARQUIVO_PASSOS, NOME_ARQUIVO_BRUTO, NOME_ARQUIVO_ENTRADA
    global NOME_ARQUIVO_FASES, NOME_ARQUIVO_RELATORIO
    PASTA_COLETA = pasta
    NOME_ARQUIVO_PASSOS = os.path.join(pasta, "resultado_completo_marcha.csv")
    NOME_ARQUIVO_BRUTO = os.path.join(pasta, "dados_brutos_sessao.csv")
    NOME_ARQUIVO_ENTRADA = os.path.join(pasta, "dados_brutos_entrada.json")
    NOME_ARQUIVO_FASES = os.path.join(pasta, "fases_sessao.csv")
    NOME_ARQUIVO_RELATORIO = os.path.join(pasta, "relatorio.txt")


class _Tee:
    """Escreve no terminal e num arquivo ao mesmo tempo (para guardar o relatório)."""
    def __init__(self, arq, terminal):
        self.arq, self.terminal = arq, terminal

    def write(self, s):
        self.terminal.write(s)
        try:
            self.arq.write(s)
        except Exception:
            pass

    def flush(self):
        self.terminal.flush()
        self.arq.flush()


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
            "participante": PARTICIPANTE,
            "circuitos": CIRCUITOS_COLETA,
            "ordem_circuitos": ORDEM_CIRCUITOS,
            "experimento": EXPERIMENTO,
            "ordem_objetos": ORDEM_OBJETOS,
            "repeticoes_altura": REPETICOES_ALTURA if EXPERIMENTO == "altura" else 1,
            "percursos_marcas": {c: marcas_chao.CIRCUITOS[c] for c in CIRCUITOS_COLETA},
            "altura_caixa_calibracao_cm": ALTURA_CAIXA_CM,
            "alturas_teste_cm": ALTURAS_TESTE_CM,
            "modo_altura": MODO_ALTURA,
            "ganho_esq": ganho_esq, "ganho_dir": ganho_dir,
            "ganhos_metodo": {k: list(v) for k, v in ganhos_metodo.items()},
            "alturas_calibracao_cm": list(ALTURAS_CALIBRACAO_CM),
            "curvas_metodo": {k: [[list(p) for p in c] for c in v] for k, v in curvas_metodo.items()},
            "fases": [list(f) for f in fases_sessao],
            "ganhos_cadeia_caixa": list(ganhos_cadeia_caixa),
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
    global rodando, PARTICIPANTE
    # 0. Cadastro do participante e pasta da coleta
    if PEDIR_PARTICIPANTE:
        PARTICIPANTE = cadastro.pedir_participante()
        PARTICIPANTE["rodada"] = cadastro.pedir_rodada()
        # Na rodada de coleta, Enter repete as alturas usadas na coleta anterior do participante
        anterior = cadastro.ultima_coleta(PARTICIPANTE["nome"]) or {}
        padroes = anterior.get("alturas_teste_cm") or ALTURAS_TESTE_PADRAO_CM
        print("   Alturas dos objetos de teste:")
        PARTICIPANTE["alturas_teste_cm"] = [cadastro.pedir_altura_teste(h, faixa=FAIXA_ALTURAS_TESTE_CM)
                                          for h in padroes]
        definir_alturas_teste(PARTICIPANTE["alturas_teste_cm"])
        PARTICIPANTE["experimento"] = EXPERIMENTO
        if EXPERIMENTO == "altura":
            # Familiarização na ordem 1, 2, 3; na rodada de coleta a ordem dos objetos é sorteada
            if PARTICIPANTE["rodada"] == "coleta" and SORTEAR_ORDEM_OBJETOS:
                sortear_ordem_objetos()
            PARTICIPANTE["ordem_objetos"] = list(ORDEM_OBJETOS)
            print("   Ordem dos objetos: " + " -> ".join(
                f"{ALTURAS_TESTE_CM[j - 1]:.1f} cm" for j in (ORDEM_OBJETOS or [1, 2, 3])) + "\n")
        else:
            # Familiarização em ordem fixa (A, B, C); a rodada de coleta tem a ordem sorteada
            if PARTICIPANTE["rodada"] == "coleta" and SORTEAR_ORDEM_CIRCUITOS:
                sortear_ordem_circuitos()
                texto_ordem = "Ordem sorteada dos circuitos"
            else:
                texto_ordem = "Ordem dos circuitos (familiarização, fixa)"
            PARTICIPANTE["ordem_circuitos"] = list(ORDEM_CIRCUITOS)
            print(f"   {texto_ordem}: {' -> '.join(ORDEM_CIRCUITOS)}\n")
        pasta = cadastro.criar_pasta_coleta(PARTICIPANTE)
        definir_pasta_saida(pasta)
        print(f"   Os arquivos desta coleta vão para {pasta}\n")
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
    print("   PILOTO — SISTEMA UNIFICADO DE MARCHA (SOLARXR IMU + OSC)")
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
        with lock:
            rodando = False  # a partir daqui as threads de captura não acrescentam mais nada
        print("\n\n[*] Captura concluída! Sincronizando sinais biométricos e calculando métricas...", flush=True)

        if proc_node:
            try:
                proc_node.terminate()
            except Exception:
                pass

        processar_coleta()


def processar_coleta():
    """Calcula os passos, salva os arquivos e gera o relatório e os gráficos da coleta."""
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

    if EXPERIMENTO == "altura":
        terminal = sys.stdout
        with open(NOME_ARQUIVO_RELATORIO, "w", encoding="utf-8") as frel:
            sys.stdout = _Tee(frel, terminal)
            try:
                if PARTICIPANTE:
                    print(f"--- Participante: {PARTICIPANTE['nome']} | altura {PARTICIPANTE['altura_cm']:.1f} cm"
                          f" | ordem dos objetos {' -> '.join(str(j) for j in ORDEM_OBJETOS) or '1 -> 2 -> 3'} ---")
                linhas = avaliar_altura_conhecida()
            finally:
                sys.stdout = terminal
        print(f"Relatório salvo em {NOME_ARQUIVO_RELATORIO}")
        grafico_altura_conhecida(linhas)
        return

    tempos_impactos, dist_impactos, impactos_ms2, pes_passos, projecoes_ap = detectar_passos_multimodal(
        t_passo, d_passo_ap, taxa
    )
    if COMPRIMENTO_PELO_PLATO and len(tempos_impactos) > 0:
        tempos_impactos, dist_impactos, impactos_ms2, pes_passos, projecoes_ap = comprimento_pelo_plato(
            t_passo, d_passo_ap, tempos_impactos, dist_impactos, impactos_ms2, pes_passos, projecoes_ap
        )

    if len(dist_impactos) == 0:
        print("Nenhum passo com a amplitude configurada foi detectado.")
        return

    clearances_passos_cm = calcular_alturas_passos(t_passo, tempos_impactos, pes_passos, clear_esq_cm, clear_dir_cm)
    tornozelos_passos_cm = calcular_alturas_passos(t_passo, tempos_impactos, pes_passos, torn_esq_cm, torn_dir_cm)
    mascara_validos = remover_outliers(dist_impactos)

    terminal = sys.stdout
    with open(NOME_ARQUIVO_RELATORIO, "w", encoding="utf-8") as frel:
        sys.stdout = _Tee(frel, terminal)
        try:
            reais = {}
            try:
                reais = comparar_com_marcas(tempos_impactos, pes_passos, dist_impactos)
            except Exception as e:
                print(f"Não foi possível comparar com as marcas: {e}")
            try:
                avaliar_altura_conhecida()
            except Exception as e:
                print(f"Não foi possível avaliar a altura conhecida: {e}")
            gerar_relatorio_e_graficos(
                t_passo, d_passo_ap, larg_base_m,
                clear_esq_cm, clear_dir_cm, torn_esq_cm, torn_dir_cm,
                alt_quadril_cm, posturas,
                tempos_impactos, dist_impactos, impactos_ms2,
                clearances_passos_cm, tornozelos_passos_cm,
                pes_passos, projecoes_ap, mascara_validos, reais_m=reais
            )
        finally:
            sys.stdout = terminal
    print(f"Relatório salvo em {NOME_ARQUIVO_RELATORIO}")
    print(f"\n📁 Todos os arquivos desta coleta estão em {PASTA_COLETA}")

    if MOSTRAR_GRAFICOS:
        import matplotlib.pyplot as plt
        plt.show()


if __name__ == "__main__":
    main()
