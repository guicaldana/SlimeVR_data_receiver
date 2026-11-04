"""
Experimento de altura: só o pé sobre objetos de altura conhecida, sem caminhada.

Usa os mesmos cálculos e o mesmo cadastro do piloto.py:
  1. nome e altura do participante, rodada (1 = familiarização, 2 = coleta) e as
     alturas dos três apoios do cone (padrão 7, 12 e 19 cm; Enter aceita);
  2. calibração com a caixa em 9,4 e 14,5 cm (CALIBRAR_CAIXA = True, 04/11): cada pé sobe
     na caixa nas duas alturas e o programa ajusta, por pé, uma reta com desvio e ganho
     (real = a + b x medido) para o OSC e outra para a cadeia das pernas. A tela, a
     conferência e o relatório mostram as duas alturas já corrigidas (OSC e cadeia);
  3. em cada objeto, os dois pés sobem alternados (E, depois D), REPETICOES_ALTURA vezes
     cada (tronco reto); o pé que fica no chão é a referência. Só o pé E: PES_ALTURA = ["ESQ"].
     Na rodada de coleta a ordem dos objetos é sorteada;
  4. antes do roteiro, um teste rápido da calibração (VERIFICAR_CALIBRACAO): cada pé sobe
     no objeto de 7 cm (5 s para subir, 3 s medindo, altura em tempo real) e, se o erro passar de 1 cm, o programa pede para refazer
     o reset do SlimeVR e calibra de novo.
Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_altura_<rodada>/, com
altura_conhecida.csv, altura_objetos.png e relatorio.txt.

    uv run python experimento_altura.py
"""
import piloto

piloto.EXPERIMENTO = "altura"
piloto.PES_ALTURA = ["ESQ", "DIR"]
# Apoios do cone (26/10): 4º apoio mais alto 7 cm, 3º 12 cm, o mais alto 19 cm
piloto.ALTURAS_TESTE_PADRAO_CM = [7.0, 12.0, 19.0]
piloto.FAIXA_ALTURAS_TESTE_CM = (5.0, 25.0)
# Calibração da caixa com desvio e ganho por pessoa (04/11): o erro mudou muito de uma pessoa
# para outra (Guilherme 29/10: OSC +0,7 cm; João 03/11: OSC +5 cm), com uma parte fixa (alinhamento
# da coxa, pelve) além da de escala. Nos dados de 29/10, a reta com duas alturas deixou o OSC com
# 0,2-0,6 cm e a cadeia com 0,3-0,9 cm de erro nos objetos que não entraram na calibração.
# A caixa (9,4 e 14,5 cm) não é nenhum dos objetos do teste (7, 12 e 19 cm).
piloto.CALIBRAR_CAIXA = True
piloto.ALTURAS_CALIBRACAO_CM = [9.4, 14.5]
# Teste da calibração antes do roteiro (29/10): o pé sobe no objeto de 7 cm; erro acima de
# 1 cm pede novo reset do SlimeVR. Na coleta de 29/10 o erro sem esse teste foi de +2,6 cm.
piloto.VERIFICAR_CALIBRACAO = True
piloto.ALTURA_VERIFICACAO_CM = 7.0
piloto.TOLERANCIA_VERIFICACAO_CM = 1.0
# Teste da calibração e caixa: 5 s para subir e para descer o pé (o padrão do piloto é 3 s)
piloto.TEMPO_TRANSICAO_CALIB_S = 5
piloto.ROTEIRO = piloto.montar_roteiro()

if __name__ == "__main__":
    piloto.main()
