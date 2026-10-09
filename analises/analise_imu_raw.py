import csv
import sys
import os
import argparse
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks

# Garante suporte UTF-8 no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def carregar_samples_csv(caminho_csv):
    """Carrega dados capturados pelo cliente SolarXR do SlimeVR."""
    if not os.path.exists(caminho_csv):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_csv}")

    dados_por_tracker = {}

    with open(caminho_csv, mode="r", encoding="utf-8") as f:
        leitor = csv.DictReader(f)
        for linha in leitor:
            body_part = linha.get("body_part_name") or "DESCONHECIDO"
            tracker_num = linha.get("tracker_num") or "0"
            chave = f"{body_part} (ID #{tracker_num})"

            if chave not in dados_por_tracker:
                dados_por_tracker[chave] = {
                    "body_part": body_part,
                    "t_unix_ms": [],
                    "t_rel_s": [],
                    "lin_ax": [], "lin_ay": [], "lin_az": [],
                    "raw_ax": [], "raw_ay": [], "raw_az": [],
                    "raw_gx": [], "raw_gy": [], "raw_gz": [],
                    "rot_x": [], "rot_y": [], "rot_z": [], "rot_w": []
                }

            try:
                t_ms = float(linha["received_unix_ms"])
            except (ValueError, KeyError):
                continue

            track = dados_por_tracker[chave]
            track["t_unix_ms"].append(t_ms)

            def get_float(k):
                val = linha.get(k, "")
                return float(val) if val and val.strip() != "" else np.nan

            track["lin_ax"].append(get_float("linear_acc_x_ms2"))
            track["lin_ay"].append(get_float("linear_acc_y_ms2"))
            track["lin_az"].append(get_float("linear_acc_z_ms2"))

            track["raw_ax"].append(get_float("raw_acc_x_ms2"))
            track["raw_ay"].append(get_float("raw_acc_y_ms2"))
            track["raw_az"].append(get_float("raw_acc_z_ms2"))

            track["raw_gx"].append(get_float("raw_gyro_x_rads"))
            track["raw_gy"].append(get_float("raw_gyro_y_rads"))
            track["raw_gz"].append(get_float("raw_gyro_z_rads"))

            track["rot_x"].append(get_float("rotation_x"))
            track["rot_y"].append(get_float("rotation_y"))
            track["rot_z"].append(get_float("rotation_z"))
            track["rot_w"].append(get_float("rotation_w"))

    # Converte listas para arrays numpy e calcula tempo relativo
    for chave, t_info in dados_por_tracker.items():
        t_arr = np.array(t_info["t_unix_ms"])
        if len(t_arr) > 0:
            t_info["t_rel_s"] = (t_arr - t_arr[0]) / 1000.0
        for k in list(t_info.keys()):
            if k not in ("body_part", "t_unix_ms"):
                t_info[k] = np.array(t_info[k])

    return dados_por_tracker


def analisar_sinal_imu(track_data):
    """Calcula aceleração resultante e detecta picos de impacto."""
    t = track_data["t_rel_s"]
    ax, ay, az = track_data["lin_ax"], track_data["lin_ay"], track_data["lin_az"]

    tem_lin = not np.all(np.isnan(ax))
    if tem_lin:
        # Preenche eventuais NaN por interpolação linear
        nan_mask = np.isnan(ax)
        if np.any(nan_mask):
            idx_validos = np.where(~nan_mask)[0]
            if len(idx_validos) > 1:
                ax = np.interp(np.arange(len(ax)), idx_validos, ax[idx_validos])
                ay = np.interp(np.arange(len(ay)), idx_validos, ay[idx_validos])
                az = np.interp(np.arange(len(az)), idx_validos, az[idx_validos])

        acc_mag = np.sqrt(ax**2 + ay**2 + az**2)
    else:
        acc_mag = np.zeros_like(t)

    # Detecção de picos de impacto de calcanhar (Heel Strike)
    duracao = t[-1] if len(t) > 0 else 1.0
    taxa_estimada = len(t) / duracao if duracao > 0 else 100.0
    dist_pontos = int(taxa_estimada * 0.35)  # Mínimo 350 ms entre impactos

    picos, props = find_peaks(acc_mag, height=3.0, distance=dist_pontos, prominence=1.5)
    return {
        "t": t,
        "acc_mag": acc_mag,
        "ax": ax, "ay": ay, "az": az,
        "picos": picos,
        "t_picos": t[picos],
        "amp_picos": acc_mag[picos],
        "taxa_hz": taxa_estimada
    }


def gerar_graficos_imu(dados_por_tracker):
    trackers_validos = [k for k, v in dados_por_tracker.items() if len(v["t_rel_s"]) > 10]
    if not trackers_validos:
        print("[Aviso] Nenhum tracker com dados suficientes para visualização.")
        return

    n = len(trackers_validos)
    fig, axes = plt.subplots(n, 1, figsize=(14, 3.5 * n), sharex=True)
    if n == 1:
        axes = [axes]

    for i, chave in enumerate(trackers_validos):
        ax = axes[i]
        analise = analisar_sinal_imu(dados_por_tracker[chave])
        t = analise["t"]
        mag = analise["acc_mag"]
        picos = analise["picos"]

        ax.plot(t, mag, color="#1f77b4", label=f"Magnitude Aceleração Linear (m/s²)", linewidth=1.3)
        ax.plot(analise["t_picos"], analise["amp_picos"], "rv", markersize=7, label="Picos de Impacto (Heel Strike)")

        ax.set_title(f"Tracker: {chave} | Taxa de Chegada: {analise['taxa_hz']:.1f} Hz | Impactos Detectados: {len(picos)}")
        ax.set_ylabel("Aceleração (m/s²)")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Tempo (s)")
    plt.tight_layout()
    plt.show()


def main():
    parser = argparse.ArgumentParser(description="Analisador de dados brutos e acelerações IMU do SlimeVR SolarXR")
    parser.add_argument("caminho_csv", type=str, help="Caminho para o arquivo samples.csv gerado pela captura")
    args = parser.parse_args()

    print("=" * 70)
    print("        ANALISADOR DE SINAIS BRUTOS E IMUS (SOLARXR SLIMEVR)")
    print("=" * 70)
    dados = carregar_samples_csv(args.caminho_csv)
    print(f"Trackers identificados no arquivo ({len(dados)}):")
    for chave, info in dados.items():
        n_amostras = len(info["t_rel_s"])
        duracao = info["t_rel_s"][-1] if n_amostras > 0 else 0
        taxa = n_amostras / duracao if duracao > 0 else 0
        tem_lin = not np.all(np.isnan(info["lin_ax"]))
        tem_raw = not np.all(np.isnan(info["raw_ax"]))
        print(f"  • {chave:<25} : {n_amostras} amostras | {duracao:.1f}s (~{taxa:.1f} Hz) | LinAcc: {'SIM' if tem_lin else 'NÃO'} | RawAcc: {'SIM' if tem_raw else 'NÃO'}")

    gerar_graficos_imu(dados)


if __name__ == "__main__":
    main()
