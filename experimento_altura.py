"""
Experimento de altura: só o pé sobre objetos de altura conhecida, sem caminhada.

Usa os mesmos cálculos e o mesmo cadastro do piloto.py:
  1. nome e altura do participante, rodada (1 = familiarização, 2 = coleta) e as
     alturas dos três apoios do cone (padrão 7, 12 e 19 cm; Enter aceita);
  2. calibração com a caixa em duas alturas (9,4 e 14,5 cm), que dá um fator de correção
     por pé e por método (OSC e cadeia das pernas), a reta pelo zero que melhor se ajusta
     às duas alturas; se elas discordarem, o programa pergunta se quer repetir;
  3. em cada objeto, só o pé E em cima (o pé D fica no chão como referência),
     REPETICOES_ALTURA vezes (tronco reto). Para usar os dois pés: PES_ALTURA = ["ESQ", "DIR"].
     Na rodada de coleta a ordem dos objetos é sorteada.
Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_altura_<rodada>/, com
altura_conhecida.csv, altura_objetos.png e relatorio.txt.

    uv run python experimento_altura.py
"""
import piloto

piloto.EXPERIMENTO = "altura"
piloto.PES_ALTURA = ["ESQ"]
# Apoios do cone (26/10): 4º apoio mais alto 7 cm, 3º 12 cm, o mais alto 19 cm
piloto.ALTURAS_TESTE_PADRAO_CM = [7.0, 12.0, 19.0]
piloto.FAIXA_ALTURAS_TESTE_CM = (5.0, 25.0)
# Calibração com a caixa em duas alturas (9,4 e 14,5 cm), diferentes das alturas do teste
piloto.ALTURAS_CALIBRACAO_CM = [9.4, 14.5]
piloto.ROTEIRO = piloto.montar_roteiro()

if __name__ == "__main__":
    piloto.main()
