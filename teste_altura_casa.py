"""
TEMPORÁRIO — altura de cada pé em tempo real, sem cadastro e sem roteiro.

Calibra com os dois pés no chão e depois mostra a altura do pé E e do pé D (mesmo cálculo
do experimento de altura, sem calibração por caixa), a inclinação do tronco e quanto cada pé
e a pelve se moveram na vertical desde a calibração (o pé de apoio e a pelve deveriam ficar
perto de 0; pé de apoio negativo = ele "afunda" no SlimeVR).
Teclas: R = calibrar de novo (dois pés no chão) | E = pé E em cima | D = pé D em cima |
        C = dois pés no chão | Ctrl+C = sair
Mostra também o ângulo de cada coxa e canela em relação à vertical, pelas IMUs (0° = como na
calibração em pé).
As teclas E/D/C só marcam o momento nos dados. Ao sair, salva os dados brutos (posições OSC de
todos os trackers, rotações das coxas e canelas, tronco, marcas) e as proporções do SlimeVR em
dados/debug_casa/<AAAAMMDD_HHMMSS>/ para análise.

    uv run python teste_altura_casa.py
"""
import csv
import json
import os
import subprocess
import threading
import time

import numpy as np

import participante as cadastro
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


def texto_diagnostico():
    """Quanto cada pé e a pelve se moveram na vertical desde a calibração (OSC)."""
    with piloto.lock:
        pe = piloto.pos_atual[piloto.TORNOZELO_ESQ]
        pd = piloto.pos_atual[piloto.TORNOZELO_DIR]
        pq = piloto.pos_atual[piloto.TRACKER_QUADRIL]
    if pe is None or pd is None or pq is None or not piloto.calibrado:
        return ""
    e, d, q = piloto._deslocamentos_osc_cm(pe, pd, pq)
    return f" | Desde a calibração: pé E {e:+5.1f} pé D {d:+5.1f} pelve {q:+5.1f} cm"


ROTULOS_SEG = (("LEFT_UPPER_LEG", "coxa E"), ("RIGHT_UPPER_LEG", "coxa D"),
               ("LEFT_LOWER_LEG", "canela E"), ("RIGHT_LOWER_LEG", "canela D"))


def angulos_pernas():
    """Ângulo (graus) de cada coxa e canela em relação à vertical, pelas IMUs (SolarXR).

    Zero = como estava na calibração (dois pés no chão, em pé)."""
    cad = piloto.cadeia
    if cad is None or not getattr(cad, "calibrado", False):
        return None
    with piloto.lock:
        quats = dict(piloto.quat_atual)
    ang = {}
    for seg, _ in ROTULOS_SEG:
        q = quats.get(seg)
        if q is None:
            return None
        v = cad.vetor(seg, np.asarray(q, dtype=float))[0]
        ang[seg] = float(np.degrees(np.arccos(np.clip(-v[1] / np.linalg.norm(v), -1, 1))))
    return ang


def texto_angulos():
    ang = angulos_pernas()
    if ang is None:
        return "   Ângulos: aguardando as IMUs das pernas (SolarXR)"
    return "   Ângulos da vertical: " + " | ".join(f"{rot} {ang[seg]:4.0f}°" for seg, rot in ROTULOS_SEG)


MARCAS = {"e": "pe_E_em_cima", "d": "pe_D_em_cima", "c": "chao"}
TRACKERS = [piloto.TRACKER_QUADRIL, piloto.TORNOZELO_ESQ, piloto.TORNOZELO_DIR,
            piloto.TRACKER_COXA_ESQ, piloto.TRACKER_COXA_DIR]
AJUDA = "   R = calibrar | E/D = pé E/D em cima | C = chão | Ctrl+C = sair e salvar\n"
linhas = []
calibracoes = []


def registrar_calibracao():
    calibracoes.append({
        "t": time.time(), "chao_esq_m": piloto.chao_esq_ref, "chao_dir_m": piloto.chao_dir_ref,
        "pelve_m": piloto.yq_ref, "dist_pelve_pe_e_m": piloto.dist_qp_e_ref,
        "dist_pelve_pe_d_m": piloto.dist_qp_d_ref, "zona_morta_cm": piloto.limiar_ruido_chao,
        "tronco_ref_graus": piloto.pitch_tronco_ref,
    })


def amostrar(marca):
    with piloto.lock:
        pos = {k: piloto.pos_atual.get(k) for k in TRACKERS}
        quats = dict(piloto.quat_atual)
    alt = piloto._altura_agora_cm()
    frente, lado = piloto._tronco_agora_graus()
    linha = {"t": round(time.time(), 3), "marca": marca,
             "altura_E_cm": alt[0] if alt else "", "altura_D_cm": alt[1] if alt else "",
             "tronco_frente_graus": frente, "tronco_lado_graus": lado}
    for k in TRACKERS:
        for i, eixo in enumerate("xyz"):
            linha[f"osc{k}_{eixo}"] = pos[k][i] if pos[k] is not None else ""
    for seg, q in quats.items():
        for i, c in enumerate("xyzw"):
            linha[f"{seg}_q{c}"] = q[i] if q is not None else ""
    ang = angulos_pernas() or {}
    for seg, rot in ROTULOS_SEG:
        linha[f"angulo_{rot.replace(' ', '_')}_graus"] = round(ang[seg], 1) if seg in ang else ""
    linhas.append(linha)


def salvar():
    if not linhas:
        return
    pasta = os.path.join("dados", "debug_casa", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(pasta, exist_ok=True)
    campos = sorted({c for l in linhas for c in l}, key=lambda c: (c not in ("t", "marca"), c))
    with open(os.path.join(pasta, "amostras.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)
    info = {"calibracoes": calibracoes, "slimevr": cadastro.ler_esqueleto_slimevr()}
    with open(os.path.join(pasta, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2, default=str)
    print(f"   Dados salvos em {pasta}")


def main():
    if os.name == "nt":
        os.system("")   # liga as cores/códigos ANSI no console do Windows
    proc = iniciar_captura()
    piloto.executar_calibracao_inicial()
    registrar_calibracao()
    print("\n" + AJUDA)
    marca = "chao"
    try:
        while True:
            k = tecla()
            if k == "r":
                print("\n")
                piloto.executar_calibracao_inicial()
                registrar_calibracao()
                marca = "chao"
                print("\n" + AJUDA)
            elif k in MARCAS:
                marca = MARCAS[k]
            amostrar(marca)
            # duas linhas que se atualizam no lugar (ESC[2K apaga a linha, ESC[1A sobe uma)
            print(f"\033[2K   [{marca}] {piloto._texto_altura().strip(' |')}{texto_diagnostico()}\n"
                  f"\033[2K{texto_angulos()}\033[1A\r", end="", flush=True)
            time.sleep(piloto.INTERVALO_FEEDBACK)
    except KeyboardInterrupt:
        print("\n\n   Fim.")
    finally:
        piloto.rodando = False
        if proc:
            proc.terminate()
        salvar()


if __name__ == "__main__":
    main()
