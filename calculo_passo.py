import csv
import time
import math
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# ======================== CONFIGURAÇÃO ========================
TORNOZELO_ESQ = "2"
TORNOZELO_DIR = "3"
TRACKER_PEITO = "6"

# Filtro passa-baixa (Butterworth)
FREQUENCIA_CORTE = 5.0  # Hz
ORDEM_FILTRO = 2

# Detecção de picos (passos)
DISTANCIA_MINIMA_PASSO = 0.50  # metros (ignora meias-voltas e oscilações menores)
PROEMINENCIA_MINIMA = 0.15     # metros de destaque em relação aos vales

# Tratamento de outliers (MAD - Median Absolute Deviation)
LIMIAR_MAD = 3.0               # Z-score modificado limite (Iglewicz & Hoaglin)

# Arquivos CSV de saída
NOME_ARQUIVO_PASSOS = "resultado_passos.csv"
NOME_ARQUIVO_BRUTO = "dados_marcha_paciente.csv"

# Prefixo OSC
PREFIXO = "/tracking/trackers/"
INTERVALO_FEEDBACK = 0.5
# ==============================================================

# Armazenamento em memória
dados = {
    TORNOZELO_ESQ: {"t": [], "x": [], "y": [], "z": []},
    TORNOZELO_DIR: {"t": [], "x": [], "y": [], "z": []},
    TRACKER_PEITO: {"t": [], "pitch": []},
}

tempo_inicial = None
pacotes = 0
ultimo_feedback = 0

pos_atual = {TORNOZELO_ESQ: None, TORNOZELO_DIR: None}
rot_atual = {TRACKER_PEITO: None}
min_y_visto = {TORNOZELO_ESQ: float("inf"), TORNOZELO_DIR: float("inf")}

def calcular_inclinacao(rotacao):
    """
    Converte quaternion ou Euler para inclinação frontal (pitch) em graus.
    Valores negativos = tronco inclinado para frente.
    Valores positivos = tronco inclinado para trás.
    """
    if len(rotacao) == 4:
        x, y, z, w = rotacao
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))
        return math.degrees(math.asin(sinp))
    elif len(rotacao) == 3:
        pitch = float(rotacao[0])
        if pitch > 180:
            pitch -= 360
        return pitch
    return 0.0

def osc_handler(address, *args):
    global tempo_inicial, pacotes
    
    if not address.startswith(PREFIXO):
        return
    
    partes = address.split("/")
    if len(partes) < 5:
        return
    
    tracker_id = partes[3]
    tipo_dado = partes[4]  # 'position' ou 'rotation'
    
    tempo_atual = time.time()
    if tempo_inicial is None:
        tempo_inicial = tempo_atual
    t_rel = tempo_atual - tempo_inicial
    
    # Captura posições dos tornozelos
    if tipo_dado == "position" and len(args) == 3:
        x, y, z = args
        if tracker_id in (TORNOZELO_ESQ, TORNOZELO_DIR):
            dados[tracker_id]["t"].append(t_rel)
            dados[tracker_id]["x"].append(x)
            dados[tracker_id]["y"].append(y)
            dados[tracker_id]["z"].append(z)
            pos_atual[tracker_id] = (x, y, z)
            min_y_visto[tracker_id] = min(min_y_visto[tracker_id], y)
            pacotes += 1
            
            with open(NOME_ARQUIVO_BRUTO, mode='a', newline='') as f:
                escritor = csv.writer(f)
                escritor.writerow([tempo_atual, tracker_id, "position", x, y, z])
                
    # Captura rotação do peito
    elif tipo_dado == "rotation" and len(args) >= 3:
        if tracker_id == TRACKER_PEITO:
            pitch = calcular_inclinacao(args)
            dados[TRACKER_PEITO]["t"].append(t_rel)
            dados[TRACKER_PEITO]["pitch"].append(pitch)
            rot_atual[TRACKER_PEITO] = pitch
            pacotes += 1
            
            with open(NOME_ARQUIVO_BRUTO, mode='a', newline='') as f:
                escritor = csv.writer(f)
                escritor.writerow([tempo_atual, tracker_id, "rotation", pitch, "", ""])

