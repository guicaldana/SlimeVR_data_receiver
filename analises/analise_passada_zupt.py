import csv
import sys
import os
import argparse
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks

# Garante suporte UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def carregar_dados_tracker_solarxr(caminho_csv, parte_alvo="FOOT"):
    """Carrega dados das IMUs dos pés a partir do samples.csv do SolarXR."""
    if not os.path.exists(caminho_csv):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_csv}")

    dados_pes = {}

    with open(caminho_csv, mode="r", encoding="utf-8") as f:
        leitor = csv.DictReader(f)
        for linha in leitor:
            nome_parte = linha.get("body_part_name") or ""
            if parte_alvo not in nome_parte:
                continue

            tracker_id = f"{nome_parte} (#{linha.get('tracker_num', '0')})"
            if tracker_id not in dados_pes:
                dados_pes[tracker_id] = {
                    "parte": nome_parte,
                    "t_ms": [],
                    "ax": [], "ay": [], "az": []
                }

            try:
                t = float(linha["received_unix_ms"])
                ax = float(linha["linear_acc_x_ms2"])
                ay = float(linha["linear_acc_y_ms2"])
                az = float(linha["linear_acc_z_ms2"])
            except (ValueError, KeyError, TypeError):
                continue

            dados_pes[tracker_id]["t_ms"].append(t)
            dados_pes[tracker_id]["ax"].append(ax)
            dados_pes[tracker_id]["ay"].append(ay)
            dados_pes[tracker_id]["az"].append(az)

    # Converte e uniformiza tempo em 100 Hz
    dados_processados = {}
    for tid, info in dados_pes.items():
        if len(info["t_ms"]) < 30:
            continue
        t_arr = np.array(info["t_ms"])
        t_s = (t_arr - t_arr[0]) / 1000.0

        # Base de tempo uniforme a 100 Hz
        dt = 0.01
        t_comum = np.arange(0, t_s[-1], dt)

        ax_i = np.interp(t_comum, t_s, info["ax"])
        ay_i = np.interp(t_comum, t_s, info["ay"])
        az_i = np.interp(t_comum, t_s, info["az"])

        dados_processados[tid] = {
            "nome": tid,
            "parte": info["parte"],
            "t": t_comum,
            "dt": dt,
            "ax": ax_i,
            "ay": ay_i,
            "az": az_i
        }

    return dados_processados


