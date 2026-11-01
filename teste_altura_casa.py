"""
TEMPORÁRIO — teste de altura em casa, sem as caixas do laboratório.

Serve para comparar a altura do pé com as proporções AUTOMÁTICAS do SlimeVR e com as
proporções MEDIDAS (coxa e canela com fita). Usa os mesmos cálculos do experimento de altura,
com estas diferenças:
  1. objetos de casa (livros, degrau, caixa...), de 1 a 3, com a altura medida com régua;
  2. no lugar da rodada, o programa pergunta quais proporções estão no SlimeVR
     (1 = automáticas, 2 = medidas). As proporções lidas do SlimeVR ficam salvas no
     participante.json da coleta;
  3. o teste da calibração usa o objeto mais baixo; erro acima de 1 cm pede novo reset.
Saída em dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_altura_casa_auto/ (ou _casa_medidas).

Sugestão: sem mexer nos trackers, rode uma vez com as proporções automáticas e outra
com as medidas, e compare os relatórios.

    uv run python teste_altura_casa.py
"""
import participante as cadastro
import piloto

piloto.EXPERIMENTO = "altura"
piloto.PES_ALTURA = ["ESQ", "DIR"]
piloto.FAIXA_ALTURAS_TESTE_CM = (2.0, 30.0)
piloto.CALIBRAR_CAIXA = False
piloto.SORTEAR_ORDEM_OBJETOS = False
piloto.VERIFICAR_CALIBRACAO = True
piloto.ALTURA_VERIFICACAO_CM = None       # usa o objeto mais baixo
piloto.TOLERANCIA_VERIFICACAO_CM = 1.0
piloto.TEMPO_TRANSICAO_CALIB_S = 5

PROPORCOES = {"1": "casa_auto", "2": "casa_medidas"}
_ultima_coleta_original = cadastro.ultima_coleta
n_objetos = 3


def pedir_proporcoes_e_objetos(entrada=input):
    """Substitui a pergunta da rodada: proporções do SlimeVR e quantos objetos."""
    global n_objetos
    while True:
        r = entrada("   Proporções no SlimeVR (1 = automáticas, 2 = medidas com fita): ").strip()
        if r in PROPORCOES:
            break
        print("   Digite 1 ou 2.")
    while True:
        n = entrada("   Quantos objetos (1 a 3, Enter = 3): ").strip() or "3"
        if n in ("1", "2", "3"):
            n_objetos = int(n)
            break
        print("   Digite 1, 2 ou 3.")
    piloto.ALTURAS_TESTE_PADRAO_CM = [None] * n_objetos
    piloto.ORDEM_OBJETOS = list(range(1, n_objetos + 1))
    print(f"   {n_objetos} objeto(s); meça cada um com régua.\n")
    return PROPORCOES[r]


def ultima_coleta_casa(nome, *args, **kwargs):
    """Enter repete as alturas só se a última coleta foi em casa, com o mesmo número de objetos."""
    c = _ultima_coleta_original(nome, *args, **kwargs)
    if c and str(c.get("rodada", "")).startswith("casa") and len(c.get("alturas_teste_cm") or []) == n_objetos:
        return c
    return None


cadastro.pedir_rodada = pedir_proporcoes_e_objetos
cadastro.ultima_coleta = ultima_coleta_casa
piloto.ROTEIRO = piloto.montar_roteiro()

if __name__ == "__main__":
    piloto.main()
