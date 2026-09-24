import csv
import sys
import time
import math
import numpy as np
import matplotlib.pyplot as plt
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Garante suporte UTF-8 no terminal Windows para acentuação
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
# Porta de Saída do VMC no SlimeVR (conforme sua tela de captura)
IP = "127.0.0.1"
PORTA_VMC = 39539

# Osso a monitorar no VMC:
# No SlimeVR, a inclinacao/pitch da cabeca e aplicada no osso 'Neck' (Pescoco)
OSSO_ALVO = "neck"

# Inversão do eixo Pitch:
# True quando o tracker está montado na parte de trás da cabeça (nuca)
INVERTER_PITCH = True

# Limiares de ângulo para classificação do olhar (em graus)
# Negativo = Olhando para baixo (flexão cervical)
# Positivo = Olhando para cima (extensão cervical)
LIMIAR_CIMA = 15.0           # Acima de +15°: Olhando para cima
LIMIAR_BAIXO = -15.0         # Abaixo de -15°: Olhando para baixo
LIMIAR_MUITO_BAIXO = -35.0   # Abaixo de -35°: Olhando muito para baixo (chão)

# Arquivos de saída
NOME_ARQUIVO_CSV = "resultado_rotacao_cabeca.csv"
INTERVALO_FEEDBACK = 0.2     # Atualização do terminal 5 vezes por segundo
# ==============================================================

dados_cabeca = {
    "t": [],
    "pitch": [],
    "yaw": [],
    "roll": []
}

tempo_inicial = None
ultimo_feedback = 0
ossos_detectados = set()

def converter_quaternion_para_euler(x, y, z, w):
    """
    Converte o quaternion (x, y, z, w) do VMC para Pitch, Yaw e Roll em graus.
    - Pitch: inclinação vertical da cabeça (cima/baixo).
    - Yaw: rotação horizontal (esquerda/direita).
    - Roll: inclinação lateral.
    """
    # Pitch (eixo X)
    sinp = 2.0 * (w * x - y * z)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.degrees(math.asin(sinp))
    
    # Yaw (eixo Y)
    siny_cosp = 2.0 * (w * y - z * x)
    cosy_cosp = 1.0 - 2.0 * (x * x + y * y)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    
    # Roll (eixo Z)
    sinr_cosp = 2.0 * (w * z + x * y)
    cosr_cosp = 1.0 - 2.0 * (y * y + z * z)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))
    
    # Inverte pitch se montado na parte de trás da cabeça
    if INVERTER_PITCH:
        pitch = -pitch
        
    return pitch, yaw, roll

def classificar_olhar(pitch):
    if pitch > LIMIAR_CIMA:
        return "CIMA [^]", "Olhando para Cima"
    elif pitch >= LIMIAR_BAIXO:
        return "NEUTRO [-]", "Olhar para Frente"
    elif pitch >= LIMIAR_MUITO_BAIXO:
        return "BAIXO [v]", "Olhando para Baixo"
    else:
        return "MUITO BAIXO [!!]", "Olhando Muito para Baixo (Chão)"

def vmc_bone_handler(address, *args):
    """
    Handler para mensagens /VMC/Ext/Bone/Pos:
    Formato VMC: (string name, float px, py, pz, float qx, qy, qz, qw)
    """
    global tempo_inicial
    
    if len(args) < 8:
        return
        
    bone_name = str(args[0])
    ossos_detectados.add(bone_name)
    
    alvo = OSSO_ALVO.lower()
    if alvo in ("neck", "head"):
        if bone_name.lower() not in ("neck", "head"):
            return
        # Prioriza o osso Neck se estiver presente
        if bone_name.lower() == "head" and "Neck" in ossos_detectados:
            return
    elif bone_name.lower() != alvo:
        return
        
    tempo_atual = time.time()
    if tempo_inicial is None:
        tempo_inicial = tempo_atual
    t_rel = tempo_atual - tempo_inicial
    
    # args: [bone_name, px, py, pz, qx, qy, qz, qw]
    qx, qy, qz, qw = args[4], args[5], args[6], args[7]
    pitch, yaw, roll = converter_quaternion_para_euler(qx, qy, qz, qw)
    
    dados_cabeca["t"].append(t_rel)
    dados_cabeca["pitch"].append(pitch)
    dados_cabeca["yaw"].append(yaw)
    dados_cabeca["roll"].append(roll)

def gerar_barra_indicadora(pitch):
    largura = 24
    min_ang = -60.0
    max_ang = 30.0
    
    clamp_pitch = max(min_ang, min(max_ang, pitch))
    pos = int(((clamp_pitch - min_ang) / (max_ang - min_ang)) * (largura - 1))
    pos_zero = int(((0.0 - min_ang) / (max_ang - min_ang)) * (largura - 1))
    
    barra = list("-" * largura)
    barra[pos_zero] = "|"  # Marcador do neutro (0°)
    barra[pos] = "O"      # Indicador do olhar
    return "[" + "".join(barra) + "]"

