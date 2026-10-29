"""
Experimento de comprimento de passada: só os três circuitos de marcas, sem objetos.

Usa os mesmos cálculos e o mesmo cadastro do piloto.py:
  1. nome e altura do participante e rodada (1 = familiarização, 2 = coleta);
  2. calibração estática, sem caixa (CALIBRAR_CAIXA = False): basta a calibração do SlimeVR;
  3. uma caminhada em cada circuito A, B e C (30, 40 e 50 cm entre os calcanhares,
     ver marcas_chao.py), com o tronco reto. Na rodada de coleta a ordem é sorteada.
A altura nos objetos é um experimento separado (experimento_altura.py).
Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_comprimento_<rodada>/.

    uv run python experimento_comprimento.py
"""
import piloto

piloto.EXPERIMENTO = "comprimento"
piloto.CALIBRAR_CAIXA = False
piloto.ROTEIRO = piloto.montar_roteiro()

if __name__ == "__main__":
    piloto.main()
