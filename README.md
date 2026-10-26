# SlimeVR Data Receiver (Estimador de Comprimento de Passo)

Este projeto é um servidor local em Python projetado para receber dados via OSC (Open Sound Control) diretamente do **SlimeVR Server**. O objetivo principal é analisar a biometria do usuário em tempo real, especificamente para estimar o **comprimento do passo** durante a caminhada.

## 🚀 Instalação (Windows)

### 1. Clonar o repositório
Abra o **PowerShell** ou **Prompt de Comando** e rode:
```powershell
git clone https://github.com/guicaldana/SlimeVR_data_receiver.git
cd SlimeVR_data_receiver
```

### 2. Criar o ambiente virtual e instalar as dependências
```powershell
uv venv
uv pip install python-osc numpy matplotlib
```

> [!NOTE]
> Não é necessário ativar o ambiente virtual manualmente. Use `uv run` antes de cada comando para rodar os scripts automaticamente dentro do venv (ex: `uv run python slimevr.py`).

## ⚙️ Configurando o SlimeVR Server
1. Abra o painel do **SlimeVR Server**.
2. Vá até as configurações de **OSC** (normalmente ligado à integração do VRChat).
3. Ative o envio de dados OSC para o endereço `127.0.0.1` na porta `9000`.
4. Certifique-se de que o envio de dados de **Posição (Position)** dos trackers está ativado.

## 📁 Estrutura de Pastas do Repositório

```text
SlimeVR_data_receiver/
├── calculo_passo.py               # Principal: Marcha unificada com Heel-Strike da IMU (SolarXR), OSC e VMC
├── plotar_todos_dados.py          # Visualizador completo de todos os canais de dados simultâneos
├── avaliar_altura_pe.py           # Avaliador de Altura do Pé e Estudo de Referência no Quadril (Hip)
├── monitor_passadas.py            # Monitor em tempo real de passadas sincronizadas
├── slimevr.py                     # Modo descoberta básico de trackers
├── analises/                      # Scripts de pós-processamento, altura e comparações
├── dados/                         # Arquivos CSV gravados
├── archives/                      # Arquivos compactados e legados
└── slimevr_client_raw/            # Bridge Node.js do protocolo SolarXR WebSocket
```

## 📂 Scripts Principais (Raiz)

### `plotar_todos_dados.py` — Gravador e Visualizador de Todos os Dados
Captura simultaneamente todas as fontes disponíveis e plota uma visão geral completa em 5 painéis:
1. **Posições Verticais Y:** Pés (2, 3), Quadril (1) e Peito (6).
2. **Separação Horizontal dos Pés:** Distância no plano $X$-$Z$.
3. **Magnitude de Aceleração das IMUs:** Impactos físicos dos pés (SolarXR).
4. **Componentes Direcionais da Aceleração:** $A_y$ vertical vs $A_z$ anterior de ambos os pés.
5. **Inclinações Angulares (Pitch):** Peito, Quadril e Cabeça.

**Como rodar:**
```powershell
uv run python plotar_todos_dados.py
```

---

### `calculo_passo.py` — Sistema Unificado de Marcha com Heel-Strike SolarXR (Principal)
Combina a precisão inercial das **IMUs físicas (Porta 21110)** para identificar o momento exato do impacto do calcanhar (**Heel-Strike**) com os dados cinemáticos do **OSC (Porta 9000)** e da **Cabeça/Olhar via VMC (Porta 39539)**.
- **Timing Preciso via IMU:** Identifica o instante exato em que cada pé colide com o chão através dos picos de aceleração linear das IMUs dos pés.
- **Comprimento do Passo:** máximo da distância anteroposterior entre os pés em cada platô (do momento em que um pé passa à frente até o outro passar). O contato do calcanhar só marca o tempo do passo; duas detecções no mesmo platô viram um passo só, e um platô sem detecção vira um passo marcado com `*`. Nas sessões de 20/10 com marcas no chão o erro foi de 3 a 7 cm por passo (antes, pelo instante do contato, 5 a 15 cm). `COMPRIMENTO_PELO_PLATO = False` volta ao método antigo e `PLATO_PERCENTIL = 80` usa o percentil 80 em vez do máximo.
- **Clearance / Altura do Passo:** Mede a elevação vertical pelo pé de apoio dinâmico ($\min(Y_e, Y_d)$).
- **Postura do Tronco:** inclinação do peito (Pitch).
- **Cadastro do participante:** antes de tudo, o terminal pede o nome e a altura (cm) do participante. Cada coleta vai para `dados/participantes/<Nome>/AAAAMMDD_HHMMSS/`, e `dados/participantes/<Nome>/participante.json` guarda o cadastro e a lista de coletas.
- **Relatório e Gráficos:** as estatísticas da marcha usam só os passos das caminhadas do roteiro.

