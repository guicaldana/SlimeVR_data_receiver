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

## 📂 Scripts Disponíveis

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
