"""
Experimento de altura: só o pé sobre objetos de altura conhecida, sem caminhada.

Usa os mesmos cálculos e o mesmo cadastro do piloto.py:
  1. nome e altura do participante, rodada (1 = familiarização, 2 = coleta) e as
     alturas dos três apoios do cone (padrão 7, 12 e 19 cm; Enter aceita);
  2. calibração com a caixa em 9,4, 14,5 e 21,4 cm (CALIBRAR_CAIXA = True, 04/11; 21,4 = caixa
     de 9,4 com a de 12 em cima): cada pé sobe nas três alturas e o programa ajusta, por pé,
     a reta mais próxima dos três pontos, com desvio e ganho
     (real = a + b x medido) para o OSC e outra para a cadeia das pernas. A tela, a
     conferência e o relatório mostram as duas alturas já corrigidas (OSC e cadeia);
  3. em cada objeto, os dois pés sobem alternados (E, depois D), REPETICOES_ALTURA vezes
     cada (tronco reto); o pé que fica no chão é a referência. Só o pé E: PES_ALTURA = ["ESQ"].
     Na familiarização cada pé sobe uma vez só em cada objeto (12/11); na rodada de coleta a ordem
     dos objetos é sorteada;
  4. tudo com Enter (COM_ENTER): o pé sobe, fica parado e o Enter mede 3 s; sem tempos de
     deslocamento, subida e descida;
  5. sem teste da calibração no objeto de 7 cm antes do roteiro (VERIFICAR_CALIBRACAO = False, 11/11).
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
# A caixa não é nenhum dos objetos do teste (7, 12 e 19 cm).
# 04/11 tarde: com a caixa só até 14,5 cm, a reta extrapolava no objeto de 19 cm (pé D −1,1 cm,
# ganho 0,82). Terceira altura: a caixa de 9,4 com a de 12 em cima (21,4 cm; medir a pilha com
# fita e trocar aqui se der diferente), para os 19 cm ficarem dentro da faixa calibrada.
piloto.CALIBRAR_CAIXA = True
piloto.ALTURAS_CALIBRACAO_CM = [9.4, 14.5, 21.4]
piloto.INSTRUCOES_CAIXA = {2: "Monte a PILHA: caixa de 9,4 cm e caixa de 12 cm uma em cima da outra (21,4 cm). "
                              "Encoste na parede, firme, com espaço para o pé inteiro."}
piloto.TEMPO_MONTAR_CAIXA_S = {2: 20}
# Teste da calibração antes do roteiro (29/10): o pé sobe no objeto de 7 cm; erro acima de
# 1 cm pede novo reset do SlimeVR. Na coleta de 29/10 o erro sem esse teste foi de +2,6 cm.
piloto.VERIFICAR_CALIBRACAO = False   # 11/11: desligado a pedido (sem conferência no objeto de 7 cm)
piloto.ALTURA_VERIFICACAO_CM = 7.0
piloto.TOLERANCIA_VERIFICACAO_CM = 1.0
# Inclinação máxima do tronco com o pé no objeto na conferência. 04/11: ao subir o pé o tronco
# vai naturalmente 8-12° para a frente (com a altura certa); para o lado, 5-7° ainda acertou.
piloto.LIMITE_TRONCO_FRENTE_GRAUS = 12.0
piloto.LIMITE_TRONCO_LADO_GRAUS = 8.0
# Teste da calibração e caixa: 5 s para subir e para descer o pé (o padrão do piloto é 3 s)
piloto.TEMPO_TRANSICAO_CALIB_S = 5
# Com Enter (11/11): sem tempos de ir, subir e descer; cada medida (3 s) começa no Enter,
# no roteiro e na calibração da caixa.
piloto.COM_ENTER = True
piloto.ROTEIRO = piloto.montar_roteiro()

if __name__ == "__main__":
    piloto.main()