**Arquivos de cada coleta** (em `dados/participantes/<Nome>/AAAAMMDD_HHMMSS/`):
- `comprimento_passo.png`: comprimento de cada passo nas caminhadas reta e inclinada, por pé, com a marca do chão quando o passo casou com ela;
- `altura_passo.png`: altura dos pés ao longo de cada caminhada e a altura máxima de cada passo;
- `painel_completo.png`: projeção anteroposterior, aceleração das IMUs, altura dos pés, quadril, tronco e barras por passo;
- `resultado_completo_marcha.csv`: um passo por linha, com participante, altura, etapa do roteiro, comprimento (e comprimento/altura), comprimento real pelas marcas, altura do passo e tronco;
- `dados_brutos_sessao.csv`: altura de cada pé ao longo do tempo (OSC, cadeia, pipeline) e o tronco;
- `dados_brutos_entrada.json` e `fases_sessao.csv`: entradas brutas e etapas, para reprocessar com o `analisar_sessoes.py`;
- `relatorio.txt`: o relatório que aparece no terminal;
- `participante.json`: nome, altura e data da coleta.

Para pular o cadastro (testes), mude `PEDIR_PARTICIPANTE = False`: os arquivos voltam para `dados/`. Com `MOSTRAR_GRAFICOS = False` os gráficos só são salvos, sem abrir janelas.

**Como rodar:**
```powershell
uv run python calculo_passo.py
```

---

### `avaliar_altura_pe.py` — Avaliador de Altura do Pé (Estudo da Referência no Quadril)
Script dedicado para testar a altura e elevação dos pés em situações livres (estático, sentado com pé sobre caixa de 6 cm, agachamentos, elevações livres). Compara simultaneamente **3 referências de altura**:
1. **Chão Estático Inicial (Tara 2.0s):** $H = Y(t) - Y_{\text{solo\_inicial}}$
2. **Diferencial Pé-com-Pé:** Diferença entre o pé elevado e o pé de apoio.
3. **Referência no Quadril (Hip / Tracker 1):** Mede o encurtamento vertical da distância Pé $\to$ Quadril, desacoplando o movimento das inclinações superiores de tronco e cabeça.

**Como rodar:**
```powershell
uv run python avaliar_altura_pe.py
```

---

### `zupt_pe.py` e `experimento_inclinacao.py` — Altura do pé imune à inclinação do tronco
**Problema:** as posições do OSC não são medidas direto nos pés. O SlimeVR monta o esqueleto a partir da cabeça e desce até os pés, então inclinar o tronco, a pelve ou as coxas muda a altura dos pés no OSC. Em `dados/teste_altura_referencias.csv`, com os dois pés no chão, os pés "sobem" 5 cm com o tronco a 24°, 18 cm a 51° e 47 cm a 67°.

**Solução (`zupt_pe.py`):** calcula a trajetória de cada pé só com a IMU do próprio pé (SolarXR), integrando a aceleração entre dois momentos em que o pé está parado (ZUPT). Nenhum outro segmento entra na conta. Para cada movimento do pé, o script mostra a altura máxima, o desnível (0 em chão plano, ±6,7 cm na caixa) e o comprimento da passada.

A aceleração que o SolarXR entrega vem num referencial girado em relação à orientação do pé, e sem correção a velocidade não volta a zero no fim da passada. Por isso há uma **calibração de montagem**: numa caminhada reta curta, o script estima uma rotação fixa para cada pé. A rotação escolhida é a que faz a velocidade voltar a zero, termina cada passada na altura do chão e mantém o pé num plano vertical.

