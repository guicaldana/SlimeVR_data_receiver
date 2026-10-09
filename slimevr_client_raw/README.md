# Captura SolarXR para análise da marcha

Cliente experimental para registrar, ao vivo, os **trackers físicos** do servidor SlimeVR. O foco inicial são os sensores dos pés das SlimeVR v1.2 com IMU ICM-45686. O projeto grava a aceleração linear sem gravidade e a orientação fusionada de cada tracker, além de solicitar aceleração e giroscópio brutos caso uma versão futura do servidor passe a fornecê-los.

## Estado atual

- `inspect` consulta dispositivos, identificação, papel corporal, firmware e presença dos sinais.
- `capture` grava `samples.csv`, `metadata.json` e `summary.json` em uma pasta de sessão nova.
- A captura passou em testes locais de codificação SolarXR e em um teste de integração com servidor WebSocket simulado. **Ainda não foi testada com IMUs reais.**
- O cliente atual **não extrai aceleração ou giroscópio brutos** do firmware SlimeVR padrão. Os campos correspondentes ficarão vazios se o servidor não os fornecer.

## Instalação

Requer Node.js **24 ou superior** e pnpm. Na pasta do projeto:

```bash
pnpm install --frozen-lockfile
```

Os bindings gerados do SolarXR estão incluídos em `vendor/`, fixados em uma revisão upstream específica. A instalação ainda baixa a biblioteca `flatbuffers` do registro npm.

## Primeiro teste com as IMUs

1. Ligue o servidor SlimeVR e conecte os trackers. Confirme no aplicativo que os sensores dos pés estão atribuídos a `LEFT_FOOT` e `RIGHT_FOOT`.
2. Liste o que o servidor expõe:

   ```bash
   node src/cli.mjs inspect
   ```

3. Faça uma captura parado, com os dois pés no chão:

   ```bash
   node src/cli.mjs capture --seconds 30 --out captures/parado
   ```

4. Faça outra caminhando em linha reta, em ritmo confortável, incluindo alguns segundos parado no começo e no fim:

   ```bash
   node src/cli.mjs capture --seconds 60 --out captures/caminhada
   ```

5. Se possível, grave vídeo dos pés com relógio ou uma batida/palma visível no início, para conferir manualmente os eventos da marcha. Registre onde cada tracker estava fixado no calçado, o firmware e a versão do servidor.

As pastas de saída precisam ser novas; o programa não sobrescreve sessões existentes. Use `--seconds 0` para gravar até `Ctrl+C`. Para um servidor em outro computador, use `--url ws://IP_DO_SERVIDOR:21110` se a configuração de rede permitir.

## Como ler os arquivos

Uma linha de `samples.csv` representa **um tracker em uma atualização do feed do servidor**, não necessariamente uma nova amostra da IMU. As colunas principais são:

| Coluna | Significado |
| --- | --- |
| `received_unix_ms` | Horário de chegada no computador, em milissegundos Unix. |
| `received_monotonic_ns` | Relógio monotônico local, em nanossegundos; apropriado para intervalos dentro da sessão. |
| `device_id`, `tracker_num`, `body_part_name` | Identificação do tracker durante esta conexão. O ID pode mudar em outra sessão. |
| `linear_acc_*_ms2` | Aceleração linear sem gravidade, em m/s², rotacionada pelo servidor para um referencial global com rumo desconhecido. |
| `rotation_*` | Quaternion fusionado do tracker, componentes `x,y,z,w`. |
| `raw_acc_*_ms2`, `raw_gyro_*_rads` | Campos opcionais solicitados ao protocolo; espera-se que fiquem vazios no servidor padrão atual. |

`summary.json` mostra, por tracker, contagens de linhas com cada sinal, taxa de linhas recebidas, intervalos entre linhas, e quantas vezes aceleração e orientação **mudaram numericamente**. A taxa de linhas **não é a frequência de amostragem da IMU**. Valores repetidos podem decorrer de um sensor parado ou de reenvio do último valor; essas contagens não distinguem as duas causas com certeza. `metadata.json` guarda as opções e ressalvas da sessão.

## Limitações e decisão seguinte

O firmware ICM-45686 lê acelerômetro a aproximadamente 102,4 Hz e giroscópio a 204,8 Hz, executa fusão VQF e limita a publicação dos sinais fusionados a cerca de 100 Hz. O cliente solicita uma atualização a cada tick do servidor (`--interval-ms 0`), mas isso **não garante** 100 amostras novas por segundo. O protocolo não inclui o timestamp da amostra da IMU; os horários gravados são de chegada ao cliente. O firmware padrão também associa o envio da aceleração ao envio de uma nova orientação. Esses pontos devem ser avaliados nos testes em movimento.

Se os dados dos pés chegarem com frequência e regularidade suficientes, a próxima etapa é detectar contato inicial e saída do pé e validar os tempos contra vídeo. Depois podemos avaliar comprimento da **passada** por integração com correção de velocidade no apoio (ZUPT). O comprimento do **passo entre pés alternados** exige mais cuidado com alinhamento espacial e temporal. Se a captura revelar lacunas ou se o giroscópio bruto se mostrar necessário, a próxima intervenção é no firmware e, possivelmente, no servidor/protocolo, para transportar amostras calibradas com timestamps de origem.

## Fontes técnicas

- [Driver ICM-45686](https://github.com/SlimeVR/SlimeVR-Tracker-ESP/blob/4904680a400e2f5ecf751d2d47a1766572a0682c/src/sensors/softfusion/drivers/icm45686.h) e [fusão/envio](https://github.com/SlimeVR/SlimeVR-Tracker-ESP/blob/4904680a400e2f5ecf751d2d47a1766572a0682c/src/sensors/softfusion/softfusionsensor.h).
- [Condição de envio da aceleração](https://github.com/SlimeVR/SlimeVR-Tracker-ESP/blob/4904680a400e2f5ecf751d2d47a1766572a0682c/src/sensors/sensor.cpp).
- [Esquema do tracker SolarXR](https://github.com/SlimeVR/SolarXR-Protocol/blob/00c38a6dc28070b30850a89c26b17928e56245d4/schema/data_feed/tracker.fbs) e [preenchimento do feed no servidor](https://github.com/SlimeVR/SlimeVR-Server/blob/83941fd38e91cc91ca6b360deab5c2ae986dd1b6/server/core/src/main/java/dev/slimevr/protocol/datafeed/DataFeedBuilder.kt).
- [Estudo de detecção de eventos pela orientação do pé a 100 Hz](https://pubmed.ncbi.nlm.nih.gov/34871897/) e [estudo sobre efeito da posição da IMU no calçado](https://pmc.ncbi.nlm.nih.gov/articles/PMC9182246/).

## Desenvolvimento

```bash
pnpm test
```

O teste de integração abre temporariamente uma porta local para simular o servidor. Nenhum firmware é alterado por este cliente.
