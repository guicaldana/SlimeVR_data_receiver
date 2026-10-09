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
├── monitor_passadas.py            # Principal: Heel-Strike (IMU ZUPT) sincronizado com Distância (OSC)
├── slimevr.py                     # Modo descoberta básico de trackers
├── analises/                      # Scripts de pós-processamento, altura e comparações
│   ├── monitor_altura_tempo_real.py
│   ├── calculo_passo.py
│   ├── captura_e_comparacao_zupt_osc.py
│   ├── analise_altura_passada.py
│   ├── analise_passada_zupt.py
│   ├── analise_cabeca.py
│   ├── analise_ruido.py
│   └── comparacao_passos.py
├── dados/                         # Arquivos CSV gravados
├── archives/                      # Arquivos compactados e legados
└── slimevr_client_raw/            # Bridge Node.js do protocolo SolarXR WebSocket
```

## 📂 Scripts Principais (Raiz)

### `monitor_passadas.py` — Estimador de Comprimento de Passada (Heel-Strike IMU + OSC)
Sincroniza em tempo real o momento exato do impacto do calcanhar no solo detectado pelas **IMUs físicas (Porta 21110)** com a **distância horizontal Euclidiana entre os pés no OSC (Porta 9000)**.
- **Detecção do Heel-Strike:** Identifica o toque do pé no solo via aceleração inercial e ZUPT.
- **Comprimento da Passada:** Mede a separação horizontal instantânea entre os pés exatamente no momento do contato.
- **Imunidade Postural:** Totalmente independente de inclinações de tronco, postura ou altura da cabeça.

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