```powershell
uv pip install scipy
# Calibra a montagem com uma caminhada reta e salva em dados/zupt_montagem.json
uv run python zupt_pe.py slimevr_client_raw/captures/CAMINHADA/samples.csv --calibrar-montagem
# Analisa outra captura usando a calibração salva
uv run python zupt_pe.py slimevr_client_raw/captures/SESSAO/samples.csv --csv dados/zupt_passadas.csv --grafico
```

**Experimento (`experimento_inclinacao.py`):** grava OSC e SolarXR ao mesmo tempo e guia o roteiro pelo terminal. Antes de rodar, abra o SlimeVR Server com o OSC ligado (porta 9000) e deixe a caixa de 6,7 cm ao lado dos pés. Também deixe uns 5 m livres para caminhar em linha reta.
1. Parado, tronco reto (5 s).
2. Caminhada reta, que serve para calibrar a montagem (12 s).
3. Pausa para ir até a caixa (15 s).
4. Para o tronco a 0°, 30° e 60°: incline e fique parado (5 s). Depois, mantendo a inclinação, coloque o pé esquerdo na caixa (5 s), volte ao chão (4 s), coloque o pé direito na caixa (5 s), volte ao chão (4 s), e repita.
5. Pausa para voltar ao início da caminhada (15 s).
6. Caminhada reta com o tronco inclinado (12 s).

A duração das fases fica no topo de `experimento_inclinacao.py` (`T_DESLOCAMENTO_S`, `T_CAMINHADA_S` etc.).

```powershell
uv run python experimento_inclinacao.py
# Para analisar de novo uma gravação já feita:
uv run python experimento_inclinacao.py --analisar dados/experimentos/inclinacao_AAAAMMDD_HHMMSS
```

No fim, o script mostra uma tabela por inclinação e por pé. Ela traz quanto os pés "sobem" no OSC só por inclinar o tronco, a altura da caixa pelo OSC (método atual, diferença entre os pés) e a altura da caixa pelo ZUPT ao subir e ao descer. Um método imune à inclinação dá ~6,7 cm em todas as linhas. Os arquivos ficam em `dados/experimentos/inclinacao_*/` (`resultado.csv`, `zupt_passadas.csv`, `osc.csv`, `fases.csv` e `solarxr/`).

> [!IMPORTANT]
> Confira a linha "Amostras novas SolarXR". O ZUPT precisa de pelo menos ~80 amostras novas por segundo por pé. A captura `sessao1` teve 85 Hz, mas as sessões de 09/10 tiveram de 25 a 44 Hz, e nessa taxa os picos de aceleração se perdem.

---

### `piloto.py` — Coleta do piloto (três circuitos e três alturas conhecidas numa coleta só)
Usa os mesmos cálculos do `calculo_passo.py`, com o protocolo do piloto (~4 min de roteiro por rodada, além da calibração e da preparação):
1. Nome e altura do participante, a rodada (1 = familiarização, 2 = coleta) e as alturas dos três objetos de teste (as três são digitadas; serão entre 15 e 25 cm, valores ainda a definir; fora dessa faixa o programa pede confirmação).
2. Calibração com a caixa de 7 cm.
3. Uma caminhada com o tronco reto em cada circuito A, B e C (uma cor de fita para cada). Na rodada de coleta, a ordem dos circuitos é sorteada para cada participante (`SORTEAR_ORDEM_CIRCUITOS`); na familiarização, a ordem é fixa (A, B, C), aparece no terminal e fica salva em `participante.json` e `dados_brutos_entrada.json` (`ordem_circuitos`). Tempo de caminhada: 25 s em cada circuito (`TEMPO_CAMINHADA_S`).
4. Pé E e pé D sobre cada objeto com o tronco reto.

Cada participante faz duas rodadas, rodando o programa duas vezes: primeiro a familiarização (1) e depois a rodada de coleta (2). As pastas terminam em `_familiarizacao` e `_coleta`. Na rodada de coleta, Enter repete as alturas dos objetos digitadas na familiarização. O `analisar_sessoes.py` deixa a familiarização fora do resumo, a não ser que a pasta seja passada direto.

