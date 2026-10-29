"""
Experimento de altura: só o pé sobre objetos de altura conhecida, sem caminhada.

Usa os mesmos cálculos e o mesmo cadastro do piloto.py:
  1. nome e altura do participante, rodada (1 = familiarização, 2 = coleta) e as
     alturas dos três apoios do cone (padrão 7, 12 e 19 cm; Enter aceita);
  2. sem calibração da caixa (CALIBRAR_CAIXA = False): o OSC bruto acertou o cone nos
     Testes 4 e 5. Com True, calibra com a caixa em 9,4 e 14,5 cm (reta pelo zero) e o
     relatório mostra também os valores sem calibração;
  2b. conferência: cada pé sobe no objeto de 7 cm; se errar mais de 1 cm, o programa pede
     para refazer o reset do SlimeVR e repetir a calibração (Enter) ou seguir assim (c);
  3. em cada objeto, os dois pés sobem alternados (E, depois D), REPETICOES_ALTURA vezes
     cada (tronco reto); o pé que fica no chão é a referência. Só o pé E: PES_ALTURA = ["ESQ"].
     Na rodada de coleta a ordem dos objetos é sorteada.
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
# Sem calibração da caixa: nos Testes 4 e 5 de 26/10 o OSC bruto acertou os apoios do cone
# (7, 12 e 19 cm, medidos com régua) com erro de ~0,2 cm, e a caixa piorava para ~1,3 cm.
# Para voltar a calibrar, troque para True (caixa em 9,4 e 14,5 cm, medidas com régua);
# o relatório então mostra também os valores sem calibração (_bruto).
piloto.CALIBRAR_CAIXA = False
piloto.ALTURAS_CALIBRACAO_CM = [9.4, 14.5]
# Conferência antes do roteiro (29/10): cada pé no objeto de 7 cm; erro > 1 cm pede novo reset
piloto.VERIFICAR_CALIBRACAO = True
piloto.TOLERANCIA_VERIFICACAO_CM = 1.0
piloto.ROTEIRO = piloto.montar_roteiro()

if __name__ == "__main__":
    piloto.main()
