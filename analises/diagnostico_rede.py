import sys
import time
import math
import threading
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

# Força UTF-8 no Windows para evitar erros de caractere
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Contadores e armazenamento de dados
stats_9000 = {
    "total_pacotes": 0,
    "trackers": {},  # { id: {"tipo": ..., "valores": ..., "ultimo_update": ...} }
    "ativo": False
}

stats_39539 = {
    "total_pacotes": 0,
    "ossos": {},     # { nome: {"rot": ..., "pos": ..., "ultimo_update": ...} }
    "ativo": False
}

lock = threading.Lock()

# ----------------- HANDLER PORTA 9000 (VRChat OSC) -----------------
def handler_9000(address, *args):
    with lock:
        stats_9000["total_pacotes"] += 1
        stats_9000["ativo"] = True
        
        partes = address.split("/")
        if len(partes) >= 5 and partes[1] == "tracking" and partes[2] == "trackers":
            t_id = partes[3]
            tipo = partes[4]
            valores_formatados = [round(x, 3) if isinstance(x, float) else x for x in args]
            
            if t_id not in stats_9000["trackers"]:
                stats_9000["trackers"][t_id] = {}
            stats_9000["trackers"][t_id][tipo] = valores_formatados
            stats_9000["trackers"][t_id]["timestamp"] = time.time()

# ----------------- HANDLER PORTA 39539 (VMC Protocol) -----------------
def handler_39539(address, *args):
    with lock:
        stats_39539["total_pacotes"] += 1
        stats_39539["ativo"] = True
        
        if address == "/VMC/Ext/Bone/Pos" and len(args) >= 8:
            bone_name = str(args[0])
            qx, qy, qz, qw = args[4], args[5], args[6], args[7]
            
            # Converte quaternion para pitch (inclinação vertical)
            sinp = 2.0 * (qw * qx - qy * qz)
            sinp = max(-1.0, min(1.0, sinp))
            pitch = math.degrees(math.asin(sinp))
            
            stats_39539["ossos"][bone_name] = {
                "pitch": round(pitch, 1),
                "quat": (round(qx, 2), round(qy, 2), round(qz, 2), round(qw, 2)),
                "timestamp": time.time()
            }

def rodar_servidor(porta, handler):
    disp = Dispatcher()
    disp.set_default_handler(handler)
    try:
        server = ThreadingOSCUDPServer(("127.0.0.1", porta), disp)
        server.serve_forever()
    except OSError as e:
        print(f"\n[AVISO] Nao foi possivel abrir a porta {porta}: {e}")
        print(f"-> Certifique-se de que nenhum outro script (como analise_cabeca ou calculo_passo) esta rodando no momento!\n")

def main():
    # Inicia servidores em threads paralelas
    t1 = threading.Thread(target=rodar_servidor, args=(9000, handler_9000), daemon=True)
    t2 = threading.Thread(target=rodar_servidor, args=(39539, handler_39539), daemon=True)
    
    t1.start()
    t2.start()
    
    print("=" * 70)
    print("       DIAGNÓSTICO COMPLETO DE DADOS OSC / VMC (SLIMEVR)")
    print("=" * 70)
    print("Escutando simultaneamente:")
    print("  • Porta 9000  (Trackers OSC do VRChat)")
    print("  • Porta 39539 (Captura Virtual de Movimentos - VMC)")
    print("-" * 70)
    print("Movimente os trackers para verificar se os dados variam.")
    print("Pressione 'Ctrl + C' para encerrar.\n")
    
    ultimo_p9000 = 0
    ultimo_p39539 = 0
    
    try:
        while True:
            time.sleep(1.0)
            
            with lock:
                p9000_atual = stats_9000["total_pacotes"]
                p39539_atual = stats_39539["total_pacotes"]
                
                taxa_9000 = p9000_atual - ultimo_p9000
                taxa_39539 = p39539_atual - ultimo_p39539
                
                ultimo_p9000 = p9000_atual
                ultimo_p39539 = p39539_atual
                
                trackers_9000 = dict(stats_9000["trackers"])
                ossos_39539 = dict(stats_39539["ossos"])
            
            # Limpa tela com quebra de linha visual
            print("\n" + "-" * 70)
            print(f"[{time.strftime('%H:%M:%S')}] STATUS DA REDE:")
            
            # Painel Porta 9000
            status_p9000 = f"RECEBENDO ({taxa_9000} pacotes/s)" if taxa_9000 > 0 else "SEM DADOS"
            print(f"• PORTA 9000 (VRChat OSC) -> {status_p9000}")
            if trackers_9000:
                print("  Trackers detectados na 9000:")
                for tid in sorted(trackers_9000.keys()):
                    t_info = trackers_9000[tid]
                    detalhes = []
                    if "position" in t_info:
                        detalhes.append(f"Pos(X={t_info['position'][0]:.2f}, Y={t_info['position'][1]:.2f}, Z={t_info['position'][2]:.2f})")
                    if "rotation" in t_info:
                        detalhes.append(f"Rot={t_info['rotation']}")
                    print(f"    ID {tid:<4}: {' | '.join(detalhes)}")
            else:
                print("    (Nenhum tracker detectado na porta 9000 ainda)")

            # Painel Porta 39539
            status_p39539 = f"RECEBENDO ({taxa_39539} pacotes/s)" if taxa_39539 > 0 else "SEM DADOS"
            print(f"\n• PORTA 39539 (VMC) -> {status_p39539}")
            if ossos_39539:
                print(f"  Total de ossos recebidos: {len(ossos_39539)}")
                
                # Destaca os principais: Head, Chest, Hips
                destaques = ["Head", "Neck", "Chest", "Hips"]
                for osso in destaques:
                    if osso in ossos_39539:
                        info = ossos_39539[osso]
                        print(f"    Osso {osso:<6} -> Pitch: {info['pitch']:+5.1f}° | Quat: {info['quat']}")
                
                outros = [o for o in sorted(ossos_39539.keys()) if o not in destaques]
                if outros:
                    print(f"    Outros ossos: {', '.join(outros[:8])}...")
            else:
                print("    (Nenhum pacote VMC detectado na porta 39539 ainda)")
                print("    -> Verifique se 'Ativar' esta ligado na aba VMC do SlimeVR.")

    except KeyboardInterrupt:
        print("\nDiagnóstico encerrado.")

if __name__ == "__main__":
    main()