def classificar_postura(angulo):
    if abs(angulo) < 5.0:
        return "Ereto ✓"
    elif angulo > 0:
        return "Inclinado Trás"
    else:
        return "Inclinado Frente"

def mostrar_feedback():
    """
    Mostra métricas em tempo real no terminal durante a captura:
    distância horizontal, altura instantânea de cada pé e postura do tronco.
    """
    global ultimo_feedback
    agora = time.time()
    
    if agora - ultimo_feedback < INTERVALO_FEEDBACK:
        return
    ultimo_feedback = agora
    
    p_esq = pos_atual[TORNOZELO_ESQ]
    p_dir = pos_atual[TORNOZELO_DIR]
    p_peito = rot_atual[TRACKER_PEITO]
    
    if p_esq is None or p_dir is None:
        esperando = []
        if p_esq is None: esperando.append(f"Tornozelo ESQ ({TORNOZELO_ESQ})")
        if p_dir is None: esperando.append(f"Tornozelo DIR ({TORNOZELO_DIR})")
        print(f"\r  Aguardando trackers: {', '.join(esperando)}...", end="", flush=True)
        return
    
    tempo = agora - tempo_inicial if tempo_inicial else 0
    dist = math.sqrt((p_dir[0] - p_esq[0])**2 + (p_dir[2] - p_esq[2])**2)
    
    # Altura instantânea em relação ao piso estimado
    alt_esq_cm = max(0.0, (p_esq[1] - min_y_visto[TORNOZELO_ESQ]) * 100)
    alt_dir_cm = max(0.0, (p_dir[1] - min_y_visto[TORNOZELO_DIR]) * 100)
    
    barra_tamanho = int(min(dist / 1.0, 1.0) * 16)
    barra = "█" * barra_tamanho + "░" * (16 - barra_tamanho)
    
    postura_str = ""
    if p_peito is not None:
        status_postura = classificar_postura(p_peito)
        postura_str = f" | Tronco: {p_peito:+.1f}° ({status_postura})"
    
    total_amostras = len(dados[TORNOZELO_ESQ]["t"]) + len(dados[TORNOZELO_DIR]["t"]) + len(dados[TRACKER_PEITO]["t"])
    print(f"\r  ⏱ {tempo:5.1f}s | Passos: {dist:.3f}m [{barra}] | Altura: E={alt_esq_cm:4.1f}cm D={alt_dir_cm:4.1f}cm{postura_str} | Pkts: {total_amostras}  ", end="", flush=True)

def aplicar_filtro(sinal, taxa_amostragem):
    nyquist = taxa_amostragem / 2.0
    freq_norm = FREQUENCIA_CORTE / nyquist
    if freq_norm >= 1.0:
        return sinal
    b, a = butter(ORDEM_FILTRO, freq_norm, btype='low')
    return filtfilt(b, a, sinal)