def processar_passadas_zupt(sinal_tracker):
    """Calcula comprimento e altura de cada passada via integração inercial com ZUPT."""
    t = sinal_tracker["t"]
    dt = sinal_tracker["dt"]
    ax, ay, az = sinal_tracker["ax"], sinal_tracker["ay"], sinal_tracker["az"]

    # Magnitude de aceleração linear resultante
    acc_mag = np.sqrt(ax**2 + ay**2 + az**2)

    # Suavização suave para detecção de repouso (fase de apoio / Stance)
    b, a = butter(2, 6.0 / (50.0), btype="low")
    acc_mag_suave = filtfilt(b, a, acc_mag)

    # 1. Detecção de fases de repouso no solo (Stance: aceleração baixa e estável)
    limiar_repouso = 2.0  # m/s²
    em_repouso = acc_mag_suave < limiar_repouso

    # 2. Detecção dos picos de impacto (Heel-Strike)
    picos_impacto, props = find_peaks(
        acc_mag,
        height=6.0,
        distance=int(0.40 / dt),  # Mínimo 400ms entre impactos do mesmo pé
        prominence=3.0
    )

    if len(picos_impacto) < 2:
        return None

    passos_calculados = []
    vel_x = np.zeros_like(t)
    vel_y = np.zeros_like(t)
    vel_z = np.zeros_like(t)
    pos_x = np.zeros_like(t)
    pos_y = np.zeros_like(t)  # Elevação vertical (altura da passada)
    pos_z = np.zeros_like(t)

    # Para cada intervalo entre impactos consecutivos do pé
    for i in range(len(picos_impacto) - 1):
        idx_inicio = picos_impacto[i]
        idx_fim = picos_impacto[i + 1]

        t_sub = t[idx_inicio:idx_fim]
        if len(t_sub) < 5:
            continue

        # Janela de balanço (após o início do movimento até o próximo impacto)
        ax_sub = ax[idx_inicio:idx_fim]
        ay_sub = ay[idx_inicio:idx_fim]
        az_sub = az[idx_inicio:idx_fim]

        # Primeira integração: aceleração -> velocidade
        vx = np.cumsum(ax_sub) * dt
        vy = np.cumsum(ay_sub) * dt
        vz = np.cumsum(az_sub) * dt

        # ZUPT (Zero-Velocity Update): Correção linear de deriva de velocidade
        # No instante final (novo impacto no solo), a velocidade deve voltar a 0
        n_pontos = len(t_sub)
        vx_corrigida = vx - np.linspace(0, vx[-1], n_pontos)
        vy_corrigida = vy - np.linspace(0, vy[-1], n_pontos)
        vz_corrigida = vz - np.linspace(0, vz[-1], n_pontos)

        # Segunda integração: velocidade corrigida -> deslocamento e altura
        dx = np.cumsum(vx_corrigida) * dt
        dy = np.cumsum(vy_corrigida) * dt
        dz = np.cumsum(vz_corrigida) * dt

        # Altura vertical da passada: corrigida para começar e terminar no solo (0)
        altura_perfil = dy - np.linspace(0, dy[-1], n_pontos)
        altura_max_cm = float(np.max(altura_perfil) * 100.0)

        # Deslocamento horizontal total (comprimento da passada do pé)
        comp_horizontal_m = float(np.sqrt((dx[-1] - dx[0])**2 + (dz[-1] - dz[0])**2))
        vel_max_ms = float(np.max(np.sqrt(vx_corrigida**2 + vy_corrigida**2 + vz_corrigida**2)))
        duracao_s = float(t_sub[-1] - t_sub[0])

        # Armazena curvas nos arrays globais para plotagem
        vel_x[idx_inicio:idx_fim] = vx_corrigida
        vel_y[idx_inicio:idx_fim] = vy_corrigida
        vel_z[idx_inicio:idx_fim] = vz_corrigida
        pos_y[idx_inicio:idx_fim] = altura_perfil

        passos_calculados.append({
            "passo": i + 1,
            "t_inicio": t[idx_inicio],
            "t_impacto": t[idx_fim],
            "duracao_s": duracao_s,
            "comprimento_m": comp_horizontal_m,
            "altura_cm": altura_max_cm,
            "vel_max_ms": vel_max_ms,
            "perfil_altura": altura_perfil,
            "t_perfil": t_sub
        })

    return {
        "tracker": sinal_tracker["nome"],
        "t": t,
        "acc_mag": acc_mag,
        "ay": ay,
        "pos_y": pos_y,
        "picos_impacto": picos_impacto,
        "passos": passos_calculados
    }


def exibir_relatorio_zupt(resultados):
    print("\n" + "=" * 80)
    print("      ANÁLISE INERCIAL DA PASSADA: COMPRIMENTO E ALTURA (ZUPT 3D)")
    print("=" * 80)

    for r in resultados:
        nome = r["tracker"]
        passos = r["passos"]
        if not passos:
            print(f"\n[{nome}] Não foram detectados passos suficientes para integração.")
            continue

        comps = [p["comprimento_m"] for p in passos]
        alts = [p["altura_cm"] for p in passos]
        durs = [p["duracao_s"] for p in passos]
        vels = [p["vel_max_ms"] for p in passos]

        print(f"\n▶ Tracker: {nome} ({len(passos)} passadas identificadas)")
        print(f"  Comprimento médio da passada: {np.mean(comps):.3f} m (± {np.std(comps):.3f} m)")
        print(f"  Altura média (Foot Clearance): {np.mean(alts):.1f} cm (± {np.std(alts):.1f} cm)")
        print(f"  Velocidade máxima média no ar: {np.mean(vels):.2f} m/s")
        print(f"  Tempo médio de ciclo:         {np.mean(durs):.2f} s")

        print(f"\n  {'#':<4} {'Tempo(s)':<10} {'Duração(s)':<12} {'Comprimento(m)':<16} {'Altura Máx(cm)':<16} {'Vel Pico(m/s)'}")
        print("  " + "-" * 72)
        for p in passos:
            print(f"  {p['passo']:<4} {p['t_inicio']:<10.2f} {p['duracao_s']:<12.2f} {p['comprimento_m']:<16.3f} {p['altura_cm']:<16.1f} {p['vel_max_ms']:<10.2f}")

    print("=" * 80)


