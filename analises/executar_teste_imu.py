import sys
import os
import time
import subprocess
import argparse
from datetime import datetime

# Garante saída UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Automatizador de teste de marcha com contagem regressiva e análise")
    parser.add_argument("--prep", type=int, default=5, help="Tempo de preparação em segundos (padrão: 5)")
    parser.add_argument("--duracao", type=int, default=10, help="Tempo de caminhada em segundos (padrão: 10)")
    parser.add_argument("--nome", type=str, default=None, help="Nome da pasta de saída (padrão: data/hora atual)")
    args = parser.parse_args()

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_saida = args.nome if args.nome else f"teste_marcha_{timestamp_str}"
    caminho_saida = os.path.join("slimevr_client_raw", "captures", pasta_saida)

    print("=" * 65)
    print("      TESTE DE MARCHA DAS IMUs - SLIMEVR SOLARXR")
    print("=" * 65)
    print(f"Tempo de preparação:  {args.prep} segundos")
    print(f"Duração do teste:     {args.duracao} segundos")
    print(f"Pasta de destino:     {caminho_saida}")
    print("=" * 65)

    # 1. Contagem regressiva de preparação
    print("\n[ ETAPA 1 ] Posicione-se no ponto de partida da caminhada:")
    for t_restante in range(args.prep, 0, -1):
        print(f"\r  >>> Prepare-se! Começando em {t_restante} segundo(s)...  ", end="", flush=True)
        time.sleep(1.0)

    msg_duracao = f"{args.duracao}s" if args.duracao > 0 else "livre até Ctrl+C"
    print("\n\n" + "#" * 65)
    print(f"  >>> PODE CAMINHAR! GRAVANDO SINAIS DAS IMUs ({msg_duracao})... <<<")
    if args.duracao == 0:
        print("  >>> Pressione 'Ctrl + C' quando quiser encerrar a caminhada. <<<")
    print("#" * 65 + "\n")

    # 2. Executa a captura via Node.js
    comando_node = [
        "node",
        os.path.join("slimevr_client_raw", "src", "cli.mjs"),
        "capture",
        "--seconds", str(args.duracao),
        "--out", caminho_saida
    ]

    try:
        proc = subprocess.run(comando_node)
    except KeyboardInterrupt:
        print("\n\nCaptura finalizada pelo usuário (Ctrl + C). Aguardando gravação dos arquivos...")
        time.sleep(1.0)
    except FileNotFoundError:
        print("\n[Erro] Node.js não foi encontrado. Verifique se o Node está no PATH.")
        return

    print("\n" + "=" * 65)
    print("  >>> TESTE CONCLUÍDO COM SUCESSO! <<<")
    print("=" * 65)

    # 3. Abre automaticamente o analisador dos dados das IMUs
    arquivo_samples = os.path.join(caminho_saida, "samples.csv")
    if os.path.exists(arquivo_samples):
        print(f"\nCalculando comprimento e altura das passadas via ZUPT para '{arquivo_samples}'...")
        comando_analise = [
            "uv", "run", "python",
            "analise_passada_zupt.py",
            arquivo_samples
        ]
        subprocess.run(comando_analise)
    else:
        print(f"[Aviso] Arquivo {arquivo_samples} não foi gerado.")


if __name__ == "__main__":
    main()
