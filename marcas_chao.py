"""
Percurso com marcas no chão: referência de verdade para o comprimento do passo.

Cada marca tem a distância (cm) desde a primeira e o pé que pisa nela com o
calcanhar. O comprimento real de cada passo é a distância entre duas marcas
seguidas. A primeira marca é a posição de partida (o pé já está nela).

O programa mede o comprimento do passo no heel-strike como a distância
anteroposterior entre os dois pés, que é exatamente essa distância entre marcas.
"""

# (posição em cm, pé que pisa). Todos começam com o pé D na marca 0.
# Depois da última marca a pessoa junta os pés; esse passo de parada não conta.


def montar_circuito(blocos):
    """Monta as marcas a partir de blocos (comprimento cm, número de passos).

    O primeiro passo é do pé E, e os pés alternam.
    """
    marcas, pos, pe = [(0, "DIR")], 0, "ESQ"
    for comp, n in blocos:
        for _ in range(n):
            pos += comp
            marcas.append((pos, pe))
            pe = "DIR" if pe == "ESQ" else "ESQ"
    return marcas


# Cada circuito tem um comprimento de passo fixo e 18 pegadas depois da partida
# (9 de cada pé). Os comprimentos ainda podem mudar: basta editar COMPRIMENTO_FIXO_CM.
PEGADAS_POR_CIRCUITO = 18
COMPRIMENTO_FIXO_CM = {"A": 30, "B": 35, "C": 40}   # provável (24/10); ainda pode mudar
BLOCOS = {c: [(comp, PEGADAS_POR_CIRCUITO)] for c, comp in COMPRIMENTO_FIXO_CM.items()}
CIRCUITOS = {c: montar_circuito(b) for c, b in BLOCOS.items()}
# Percurso usado nos testes de 20/10/2026
CIRCUITOS["20-10"] = [(0, "DIR"), (45, "ESQ"), (95, "DIR"), (150, "ESQ"), (190, "DIR"), (240, "ESQ"),
                      (285, "DIR"), (330, "ESQ"), (380, "DIR"), (410, "ESQ")]


def _descrever(c):
    m = CIRCUITOS[c]
    return f"{COMPRIMENTO_FIXO_CM[c]} cm, {len(m) - 1} passos, {m[-1][0] / 100:.2f} m".replace(".", ",")


DESCRICAO = {"A": "curtos, " + _descrever("A"), "B": "médios, " + _descrever("B"),
             "C": "longos, " + _descrever("C"), "20-10": "percurso de 20/10"}

CIRCUITO_ATIVO = "20-10"
PERCURSO_MARCAS = CIRCUITOS[CIRCUITO_ATIVO]


def definir_circuito(nome):
    """Escolhe o circuito usado na comparação com as marcas."""
    global CIRCUITO_ATIVO, PERCURSO_MARCAS
    CIRCUITO_ATIVO = nome
    PERCURSO_MARCAS = CIRCUITOS[nome]


def passos_esperados(percurso=None):
    """Lista de (pé, comprimento real em m) de cada passo do percurso."""
    percurso = percurso or PERCURSO_MARCAS
    return [(pe, (pos - percurso[i - 1][0]) / 100.0)
            for i, (pos, pe) in enumerate(percurso) if i > 0]


def casar_com_marcas(pes_detectados, comprimentos_m, tempos=None, percurso=None):
    """Liga, em ordem, cada passo detectado a um passo do percurso pelo pé.

    Começa no primeiro passo do pé que sai da partida. Se o pé detectado não
    bate com o esperado, mas bate com o seguinte, considera que um passo não foi
    detectado e pula. Passos que não casam ficam de fora.
    Retorna lista de dicts com pe, real_m, medido_m, erro_m (e t, se houver tempos).
    """
    esperados = passos_esperados(percurso)
    pes = [str(p).replace("*", "") for p in pes_detectados]
    saida, j = [], 0
    try:
        inicio = pes.index(esperados[0][0])
    except ValueError:
        return saida
    for i in range(inicio, len(pes)):
        if j >= len(esperados):
            break
        if pes[i] == esperados[j][0]:
            k = j
        elif j + 1 < len(esperados) and pes[i] == esperados[j + 1][0]:
            k = j + 1  # um passo do percurso não foi detectado
        else:
            continue   # passo repetido do mesmo pé: ignora
        real = esperados[k][1]
        medido = float(abs(comprimentos_m[i]))
        item = {"marca": k + 1, "pe": pes[i], "real_m": real, "medido_m": medido,
                "erro_m": medido - real, "imu": "*" not in str(pes_detectados[i])}
        if tempos is not None:
            item["t"] = float(tempos[i])
        saida.append(item)
        j = k + 1
    return saida


def resumo_texto(casados, percurso=None):
    if not casados:
        return "    nenhum passo casou com as marcas"
    import numpy as np
    e = np.array([c["erro_m"] for c in casados]) * 100
    r = np.array([c["medido_m"] / c["real_m"] for c in casados])
    linhas = [f"    {len(casados)}/{len(passos_esperados(percurso))} passos | erro médio {e.mean():+.1f} cm "
              f"(desvio {e.std():.1f}) | erro absoluto médio {np.abs(e).mean():.1f} cm | medido/real {np.median(r):.2f}"]
    linhas.append("    " + "  ".join(f"{c['pe'][0]}{c['real_m']*100:.0f}→{c['medido_m']*100:.0f}" for c in casados))
    return "\n".join(linhas)
