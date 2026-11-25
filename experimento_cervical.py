"""
Experimento da inclinação cervical: caminhada no corredor de pegadas com ou sem o Meta Quest 3S.

Cada participante faz uma condição só (com Quest ou sem Quest; grupos diferentes). Mede só a
cabeça: não há talco nem medidas de régua. Trackers usados (orientação pelo SolarXR):
  - cabeça: tracker na nuca, parte do corpo HEAD no SlimeVR (ou NECK);
  - tronco: o tracker do peito de sempre (UPPER_CHEST ou CHEST).
O Quest não manda nada para o SlimeVR: não ligue o Quest ao SlimeVR (SteamVR), senão a cabeça do
SlimeVR passa a vir do Quest.

Roteiro (tudo com Enter):
  1. nome, altura e condição (1 = sem Quest, 2 = com Quest);
  2. postura neutra: de pé, tronco reto, olhando um alvo na altura dos olhos, parado 5 s;
  3. olhar para baixo: abaixa só a cabeça olhando para os pés, parado 3 s. Serve para achar
     sozinho a direção frente-trás da cabeça, sem depender de como o tracker foi preso na nuca;
  4. passagens no corredor: [Enter] começa, caminha pisando nas pegadas, para no fim, [Enter] termina.

Ângulos (graus, 0 = postura neutra, positivo = flexão / olhar para baixo):
  - cabeça: inclinação da cabeça em relação à gravidade, a partir da postura neutra. Não depende
    do tronco nem do tracker do peito;
  - tronco: o mesmo para o peito;
  - cervical: cabeça − tronco (quanto o pescoço dobrou além do que o tronco inclinou);
  - lateral da cabeça (inclinação para o lado, positivo = orelha direita para baixo; conferir o sinal
    na primeira gravação).
Por passagem e no total: média, desvio, pico de flexão, maior extensão, amplitude (ROM) e % do tempo
em cada faixa (extensão < 0°, 0-10°, 10-20°, > 20°).

Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_cervical_<condicao>/: dados_brutos.json
(quaternions de todos os trackers + eventos), serie_cervical.csv, resumo_cervical.json,
resumo_cervical.txt e cervical.png.

    uv run python experimento_cervical.py                  # coleta e análise
    uv run python experimento_cervical.py --passagens 2    # número de passagens (padrão 3)
    uv run python experimento_cervical.py --analisar PASTA # refaz a análise de uma coleta
"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys
import threading
import time

import numpy as np

import participante as cadastro

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== CONFIGURAÇÃO ========================
PASSAGENS_PADRAO = 3
PARTES_CABECA = ["HEAD", "NECK"]            # primeira que aparecer
PARTES_TRONCO = ["UPPER_CHEST", "CHEST"]
TEMPO_NEUTRO_S = 5.0
TEMPO_BAIXO_S = 3.0
GIRO_MIN_BAIXO_GRAUS = 10.0                 # abaixo disso o "olhar para baixo" não serve
CIMA = np.array([0.0, 1.0, 0.0])            # SlimeVR: Y para cima
FRENTE_PADRAO = np.array([0.0, 0.0, -1.0])  # usado só se o "olhar para baixo" falhar
LIMIAR_RISCO_GRAUS = 20.0
FAIXAS_GRAUS = [(-90.0, 0.0, "extensao"), (0.0, 10.0, "0-10"), (10.0, 20.0, "10-20"), (20.0, 90.0, ">20")]
FREQ_GRADE_HZ = 60.0                        # série reamostrada em grade uniforme
CORTE_HZ = 6.0                              # Butterworth 2ª ordem ida e volta (0 = sem filtro)
CONGELADO_S = 2.0                           # quaternion igual por mais que isso = tracker parado/travado
SCRIPT_STREAM = os.path.join("slimevr_client_raw", "src", "stream_orientacao.mjs")
CONDICOES = {"1": "semQuest", "2": "comQuest"}
NOME_CONDICAO = {"semQuest": "sem Quest", "comQuest": "com Quest 3S"}
# ==============================================================


# ----------------------------- quaternions (x, y, z, w) -----------------------------
def q_conj(q):
    q = np.asarray(q, dtype=float)
    return np.array([-q[0], -q[1], -q[2], q[3]])


def q_mult(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def q_girar(q, v):
    """Gira o vetor v pelo quaternion q (aceita q com forma (4,) ou (N, 4))."""
    q = np.asarray(q, dtype=float)
    u, w = q[..., :3], q[..., 3:4]
    v = np.broadcast_to(np.asarray(v, dtype=float), u.shape)
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def q_media(qs):
    """Média de quaternions próximos (alinha o sinal e normaliza)."""
    qs = np.array(qs, dtype=float)
    ref = qs[0]
    qs = np.where((qs @ ref)[:, None] < 0, -qs, qs)
    m = qs.mean(axis=0)
    return m / np.linalg.norm(m)


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else None


# ----------------------------- captura -----------------------------
class Captura:
    def __init__(self):
        self.lock = threading.Lock()
        self.series = {}          # parte -> {"t": [], "q": []}
        self.t0_ms = None
        self.proc = None

    def iniciar(self):
        if not os.path.exists(SCRIPT_STREAM):
            raise RuntimeError(f"Não achei {SCRIPT_STREAM}")
        self.proc = subprocess.Popen(["node", SCRIPT_STREAM], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        for linha in self.proc.stdout:
            p = linha.split()
            if len(p) != 7 or p[0] != "Q":
                continue
            try:
                q = [float(v) for v in p[2:6]]
                ms = float(p[6])
            except ValueError:
                continue
            with self.lock:
                if self.t0_ms is None:
                    self.t0_ms = ms
                s = self.series.setdefault(p[1], {"t": [], "q": []})
                s["t"].append((ms - self.t0_ms) / 1000.0)
                s["q"].append(q)

    def agora(self):
        with self.lock:
            return max((s["t"][-1] for s in self.series.values() if s["t"]), default=0.0)

    def parar(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def copia(self):
        with self.lock:
            return {k: {"t": list(v["t"]), "q": [list(q) for q in v["q"]]} for k, v in self.series.items()}


def escolher_parte(series, opcoes):
    return next((p for p in opcoes if p in series and len(series[p]["t"]) > 0), None)


def esperar_trackers(cap, tempo_max_s=15.0):
    fim = time.time() + tempo_max_s
    while time.time() < fim:
        s = cap.copia()
        if escolher_parte(s, PARTES_CABECA) and escolher_parte(s, PARTES_TRONCO):
            break
        time.sleep(0.5)
    s = cap.copia()
    print(f"   Trackers vistos no SolarXR: {', '.join(sorted(s)) or 'nenhum'}")
    cab, tro = escolher_parte(s, PARTES_CABECA), escolher_parte(s, PARTES_TRONCO)
    if not cab:
        print(f"   [Aviso] Nenhum tracker de cabeça ({'/'.join(PARTES_CABECA)}). Confira a parte do corpo no SlimeVR.")
    if not tro:
        print(f"   [Aviso] Nenhum tracker de tronco ({'/'.join(PARTES_TRONCO)}): só o ângulo da cabeça sai.")
    return cab, tro


def janela(cap, nome, duracao_s, eventos):
    t_ini = cap.agora()
    time.sleep(duracao_s)
    eventos.append({"nome": nome, "t_ini": t_ini, "t_fim": cap.agora()})


def coletar(n_passagens):
    esqueleto = cadastro.ler_esqueleto_slimevr()
    part = cadastro.pedir_participante(esqueleto=esqueleto)
    while True:
        c = input("   Condição (1 = sem Quest, 2 = com Quest 3S): ").strip()
        if c in CONDICOES:
            break
        print("   Digite 1 ou 2.")
    part["condicao"] = CONDICOES[c]
    part["experimento"] = f"cervical_{part['condicao']}"
    part["passagens"] = n_passagens
    print(f"   Condição: {NOME_CONDICAO[part['condicao']]}\n")
    if part["condicao"] == "comQuest":
        print("   Lembrete: o Quest NÃO pode estar ligado ao SlimeVR (SteamVR). O tracker da nuca fica na")
        print("   mesma faixa elástica, por baixo da alça do Quest.\n")

    cap = Captura()
    cap.iniciar()
    print("   Conectando ao SolarXR (porta 21110)...")
    esperar_trackers(cap)
    eventos = []

    input("\n   POSTURA NEUTRA: de pé, tronco reto, olhando o alvo na altura dos olhos. [Enter] e fique parado...")
    print(f"   Medindo {TEMPO_NEUTRO_S:.0f} s...")
    janela(cap, "neutro", TEMPO_NEUTRO_S, eventos)
    input("\n   OLHAR PARA BAIXO: abaixe só a cabeça olhando para os pés (tronco reto). [Enter] e fique parado...")
    print(f"   Medindo {TEMPO_BAIXO_S:.0f} s...")
    janela(cap, "baixo", TEMPO_BAIXO_S, eventos)
    print("   Pode voltar a olhar para a frente.")

    for i in range(1, n_passagens + 1):
        input(f"\n   PASSAGEM {i}/{n_passagens}: no início do corredor. [Enter] para começar a caminhar...")
        t_ini = cap.agora()
        input("   Caminhando... [Enter] quando parar no fim do corredor.")
        eventos.append({"nome": f"P{i}", "t_ini": t_ini, "t_fim": cap.agora()})
        print(f"   Passagem {i}: {eventos[-1]['t_fim'] - t_ini:.1f} s")

    cap.parar()
    pasta = cadastro.criar_pasta_coleta(part)
    with open(os.path.join(pasta, "dados_brutos.json"), "w", encoding="utf-8") as f:
        json.dump({"series": cap.copia(), "eventos": eventos, "condicao": part["condicao"]}, f)
    print(f"\n   Dados brutos salvos em {pasta}")
    return pasta


# ----------------------------- análise -----------------------------
def amostras_em(serie, ev):
    t = np.array(serie["t"])
    m = (t >= ev["t_ini"]) & (t <= ev["t_fim"])
    return np.array(serie["q"])[m]


def frente_da_cabeca(q_neutro, q_baixo):
    """Direção horizontal "para a frente" (mundo) pela rotação neutro -> olhar para baixo."""
    if q_baixo is None:
        return None, 0.0
    r = q_mult(q_baixo, q_conj(q_neutro))
    if r[3] < 0:
        r = -r
    giro = math.degrees(2.0 * math.acos(min(1.0, r[3])))
    eixo = _unit(r[:3])
    if eixo is None or giro < GIRO_MIN_BAIXO_GRAUS:
        return None, giro
    lateral = _unit(eixo - (eixo @ CIMA) * CIMA)
    if lateral is None:
        return None, giro
    frente = _unit(np.cross(CIMA, lateral))
    if q_girar(r, frente)[1] > 0:        # olhar para baixo tem que baixar a frente
        frente = -frente
    return frente, giro


def angulos(serie, q_neutro, frente):
    """Flexão (+ = para baixo) e inclinação lateral (+ = direita para baixo), em graus, vs a postura neutra."""
    q = np.array(serie["q"], dtype=float)
    f_local = q_girar(q_conj(q_neutro), frente)
    direita = np.cross(frente, CIMA)
    d_local = q_girar(q_conj(q_neutro), direita)
    fy = q_girar(q, f_local)[:, 1]
    dy = q_girar(q, d_local)[:, 1]
    return (np.degrees(np.arcsin(np.clip(-fy, -1, 1))), np.degrees(np.arcsin(np.clip(-dy, -1, 1))))


def filtrar(t_grade, t, x):
    y = np.interp(t_grade, t, x)
    if CORTE_HZ and len(y) > 15:
        from scipy.signal import butter, filtfilt
        b, a = butter(2, CORTE_HZ / (FREQ_GRADE_HZ / 2.0))
        y = filtfilt(b, a, y)
    return y


def congelamentos(serie, t_ini, t_fim):
    """Trechos em que o quaternion ficou idêntico por mais de CONGELADO_S (tracker travado)."""
    t = np.array(serie["t"])
    q = np.array(serie["q"])
    m = (t >= t_ini) & (t <= t_fim)
    t, q = t[m], q[m]
    trechos, ini = [], None
    for k in range(1, len(t)):
        igual = np.allclose(q[k], q[k - 1], atol=1e-6)
        if igual and ini is None:
            ini = t[k - 1]
        if (not igual or k == len(t) - 1) and ini is not None:
            fim = t[k] if igual else t[k - 1]
            if fim - ini > CONGELADO_S:
                trechos.append((round(float(ini), 1), round(float(fim), 1)))
            ini = None
    buracos = [(round(float(a), 1), round(float(b), 1)) for a, b in zip(t[:-1], t[1:]) if b - a > CONGELADO_S]
    return trechos + buracos


def estatisticas(x):
    x = np.asarray(x)
    if len(x) == 0:
        return None
    est = {"media": float(np.mean(x)), "desvio": float(np.std(x)), "pico_flexao": float(np.max(x)),
           "maior_extensao": float(np.min(x)), "rom": float(np.max(x) - np.min(x)),
           "p95": float(np.percentile(x, 95)),
           "pct_acima_limiar": float(100.0 * np.mean(x > LIMIAR_RISCO_GRAUS))}
    for lo, hi, nome in FAIXAS_GRAUS:
        est[f"pct_{nome}"] = float(100.0 * np.mean((x >= lo) & (x < hi)))
    return {k: round(v, 2) for k, v in est.items()}


def analisar(pasta):
    with open(os.path.join(pasta, "dados_brutos.json"), encoding="utf-8") as f:
        bruto = json.load(f)
    series, eventos = bruto["series"], bruto["eventos"]
    ev = {e["nome"]: e for e in eventos}
    passagens = [e for e in eventos if e["nome"].startswith("P")]
    cab, tro = escolher_parte(series, PARTES_CABECA), escolher_parte(series, PARTES_TRONCO)
    if not cab:
        print("   Sem tracker de cabeça na gravação: nada a analisar.")
        return None
    avisos = []

    qn_cab = q_media(amostras_em(series[cab], ev["neutro"]))
    qb = amostras_em(series[cab], ev["baixo"]) if "baixo" in ev else []
    frente, giro_baixo = frente_da_cabeca(qn_cab, q_media(qb) if len(qb) else None)
    if frente is None:
        avisos.append(f"Olhar para baixo girou só {giro_baixo:.0f}° (mín. {GIRO_MIN_BAIXO_GRAUS:.0f}°): "
                      "frente da cabeça tirada do reset do SlimeVR (-Z); confira o sinal.")
        frente = _unit(FRENTE_PADRAO - (FRENTE_PADRAO @ CIMA) * CIMA)

    t_ini = min(e["t_ini"] for e in eventos)
    t_fim = max(e["t_fim"] for e in eventos)
    t_grade = np.arange(t_ini, t_fim, 1.0 / FREQ_GRADE_HZ)
    pitch_c, lat_c = angulos(series[cab], qn_cab, frente)
    cabeca = filtrar(t_grade, np.array(series[cab]["t"]), pitch_c)
    lateral = filtrar(t_grade, np.array(series[cab]["t"]), lat_c)
    tronco = cervical = None
    if tro:
        qn_tro = q_media(amostras_em(series[tro], ev["neutro"]))
        pitch_t, _ = angulos(series[tro], qn_tro, frente)
        tronco = filtrar(t_grade, np.array(series[tro]["t"]), pitch_t)
        cervical = cabeca - tronco
    else:
        avisos.append("Sem tracker de tronco: só o ângulo da cabeça (sem cervical relativo).")

    for parte in [p for p in (cab, tro) if p]:
        for p in passagens:
            for a, b in congelamentos(series[parte], p["t_ini"], p["t_fim"]):
                avisos.append(f"{parte} sem mudança/sem dados de {a} a {b} s ({p['nome']}): trecho suspeito.")

    resumo = {"pasta": pasta, "condicao": bruto.get("condicao"), "tracker_cabeca": cab, "tracker_tronco": tro,
              "giro_olhar_baixo_graus": round(giro_baixo, 1), "frente_mundo": [round(v, 3) for v in frente],
              "limiar_graus": LIMIAR_RISCO_GRAUS, "corte_hz": CORTE_HZ, "avisos": avisos, "passagens": {}}
    m_total = np.zeros_like(t_grade, dtype=bool)
    rotulo = np.full(len(t_grade), "", dtype=object)
    for p in passagens:
        m = (t_grade >= p["t_ini"]) & (t_grade <= p["t_fim"])
        m_total |= m
        rotulo[m] = p["nome"]
        resumo["passagens"][p["nome"]] = {
            "duracao_s": round(p["t_fim"] - p["t_ini"], 1),
            "cabeca": estatisticas(cabeca[m]), "lateral_cabeca": estatisticas(lateral[m]),
            "tronco": estatisticas(tronco[m]) if tronco is not None else None,
            "cervical": estatisticas(cervical[m]) if cervical is not None else None}
    resumo["total"] = {"cabeca": estatisticas(cabeca[m_total]), "lateral_cabeca": estatisticas(lateral[m_total]),
                       "tronco": estatisticas(tronco[m_total]) if tronco is not None else None,
                       "cervical": estatisticas(cervical[m_total]) if cervical is not None else None}

    with open(os.path.join(pasta, "serie_cervical.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "trecho", "cabeca_graus", "lateral_cabeca_graus", "tronco_graus", "cervical_graus"])
        for k, t in enumerate(t_grade):
            w.writerow([f"{t:.3f}", rotulo[k], f"{cabeca[k]:.2f}", f"{lateral[k]:.2f}",
                        f"{tronco[k]:.2f}" if tronco is not None else "",
                        f"{cervical[k]:.2f}" if cervical is not None else ""])
    with open(os.path.join(pasta, "resumo_cervical.json"), "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2)
    texto = texto_resumo(resumo)
    with open(os.path.join(pasta, "resumo_cervical.txt"), "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto)
    grafico(pasta, t_grade, cabeca, tronco, cervical, passagens)
    return resumo


def texto_resumo(r):
    linhas = ["=" * 75, f"  INCLINAÇÃO CERVICAL — {NOME_CONDICAO.get(r['condicao'], r['condicao'])}", "=" * 75,
              f"  Cabeça: {r['tracker_cabeca']} | tronco: {r['tracker_tronco'] or '—'} | "
              f"olhar para baixo girou {r['giro_olhar_baixo_graus']:.0f}°",
              "  Graus a partir da postura neutra; + = flexão (para baixo).", ""]
    cab = f"{'trecho':<7}{'ângulo':<10}{'média':>7}{'desvio':>8}{'pico':>7}{'ext.':>7}{'ROM':>7}" \
          f"{'%>' + str(int(r['limiar_graus'])) + '°':>8}"
    linhas.append(cab)
    blocos = list(r["passagens"].items()) + [("total", r["total"])]
    for nome, b in blocos:
        for ang in ("cabeca", "tronco", "cervical", "lateral_cabeca"):
            e = b.get(ang)
            if not e:
                continue
            linhas.append(f"{nome:<7}{ang.replace('_cabeca', ''):<10}{e['media']:>7.1f}{e['desvio']:>8.1f}"
                          f"{e['pico_flexao']:>7.1f}{e['maior_extensao']:>7.1f}{e['rom']:>7.1f}"
                          f"{e['pct_acima_limiar']:>8.1f}")
        linhas.append("")
    if r["avisos"]:
        linhas.append("  Avisos:")
        linhas += [f"   - {a}" for a in r["avisos"]]
    return "\n".join(linhas) + "\n"


def grafico(pasta, t, cabeca, tronco, cervical, passagens):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(t, cabeca, label="cabeça (vs gravidade)", lw=1.2)
    if tronco is not None:
        ax.plot(t, tronco, label="tronco", lw=1.0, alpha=0.8)
        ax.plot(t, cervical, label="cervical (cabeça − tronco)", lw=1.2)
    for p in passagens:
        ax.axvspan(p["t_ini"], p["t_fim"], color="0.9", zorder=0)
        ax.text((p["t_ini"] + p["t_fim"]) / 2, 0.98, p["nome"], transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=9)
    ax.axhline(LIMIAR_RISCO_GRAUS, ls="--", c="0.5", lw=0.8)
    ax.axhline(0, c="0.3", lw=0.6)
    ax.set_xlabel("tempo (s)")
    ax.set_ylabel("flexão a partir da postura neutra (°)")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(pasta, "cervical.png"), dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--passagens", type=int, default=PASSAGENS_PADRAO)
    ap.add_argument("--analisar", metavar="PASTA")
    args = ap.parse_args()
    pasta = args.analisar or coletar(args.passagens)
    analisar(pasta)


if __name__ == "__main__":
    main()