Além dos arquivos do `calculo_passo.py`, a pasta da coleta traz `altura_conhecida.csv` (altura medida e erro em cada objeto). Os gráficos e o relatório mostram cada caminhada separada (por circuito), porque os circuitos têm comprimentos diferentes.

```powershell
uv run python piloto.py
```

### `experimento_altura.py` — Experimento só de altura (sem caminhada)
Mesmo cadastro e rodadas (1 = familiarização, 2 = coleta) do `piloto.py`. A calibração usa a caixa em duas alturas (9 e 14,6 cm, `ALTURAS_CALIBRACAO_CM`), diferentes das alturas do teste: cada pé e cada método (OSC e cadeia das pernas) ganha uma curva de correção que passa pelo chão e pelos dois pontos medidos, porque o erro cresce com a altura (na coleta de 26/10, com só a caixa de 7 cm, o OSC mediu 6,1, 9,0 e 14,9 cm nos apoios de 7, 12 e 19 cm). Acima de 14,6 cm a curva segue a inclinação do último trecho. O relatório compara OSC e cadeia, cada um com a sua calibração; mas o roteiro tem só os objetos de teste (apoios do cone de 7, 12 e 19 cm, padrão no terminal): em cada objeto, só o pé E sobe (o pé D fica no chão como referência), `REPETICOES_ALTURA` vezes (padrão 3), com o tronco reto. Para usar os dois pés, troque `PES_ALTURA` em `experimento_altura.py`. Na rodada de coleta a ordem dos objetos é sorteada. São ~2,7 min de roteiro.

A pasta termina em `_altura_familiarizacao` ou `_altura_coleta` e traz `altura_conhecida.csv` (cada subida: objeto, repetição, pé, altura medida e erro), `altura_objetos.png` (medido contra real e erro) e `relatorio.txt` (com a média ± desvio por objeto e pé).

```powershell
uv run python experimento_altura.py
```

### `marcas_chao.py` e `analisar_sessoes.py` — Comprimento do passo contra marcas no chão e resumo de várias sessões

O comprimento do passo é conferido com um percurso de marcas no chão (`CIRCUITOS` em `marcas_chao.py`: posição em cm e pé que pisa com o calcanhar). O `calculo_passo.py` usa o percurso de 20/10. O `piloto.py` usa os três numa coleta só:

| Circuito | Comprimento do passo | Pegadas | Total |
|---|---|---|---|
| A, curtos | 30 cm | 18 | 5,40 m |
| B, médios | 35 cm | 18 | 6,30 m |
| C, longos | 40 cm | 18 | 7,20 m |

Cada circuito tem um comprimento de passo fixo e 18 pegadas depois da partida (9 de cada pé). Os comprimentos (provavelmente 30, 35 e 40 cm) ficam em `COMPRIMENTO_FIXO_CM`, em `marcas_chao.py`. Em todos, o pé D começa na marca 0 e o primeiro passo é do pé E; depois da última marca a pessoa junta os pés (esse passo de parada não conta). As coletas antigas, sem circuito salvo, usam o percurso de 20/10. Nas caminhadas do roteiro a pessoa parte com o pé direito na marca 0 e pisa com o calcanhar em cada marca. No fim da coleta, `calculo_passo.py` liga cada passo detectado à marca correspondente (pela ordem e pelo pé) e mostra o erro de cada passo.

Cada coleta fica em `dados/participantes/<Nome>/AAAAMMDD_HHMMSS/` (as coletas antigas estão em `dados/sessoes/`). Depois de algumas sessões:

```powershell
uv run python analisar_sessoes.py            # todas as pastas em dados/participantes/ e dados/sessoes/
uv run python analisar_sessoes.py dados/participantes/Joao_Silva/20261023_1000 dados/participantes/Joao_Silva/20261023_1030
```

