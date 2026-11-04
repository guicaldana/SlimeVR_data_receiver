"""
TEMPORÁRIO — comprimento de cada passo, sem cadastro e sem roteiro.

Usa o calculo_passo.py como na versão inicial: calibração basal com os dois pés no chão,
depois a caminhada de calibração do passo sobre as marcas (CALIBRAR_PASSO, 04/11: fator de
cada pé = distância das marcas / passo medido; pergunta a distância, padrão 30 cm) e então a
caminhada livre. Os passos da calibração não entram no resultado; os outros saem com o fator
(o relatório mostra também sem o fator). Para validar, calibre numa distância (ex.: 30 cm) e
caminhe na coleta em outra (ex.: 40 cm). Ctrl+C termina e mostra a tabela passo a passo (pé, comprimento,
altura do pé, tronco) e os gráficos. Sem calibração por caixa.
Cada execução vai para uma pasta nova, dados/debug_casa/<AAAAMMDD_HHMMSS>_passo[_<N>cm]/
(o enviar_dados.py manda a mais recente pelo git).

    uv run python teste_passo_casa.py           # caminhada livre
    uv run python teste_passo_casa.py 30        # marcas com 30 cm entre calcanhares da coleta (só no nome da pasta)
"""
import os
import sys
import time

import calculo_passo as cp

cp.PEDIR_PARTICIPANTE = False   # sem cadastro
cp.ROTEIRO_GUIADO = False       # sem roteiro: grava até Ctrl+C
cp.CALIBRAR_CAIXA = False       # OSC bruto, sem ganho da caixa
cp.CALIBRAR_PASSO = True        # caminhada nas marcas antes da coleta: fator do passo por pé

if __name__ == "__main__":
    nome = time.strftime("%Y%m%d_%H%M%S") + "_passo"
    if len(sys.argv) > 1:
        nome += f"_{sys.argv[1]}cm"
    pasta = os.path.join("dados", "debug_casa", nome)
    os.makedirs(pasta, exist_ok=True)
    cp.definir_pasta_saida(pasta)
    print(f"Os arquivos desta coleta vão para {pasta}")
    cp.main()
