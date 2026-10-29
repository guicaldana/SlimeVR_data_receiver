"""
Cadastro do participante e pastas de saída de cada coleta.

Antes do experimento o calculo_passo.py pede o nome e a altura do participante.
Cada coleta vai para dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_circuitoX/, e o arquivo
dados/participantes/<Nome>/participante.json guarda o cadastro e a lista de coletas.
"""

import json
import os
import re
import subprocess
import time
import unicodedata

PASTA_PARTICIPANTES = os.path.join("dados", "participantes")
ALTURA_MIN_CM = 100.0
ALTURA_MAX_CM = 230.0
# Diferença (cm) entre a altura digitada e a configurada no SlimeVR que gera aviso
DIVERGENCIA_ALTURA_SLIMEVR_CM = 3.0
CLIENTE_SOLARXR = os.path.join("slimevr_client_raw", "src", "cli.mjs")
# O SlimeVR guarda a altura dos olhos (soma das proporções do pescoço às pernas); a altura total
# mostrada na tela dele é essa altura dividida por 0,936 (28/10: 157,25 cm -> 168,0 cm)
RAZAO_OLHOS_ALTURA_SLIMEVR = 0.936


def nome_para_pasta(nome):
    """'João da Silva' -> 'Joao_da_Silva' (sem acentos nem caracteres proibidos no Windows)."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    limpo = re.sub(r"[^A-Za-z0-9]+", "_", sem_acento).strip("_")
    return limpo or "participante"


def ler_altura_cm(texto):
    """Aceita '175', '175 cm', '1,75' ou '1.75 m'. Devolve centímetros ou None se inválida."""
    t = texto.strip().lower().replace("cm", "").replace("m", "").replace(",", ".").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    if v < 3.0:          # veio em metros
        v *= 100.0
    if not (ALTURA_MIN_CM <= v <= ALTURA_MAX_CM):
        return None
    return round(v, 1)


def ler_esqueleto_slimevr(tempo_max_s=15.0, executar=subprocess.run):
    """Altura e proporções do corpo configuradas no SlimeVR Server (via SolarXR), ou None.

    Devolve {"altura_cm": ..., "altura_olhos_cm": ..., "proporcoes_cm": {"UPPER_LEG": ..., ...}}. O SlimeVR não mede a
    altura: sem óculos de VR, ela é a que foi informada nas configurações dele.
    """
    if not os.path.exists(CLIENTE_SOLARXR):
        return None
    try:
        r = executar(["node", CLIENTE_SOLARXR, "esqueleto"], capture_output=True, text=True,
                     timeout=tempo_max_s)
        linha = [l for l in r.stdout.splitlines() if l.strip().startswith("{")]
        if r.returncode != 0 or not linha:
            return None
        dados = json.loads(linha[-1])
    except Exception:
        return None
    olhos = float(dados.get("userHeight") or 0.0) * 100.0
    altura = olhos / RAZAO_OLHOS_ALTURA_SLIMEVR
    partes = {k: round(float(v) * 100.0, 1) for k, v in (dados.get("parts") or {}).items()}
    if not (ALTURA_MIN_CM <= altura <= ALTURA_MAX_CM):
        return None
    return {"altura_cm": round(altura, 1), "altura_olhos_cm": round(olhos, 1), "proporcoes_cm": partes}


def _mostrar_esqueleto(esq):
    p = esq["proporcoes_cm"]
    txt = ", ".join(f"{rot} {p[k]:.1f}" for k, rot in (("UPPER_LEG", "coxa"), ("LOWER_LEG", "canela"),
                                                      ("HIP", "quadril"), ("WAIST", "cintura"),
                                                      ("CHEST", "peito"), ("NECK", "pescoço")) if k in p)
    print(f"   SlimeVR: altura configurada {esq['altura_cm']:.1f} cm" + (f" | {txt} cm" if txt else ""))


def pedir_participante(entrada=input, esqueleto=None):
    """Pergunta nome e altura no terminal até receber valores válidos.

    Com esqueleto (de ler_esqueleto_slimevr), Enter aceita a altura configurada no SlimeVR e o
    programa avisa se a altura digitada for diferente dela.
    """
    print("-" * 75)
    print("   CADASTRO DO PARTICIPANTE")
    print("-" * 75)
    while True:
        nome = " ".join(entrada("   Nome do participante: ").split())
        if nome:
            break
        print("   O nome não pode ficar vazio.")
    padrao = esqueleto["altura_cm"] if esqueleto else None
    if esqueleto:
        _mostrar_esqueleto(esqueleto)
    while True:
        dica = f"Enter = {padrao:.1f} do SlimeVR" if padrao else "cm, por exemplo 172"
        texto = entrada(f"   Altura ({dica}): ")
        if padrao and not texto.strip():
            altura, fonte = padrao, "SlimeVR"
            break
        altura, fonte = ler_altura_cm(texto), "digitada"
        if altura is not None:
            break
        print(f"   Altura inválida. Digite um valor entre {ALTURA_MIN_CM:.0f} e {ALTURA_MAX_CM:.0f} cm.")
    if padrao and abs(altura - padrao) > DIVERGENCIA_ALTURA_SLIMEVR_CM:
        print(f"   [Aviso] A altura do SlimeVR ({padrao:.1f} cm) é diferente de {altura:.1f} cm.")
        print("           Ajuste a altura e as proporções no SlimeVR e refaça o reset antes de coletar.")
    print(f"\n   Participante: {nome} | altura {altura:.1f} cm ({fonte})\n")
    cad = {"nome": nome, "altura_cm": altura, "altura_fonte": fonte}
    if esqueleto:
        cad["slimevr"] = esqueleto
    return cad


def pedir_circuito(circuitos, descricao, entrada=input):
    """Pergunta qual circuito de marcas será usado (A, B ou C)."""
    opcoes = [c for c in circuitos if len(c) == 1]
    for c in opcoes:
        print(f"   Circuito {c}: {descricao.get(c, '')}")
    while True:
        c = entrada(f"   Circuito ({'/'.join(opcoes)}): ").strip().upper()
        if c in opcoes:
            print()
            return c
        print(f"   Digite {', '.join(opcoes[:-1])} ou {opcoes[-1]}.")


def pedir_altura_teste(padrao=None, entrada=input, faixa=None):
    """Altura (cm) do objeto de teste desta coleta. Enter aceita o valor padrão.

    faixa=(mín, máx) só avisa quando o valor fica fora da faixa prevista e pede confirmação.
    """
    while True:
        dica = (f"Enter = {padrao:.1f}" if padrao else
                f"entre {faixa[0]:g} e {faixa[1]:g} cm" if faixa else "por exemplo 3,5")
        txt = entrada(f"   Altura do objeto de teste em cm ({dica}): ").strip().replace(",", ".")
        if not txt and padrao:
            v = float(padrao)
        else:
            try:
                v = float(txt)
            except ValueError:
                print("   Digite um número em cm.")
                continue
        if 0.5 <= v <= 40:
            if faixa and not (faixa[0] <= v <= faixa[1]):
                ok = entrada(f"   {v:.1f} cm está fora da faixa prevista ({faixa[0]:g} a {faixa[1]:g} cm). Usar assim? (s/n): ")
                if ok.strip().lower() not in ("s", "sim"):
                    continue
            print(f"   Objeto de teste: {v:.1f} cm\n")
            return round(v, 2)
        print("   A altura precisa estar entre 0,5 e 40 cm.")


RODADAS = {"1": "familiarizacao", "2": "coleta"}
NOME_RODADA = {"familiarizacao": "familiarização", "coleta": "coleta"}


def pedir_rodada(entrada=input):
    """Pergunta se esta coleta é a rodada de familiarização ou a rodada de coleta."""
    while True:
        r = entrada("   Rodada (1 = familiarização, 2 = coleta): ").strip()
        if r in RODADAS:
            print(f"   Rodada: {NOME_RODADA[RODADAS[r]]}\n")
            return RODADAS[r]
        print("   Digite 1 ou 2.")


def ultima_coleta(nome, base=PASTA_PARTICIPANTES):
    """Última coleta registrada no participante.json desse participante (ou None)."""
    arq = os.path.join(base, nome_para_pasta(nome), "participante.json")
    try:
        with open(arq, encoding="utf-8") as f:
            coletas = json.load(f).get("coletas", [])
        return coletas[-1] if coletas else None
    except Exception:
        return None


def criar_pasta_coleta(participante, base=PASTA_PARTICIPANTES):
    """Cria dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>[_circuitoX]/ e atualiza o participante.json."""
    data_hora = time.strftime("%Y%m%d_%H%M%S")
    if participante.get("circuito"):
        data_hora += f"_circuito{participante['circuito']}"
    if participante.get("experimento") not in (None, "", "completo"):   # "altura", "livre", ...
        data_hora += f"_{participante['experimento']}"
    if participante.get("rodada"):
        data_hora += f"_{participante['rodada']}"
    pasta_participante = os.path.join(base, nome_para_pasta(participante["nome"]))
    pasta = os.path.join(pasta_participante, data_hora)
    os.makedirs(pasta, exist_ok=True)

    cadastro_arq = os.path.join(pasta_participante, "participante.json")
    cadastro = {}
    if os.path.exists(cadastro_arq):
        try:
            with open(cadastro_arq, encoding="utf-8") as f:
                cadastro = json.load(f)
        except Exception:
            cadastro = {}
    cadastro["nome"] = participante["nome"]
    cadastro["altura_cm"] = participante["altura_cm"]
    coletas = cadastro.get("coletas", [])
    coletas.append({"pasta": data_hora, "altura_cm": participante["altura_cm"],
                    "circuito": participante.get("circuito"), "rodada": participante.get("rodada"),
                    "experimento": participante.get("experimento"),
                    "alturas_teste_cm": participante.get("alturas_teste_cm"),
                    "ordem_circuitos": participante.get("ordem_circuitos")})
    cadastro["coletas"] = coletas
    with open(cadastro_arq, "w", encoding="utf-8") as f:
        json.dump(cadastro, f, ensure_ascii=False, indent=2)

    info = dict(participante, data_hora=data_hora)
    with open(os.path.join(pasta, "participante.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return pasta
