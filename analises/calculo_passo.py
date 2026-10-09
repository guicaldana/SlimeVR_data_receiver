import csv
import sys
import time
import math
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
PORTA_OSC = 9000     # Trackers OSC do VRChat (Tornozelos e Peito)
PORTA_VMC = 39539    # Captura Virtual de Movimentos - VMC (Cabeça)
IP = "127.0.0.1"

# Identificação dos trackers
TORNOZELO_ESQ = "2"
TORNOZELO_DIR = "3"
TRACKER_PEITO = "6"
OSSO_CABECA = "neck"       # No SlimeVR VMC, a inclinação da cabeça atua no osso 'Neck'
INVERTER_PITCH_CABECA = True  # True para tracker montado na nuca / trás da cabeça
INVERTER_PITCH_TRONCO = True  # True para inverter frente/trás do peito/tronco

# Filtro passa-baixa (Butterworth)
FREQUENCIA_CORTE = 5.0     # Hz
ORDEM_FILTRO = 2

# Detecção de passos
DISTANCIA_MINIMA_PASSO = 0.30  # metros (aceita passos mais curtos de 30-50cm, corta apenas meias-voltas de ~20cm)
PROEMINENCIA_MINIMA = 0.10     # metros de destaque em relação aos vales
LIMIAR_MAD = 4.5               # Z-score modificado mais tolerante (não descarta passos curtos legítimos)

# Limiares de classificação do olhar (cabeça)
LIMIAR_CIMA = 15.0             # Acima de +15°: Olhando para cima
LIMIAR_BAIXO = -15.0           # Abaixo de -15°: Olhando para baixo
LIMIAR_MUITO_BAIXO = -35.0     # Abaixo de -35°: Olhando muito para baixo (chão)

# Arquivos de saída
NOME_ARQUIVO_PASSOS = "resultado_completo_marcha.csv"
NOME_ARQUIVO_BRUTO = "dados_brutos_sessao.csv"
INTERVALO_FEEDBACK = 0.25      # Atualização do terminal 4 vezes por segundo
# ==============================================================

# Estruturas de dados em memória
dados = {
    TORNOZELO_ESQ: {"t": [], "x": [], "y": [], "z": []},
    TORNOZELO_DIR: {"t": [], "x": [], "y": [], "z": []},
    TRACKER_PEITO: {"t": [], "pitch": []},
    "cabeca": {"t": [], "pitch": [], "yaw": [], "roll": []}
}

tempo_inicial = None
ultimo_feedback = 0
lock = threading.Lock()

# Posições e rotações em tempo real para feedback
pos_atual = {TORNOZELO_ESQ: None, TORNOZELO_DIR: None}
min_y_visto = {TORNOZELO_ESQ: float("inf"), TORNOZELO_DIR: float("inf")}
rot_peito_atual = None
rot_cabeca_atual = None
ossos_vmc_detectados = set()

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

# ----------------- RECEPÇÃO DE DADOS (OSC & VMC) -----------------
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
        
        # Posições dos tornozelos
        if tipo == "position" and len(args) == 3:
            x, y, z = args
            if tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR):
                dados[tracker_id]["t"].append(t_rel)
                dados[tracker_id]["x"].append(x)
                dados[tracker_id]["y"].append(y)
                dados[tracker_id]["z"].append(z)
                pos_atual[tracker_id] = (x, y, z)
                min_y_visto[tracker_id] = min(min_y_visto[tracker_id], y)
                
        # Rotação do peito
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
        
    if p_esq is None or p_dir is None:
        esperando = []
        if p_esq is None: esperando.append(f"Tornozelo ESQ({TORNOZELO_ESQ})")
        if p_dir is None: esperando.append(f"Tornozelo DIR({TORNOZELO_DIR})")
        print(f"\r  Aguardando trackers: {', '.join(esperando)}...", end="", flush=True)
        return
        
    tempo = agora - t_init if t_init else 0
    dist = math.sqrt((p_dir[0] - p_esq[0])**2 + (p_dir[2] - p_esq[2])**2)
    
    # Elevação instantânea
    alt_esq_cm = max(0.0, (p_esq[1] - min_y_visto[TORNOZELO_ESQ]) * 100)
    alt_dir_cm = max(0.0, (p_dir[1] - min_y_visto[TORNOZELO_DIR]) * 100)
    
    barra_tamanho = int(min(dist / 1.0, 1.0) * 12)
    barra = "#" * barra_tamanho + "-" * (12 - barra_tamanho)
    
    str_peito = f" | Tronco: {p_peito:+4.1f}° [{classificar_postura_tronco(p_peito)}]" if p_peito is not None else ""
    str_cabeca = ""
    if p_cabeca is not None:
        badge, _ = classificar_olhar(p_cabeca)
        str_cabeca = f" | Cabeça: {p_cabeca:+4.1f}° {badge}"
        
    print(f"\r  Tempo: {tempo:4.1f}s | Passos: {dist:.3f}m [{barra}] | Alt: E={alt_esq_cm:4.1f}cm D={alt_dir_cm:4.1f}cm{str_peito}{str_cabeca}  ", end="", flush=True)