O script recalcula tudo a partir dos dados brutos salvos e grava em `dados/analise_sessoes/`:
- `caixa.csv`: altura medida na caixa (6,7 cm) por OSC, cadeia e pipeline, com o tronco reto e inclinado;
- `inclinar.csv`: quanto a diferença entre os pés muda só por inclinar o tronco parado (ideal 0);
- `passos.csv`: cada passo, com o comprimento medido, o comprimento real pelas marcas e a altura por cada método;
- `resumo.txt` e `analise_sessoes.png`: erros médios, medido/real, Bland-Altman cadeia − OSC e o efeito da inclinação no comprimento.

### `cadeia_pernas.py` — Altura do pé pela cadeia de transformações das pernas
Reconstrói as pernas a partir da calibração parada: para cada coxa e canela, a rotação em relação à calibração é `R(t) = q(t) · q_calib⁻¹` (orientações do SolarXR), aplicada a um segmento reto de comprimento `L`. A altura de um pé em relação ao outro sai da cadeia pé → canela → coxa → quadril → coxa → canela → pé. O tronco, a cabeça e o tracker do quadril não entram (a pelve é considerada nivelada), então a inclinação do tronco não afeta a medida. Os comprimentos vêm das posições OSC na calibração.

No `calculo_passo.py`, a opção `MODO_ALTURA` escolhe a medida de altura:
- `"osc"`: diferença entre os pés no OSC (método anterior);
- `"cadeia"`: cadeia das pernas;
- `"mescla"`: média simples das duas;
- `"pipeline"` (padrão): troca pela inclinação do tronco (tracker do peito). Com o tronco até `TRONCO_RETO_GRAUS` (10°) usa o OSC; a partir de `TRONCO_INCLINADO_GRAUS` (30°) usa a cadeia; entre os dois, passa de um para o outro aos poucos. Cada método tem o seu próprio ganho, aprendido na calibração da caixa (`ganhos_metodo`). A opção `CADEIA_ESCALA_NA_MARCHA` (desligada desde 20/10) troca o ganho da cadeia pelo que a faz medir a mesma altura de passo que o OSC na `caminhada_reta`. Ela foi desligada porque, na sessão de 20/10, o ganho da caixa acertou a caixa com o tronco reto e inclinado (erro médio 0,3 cm) e a cadeia concordou com o OSC na caminhada inclinada.

**Roteiro guiado no `calculo_passo.py`:** com `ROTEIRO_GUIADO = True` (padrão), depois das calibrações o programa conduz as etapas da lista `ROTEIRO`, cada uma com tempo fixo e aviso sonoro na troca: ir ao início (15 s), caminhada reta (15 s), voltar (15 s), caminhada inclinada (15 s), ir até a caixa (25 s), pé E e pé D na caixa com o tronco reto (7 s cada, 6 s no chão depois de cada um), inclinar (6 s) e pé E e pé D na caixa inclinado. São 143 s no total. Nos últimos 3 s de cada etapa a linha do terminal mostra a próxima etapa em contagem regressiva, e a gravação encerra sozinha no fim. Ao final ficam salvos `dados/fases_sessao.csv` (início e fim de cada etapa), `dados/dados_brutos_sessao.csv` (altura de cada pé a cada instante, com OSC, cadeia, inclinação e peso da cadeia) e `dados/dados_brutos_entrada.json` (entradas e calibração, para reprocessar).

**Sentido do eixo frente/trás (`CORRIGIR_SENTIDO_AP`):** o heel-strike só é aceito se o pé que bateu estiver à frente do outro. Na sessão de 19/10, o eixo tirado do quadril apontava para trás, então o programa aceitava o impulso (toe-off) e descartava o heel-strike. Isso foi confirmado de três jeitos: o pé no ar andava "para trás" nesse eixo, a cadeia das pernas mostrava o contrário e a IMU ficava quieta antes do pico e agitada depois. Agora o programa confere o sentido pelo balanço dos pés e inverte o sinal quando preciso. Os picos de aceleração também são procurados com distância curta (0,2 s), para que o impulso maior não esconda o heel-strike do mesmo pé. Na caminhada inclinada, os passos marcados pela IMU passaram de 5 para 12 de 12.

