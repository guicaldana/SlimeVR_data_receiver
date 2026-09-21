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

def position_handler(address, x, y, z):
    global tempo_inicial
    tempo_atual = time.time()
    
    if tempo_inicial is None:
        tempo_inicial = tempo_atual
        
    # Tempo relativo a partir do início da gravação (facilita o gráfico)
    tempo_relativo = tempo_atual - tempo_inicial
    
    # O endereço é algo como /tracking/trackers/1/position. Vamos extrair o '1'
    tracker_id = address.split("/")[-2] 
    
    # Inicializa a lista para esse tracker, se ainda não existir
    if tracker_id not in dados_memoria:
        dados_memoria[tracker_id] = {"t": [], "x": [], "y": [], "z": []}
        
    # Adiciona na memória para a Transformada de Fourier
    dados_memoria[tracker_id]["t"].append(tempo_relativo)
    dados_memoria[tracker_id]["x"].append(x)
    dados_memoria[tracker_id]["y"].append(y)
    dados_memoria[tracker_id]["z"].append(z)
    
    # Salva no arquivo CSV, assim como no seu código original
    with open(NOME_ARQUIVO, mode='a', newline='') as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow([tempo_atual, tracker_id, x, y, z])
        
    # Descomente a linha abaixo se quiser ver no terminal enquanto grava
    # print(f"Gravado -> Tracker {tracker_id} | X: {x:.3f}")

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
    disp.map("/tracking/trackers/*/position", position_handler)

    server = ThreadingOSCUDPServer(("127.0.0.1", 9000), disp)
    print(f"Servidor OSC iniciado. Gravando dados no arquivo '{NOME_ARQUIVO}'...")
    print("-> Ande / faça o movimento para gerar dados de marcha.")
    print("-> Pressione 'Ctrl + C' quando terminar para gerar os gráficos.")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGravação finalizada! Processando as frequências...")
        plotar_analise_frequencia()

if __name__ == "__main__":
    main()