def calcular_sinais_tornozelos(dados_esq, dados_dir):
    """
    Sincroniza timestamps e calcula distância horizontal e elevações verticais dos pés.
    """
    t_esq = np.array(dados_esq["t"])
    x_esq = np.array(dados_esq["x"])
    y_esq = np.array(dados_esq["y"])
    z_esq = np.array(dados_esq["z"])
    
    t_dir = np.array(dados_dir["t"])
    x_dir = np.array(dados_dir["x"])
    y_dir = np.array(dados_dir["y"])
    z_dir = np.array(dados_dir["z"])
    
    t_inicio = max(t_esq[0], t_dir[0])
    t_fim = min(t_esq[-1], t_dir[-1])
    
    dt = np.mean(np.diff(t_esq))
    taxa_amostragem = 1.0 / dt if dt > 0 else 50.0
    
    t_comum = np.arange(t_inicio, t_fim, dt)
    x_esq_interp = np.interp(t_comum, t_esq, x_esq)
    z_esq_interp = np.interp(t_comum, t_esq, z_esq)
    x_dir_interp = np.interp(t_comum, t_dir, x_dir)
    z_dir_interp = np.interp(t_comum, t_dir, z_dir)
    
    # Distância horizontal (X-Z)
    dist = np.sqrt((x_dir_interp - x_esq_interp)**2 + (z_dir_interp - z_esq_interp)**2)
    
    # Elevação vertical (Y)
    y_esq_interp = np.interp(t_comum, t_esq, y_esq)
    y_dir_interp = np.interp(t_comum, t_dir, y_dir)
    
    y_esq_filt = aplicar_filtro(y_esq_interp, taxa_amostragem)
    y_dir_filt = aplicar_filtro(y_dir_interp, taxa_amostragem)
    
    # Linha base do chão (percentil 5 para evitar ruído de contato)
    chao_esq = np.percentile(y_esq_filt, 5)
    chao_dir = np.percentile(y_dir_filt, 5)
    
    elevacao_esq = np.maximum(0.0, y_esq_filt - chao_esq)
    elevacao_dir = np.maximum(0.0, y_dir_filt - chao_dir)
    
    return t_comum, dist, elevacao_esq, elevacao_dir, taxa_amostragem

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
    """
    Para cada passo detectado, identifica qual pé realizou o balanço
    e calcula a altura máxima (clearance) atingida durante a passada.
    """
    alturas = []
    pes = []
    
    for i, t_pico in enumerate(tempos_picos):
        # Janela de balanço do passo: do passo anterior até o passo atual
        t_inicio = tempos_picos[i - 1] if i > 0 else max(t[0], t_pico - 0.8)
        t_fim = t_pico
        
        mask = (t >= t_inicio) & (t <= t_fim)
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
    """
    Identifica outliers usando o método MAD (Median Absolute Deviation).
    Utiliza o Z-score modificado (Boris Iglewicz & David Hoaglin):
        M_i = 0.6745 * |x_i - mediana| / MAD
    Um passo é considerado outlier se M_i > limiar (padrão = 3.0).
    """
    if len(valores) < 4:
        return np.ones(len(valores), dtype=bool)
    
    mediana = np.median(valores)
    desvios = np.abs(valores - mediana)
    mad = np.median(desvios)
    
    if mad == 0:
        mad = np.mean(desvios)
        if mad == 0:
            return np.ones(len(valores), dtype=bool)
            
    z_modificado = 0.6745 * desvios / mad
    return z_modificado <= limiar