def mostrar_feedback():
    global ultimo_feedback
    agora = time.time()
    
    if agora - ultimo_feedback < INTERVALO_FEEDBACK:
        return
    ultimo_feedback = agora
    
    if len(dados_cabeca["pitch"]) == 0:
        if len(ossos_detectados) > 0:
            print(f"\r  Conectado! Aguardando osso '{OSSO_ALVO}' (detectados: {len(ossos_detectados)} ossos)...", end="", flush=True)
        else:
            print(f"\r  Aguardando dados VMC na porta {PORTA_VMC}...", end="", flush=True)
        return
        
    t_atual = dados_cabeca["t"][-1]
    pitch_atual = dados_cabeca["pitch"][-1]
    badge, _ = classificar_olhar(pitch_atual)
    barra = gerar_barra_indicadora(pitch_atual)
    total = len(dados_cabeca["t"])
    
    print(f"\r  Tempo: {t_atual:5.1f}s | Pitch: {pitch_atual:+5.1f}° {barra} | Estado: {badge:<18} | Amostras: {total} ", end="", flush=True)

def gerar_relatorio_e_graficos():
    print("\n\n" + "=" * 65)
    print("      RELATÓRIO DE MONITORAMENTO DA CABEÇA / OLHAR (VMC)")
    print("=" * 65)
    
    t = np.array(dados_cabeca["t"])
    pitch = np.array(dados_cabeca["pitch"])
    yaw = np.array(dados_cabeca["yaw"])
    roll = np.array(dados_cabeca["roll"])
    
    total_amostras = len(t)
    if total_amostras < 10:
        print("Dados insuficientes capturados.")
        return
        
    duracao = t[-1] - t[0]
    dt_medio = np.mean(np.diff(t)) if len(t) > 1 else 0.02
    
    tempo_cima = np.sum(pitch > LIMIAR_CIMA) * dt_medio
    tempo_neutro = np.sum((pitch >= LIMIAR_BAIXO) & (pitch <= LIMIAR_CIMA)) * dt_medio
    tempo_baixo = np.sum((pitch < LIMIAR_BAIXO) & (pitch >= LIMIAR_MUITO_BAIXO)) * dt_medio
    tempo_muito_baixo = np.sum(pitch < LIMIAR_MUITO_BAIXO) * dt_medio
    
    pct_cima = (tempo_cima / duracao) * 100 if duracao > 0 else 0
    pct_neutro = (tempo_neutro / duracao) * 100 if duracao > 0 else 0
    pct_baixo = (tempo_baixo / duracao) * 100 if duracao > 0 else 0
    pct_muito_baixo = (tempo_muito_baixo / duracao) * 100 if duracao > 0 else 0
    
    media_pitch = np.mean(pitch)
    std_pitch = np.std(pitch)
    min_pitch = np.min(pitch)
    max_pitch = np.max(pitch)
    
    print(f"Osso monitorado:       {OSSO_ALVO.upper()} (VMC Protocol)")
    print(f"Duração da sessão:     {duracao:.1f} segundos")
    print(f"Taxa de amostragem:    ~{1.0/dt_medio:.1f} Hz ({total_amostras} amostras)")
    print("-" * 65)
    print(f"Inclinação média (Pitch):  {media_pitch:+.1f}° (± {std_pitch:.1f}°)")
    print(f"Máximo para cima:          {max_pitch:+.1f}°")
    print(f"Máximo para baixo:         {min_pitch:+.1f}°")
    print("-" * 65)
    print("DISTRIBUIÇÃO TEMPORAL DA ATENÇÃO VISUAL:")
    print(f"  - Olhar Neutro (Frente)     [-15° a +15°]:  {tempo_neutro:5.1f}s ({pct_neutro:5.1f}%)")
    print(f"  - Olhando para Baixo        [-35° a -15°]:  {tempo_baixo:5.1f}s ({pct_baixo:5.1f}%)")
    print(f"  - Olhando Muito para Baixo  [< -35°]:       {tempo_muito_baixo:5.1f}s ({pct_muito_baixo:5.1f}%) [ALERTA]")
    print(f"  - Olhando para Cima         [> +15°]:       {tempo_cima:5.1f}s ({pct_cima:5.1f}%)")
    print("=" * 65)
    
    with open(NOME_ARQUIVO_CSV, mode='w', newline='') as f:
        escritor = csv.writer(f)
        escritor.writerow(["Tempo_s", "Pitch_graus", "Yaw_graus", "Roll_graus", "Estado_Olhar"])
        for ti, pi, yi, ri in zip(t, pitch, yaw, roll):
            _, estado = classificar_olhar(pi)
            escritor.writerow([f"{ti:.3f}", f"{pi:.2f}", f"{yi:.2f}", f"{ri:.2f}", estado])
    print(f"\nDados detalhados salvos em '{NOME_ARQUIVO_CSV}'.")
    
    # Gráficos
    fig = plt.figure(figsize=(14, 8))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.2, 1.0])
    
    ax_linha = fig.add_subplot(gs[0, :])
    ax_linha.plot(t, pitch, color='black', linewidth=1.5, label='Inclinação Vertical (Pitch)')
    ax_linha.axhline(0, color='gray', linestyle=':', alpha=0.7, label='Linha Neutra (0°)')
    ax_linha.axhline(media_pitch, color='blue', linestyle='--', label=f'Média ({media_pitch:+.1f}°)')
    
    ax_linha.axhspan(LIMIAR_CIMA, 60, color='dodgerblue', alpha=0.12, label='Zona: Olhar p/ Cima')
    ax_linha.axhspan(LIMIAR_BAIXO, LIMIAR_CIMA, color='mediumseagreen', alpha=0.15, label='Zona: Neutro (Frente)')
    ax_linha.axhspan(LIMIAR_MUITO_BAIXO, LIMIAR_BAIXO, color='gold', alpha=0.18, label='Zona: Olhar p/ Baixo')
    ax_linha.axhspan(-90, LIMIAR_MUITO_BAIXO, color='tomato', alpha=0.20, label='Zona: Muito p/ Baixo')
    
    ax_linha.set_xlim(t[0], t[-1])
    ax_linha.set_ylim(min(-70, min_pitch - 10), max(40, max_pitch + 10))
    ax_linha.set_xlabel('Tempo (segundos)')
    ax_linha.set_ylabel('Ângulo Pitch (°)')
    ax_linha.set_title('Trajetória Angular da Cabeça ao Longo do Tempo (VMC Head)')
    ax_linha.grid(True, alpha=0.3)
    ax_linha.legend(loc='upper right', ncol=3, fontsize=9)
    
    ax_bar = fig.add_subplot(gs[1, 0])
    categorias = ['Muito p/ Baixo', 'P/ Baixo', 'Neutro (Frente)', 'P/ Cima']
    percentuais = [pct_muito_baixo, pct_baixo, pct_neutro, pct_cima]
    cores = ['tomato', 'gold', 'mediumseagreen', 'dodgerblue']
    barras = ax_bar.barh(categorias, percentuais, color=cores, edgecolor='black', alpha=0.85)
    ax_bar.set_xlabel('Porcentagem do Tempo (%)')
    ax_bar.set_xlim(0, 100)
    ax_bar.set_title('Distribuição da Direção do Olhar')
    ax_bar.grid(True, alpha=0.3, axis='x')
    for b, p in zip(barras, percentuais):
        ax_bar.text(p + 1.5, b.get_y() + b.get_height()/2, f"{p:.1f}%", va='center', fontweight='bold', fontsize=9)
        
    ax_hist = fig.add_subplot(gs[1, 1])
    ax_hist.hist(pitch, bins=30, color='royalblue', edgecolor='navy', alpha=0.75, density=True)
    ax_hist.axvline(media_pitch, color='red', linestyle='--', label=f'Média: {media_pitch:+.1f}°')
    ax_hist.axvline(0, color='gray', linestyle=':', label='0°')
    ax_hist.set_xlabel('Ângulo Pitch (°)')
    ax_hist.set_ylabel('Densidade')
    ax_hist.set_title('Histograma da Distribuição do Ângulo')
    ax_hist.legend()
    ax_hist.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.show()

