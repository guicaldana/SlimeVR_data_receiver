"""
TEMPORÁRIO — envia a pasta da coleta mais recente para o git (branch atual), para análise remota.

Procura a pasta mais nova em dados/participantes/<Nome>/<data_hora>/ e dados/debug_casa/<data_hora>/
(ou a pasta passada como argumento), adiciona os arquivos (mesmo os que o .gitignore ignora),
faz o commit e o push.

    uv run python enviar_dados.py                  # pasta mais recente
    uv run python enviar_dados.py CAMINHO/DA/PASTA # uma pasta específica
"""
import glob
import os
import subprocess
import sys

LIMITE_MB = 90   # o GitHub recusa arquivos acima de 100 MB


def pasta_mais_recente():
    candidatas = [p for p in glob.glob(os.path.join("dados", "participantes", "*", "*"))
                  + glob.glob(os.path.join("dados", "debug_casa", "*")) if os.path.isdir(p)]
    if not candidatas:
        sys.exit("Nenhuma pasta de coleta encontrada em dados/.")
    return max(candidatas, key=os.path.getmtime)


def git(*args):
    print("> git " + " ".join(args))
    r = subprocess.run(["git", *args])
    if r.returncode != 0:
        sys.exit(f"Falhou: git {' '.join(args)}")


def main():
    pasta = sys.argv[1] if len(sys.argv) > 1 else pasta_mais_recente()
    arquivos = []
    for raiz, _, nomes in os.walk(pasta):
        for n in nomes:
            arq = os.path.join(raiz, n)
            mb = os.path.getsize(arq) / 1e6
            if mb > LIMITE_MB:
                print(f"Pulando {arq} ({mb:.0f} MB, grande demais para o git)")
                continue
            arquivos.append(arq)
    # o participante.json geral (cadastro) fica uma pasta acima
    cadastro = os.path.join(os.path.dirname(pasta), "participante.json")
    if os.path.exists(cadastro):
        arquivos.append(cadastro)
    if not arquivos:
        sys.exit(f"A pasta {pasta} está vazia.")
    print(f"Enviando {pasta} ({len(arquivos)} arquivos)")
    git("add", "-f", *arquivos)
    git("commit", "-m", f"Dados da coleta {pasta.replace(os.sep, '/')}")
    ramo = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    git("push", "origin", ramo)
    print("Pronto: dados enviados.")


if __name__ == "__main__":
    main()