A aceleração das IMUs continua sendo usada só para o tempo dos passos (heel-strike). Na gravação de 19/10 (tronco a ~3°, ~23° e ~40°), com o ganho da caixa calibrado com o tronco reto, o erro médio por subida na caixa de 6,7 cm foi de 0,67 cm pelo OSC, 0,81 cm pela cadeia e 0,58 cm pelo pipeline. Com os pés no chão, a cadeia variou menos de 1 cm com o tronco a 40°, enquanto o OSC "subiu" 14,5 cm. O `experimento_inclinacao.py` mostra as colunas para comparar.

---

### `monitor_passadas.py` — Monitor em Tempo Real de Passadas (Heel-Strike IMU + OSC)
Monitora continuamente os passos em tempo real durante a caminhada, exibindo no terminal o comprimento e os impactos.

**Como rodar:**
```powershell
uv run python monitor_passadas.py
```

---

### `slimevr.py` — Modo Descoberta (Identificar Trackers dos Pés)
Exibe em tempo real as posições de todos os trackers e calcula a distância entre cada par. Serve para descobrir quais IDs correspondem aos seus pés.

**Como rodar:**
```powershell
uv run python slimevr.py
```

**Como usar:**
1. Com o SlimeVR rodando e enviando OSC, execute o comando acima.
2. Fique de pé e **afaste bem as pernas** uma da outra.
3. Observe no terminal qual par de trackers (ex: `7 <-> 8`) mostra a **maior distância horizontal**. Esses são os seus pés!
4. Pressione `Ctrl + C` para encerrar.

---

### `analise_ruido.py` — Análise de Ruído por FFT
Grava os dados de posição em um arquivo CSV e, ao encerrar, gera gráficos do espectro de frequências (Transformada de Fourier) para identificar ruídos nos sensores.

**Como rodar:**
```powershell
uv run python analise_ruido.py
```

---

### `analise_cabeca.py` — Rotação da Cabeça e Atenção Visual (VMC Protocol)
Script específico para monitorar a inclinação vertical da cabeça (Pitch). Como o VRChat OSC não possui suporte nativo a tracker de cabeça, este script utiliza o protocolo **VMC (Virtual Motion Capture)** na porta **39539**, recebendo os dados do osso `Head` diretamente do SlimeVR.

**Como rodar:**
```powershell
uv run python analise_cabeca.py
```

**Como usar:**
1. No SlimeVR, vá em **Opções > OSC > VMC** (conforme sua tela de captura virtual de movimentos) e deixe **Ativado** com a porta de saída padrão **39539**.
2. Execute o script. O terminal exibirá um medidor gráfico ao vivo indicando o ângulo do olhar e o estado correspondente (Neutro, Cima, Baixo, Muito Baixo).
3. Pressione `Ctrl + C` para finalizar.
4. Gera um relatório estatístico (tempo e porcentagem em cada zona de olhar, ângulo médio, desvio padrão), salva os dados em `resultado_rotacao_cabeca.csv` e abre os gráficos (trajetória temporal com zonas coloridas, gráfico de barras percentual e histograma).

---

### `calculo_passo.py` — Sistema Unificado de Marcha, Postura e Olhar (Principal)
Script completo e unificado que combina os dados da **Porta 9000** (Tornozelos 2 e 3 + Peito 6) e da **Porta 39539** (Cabeça via protocolo VMC). 

Analisa simultaneamente:
- **Comprimento da passada (m):** Distância horizontal com filtro passa-baixa e remoção de outliers via MAD.
- **Altura da passada (cm):** Elevação vertical de cada tornozelo, identificação automática do pé em balanço (`ESQ` ou `DIR`) e índice de simetria.
- **Postura do tronco (°):** Inclinação frontal do peito e estabilidade postural.
- **Atenção visual e orientação da cabeça (°):** Inclinação do olhar (Pitch), tempo e porcentagem em cada zona (Neutro, Baixo, Muito Baixo/Chão, Cima).

**Como rodar:**
```powershell
uv run python calculo_passo.py
```