def plotar_graficos_zupt(resultados):
    validos = [r for r in resultados if r and r["passos"]]
    if not validos:
        return

    n_trackers = len(validos)
    fig, axes = plt.subplots(3, n_trackers, figsize=(7 * n_trackers, 9), sharex="col")
    if n_trackers == 1:
        axes = np.expand_dims(axes, axis=1)

    for col, r in enumerate(validos):
        t = r["t"]
        passos = r["passos"]

        # Painel 1: Aceleração linear com impactos marcados
        ax_acc = axes[0, col]
        ax_acc.plot(t, r["acc_mag"], color="#1f77b4", label="Aceleração Linear (m/s²)", alpha=0.85)
        t_picos = [p["t_impacto"] for p in passos]
        ax_acc.plot(t_picos, [r["acc_mag"][int(tp / 0.01)] for tp in t_picos], "rv", markersize=7, label="Impacto (Heel Strike)")
        ax_acc.set_title(f"1. Aceleração e Impactos — {r['tracker']}", fontsize=10, fontweight="bold")
        ax_acc.set_ylabel("Aceleração (m/s²)")
        ax_acc.legend(loc="upper right", fontsize=8)
        ax_acc.grid(True, alpha=0.3)

        # Painel 2: Trajetória Vertical Integrada (Altura / Clearance em cm)
        ax_alt = axes[1, col]
        ax_alt.plot(t, r["pos_y"] * 100.0, color="darkorange", label="Trajetória Vertical do Pé (cm)", linewidth=1.4)
        for p in passos:
            idx_max = np.argmax(p["perfil_altura"])
            t_max = p["t_perfil"][idx_max]
            ax_alt.plot(t_max, p["altura_cm"], "go", markersize=6)
        ax_alt.set_title("2. Altura da Passada (Clearance Vertical do Pé)", fontsize=10, fontweight="bold")
        ax_alt.set_ylabel("Elevação (cm)")
        ax_alt.axhline(0, color="gray", linestyle=":")
        ax_alt.legend(loc="upper right", fontsize=8)
        ax_alt.grid(True, alpha=0.3)

        # Painel 3: Comprimento e Altura por passada (barras lado a lado)
        ax_bar = axes[2, col]
        idx_p = np.arange(1, len(passos) + 1)
        largura = 0.38
        comps = [p["comprimento_m"] for p in passos]
        alts = [p["altura_cm"] for p in passos]

        ax_bar.bar(idx_p - largura/2, comps, width=largura, color="steelblue", label="Comprimento (m)")
        ax_bar.set_ylabel("Comprimento (m)", color="steelblue")
        ax_bar.tick_params(axis="y", labelcolor="steelblue")
        ax_bar.set_xlabel("Número da Passada")
        ax_bar.set_title("3. Comprimento (m) vs Altura (cm) por Passada", fontsize=10, fontweight="bold")
        ax_bar.set_xticks(idx_p)

        ax_twin = ax_bar.twinx()
        ax_twin.bar(idx_p + largura/2, alts, width=largura, color="coral", label="Altura (cm)")
        ax_twin.set_ylabel("Altura (cm)", color="coral")
        ax_twin.tick_params(axis="y", labelcolor="coral")
        ax_bar.grid(True, alpha=0.3, axis="x")

    plt.tight_layout()
    plt.show()


def main():
    parser = argparse.ArgumentParser(description="Cálculo inercial de comprimento e altura da passada via ZUPT (SolarXR IMU)")
    parser.add_argument("caminho_csv", type=str, help="Caminho para o samples.csv da captura SolarXR")
    args = parser.parse_args()

    dados_pes = carregar_dados_tracker_solarxr(args.caminho_csv, parte_alvo="FOOT")
    if not dados_pes:
        # Se não encontrar 'FOOT', tenta 'LEG' (tornozelos/canelas)
        dados_pes = carregar_dados_tracker_solarxr(args.caminho_csv, parte_alvo="LEG")

    if not dados_pes:
        print("[Erro] Nenhum tracker de pé ou perna encontrado no arquivo CSV.")
        return

    resultados = []
    for tid, info in dados_pes.items():
        res = processar_passadas_zupt(info)
        if res:
            resultados.append(res)

    exibir_relatorio_zupt(resultados)
    plotar_graficos_zupt(resultados)


if __name__ == "__main__":
    main()

