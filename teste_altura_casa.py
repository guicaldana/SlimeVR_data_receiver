"""
TEMPORÁRIO — altura de cada pé em tempo real, sem cadastro e sem roteiro.

Calibra com os dois pés no chão e depois mostra a altura do pé E e do pé D (mesmo cálculo
do experimento de altura, sem calibração por caixa) e a inclinação do tronco.
  R = calibrar de novo (dois pés no chão)    Ctrl+C = sair
Não salva nada.

    uv run python teste_altura_casa.py
"""
import os
import subprocess
import threading
import time

import piloto

piloto.CALIBRAR_CAIXA = False


def iniciar_captura():
    for porta, handler in ((piloto.PORTA_OSC, piloto.osc_handler_9000),
                           (piloto.PORTA_VMC, piloto.vmc_handler_39539)):
        threading.Thread(target=piloto.rodar_servidor, args=(porta, handler), daemon=True).start()
    script_node = os.path.join("slimevr_client_raw", "src", "stream.mjs")
    if os.path.exists(script_node):
        try:
            proc = subprocess.Popen(["node", script_node], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1)
            threading.Thread(target=piloto.thread_leitor_solarxr, args=(proc,), daemon=True).start()
            return proc
        except Exception as e:
            print(f"[Aviso] SolarXR não iniciou ({e}); a altura usa só o OSC.")
    return None


def tecla():
    try:
        import msvcrt
    except ImportError:
        return None
    if msvcrt.kbhit():
        return msvcrt.getwch().lower()
    return None


def main():
    proc = iniciar_captura()
    piloto.executar_calibracao_inicial()
    print("\n   Altura dos pés em tempo real.  R = calibrar de novo   Ctrl+C = sair\n")
    try:
        while True:
            if tecla() == "r":
                print()
                piloto.executar_calibracao_inicial()
                print("\n   Altura dos pés em tempo real.  R = calibrar de novo   Ctrl+C = sair\n")
            print(f"   {piloto._texto_altura().strip(' |')}        ", end="\r", flush=True)
            time.sleep(piloto.INTERVALO_FEEDBACK)
    except KeyboardInterrupt:
        print("\n   Fim.")
    finally:
        piloto.rodando = False
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
