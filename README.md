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

### `calculo_passo.py` — Estimativa de Passo, Altura e Postura (Principal)
Script completo que combina os trackers dos tornozelos (IDs 2 e 3) e do peito (ID 6). Analisa tanto o **comprimento** quanto a **altura da passada (elevação/clearance do pé)**, identifica o pé em balanço (Esquerdo vs Direito), calcula a angulação postural do tronco, remove meias-voltas e outliers (MAD - Median Absolute Deviation), exibindo métricas ao vivo no terminal e gerando relatórios completos com gráficos e CSV.

**Como rodar:**
```powershell
uv run python calculo_passo.py
```

**Como usar:**
1. Execute o script com o SlimeVR conectado.
2. Caminhe pelo ambiente. O terminal mostrará ao vivo a distância entre pés, barra visual, elevação instantânea dos pés (`E=15.2cm D=0.0cm`) e inclinação do peito (ex: `Tronco: -2.1° (Ereto ✓)`).
3. Pressione `Ctrl + C` para finalizar.
4. Veja o relatório estatístico detalhado (comprimento médio, altura média por pé e índice de simetria), a tabela de passos e a janela com 4 gráficos sincronizados:
   - Distância horizontal entre tornozelos (comprimento)
   - Trajetória vertical dos tornozelos (altura/clearance do pé esquerdo vs direito)
   - Inclinação postural do tronco
   - Gráfico de barras comparativo (Comprimento em m vs Altura em cm por passada)
5. O resultado é salvo automaticamente em `resultado_passos.csv`.

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
