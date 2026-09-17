import time
import math
import itertools
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Dicionário para guardar as posições { '1': (x, y, z), '2': (x, y, z), ... }
posicoes_trackers = {}

ultimo_tempo = 0
INTERVALO = 0.5 

PREFIXO_POSICAO = "/tracking/trackers/"

def filter_handler(address, *args):
    """
    Filtra apenas os dados de posição dos trackers.
    Exemplo de endereço OSC: /tracking/trackers/1/position -> args: (x, y, z)
    """
    if address.startswith(PREFIXO_POSICAO) and address.endswith("/position"):
        # Divide o endereço. Ex: ['', 'tracking', 'trackers', '1', 'position']
        partes = address.split("/")
        if len(partes) >= 4:
            tracker_id = partes[3] # Pega o ID numérico/nome do tracker
            
            # A posição geralmente é enviada como 3 valores floats (x, y, z)
            if len(args) == 3:
                posicoes_trackers[tracker_id] = args

def print_distancias():
    """
    Imprime as posições e as distâncias entre todos os pares de trackers.
    """
    global ultimo_tempo
    tempo_atual = time.time()
    
    if tempo_atual - ultimo_tempo >= INTERVALO:
        if posicoes_trackers:
            print(f"\n--- Modo Descoberta ({time.strftime('%H:%M:%S')}) ---")
            
            # Imprime os trackers detectados
            print("Trackers detectados:")
            trackers_ids = sorted(posicoes_trackers.keys())
            for t_id in trackers_ids:
                x, y, z = posicoes_trackers[t_id]
                print(f"  ID {t_id}: X={x:.3f} | Y={y:.3f} | Z={z:.3f}")
            
            # Calcula a distância entre os pares se houver mais de 1 tracker
            if len(trackers_ids) >= 2:
                print("\nDistância horizontal (plano X-Z) entre pares:")
                # Gera todas as combinações de 2 trackers possíveis
                pares = list(itertools.combinations(trackers_ids, 2))
                
                for id1, id2 in pares:
                    x1, y1, z1 = posicoes_trackers[id1]
                    x2, y2, z2 = posicoes_trackers[id2]
                    
                    # Distância horizontal (Ignora a altura Y)
                    dist_hz = math.sqrt((x2 - x1)**2 + (z2 - z1)**2)
                    
                    # Distância 3D total (pra referência)
                    dist_3d = math.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)
                    
                    print(f"  {id1} <-> {id2}: {dist_hz:.3f}m (3D: {dist_3d:.3f}m)")
                
                print("\n-> DICA: Fique de pé, afaste bem as pernas e veja qual par mostra o MAIOR valor!")
        ultimo_tempo = tempo_atual

def main():
    disp = Dispatcher()
    # O handler padrão agora lida com a captura das posições e descarte do resto
    disp.set_default_handler(filter_handler)

    server = ThreadingOSCUDPServer(("127.0.0.1", 9000), disp)
    print("Iniciando modo de Descoberta de Pés...")
    print("Aguardando dados de /position na porta 9000 (Ctrl+C para sair).")
    
    server.timeout = 0
    try:
        while True:
            server.handle_request()
            print_distancias()
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nServidor encerrado.")

if __name__ == "__main__":
    main()