import csv
import time
import numpy as np
import matplotlib.pyplot as plt
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Nome do arquivo que será gerado
NOME_ARQUIVO = "dados_marcha_paciente.csv"

# Dicionário para armazenar os dados em memória e plotar no final
# Formato: { "tracker_id": { "t": [], "x": [], "y": [], "z": [] } }
dados_memoria = {}
tempo_inicial = None

# IDs dos trackers que queremos analisar (ignorar head e 1/quadril)
TRACKERS_INTERESSE = {"2", "3", "4", "5"}

PREFIXO_POSICAO = "/tracking/trackers/"

# Contador de pacotes gravados para feedback no terminal
pacotes_gravados = 0

def position_handler(address, *args):
    global tempo_inicial, pacotes_gravados
    
    # Filtra: só pega endereços de posição
    if not (address.startswith(PREFIXO_POSICAO) and address.endswith("/position")):
        return
    
    # Extrai o ID do tracker
    partes = address.split("/")
    if len(partes) < 4:
        return
    tracker_id = partes[3]
    
    # Ignora trackers fora da lista de interesse
    if tracker_id not in TRACKERS_INTERESSE:
        return
    
    # Precisa de exatamente 3 valores (x, y, z)
    if len(args) != 3:
        return
    
    x, y, z = args
    tempo_atual = time.time()
    
    if tempo_inicial is None:
        tempo_inicial = tempo_atual
        
    tempo_relativo = tempo_atual - tempo_inicial
    
    if tracker_id not in dados_memoria:
        dados_memoria[tracker_id] = {"t": [], "x": [], "y": [], "z": []}
        print(f"  -> Tracker {tracker_id} detectado!")
        
    dados_memoria[tracker_id]["t"].append(tempo_relativo)
    dados_memoria[tracker_id]["x"].append(x)
    dados_memoria[tracker_id]["y"].append(y)
    dados_memoria[tracker_id]["z"].append(z)
    
    with open(NOME_ARQUIVO, mode='a', newline='') as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow([tempo_atual, tracker_id, x, y, z])
    
    pacotes_gravados += 1
    if pacotes_gravados % 500 == 0:
        print(f"  {pacotes_gravados} pacotes gravados...")

def plotar_analise_frequencia():
    print("\nGerando gráficos de análise de ruído (FFT)...")
    
    for tracker_id, dados in dados_memoria.items():
        t = np.array(dados["t"])
        x = np.array(dados["x"])
        # Você também pode fazer para y e z repetindo a lógica
        
        if len(t) < 20:
            print(f"Dados insuficientes para analisar o Tracker {tracker_id}")
            continue
            
        # Calcula a taxa de amostragem média (Hz)
        # Importante porque o OSC pode variar o intervalo de envio
        dt_medio = np.mean(np.diff(t))
        taxa_amostragem = 1.0 / dt_medio
        
        N = len(t)
        
        # Tira a média do sinal para remover o componente DC (Frequência 0)
        x_sem_dc = x - np.mean(x)
        
        # ------------------ CÁLCULO DA FFT ------------------
        # Transformada de Fourier
        yf = np.fft.fft(x_sem_dc)
        xf = np.fft.fftfreq(N, dt_medio)[:N//2]
        
        # Calcula a magnitude (amplitude) de cada frequência
        magnitude = 2.0/N * np.abs(yf[0:N//2])
        
        # ------------------ PLOTAGEM DOS GRÁFICOS ------------------
        plt.figure(figsize=(14, 5))
        
        # Gráfico 1: Sinal Bruto no Tempo
        plt.subplot(1, 2, 1)
        plt.plot(t, x, label='Posição X')
        plt.title(f'Sinal Bruto no Tempo - Tracker {tracker_id}')
        plt.xlabel('Tempo (segundos)')
        plt.ylabel('Posição (metros)')
        plt.legend()
        plt.grid()
        
        # Gráfico 2: Espectro de Frequências (FFT)
        plt.subplot(1, 2, 2)
        plt.plot(xf, magnitude, color='red')
        plt.title('Espectro de Frequências (Detectar Ruído)')
        plt.xlabel('Frequência (Hz)')
        plt.ylabel('Magnitude (Amplitude)')
        
        # Limita o eixo X. Ruídos de sensores costumam ser analisados até uns 20-30Hz
        plt.xlim(0, min(30, taxa_amostragem/2)) 
        plt.grid()
        
        plt.tight_layout()
        plt.show()

def main():
    with open(NOME_ARQUIVO, mode='w', newline='') as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(['Timestamp', 'Tracker_ID', 'Pos_X', 'Pos_Y', 'Pos_Z'])

    disp = Dispatcher()
    # Usa o handler padrão (mesmo método que funciona no slimevr.py)
    disp.set_default_handler(position_handler)

    server = ThreadingOSCUDPServer(("127.0.0.1", 9000), disp)
    print(f"Servidor OSC iniciado. Gravando dados no arquivo '{NOME_ARQUIVO}'...")
    print(f"Monitorando trackers: {', '.join(sorted(TRACKERS_INTERESSE))}")
    print("-> Ande / faça o movimento para gerar dados de marcha.")
    print("-> Pressione 'Ctrl + C' quando terminar para gerar os gráficos.")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGravação finalizada! Processando as frequências...")
        plotar_analise_frequencia()

if __name__ == "__main__":
    main()

