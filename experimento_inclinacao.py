"""
Experimento: a altura do pé muda quando o tronco inclina?

Grava ao mesmo tempo:
  - OSC do SlimeVR (posição dos pés, quadril e rotação do peito), que é o que o
    calculo_passo.py usa hoje;
  - SolarXR (aceleração e orientação das IMUs), usado pelo ZUPT (zupt_pe.py).

O roteiro é guiado por tempo, com instruções no terminal:
  1. Parado, tronco reto (referência dos pés no chão).
  2. Caminhada curta em linha reta (calibra a correção de montagem do ZUPT).
  3. Para cada inclinação do tronco (0°, 30°, 60°):
       - incline e fique parado com os dois pés no chão;
       - pé esquerdo na caixa (6,7 cm) por 5 s, volta ao chão; pé direito na
         caixa por 5 s, volta ao chão; repete. Sempre mantendo a inclinação.
  4. Caminhada com o tronco inclinado.

No fim, compara por inclinação a altura medida na caixa pelo OSC (método atual,
diferença entre os pés) e pelo ZUPT, e quanto os pés "sobem" no OSC só por
inclinar o tronco. Se o método for imune à inclinação, a altura na caixa fica
perto de 6,7 cm em todas as inclinações.

Uso:
    python experimento_inclinacao.py                 # grava e analisa
    python experimento_inclinacao.py --analisar dados/experimentos/inclinacao_AAAAMMDD_HHMMSS
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import numpy as np

import zupt_pe

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
PORTA_OSC = 9000
IP_OSC = "127.0.0.1"
TRACKER_QUADRIL = "1"
TORNOZELO = {"ESQ": "2", "DIR": "3"}
TRACKER_PEITO = "6"
INVERTER_PITCH_TRONCO = True     # Mesmo valor do calculo_passo.py

ALTURA_CAIXA_CM = 6.7
INCLINACOES = (0, 30, 60)        # graus de flexão do tronco a testar
REPETICOES_CAIXA = 2             # subidas de cada pé na caixa por inclinação
T_PARADO_S = 5.0
T_CAMINHADA_S = 12.0
T_INCLINAR_S = 5.0
T_SUBIR_S = 5.0          # pé parado em cima da caixa
T_DESCER_S = 4.0         # pé parado de volta no chão
T_DESLOCAMENTO_S = 15.0  # pausa para ir até a caixa ou até o início da caminhada
PASTA_EXPERIMENTOS = os.path.join("dados", "experimentos")
CLIENTE_SOLARXR = os.path.join("slimevr_client_raw", "src", "cli.mjs")
# ==============================================================

lock = threading.Lock()
amostras_osc = []  # (t_unix, tracker, tipo, v1, v2, v3)


def osc_handler(address, *args):
    if not address.startswith("/tracking/trackers/"):
        return
    partes = address.split("/")
    if len(partes) < 5 or len(args) < 3:
        return
    tracker, tipo = partes[3], partes[4]
    if tipo not in ("position", "rotation"):
        return
    with lock:
        amostras_osc.append((time.time(), tracker, tipo, float(args[0]), float(args[1]), float(args[2])))


def montar_roteiro():
    """Lista de fases: (nome, duração, instrução, inclinação)."""
    nomes = {"ESQ": "ESQUERDO", "DIR": "DIREITO"}
    fases = [
        ("parado_reto", T_PARADO_S, "Fique parado, tronco reto, os dois pés no chão.", 0),
        ("caminhada_reta", T_CAMINHADA_S, "Caminhe em linha reta, ritmo normal, tronco reto. Pare no fim.", 0),
        ("deslocamento_caixa", T_DESLOCAMENTO_S, "PAUSA: vá até a caixa e fique em pé ao lado dela.", 0),
    ]
    for inc in INCLINACOES:
        fases.append((f"inclinado_{inc}", T_INCLINAR_S,
                      f"Incline o tronco para a frente ~{inc}° e fique parado (pés no chão).", inc))
        for k in range(1, REPETICOES_CAIXA + 1):
            for pe in ("ESQ", "DIR"):
                fases.append((f"subir_{inc}_{pe}_{k}", T_SUBIR_S,
                              f"[{inc}°] Pé {nomes[pe]} em cima da caixa e parado ({k}/{REPETICOES_CAIXA}).", inc))
                fases.append((f"descer_{inc}_{pe}_{k}", T_DESCER_S,
                              f"[{inc}°] Pé {nomes[pe]} de volta ao chão e parado.", inc))
    fases.append(("deslocamento_caminhada", T_DESLOCAMENTO_S,
                  "PAUSA: vá até o início da caminhada e fique parado.", 0))
    fases.append(("caminhada_inclinada", T_CAMINHADA_S,
                  "Caminhe em linha reta com o tronco inclinado ~30°. Pare no fim.", 30))
    return fases


def gravar():
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer

    pasta = os.path.join(PASTA_EXPERIMENTOS, datetime.now().strftime("inclinacao_%Y%m%d_%H%M%S"))
    os.makedirs(pasta)
    fases = montar_roteiro()
    total = sum(f[1] for f in fases)

    print("=" * 78)
    print("  EXPERIMENTO: ALTURA DO PÉ x INCLINAÇÃO DO TRONCO")
    print("=" * 78)
    print(f"  Caixa: {ALTURA_CAIXA_CM} cm | inclinações: {INCLINACOES} | duração: {total:.0f} s")
    print("  Deixe a caixa ao lado do pé e espaço para caminhar ~5 m em linha reta.")
    print(f"  Saída: {pasta}")
    input("\n  Pressione ENTER quando estiver pronto...")

    disp = Dispatcher()
    disp.set_default_handler(osc_handler)
    servidor = ThreadingOSCUDPServer((IP_OSC, PORTA_OSC), disp)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    cmd = ["node", CLIENTE_SOLARXR, "capture", "--seconds", str(int(total + 4)),
           "--out", os.path.join(pasta, "solarxr")]
    try:
        proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("[Erro] Node.js não encontrado no PATH.")
        servidor.shutdown()
        return None
    time.sleep(2.0)  # conexão do cliente SolarXR

    registro_fases = []
    for nome, dur, instrucao, inc in fases:
        print("\n" + "-" * 78)
        print(f"  >>> {instrucao}")
        t0 = time.time()
        while time.time() - t0 < dur:
            print(f"\r      {dur - (time.time() - t0):4.1f} s   ", end="", flush=True)
            time.sleep(0.1)
        registro_fases.append({"fase": nome, "inclinacao": inc, "t_inicio_unix": t0, "t_fim_unix": time.time()})
    print("\n\n  Fim do roteiro. Finalizando a gravação...")
    proc.wait()
    servidor.shutdown()

    with open(os.path.join(pasta, "fases.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["fase", "inclinacao", "t_inicio_unix", "t_fim_unix"])
        w.writeheader()
        w.writerows(registro_fases)
    with lock:
        linhas = list(amostras_osc)
    with open(os.path.join(pasta, "osc.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_unix", "tracker", "tipo", "v1", "v2", "v3"])
        w.writerows(linhas)
    with open(os.path.join(pasta, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"altura_caixa_cm": ALTURA_CAIXA_CM, "inclinacoes": INCLINACOES}, f, indent=2)
    print(f"  OSC: {len(linhas)} mensagens | fases: {len(registro_fases)}")
    return pasta


# ----------------------------- ANÁLISE -----------------------------
def ler_osc(pasta):
    serie = {}
    with open(os.path.join(pasta, "osc.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            chave = (r["tracker"], r["tipo"])
            serie.setdefault(chave, []).append((float(r["t_unix"]), float(r["v1"]), float(r["v2"]), float(r["v3"])))
    return {k: np.array(v) for k, v in serie.items()}


def ler_fases(pasta):
    with open(os.path.join(pasta, "fases.csv"), newline="", encoding="utf-8") as f:
        return [{"fase": r["fase"], "inclinacao": int(r["inclinacao"]),
                 "t0": float(r["t_inicio_unix"]), "t1": float(r["t_fim_unix"])} for r in csv.DictReader(f)]


def janela(arr, t0, t1, col):
    if arr is None or len(arr) == 0:
        return np.array([])
    m = (arr[:, 0] >= t0) & (arr[:, 0] <= t1)
    return arr[m, col]


def mediana_final(arr, fase, col, frac=0.5):
    """Mediana na parte final da fase (o pé já parou)."""
    t0 = fase["t0"] + (fase["t1"] - fase["t0"]) * (1.0 - frac)
    v = janela(arr, t0, fase["t1"], col)
    return float(np.median(v)) if len(v) else float("nan")


def pitch_tronco(rot, fase):
    p = mediana_final(rot, fase, 1)
    if np.isnan(p):
        return p
    p = (p + 180.0) % 360.0 - 180.0
    return -p if INVERTER_PITCH_TRONCO else p


def analisar(pasta):
    with open(os.path.join(pasta, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    caixa = float(cfg["altura_caixa_cm"])
    porfase = {f["fase"]: f for f in ler_fases(pasta)}
    osc = ler_osc(pasta)
    y = {pe: osc.get((TORNOZELO[pe], "position")) for pe in TORNOZELO}
    rot_peito = osc.get((TRACKER_PEITO, "rotation"))
    samples = os.path.join(pasta, "solarxr", "samples.csv")
    tem_imu = os.path.exists(samples)

    print("\n" + "=" * 78)
    print("  ANÁLISE")
    print("=" * 78)
    passadas = {"ESQ": [], "DIR": []}
    if tem_imu:
        taxas = zupt_pe.taxa_amostras_novas(samples)
        print("  Amostras novas SolarXR: " + ", ".join(f"{k}={v:.0f} Hz" for k, v in taxas.items()))
        if taxas and min(taxas.values()) < 60:
            print("  [Aviso] Taxa baixa: o ZUPT perde picos de aceleração e fica pouco confiável.")
        # Correção de montagem de cada pé, estimada na caminhada reta
        for pe in passadas:
            cam = porfase.get("caminhada_reta")
            janela_cam = (cam["t0"], cam["t1"]) if cam else None
            rotvec, antes, depois, n = zupt_pe.estimar_montagem(samples, pe, janela_unix=janela_cam)
            if rotvec is None:
                print(f"  [Aviso] Pé {pe}: caminhada com só {n} balanços. ZUPT sem correção de montagem.")
            else:
                print(f"  Correção de montagem pé {pe}: velocidade residual {antes:.2f} -> {depois:.2f} m/s")
            passadas[pe], _ = zupt_pe.processar_pe(samples, pe, rotvec)
    else:
        print("  [Aviso] Sem captura SolarXR: só o OSC será analisado.")

    # Referência do OSC com tudo reto e parado
    ref = porfase["parado_reto"]
    base = {pe: mediana_final(y[pe], ref, 2) for pe in y}

    linhas = []
    for inc in cfg["inclinacoes"]:
        fi = porfase.get(f"inclinado_{inc}")
        if fi is None:
            continue
        # Quanto os dois pés "sobem" no OSC só por inclinar o tronco (deveria ser 0)
        subida_osc = min(mediana_final(y[pe], fi, 2) - base[pe] for pe in y) * 100.0
        for pe in ("ESQ", "DIR"):
            outro = "DIR" if pe == "ESQ" else "ESQ"
            alt_osc, sobe, desce, troncos = [], [], [], []
            k = 1
            while f"subir_{inc}_{pe}_{k}" in porfase:
                fs, fd = porfase[f"subir_{inc}_{pe}_{k}"], porfase[f"descer_{inc}_{pe}_{k}"]
                troncos.append(pitch_tronco(rot_peito, fs))
                # Método atual: diferença entre os pés, descontada a diferença com tudo reto
                dif = (mediana_final(y[pe], fs, 2) - base[pe]) - (mediana_final(y[outro], fs, 2) - base[outro])
                alt_osc.append(dif * 100.0)
                # ZUPT: desnível do movimento que começa em cada fase.
                # A fase "subir" começa com o pé ainda no chão, então o movimento chão -> caixa
                # pode começar um pouco antes; aceita 1 s de folga no início.
                for p in passadas[pe]:
                    if fs["t0"] - 1.0 <= p["t_inicio_unix"] <= fs["t1"] - 1.0 and abs(p["desnivel_cm"]) > 1.0:
                        sobe.append(p["desnivel_cm"])
                    elif fd["t0"] - 1.0 <= p["t_inicio_unix"] <= fd["t1"] - 1.0 and abs(p["desnivel_cm"]) > 1.0:
                        desce.append(-p["desnivel_cm"])
                k += 1
            linhas.append({
                "inclinacao_alvo": inc,
                "pe": pe,
                "tronco_medido_graus": float(np.nanmedian(troncos)) if troncos else float("nan"),
                "subida_osc_parado_cm": subida_osc,
                "caixa_osc_cm": float(np.nanmedian(alt_osc)) if alt_osc else float("nan"),
                "caixa_zupt_subida_cm": float(np.median(sobe)) if sobe else float("nan"),
                "caixa_zupt_descida_cm": float(np.median(desce)) if desce else float("nan"),
                "n_subidas_zupt": len(sobe),
                "n_descidas_zupt": len(desce),
            })

    print(f"\n  Caixa real: {caixa:.1f} cm. Imune à inclinação = valores iguais em todas as inclinações.\n")
    print(f"  {'alvo':>5} {'pé':>4} {'tronco':>7} {'OSC sobe parado':>16} {'caixa OSC':>10} {'caixa ZUPT ↑':>13} {'caixa ZUPT ↓':>13}")
    for l in linhas:
        print(f"  {l['inclinacao_alvo']:>4}° {l['pe']:>4} {l['tronco_medido_graus']:>6.0f}° {l['subida_osc_parado_cm']:>13.1f} cm "
              f"{l['caixa_osc_cm']:>7.1f} cm {l['caixa_zupt_subida_cm']:>10.1f} cm {l['caixa_zupt_descida_cm']:>10.1f} cm")

    # Caminhadas: altura máxima e comprimento das passadas do ZUPT, reto x inclinado
    print("\n  Caminhadas (ZUPT):")
    resumo_cam = {}
    for nome in ("caminhada_reta", "caminhada_inclinada"):
        f = porfase.get(nome)
        if f is None:
            continue
        for pe in passadas:
            ps = [p for p in passadas[pe] if f["t0"] <= p["t_inicio_unix"] <= f["t1"] and p["comprimento_m"] > 0.15]
            if not ps:
                print(f"    {nome:<20} pé {pe}: nenhuma passada detectada")
                continue
            alt = float(np.median([p["altura_max_cm"] for p in ps]))
            comp = float(np.median([p["comprimento_m"] for p in ps]))
            resumo_cam[f"{nome}_{pe}"] = {"passadas": len(ps), "altura_max_cm": alt, "comprimento_m": comp}
            print(f"    {nome:<20} pé {pe}: {len(ps):>2} passadas | altura máx {alt:5.1f} cm | comprimento {comp:4.2f} m")

    saida = os.path.join(pasta, "resultado.csv")
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()) if linhas else ["inclinacao_alvo"])
        w.writeheader()
        for l in linhas:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in l.items()})
    with open(os.path.join(pasta, "resultado_caminhadas.json"), "w", encoding="utf-8") as f:
        json.dump(resumo_cam, f, indent=2)
    zupt_pe.salvar_csv(passadas["ESQ"] + passadas["DIR"], os.path.join(pasta, "zupt_passadas.csv"))
    print(f"\n  Resultados salvos em {saida}")
    return linhas


def main():
    ap = argparse.ArgumentParser(description="Testa se a altura do pé muda com a inclinação do tronco (OSC x ZUPT).")
    ap.add_argument("--analisar", metavar="PASTA", help="Só analisa uma gravação já feita")
    args = ap.parse_args()
    pasta = args.analisar or gravar()
    if pasta:
        analisar(pasta)


if __name__ == "__main__":
    main()