# ----------------- PROCESSAMENTO DE SINAIS -----------------
def aplicar_filtro(sinal, taxa_amostragem):
    nyquist = taxa_amostragem / 2.0
    freq_norm = FREQUENCIA_CORTE / nyquist
    if freq_norm >= 1.0:
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
    dt = np.mean(np.diff(t_esq))
    taxa_amostragem = 1.0 / dt if dt > 0 else 50.0
    
    t_comum = np.arange(t_inicio, t_fim, dt)
    x_esq_i, z_esq_i = np.interp(t_comum, t_esq, x_esq), np.interp(t_comum, t_esq, z_esq)
    x_dir_i, z_dir_i = np.interp(t_comum, t_dir, x_dir), np.interp(t_comum, t_dir, z_dir)
    
    dist = np.sqrt((x_dir_i - x_esq_i)**2 + (z_dir_i - z_esq_i)**2)
    
    y_esq_filt = aplicar_filtro(np.interp(t_comum, t_esq, y_esq), taxa_amostragem)
    y_dir_filt = aplicar_filtro(np.interp(t_comum, t_dir, y_dir), taxa_amostragem)
    
    chao_esq = np.percentile(y_esq_filt, 5)
    chao_dir = np.percentile(y_dir_filt, 5)
    
    elev_esq = np.maximum(0.0, y_esq_filt - chao_esq)
    elev_dir = np.maximum(0.0, y_dir_filt - chao_dir)
    
    return t_comum, dist, elev_esq, elev_dir, taxa_amostragem

def detectar_passos(t, dist_filtrada):
    intervalo_min = int(len(t) / (t[-1] - t[0]) * 0.3) if (t[-1] - t[0]) > 0 else 10
    indices_picos, _ = find_peaks(
        dist_filtrada,
        height=DISTANCIA_MINIMA_PASSO,
        distance=intervalo_min,
        prominence=PROEMINENCIA_MINIMA
    )
    return indices_picos, t[indices_picos], dist_filtrada[indices_picos]