**Como usar:**
1. Mantenha tanto o **VRChat OSC** (porta 9000) quanto o **VMC** (porta 39539) ativados no SlimeVR.
2. Execute o script e caminhe normalmente. O terminal exibirá em tempo real:
   ```
   Tempo: 12.5s | Passos: 0.785m [######------] | Alt: E=15.2cm D= 0.0cm | Tronco: -2.1° | Cabeça: -12.4° NEUTRO [-]
   ```
3. Pressione `Ctrl + C` para finalizar.
4. Gera um relatório estatístico completo no terminal, cruza todas as variáveis em uma **tabela passo a passo**, salva os dados em `resultado_completo_marcha.csv` e abre uma janela com **5 gráficos sincronizados**.

---

### `diagnostico_rede.py` — Diagnóstico de Rede e Pacotes SlimeVR
Monitora simultaneamente as portas 9000 e 39539 em tempo real, exibindo taxas de pacotes por segundo, trackers conectados e ossos ativos para verificar se todos os sensores estão se comunicando corretamente.

**Como rodar:**
```powershell
uv run python diagnostico_rede.py
```

---

### `comparacao_passos.py` — Comparação Científica de Métodos de Passo (M1 vs M2 vs M3)
Compara lado a lado três abordagens biomecânicas para detecção do timing e comprimento do passo:
1. **Método 1 (Geométrico / Pico de Distância):** Máxima separação horizontal euclidiana entre os tornozelos.
2. **Método 2 (Cinemático / Contato com o Solo - Foot Landing):** Momento de assentamento e toque do pé no solo (fim da descida vertical e velocidade vertical de impacto $\approx 0$).
3. **Método 3 (Dinâmico / Aceleração e Impacto Mecânico):** Pico de desaceleração/impacto mecânico no tornozelo do pé oscilante ao colidir com o chão.

**Como rodar ao vivo (via SlimeVR OSC):**
```powershell
uv run python comparacao_passos.py
```

**Como rodar com arquivo gravado anteriormente:**
```powershell
uv run python comparacao_passos.py --arquivo dados_marcha_paciente.csv
```

**Saídas:**
- Tabela comparativa detalhada passo a passo no terminal mostrando timings, comprimentos e discrepâncias ($\Delta$ em cm e ms).
- Exportação dos dados para `comparacao_metodos_passos.csv`.
- Gráficos integrados com 4 painéis ilustrando os sinais, momentos de corte de cada método e a curva comparativa de passos.

---

### `slimevr_client_raw/` e `analise_imu_raw.py` — Extração de Dados Brutos das IMUs (SolarXR WebSocket)
Cliente oficial de extração de sinais direto do protocolo SolarXR FlatBuffers na porta WebSocket `21110`:
- **Aceleração linear dos pés (`linear_acc_*` em m/s²)** calculada sem gravidade.
- **Orientação angular dos pés (`rotation_*` quaternion)**.
- **Acelerações e giroscópios brutos (`raw_acc_*`, `raw_gyro_*`)**.

**1. Listar trackers físicos ativos:**
```powershell
node slimevr_client_raw/src/cli.mjs inspect
```

**2. Capturar sessão da caminhada (ex: 30 segundos):**
```powershell
node slimevr_client_raw/src/cli.mjs capture --seconds 30 --out slimevr_client_raw/captures/sessao1
```
*(Gera `samples.csv`, `metadata.json` e `summary.json` na pasta de saída)*

**3. Analisar e plotar os sinais das IMUs em Python:**
```powershell
uv run python analise_imu_raw.py slimevr_client_raw/captures/sessao1/samples.csv
```

**4. Calcular Comprimento e Altura da Passada via ZUPT 3D:**
```powershell
uv run python analise_passada_zupt.py slimevr_client_raw/captures/sessao1/samples.csv
```

---

### `captura_e_comparacao_zupt_osc.py` — Comparação Simultânea ZUPT (IMU) vs Posição (OSC)
Captura simultaneamente os dados das duas fontes (porta 21110 SolarXR para IMUs e porta 9000 OSC para posições) e calcula lado a lado para cada passada:
- **Comprimento da passada ($m$):** ZUPT inercial vs Posição OSC
- **Altura da passada ($cm$):** Clearance vertical ZUPT vs Elevação vertical OSC
- **Discrepâncias relativas ($\Delta$ em cm):** Diferença entre o modelo inercial e o esqueleto cinemático

