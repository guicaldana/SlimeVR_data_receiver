"""
TEMPORÁRIO — comprimento de cada passo, sem cadastro e sem roteiro.

Usa o calculo_passo.py como na versão inicial: calibração basal com os dois pés no chão,
depois caminhada livre. Ctrl+C termina e mostra a tabela passo a passo (pé, comprimento,
altura do pé, tronco) e os gráficos. Sem calibração por caixa.
Os arquivos vão para a pasta dados/ (sobrescritos a cada execução).

    uv run python teste_passo_casa.py
"""
import calculo_passo as cp

cp.PEDIR_PARTICIPANTE = False   # sem cadastro
cp.ROTEIRO_GUIADO = False       # sem roteiro: grava até Ctrl+C
cp.CALIBRAR_CAIXA = False       # OSC bruto, sem ganho da caixa

if __name__ == "__main__":
    cp.main()