def gerar_relatorio_e_graficos(t_passo, dist, dist_filtrada, elevacao_esq, elevacao_dir,
                               tempos_picos, dist_picos, alturas_picos, pes_picos, mascara_validos):
    print("\n" + "=" * 70)
    print("      RELATÓRIO COMPLETO DE MARCHA E BIOMECÂNICA (SLIMEVR)")
    print("=" * 70)
    
    n_total = len(dist_picos)
    n_outliers = np.sum(~mascara_validos)
    passos_validos = dist_picos[mascara_validos]
    tempos_validos = tempos_picos[mascara_validos]
    alturas_validas = alturas_picos[mascara_validos]
    pes_validos = pes_picos[mascara_validos]
    
    print(f"Duração da sessão: {t_passo[-1]:.1f} s")
    print(f"Passos identificados: {n_total} (Válidos: {len(passos_validos)} | Outliers: {n_outliers} via MAD)")
    
    # Estatísticas de postura (peito)
    tem_peito = len(dados[TRACKER_PEITO]["t"]) >= 10
    inclinacoes_nos_passos = []
    
    if tem_peito:
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        inclinacoes_nos_passos = np.interp(tempos_picos, t_peito, pitch_peito)
        
        media_tronco = np.mean(pitch_peito)
        desvio_tronco = np.std(pitch_peito)
        print(f"\n--- Postura do Tronco (Tracker {TRACKER_PEITO}) ---")
        print(f"  Inclinação média:    {media_tronco:+.2f}° ({classificar_postura(media_tronco)})")
        print(f"  Desvio padrão:       {desvio_tronco:.2f}° (estabilidade)")
        print(f"  Variação angular:    {np.min(pitch_peito):+.2f}° até {np.max(pitch_peito):+.2f}°")
    else:
        inclinacoes_nos_passos = [0.0] * n_total
        print("\n[Aviso] Dados do tracker de peito insuficientes para análise postural.")

    print(f"\n--- Estatísticas do Comprimento do Passo (sem outliers) ---")
    if len(passos_validos) > 0:
        media_passo = np.mean(passos_validos)
        desvio_passo = np.std(passos_validos)
        print(f"  Comprimento médio:   {media_passo:.3f} m")
        print(f"  Desvio padrão:       {desvio_passo:.3f} m")
        print(f"  Menor passo:         {np.min(passos_validos):.3f} m")
        print(f"  Maior passo:         {np.max(passos_validos):.3f} m")
        if len(tempos_validos) >= 2:
            cadencia = 60.0 / np.mean(np.diff(tempos_validos))
            print(f"  Cadência estimada:   {cadencia:.1f} passos/min")
    else:
        media_passo = 0.0
        print("  Nenhum passo válido registrado.")

    print(f"\n--- Estatísticas da Altura da Passada (Elevação dos Pés) ---")
    if len(alturas_validas) > 0:
        media_alt = np.mean(alturas_validas) * 100
        desvio_alt = np.std(alturas_validas) * 100
        
        # Separação por pé
        mask_esq = pes_validos == "ESQ"
        mask_dir = pes_validos == "DIR"
        alt_esq_cm = np.mean(alturas_validas[mask_esq]) * 100 if np.any(mask_esq) else 0.0
        alt_dir_cm = np.mean(alturas_validas[mask_dir]) * 100 if np.any(mask_dir) else 0.0
        
        print(f"  Altura média geral:  {media_alt:.1f} cm (± {desvio_alt:.1f} cm)")
        print(f"  Pé Esquerdo médio:   {alt_esq_cm:.1f} cm ({np.sum(mask_esq)} passos)")
        print(f"  Pé Direito médio:    {alt_dir_cm:.1f} cm ({np.sum(mask_dir)} passos)")
        
        if alt_esq_cm > 0 and alt_dir_cm > 0:
            simetria = (min(alt_esq_cm, alt_dir_cm) / max(alt_esq_cm, alt_dir_cm)) * 100
            print(f"  Índice de Simetria:  {simetria:.1f}%")
    else:
        print("  Dados de elevação insuficientes.")

    print("\n--- Tabela Detalhada de Passos ---")
    print(f"{'#':<4} {'Tempo(s)':<10} {'Pé':<6} {'Comprim.(m)':<14} {'Altura(cm)':<14} {'Tronco':<12} {'Status'}")
    print("-" * 72)
    for i, (t_p, d_p, alt_p, pe_p, inc_p) in enumerate(zip(tempos_picos, dist_picos, alturas_picos, pes_picos, inclinacoes_nos_passos)):
        valido = mascara_validos[i]
        status = "Válido" if valido else "⚠ Outlier"
        tronco_str = f"{inc_p:+.1f}°" if tem_peito else "N/A"
        print(f"{i+1:<4} {t_p:<10.2f} {pe_p:<6} {d_p:<14.3f} {alt_p*100:<14.1f} {tronco_str:<12} {status}")
    print("=" * 70)

    # Gravação no CSV consolidado
    with open(NOME_ARQUIVO_PASSOS, mode='w', newline='') as f:
        escritor = csv.writer(f)
        escritor.writerow(["Passo", "Tempo_s", "Pe", "Comprimento_m", "Altura_Passada_cm", "Inclinacao_Tronco_graus", "Outlier"])
        for i, (t_p, d_p, alt_p, pe_p, inc_p) in enumerate(zip(tempos_picos, dist_picos, alturas_picos, pes_picos, inclinacoes_nos_passos)):
            escritor.writerow([
                i+1, f"{t_p:.3f}", pe_p, f"{d_p:.3f}", f"{alt_p*100:.2f}",
                f"{inc_p:.2f}", "NAO" if mascara_validos[i] else "SIM"
            ])
    print(f"\nResultados consolidados salvos em '{NOME_ARQUIVO_PASSOS}'.")

    # ================= GRÁFICOS =================
    num_subplots = 4 if tem_peito else 3
    fig, axes = plt.subplots(num_subplots, 1, figsize=(14, 3.2 * num_subplots))
    
    if tem_peito:
        ax_dist, ax_alt, ax_tronco, ax_bar = axes
    else:
        ax_dist, ax_alt, ax_bar = axes
        ax_tronco = None

    # Subplot 1: Comprimento dos Passos (Distância entre tornozelos)
    ax_dist.plot(t_passo, dist, color='lightblue', alpha=0.4, label='Distância bruta')
    ax_dist.plot(t_passo, dist_filtrada, color='blue', label='Distância filtrada')
    ax_dist.plot(tempos_picos[mascara_validos], dist_picos[mascara_validos], 'gv', markersize=9, label='Passo válido')
    if n_outliers > 0:
        ax_dist.plot(tempos_picos[~mascara_validos], dist_picos[~mascara_validos], 'rx', markersize=10, markeredgewidth=2, label='Outlier (MAD)')
    if len(passos_validos) > 0:
        ax_dist.axhline(media_passo, color='green', linestyle='--', label=f'Média: {media_passo:.3f} m')
    ax_dist.set_ylabel('Distância (m)')
    ax_dist.set_title('Comprimento dos Passos (Distância Horizontal entre Tornozelos)')
    ax_dist.legend(loc='upper right')
    ax_dist.grid(True, alpha=0.3)

    # Subplot 2: Trajetória Vertical dos Tornozelos (Altura da Passada)
    ax_alt.plot(t_passo, elevacao_esq * 100, color='dodgerblue', label='Elevação Pé Esquerdo (cm)')
    ax_alt.plot(t_passo, elevacao_dir * 100, color='darkorange', label='Elevação Pé Direito (cm)')
    ax_alt.set_ylabel('Elevação (cm)')
    ax_alt.set_title('Altura da Passada — Elevação Vertical dos Pés ao Longo do Tempo')
    ax_alt.legend(loc='upper right')
    ax_alt.grid(True, alpha=0.3)

    # Subplot 3: Postura do Peito (se disponível)
    if ax_tronco is not None:
        t_peito = np.array(dados[TRACKER_PEITO]["t"])
        pitch_peito = np.array(dados[TRACKER_PEITO]["pitch"])
        ax_tronco.plot(t_peito, pitch_peito, color='purple', label='Inclinação do Peito (Pitch)')
        ax_tronco.axhline(0, color='gray', linestyle=':', label='Referência vertical (0°)')
        ax_tronco.axhline(np.mean(pitch_peito), color='darkmagenta', linestyle='--', label=f'Média: {np.mean(pitch_peito):+.1f}°')
        ax_tronco.set_ylabel('Inclinação (°)')
        ax_tronco.set_title('Postura do Tronco ao Longo da Marcha')
        ax_tronco.legend(loc='upper right')
        ax_tronco.grid(True, alpha=0.3)

    # Subplot 4: Gráfico de Barras Comparativo (Comprimento e Altura por Passo)
    x_indices = np.arange(1, n_total + 1)
    largura = 0.38
    
    ax_bar.bar(x_indices - largura/2, dist_picos, width=largura, color='steelblue', label='Comprimento (m)')
    ax_bar.set_ylabel('Comprimento (m)', color='steelblue')
    ax_bar.tick_params(axis='y', labelcolor='steelblue')
    ax_bar.set_xlabel('Número do Passo')
    ax_bar.set_title('Comprimento (m) e Altura da Passada (cm) por Passo')
    ax_bar.set_xticks(x_indices)
    
    ax_bar_twin = ax_bar.twinx()
    ax_bar_twin.bar(x_indices + largura/2, alturas_picos * 100, width=largura, color='coral', label='Altura (cm)')
    ax_bar_twin.set_ylabel('Altura (cm)', color='coral')
    ax_bar_twin.tick_params(axis='y', labelcolor='coral')
    ax_bar.grid(True, alpha=0.3, axis='x')

    plt.tight_layout()
    plt.show()

