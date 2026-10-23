"""
Cadastro do participante e pastas de saída de cada coleta.

Antes do experimento o calculo_passo.py pede o nome e a altura do participante.
Cada coleta vai para dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>_circuitoX/, e o arquivo
dados/participantes/<Nome>/participante.json guarda o cadastro e a lista de coletas.
"""

import json
import os
import re
import time
import unicodedata

PASTA_PARTICIPANTES = os.path.join("dados", "participantes")
ALTURA_MIN_CM = 100.0
ALTURA_MAX_CM = 230.0


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


def pedir_participante(entrada=input):
    """Pergunta nome e altura no terminal até receber valores válidos."""
    print("-" * 75)
    print("   CADASTRO DO PARTICIPANTE")
    print("-" * 75)
    while True:
        nome = " ".join(entrada("   Nome do participante: ").split())
        if nome:
            break
        print("   O nome não pode ficar vazio.")
    while True:
        altura = ler_altura_cm(entrada("   Altura (cm, por exemplo 172): "))
        if altura is not None:
            break
        print(f"   Altura inválida. Digite um valor entre {ALTURA_MIN_CM:.0f} e {ALTURA_MAX_CM:.0f} cm.")
    print(f"\n   Participante: {nome} | altura {altura:.1f} cm\n")
    return {"nome": nome, "altura_cm": altura}


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


def pedir_altura_teste(padrao=None, entrada=input):
    """Altura (cm) do objeto de teste desta coleta. Enter aceita o valor padrão."""
    while True:
        dica = f"Enter = {padrao:.1f}" if padrao else "por exemplo 3,5"
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
            print(f"   Objeto de teste: {v:.1f} cm\n")
            return round(v, 2)
        print("   A altura precisa estar entre 0,5 e 40 cm.")


def criar_pasta_coleta(participante, base=PASTA_PARTICIPANTES):
    """Cria dados/participantes/<Nome>/<AAAAMMDD_HHMMSS>[_circuitoX]/ e atualiza o participante.json."""
    data_hora = time.strftime("%Y%m%d_%H%M%S")
    if participante.get("circuito"):
        data_hora += f"_circuito{participante['circuito']}"
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
                    "circuito": participante.get("circuito")})
    cadastro["coletas"] = coletas
    with open(cadastro_arq, "w", encoding="utf-8") as f:
        json.dump(cadastro, f, ensure_ascii=False, indent=2)

    info = dict(participante, data_hora=data_hora)
    with open(os.path.join(pasta, "participante.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return pasta
