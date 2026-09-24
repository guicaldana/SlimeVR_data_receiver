import time
import math
import itertools
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Dicionário para guardar as posições { '1': (x, y, z), '2': (x, y, z), ... }
posicoes_trackers = {}
# Dicionário para guardar as rotações { '1': (x, y, z, w), ... }
rotacoes_trackers = {}

ultimo_tempo = 0
INTERVALO = 2

PREFIXO = "/tracking/trackers/"

# Trackers individuais conhecidos: { id: nome }
TRACKERS_NOMEADOS = {"1": "Quadril", "6": "Peito"}

def filter_handler(address, *args):
    """
    Filtra dados de posição e rotação dos trackers.
    """
    if not address.startswith(PREFIXO):
        return
    
    partes = address.split("/")
    if len(partes) < 5:
        return
    tracker_id = partes[3]
    tipo_dado = partes[4]  # 'position' ou 'rotation'
    
    if tipo_dado == "position" and len(args) == 3:
        posicoes_trackers[tracker_id] = args
    elif tipo_dado == "rotation" and len(args) >= 3:
        rotacoes_trackers[tracker_id] = args

# Pares conhecidos: (id_a, id_b, nome_da_parte)
PARES_CONHECIDOS = [("2", "3", "Tornozelo"), ("4", "5", "Coxa")]

def inferir_lado(tracker_id):
    """
    Retorna o label do tracker: nome individual ou lado (ESQ/DIR) para pares.
    """
    # Primeiro verifica se é um tracker com nome individual
    if tracker_id in TRACKERS_NOMEADOS:
        return TRACKERS_NOMEADOS[tracker_id]
    
    # Depois verifica se faz parte de um par (esquerdo/direito)
    for id_a, id_b, parte in PARES_CONHECIDOS:
        if tracker_id in (id_a, id_b):
            outro_id = id_b if tracker_id == id_a else id_a
            if outro_id in posicoes_trackers and tracker_id in posicoes_trackers:
                x_este = posicoes_trackers[tracker_id][0]
                x_outro = posicoes_trackers[outro_id][0]
                lado = "DIR" if x_este > x_outro else "ESQ"
                return f"{parte} {lado}"
    return ""

def calcular_inclinacao(rotacao):
    """
    Converte quaternion/euler para ângulo de inclinação frontal (pitch) em graus.
    Se receber 4 valores → quaternion (x, y, z, w).
    Se receber 3 valores → euler (x, y, z) em radianos.
    """
    if len(rotacao) == 4:
        # Quaternion → pitch (inclinação frente/trás)
        x, y, z, w = rotacao
        # Fórmula de conversão de quaternion para pitch
        sinp = 2.0 * (w * x - y * z)
        sinp = max(-1.0, min(1.0, sinp))  # clamp
        pitch_rad = math.asin(sinp)
        return math.degrees(pitch_rad)
    elif len(rotacao) == 3:
        # Euler angles em radianos → converte o pitch para graus
        return math.degrees(rotacao[0])
    return 0.0

def print_distancias():
    """
    Imprime posições, rotações e distâncias entre trackers.
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
                lado = inferir_lado(t_id)
                label = f"  [{lado}]" if lado else ""
                
                linha = f"  ID {t_id}{label}: X={x:.3f} | Y={y:.3f} | Z={z:.3f}"
                
                # Se tiver rotação, mostra o ângulo de inclinação
                if t_id in rotacoes_trackers:
                    rot = rotacoes_trackers[t_id]
                    inclinacao = calcular_inclinacao(rot)
                    linha += f"  | Inclinação: {inclinacao:+.1f}°"
                
                print(linha)
            
            # Mostra a postura do peito se disponível
            if "6" in rotacoes_trackers:
                inc_peito = calcular_inclinacao(rotacoes_trackers["6"])
                print(f"\n  🧍 Postura do peito: {inc_peito:+.1f}° ", end="")
                if abs(inc_peito) < 5:
                    print("(Ereto ✓)")
                elif inc_peito > 0:
                    print("(Inclinado para trás)")
                else:
                    print("(Inclinado para frente)")
            
            # Calcula a distância entre os pares se houver mais de 1 tracker
            if len(trackers_ids) >= 2:
                print("\nDistância horizontal (plano X-Z) entre pares:")
                pares = list(itertools.combinations(trackers_ids, 2))
                
                for id1, id2 in pares:
                    x1, y1, z1 = posicoes_trackers[id1]
                    x2, y2, z2 = posicoes_trackers[id2]
                    
                    dist_hz = math.sqrt((x2 - x1)**2 + (z2 - z1)**2)
                    dist_3d = math.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)
                    
                    print(f"  {id1} <-> {id2}: {dist_hz:.3f}m (3D: {dist_3d:.3f}m)")
        ultimo_tempo = tempo_atual

def main():
    disp = Dispatcher()
    # O handler padrão agora lida com a captura das posições e descarte do resto
    disp.set_default_handler(filter_handler)

    server = ThreadingOSCUDPServer(("127.0.0.1", 39539), disp)
    print("Iniciando modo de Descoberta de Pés...")
    print("Aguardando dados de /position na porta 39539 (Ctrl+C para sair).")
    
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