def main():
    with open(NOME_ARQUIVO_BRUTO, mode='w', newline='') as f:
        escritor = csv.writer(f)
        escritor.writerow(['Timestamp', 'Tracker_ID', 'Tipo', 'Valor1', 'Valor2', 'Valor3'])

    disp = Dispatcher()
    disp.set_default_handler(osc_handler)

    server = ThreadingOSCUDPServer(("127.0.0.1", 9000), disp)
    print("=" * 60)
    print("    MONITOR INTEGRADO DE MARCHA E POSTURA (SLIMEVR)")
    print("=" * 60)
    print(f"Tornozelo ESQ: Tracker {TORNOZELO_ESQ}")
    print(f"Tornozelo DIR: Tracker {TORNOZELO_DIR}")
    print(f"Peito/Tronco : Tracker {TRACKER_PEITO} (Angulação da postura)")
    print(f"Filtro       : Butterworth {ORDEM_FILTRO}ª ordem ({FREQUENCIA_CORTE} Hz)")
    print(f"Limiares     : Mínimo {DISTANCIA_MINIMA_PASSO}m | Proeminência {PROEMINENCIA_MINIMA}m | Outliers: MAD (z={LIMIAR_MAD})")
    print("-" * 60)
    print("-> Caminhe normalmente pelo ambiente.")
    print("-> Pressione 'Ctrl + C' para encerrar e gerar o relatório.\n")

    server.timeout = 0
    try:
        while True:
            server.handle_request()
            mostrar_feedback()
            time.sleep(0.001)
    except KeyboardInterrupt:
        print("\n\nCaptura finalizada! Processando sinais...")
        
        d_esq = dados[TORNOZELO_ESQ]
        d_dir = dados[TORNOZELO_DIR]
        
        if len(d_esq["t"]) < 20 or len(d_dir["t"]) < 20:
            print("Dados insuficientes de passos para análise. Caminhe por mais tempo.")
            return

        t_passo, dist, elevacao_esq, elevacao_dir, taxa = calcular_sinais_tornozelos(d_esq, d_dir)
        dist_filtrada = aplicar_filtro(dist, taxa)
        _, tempos_picos, dist_picos = detectar_passos(t_passo, dist_filtrada)

        if len(dist_picos) == 0:
            print("Nenhum passo com a amplitude configurada foi detectado.")
            return

        # Calcula a altura da passada e identifica o pé de cada passo
        alturas_picos, pes_picos = calcular_alturas_passos(t_passo, tempos_picos, elevacao_esq, elevacao_dir)

        mascara_validos = remover_outliers(dist_picos)
        gerar_relatorio_e_graficos(
            t_passo, dist, dist_filtrada, elevacao_esq, elevacao_dir,
            tempos_picos, dist_picos, alturas_picos, pes_picos, mascara_validos
        )

if __name__ == "__main__":
    main()