def calcular_alturas_passos(t, tempos_picos, elevacao_esq, elevacao_dir):
    alturas, pes = [], []
    for i, t_pico in enumerate(tempos_picos):
        t_inicio = tempos_picos[i - 1] if i > 0 else max(t[0], t_pico - 0.8)
        mask = (t >= t_inicio) & (t <= t_pico)
        if np.any(mask):
            max_esq = np.max(elevacao_esq[mask])
            max_dir = np.max(elevacao_dir[mask])
            if max_dir >= max_esq:
                pes.append("DIR")
                alturas.append(max_dir)
            else:
                pes.append("ESQ")
                alturas.append(max_esq)
        else:
            pes.append("?")
            alturas.append(0.0)
    return np.array(alturas), np.array(pes)

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
def gerar_relatorio_e_graficos(t_passo, dist, dist_filtrada, elev_esq, elev_dir,
                               tempos_picos, dist_picos, alturas_picos, pes_picos, mascara_validos):
    print("\n\n" + "=" * 75)
    print("      RELATÓRIO UNIFICADO: MARCHA, BIOMECÂNICA E ATENÇÃO POSTURAL")
    print("=" * 75)
    
    n_total = len(dist_picos)
    n_outliers = np.sum(~mascara_validos)
    passos_validos = dist_picos[mascara_validos]
    tempos_validos = tempos_picos[mascara_validos]
    alturas_validas = alturas_picos[mascara_validos]
    pes_validos = pes_picos[mascara_validos]
    
    duracao = t_passo[-1]
    print(f"Duração da sessão: {duracao:.1f} s")
    print(f"Passos totais:     {n_total} (Válidos: {len(passos_validos)} | Outliers: {n_outliers} via MAD)")
    
    # 1. Estatísticas de Postura do Tronco
    tem_peito = len(dados[TRACKER_PEITO]["t"]) >= 10
    tronco_nos_passos = []
    if tem_peito:
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        tronco_nos_passos = np.interp(tempos_picos, t_peito, pitch_peito)
        med_tronco = np.mean(pitch_peito)
        std_tronco = np.std(pitch_peito)
        print(f"\n--- Postura do Tronco (Peito - Tracker {TRACKER_PEITO}) ---")
        print(f"  Inclinação média:    {med_tronco:+.2f}° ({classificar_postura_tronco(med_tronco)})")
        print(f"  Estabilidade (±DP):  {std_tronco:.2f}°")
    else:
        tronco_nos_passos = [0.0] * n_total
        print("\n[Aviso] Dados do tracker de peito insuficientes.")

    # 2. Estatísticas da Cabeça e Olhar
    tem_cabeca = len(dados["cabeca"]["t"]) >= 10
    cabeca_nos_passos = []
    if tem_cabeca:
        t_cabeca = np.array(dados["cabeca"]["t"])
        pitch_cabeca = np.array(dados["cabeca"]["pitch"])
        cabeca_nos_passos = np.interp(tempos_picos, t_cabeca, pitch_cabeca)
        
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
        print(f"  Olhar no Chão / Pés  [< -35°]:      {pct_chao:5.1f}% ({t_chao:.1f}s) ⚠️")
        print(f"  Olhar para Cima      [> +15°]:      {pct_cima:5.1f}% ({t_cima:.1f}s)")
    else:
        cabeca_nos_passos = [0.0] * n_total
        print("\n[Aviso] Dados da cabeça (VMC) insuficientes.")

    # 3. Estatísticas de Passada (Comprimento e Altura)
    print(f"\n--- Parâmetros Espaciais da Marcha (sem outliers) ---")
    if len(passos_validos) > 0:
        media_passo = np.mean(passos_validos)
        desvio_passo = np.std(passos_validos)
        media_alt = np.mean(alturas_validas) * 100
        desvio_alt = np.std(alturas_validas) * 100
        
        print(f"  Comprimento médio:   {media_passo:.3f} m (± {desvio_passo:.3f} m)")
        print(f"  Altura média (lift): {media_alt:.1f} cm (± {desvio_alt:.1f} cm)")
        
        mask_esq = pes_validos == "ESQ"
        mask_dir = pes_validos == "DIR"
        alt_e = np.mean(alturas_validas[mask_esq]) * 100 if np.any(mask_esq) else 0.0
        alt_d = np.mean(alturas_validas[mask_dir]) * 100 if np.any(mask_dir) else 0.0
        print(f"  Elevação Pé ESQ:     {alt_e:.1f} cm | Pé DIR: {alt_d:.1f} cm")
        if alt_e > 0 and alt_d > 0:
            simetria = (min(alt_e, alt_d) / max(alt_e, alt_d)) * 100
            print(f"  Índice de Simetria:  {simetria:.1f}%")
            
        if len(tempos_validos) >= 2:
            cadencia = 60.0 / np.mean(np.diff(tempos_validos))
            print(f"  Cadência estimada:   {cadencia:.1f} passos/min")
    else:
        media_passo = 0.0

    # 4. Tabela Integrada
    print("\n--- Tabela Integrada Passo a Passo ---")
    print(f"{'#':<4} {'Tempo(s)':<9} {'Pé':<5} {'Comprim.(m)':<13} {'Altura(cm)':<12} {'Tronco':<10} {'Cabeça':<10} {'Olhar':<14} {'Status'}")
    print("-" * 88)
    for i, (t_p, d_p, alt_p, pe_p, tr_p, cb_p) in enumerate(zip(tempos_picos, dist_picos, alturas_picos, pes_picos, tronco_nos_passos, cabeca_nos_passos)):
        valido = mascara_validos[i]
        status = "Válido" if valido else "⚠ Outlier"
        _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
        tr_str = f"{tr_p:+.1f}°" if tem_peito else "N/A"
        cb_str = f"{cb_p:+.1f}°" if tem_cabeca else "N/A"
        print(f"{i+1:<4} {t_p:<9.2f} {pe_p:<5} {d_p:<13.3f} {alt_p*100:<12.1f} {tr_str:<10} {cb_str:<10} {olhar_lbl:<14} {status}")
    print("=" * 75)

    # 5. Salva CSV Unificado
    with open(NOME_ARQUIVO_PASSOS, mode='w', newline='') as f:
        escritor = csv.writer(f)
        escritor.writerow(["Passo", "Tempo_s", "Pe", "Comprimento_m", "Altura_Passada_cm", "Tronco_graus", "Cabeca_Pitch_graus", "Estado_Olhar", "Outlier"])
        for i, (t_p, d_p, alt_p, pe_p, tr_p, cb_p) in enumerate(zip(tempos_picos, dist_picos, alturas_picos, pes_picos, tronco_nos_passos, cabeca_nos_passos)):
            _, olhar_lbl = classificar_olhar(cb_p) if tem_cabeca else ("", "N/A")
            escritor.writerow([
                i+1, f"{t_p:.3f}", pe_p, f"{d_p:.3f}", f"{alt_p*100:.2f}",
                f"{tr_p:.2f}", f"{cb_p:.2f}", olhar_lbl, "NAO" if mascara_validos[i] else "SIM"
            ])
    print(f"\nResultados consolidados salvos em '{NOME_ARQUIVO_PASSOS}'.")

    # ================= GRÁFICOS INTEGRADOS (5 PAINÉIS) =================
    painéis_extras = (1 if tem_peito else 0) + (1 if tem_cabeca else 0)
    total_subplots = 3 + painéis_extras
    fig, axes = plt.subplots(total_subplots, 1, figsize=(14, 2.7 * total_subplots), sharex=False)
    
    idx_ax = 0
    
    # Painel 1: Comprimento dos Passos
    ax_dist = axes[idx_ax]
    ax_dist.plot(t_passo, dist, color='lightblue', alpha=0.4, label='Distância bruta')
    ax_dist.plot(t_passo, dist_filtrada, color='blue', label='Distância filtrada')
    ax_dist.plot(tempos_picos[mascara_validos], dist_picos[mascara_validos], 'gv', markersize=8, label='Passo válido')
    if n_outliers > 0:
        ax_dist.plot(tempos_picos[~mascara_validos], dist_picos[~mascara_validos], 'rx', markersize=9, markeredgewidth=2, label='Outlier (MAD)')
    if len(passos_validos) > 0:
        ax_dist.axhline(media_passo, color='green', linestyle='--', label=f'Média: {media_passo:.3f} m')
    ax_dist.set_ylabel('Distância (m)')
    ax_dist.set_title('1. Comprimento dos Passos (Distância Horizontal entre Tornozelos)')
    ax_dist.legend(loc='upper right', fontsize=8)
    ax_dist.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 2: Altura dos Passos (Trajetória Vertical)
    ax_alt = axes[idx_ax]
    ax_alt.plot(t_passo, elev_esq * 100, color='dodgerblue', label='Elevação Pé ESQ (cm)')
    ax_alt.plot(t_passo, elev_dir * 100, color='darkorange', label='Elevação Pé DIR (cm)')
    ax_alt.set_ylabel('Elevação (cm)')
    ax_alt.set_title('2. Altura da Passada (Clearance Vertical dos Tornozelos)')
    ax_alt.legend(loc='upper right', fontsize=8)
    ax_alt.grid(True, alpha=0.3)
    idx_ax += 1

    # Painel 3: Postura do Tronco (se houver)
    if tem_peito:
        ax_tr = axes[idx_ax]
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        ax_tr.plot(t_peito, pitch_peito, color='purple', label='Inclinação do Tronco (Pitch)')
        ax_tr.axhline(0, color='gray', linestyle=':', label='Vertical (0°)')
        ax_tr.axhline(np.mean(pitch_peito), color='darkmagenta', linestyle='--', label=f'Média ({np.mean(pitch_peito):+.1f}°)')
        ax_tr.set_ylabel('Tronco (°)')
        ax_tr.set_title('3. Postura e Estabilidade do Tronco')
        ax_tr.legend(loc='upper right', fontsize=8)
        ax_tr.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 4: Orientação da Cabeça e Atenção Visual (se houver)
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
        ax_cb.set_ylim(min(-70, np.min(pitch_cb)-10), max(40, np.max(pitch_cb)+10))
        ax_cb.set_title('4. Direção da Atenção Visual (Inclinação da Cabeça)')
        ax_cb.legend(loc='upper right', fontsize=8, ncol=3)
        ax_cb.grid(True, alpha=0.3)
        idx_ax += 1

    # Painel 5: Gráfico de Barras Comparativo
    ax_bar = axes[idx_ax]
    x_indices = np.arange(1, n_total + 1)
    largura = 0.38
    ax_bar.bar(x_indices - largura/2, dist_picos, width=largura, color='steelblue', label='Comprimento (m)')
    ax_bar.set_ylabel('Comprimento (m)', color='steelblue')
    ax_bar.tick_params(axis='y', labelcolor='steelblue')
    ax_bar.set_xlabel('Número do Passo')
    ax_bar.set_title('5. Relação Comprimento (m) vs Altura (cm) por Passo')
    ax_bar.set_xticks(x_indices)
    
    ax_bar_twin = ax_bar.twinx()
    ax_bar_twin.bar(x_indices + largura/2, alturas_picos * 100, width=largura, color='coral', label='Altura (cm)')
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
    # Cria os servidores para as duas portas em threads paralelas
    t_osc = threading.Thread(target=rodar_servidor, args=(PORTA_OSC, osc_handler_9000), daemon=True)
    t_vmc = threading.Thread(target=rodar_servidor, args=(PORTA_VMC, vmc_handler_39539), daemon=True)
    t_osc.start()
    t_vmc.start()
    
    print("=" * 70)
    print("     SISTEMA UNIFICADO DE ANÁLISE DE MARCHA E POSTURA (SLIMEVR)")
    print("=" * 70)
    print(f"Porta 9000 (VRChat OSC) : Tornozelos ({TORNOZELO_ESQ}, {TORNOZELO_DIR}) e Peito ({TRACKER_PEITO})")
    print(f"Porta 39539 (VMC)       : Cabeça ({OSSO_CABECA.upper()} - Nuca Invertida)")
    print(f"Limiares de Passo       : Mínimo {DISTANCIA_MINIMA_PASSO}m | Proeminência {PROEMINENCIA_MINIMA}m | MAD (z={LIMIAR_MAD})")
    print("-" * 70)
    print("-> Caminhe normalmente pelo ambiente.")
    print("-> Pressione 'Ctrl + C' para finalizar e gerar os relatórios completos.\n")

    try:
        while True:
            mostrar_feedback()
            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\n\nCaptura concluída! Processando todos os sinais biométricos...")
        
        d_esq = dados[TORNOZELO_ESQ]
        d_dir = dados[TORNOZELO_DIR]
        
        if len(d_esq["t"]) < 20 or len(d_dir["t"]) < 20:
            print("Dados insuficientes de passos para análise. Caminhe por mais tempo.")
            return

        t_passo, dist, elev_esq, elev_dir, taxa = calcular_sinais_tornozelos(d_esq, d_dir)
        dist_filtrada = aplicar_filtro(dist, taxa)
        _, tempos_picos, dist_picos = detectar_passos(t_passo, dist_filtrada)

        if len(dist_picos) == 0:
            print("Nenhum passo com a amplitude configurada foi detectado.")
            return

        alturas_picos, pes_picos = calcular_alturas_passos(t_passo, tempos_picos, elev_esq, elev_dir)
        mascara_validos = remover_outliers(dist_picos)

        gerar_relatorio_e_graficos(
            t_passo, dist, dist_filtrada, elev_esq, elev_dir,
            tempos_picos, dist_picos, alturas_picos, pes_picos, mascara_validos
        )

if __name__ == "__main__":
    main()