**Como rodar (preparação de 5s e grava livre até `Ctrl + C`):**
```powershell
uv run python captura_e_comparacao_zupt_osc.py
```

---

### `analise_altura_passada.py` — Analisador Dedicado de Altura da Passada (Foot Clearance)
Focado exclusivamente na elevação vertical ($cm$) de cada pé, clearance do solo e simetria de levantamento:
- Altímetro em tempo real no terminal durante a caminhada.
- Curvas contínuas de elevação vertical do pé esquerdo e pé direito.
- Cálculo de altura média, desvios e índice de simetria vertical (%).
- Tabela passo a passo das alturas máximas e gráficos comparativos.

**Como rodar:**
```powershell
uv run python analise_altura_passada.py
```

---

### `comparacao_altura_imu_osc.py` — Comparador Dedicado de Altura (IMU SolarXR vs OSC)
Compara lado a lado a elevação vertical medida pela **IMU do pé (ZUPT de Toe-Off a Heel-Strike + Pitch angular)** contra o **OSC com solo estabilizado localmente**:
- Remove a flutuação do quadril e do chão virtual do OSC.
- Mostra a flexão angular do pé (dorsiflexão em graus) durante o voo.
- Plota gráficos sobrepostos de altura vertical ($cm$) e velocidade vertical ($m/s$).

**Como rodar:**
```powershell
uv run python comparacao_altura_imu_osc.py
```

---

### `monitor_altura_tempo_real.py` — Monitor de Altura em Tempo Real (IMU vs OSC ao Vivo)
Exibe **ao mesmo tempo e em tempo real** (10 vezes por segundo no terminal) a altura medida pelos dois métodos lado a lado para cada pé:
- **Pé Esquerdo:** IMU ($cm$) vs OSC ($cm$) com barras gráficas simultâneas.
- **Pé Direito:** IMU ($cm$) vs OSC ($cm$) com barras gráficas simultâneas.
- Ao apertar `Ctrl + C`, plota as curvas de elevação contínua sobrepostas e o pitch angular.

**Como rodar:**
```powershell
uv run python monitor_altura_tempo_real.py
```

## 🧠 Como Funciona

### Modo Descoberta (`slimevr.py`)
O script opera filtrando mensagens OSC que terminam em `/position` e realiza as seguintes etapas:
1. **Escuta na porta 9000:** Inicia um servidor UDP (não-bloqueante) usando a biblioteca `python-osc`.
2. **Filtra Posições Espaciais:** Ignora dados de rotação e foca apenas nos endereços que transmitem posição no espaço virtual (ex: `/tracking/trackers/<ID>/position`).
3. **Extrai Coordenadas (X, Y, Z):** Armazena a posição tridimensional mais recente de cada tracker detectado.
4. **Cálculo de Distâncias (Euclidiana):** Faz um cruzamento combinatório entre todos os trackers e calcula a distância usando a **distância euclidiana**:
   - **Distância Horizontal 2D (Plano X-Z):** Ideal para o cálculo do passo, pois ignora a elevação do pé. Fórmula: `√((x2 - x1)² + (z2 - z1)²)`.
   - **Distância Total 3D (X-Y-Z):** Distância absoluta no espaço, incluindo a altura (eixo Y). Fórmula: `√((x2 - x1)² + (y2 - y1)² + (z2 - z1)²)`.

### Análise de Ruído (`analise_ruido.py`)
Utiliza a **Transformada Rápida de Fourier (FFT)** via NumPy para decompor o sinal de posição em suas frequências componentes, permitindo visualizar onde estão os ruídos e decidir se é necessário aplicar um filtro (ex: Butterworth passa-baixa).

## 🛣️ Próximos Passos (Roadmap)
- [ ] Focar exclusivamente no par de trackers dos pés.
- [ ] Implementar filtro passa-baixa (se necessário após análise de ruído).
- [ ] Gravar o histórico de distâncias ao longo do tempo.
- [ ] Implementar algoritmo de detecção de picos para identificar o comprimento do passo.