def main():
    disp = Dispatcher()
    disp.map("/VMC/Ext/Bone/Pos", vmc_bone_handler)

    server = ThreadingOSCUDPServer((IP, PORTA_VMC), disp)
    print("=" * 65)
    print("      MONITOR DE ORIENTAÇÃO DA CABEÇA VIA VMC (SLIMEVR)")
    print("=" * 65)
    print(f"Protocolo    : VMC (Virtual Motion Capture)")
    print(f"Porta        : {PORTA_VMC} (UDP)")
    print(f"Osso Alvo    : {OSSO_ALVO.upper()}")
    print(f"Montagem     : {'Parte de Trás da Cabeça (Invertido)' if INVERTER_PITCH else 'Frente / Padrão'}")
    print(f"Limiares     : Cima > {LIMIAR_CIMA:+.0f}° | Baixo < {LIMIAR_BAIXO:+.0f}° | Muito Baixo < {LIMIAR_MUITO_BAIXO:+.0f}°")
    print("-" * 65)
    print("-> Conexão estabelecida na porta 39539.")
    print("-> Movimente a cabeça normalmente.")
    print("-> Pressione 'Ctrl + C' para finalizar e abrir os relatórios e gráficos.\n")

    server.timeout = 0
    try:
        while True:
            server.handle_request()
            mostrar_feedback()
            time.sleep(0.002)
    except KeyboardInterrupt:
        print("\n\nCaptura concluída! Gerando análises...")
        gerar_relatorio_e_graficos()

if __name__ == "__main__":
    main()
