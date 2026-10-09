#!/usr/bin/env node
import { createWriteStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { finished } from 'node:stream/promises';
import path from 'node:path';
import process from 'node:process';
import { CSV_HEADER, Diagnostics, recordToCsv } from './diagnostics.mjs';
import { decodeDataFeedUpdates, encodeDataFeedRequest, flattenUpdate } from './protocol.mjs';

const HELP = `Uso:
  node src/cli.mjs inspect [--url ws://127.0.0.1:21110]
  node src/cli.mjs capture [--url ws://127.0.0.1:21110] [--seconds 60]
                           [--interval-ms 0] [--out captures/NOME]

inspect: lista os dispositivos e trackers físicos vistos pelo servidor.
capture: grava samples.csv, metadata.json e summary.json.
--seconds 0: captura até Ctrl+C. --interval-ms 0: a cada tick do servidor.
`;

function parseCommand(args) {
  if (args.length === 0 || args.includes('--help') || args.includes('-h')) {
    return { command: 'help' };
  }
  const command = args[0];
  if (!['inspect', 'capture'].includes(command)) throw new Error(`Comando inválido: ${command}`);
  const options = { command, url: 'ws://127.0.0.1:21110', seconds: 60, intervalMs: 0 };
  for (let i = 1; i < args.length; i += 2) {
    const flag = args[i];
    const value = args[i + 1];
    if (value === undefined) throw new Error(`Falta valor para ${flag}`);
    if (flag === '--url') options.url = value;
    else if (flag === '--out') options.out = value;
    else if (flag === '--seconds') options.seconds = Number(value);
    else if (flag === '--interval-ms') options.intervalMs = Number(value);
    else throw new Error(`Opção inválida: ${flag}`);
  }
  const url = new URL(options.url);
  if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('--url precisa ser ws:// ou wss://');
  if (!Number.isFinite(options.seconds) || options.seconds < 0) {
    throw new Error('--seconds precisa ser um número não negativo');
  }
  if (!Number.isInteger(options.intervalMs) || options.intervalMs < 0 || options.intervalMs > 65535) {
    throw new Error('--interval-ms precisa ser inteiro entre 0 e 65535');
  }
  return options;
}

async function connect(url) {
  const socket = new WebSocket(url);
  socket.binaryType = 'arraybuffer';
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Sem conexão com ${url} em 10 segundos`)), 10_000);
    socket.addEventListener('open', () => {
      clearTimeout(timeout);
      resolve();
    }, { once: true });
    socket.addEventListener('error', () => {
      clearTimeout(timeout);
      reject(new Error(`Falha ao conectar a ${url}; confira se o servidor SlimeVR está aberto`));
    }, { once: true });
  }).catch((error) => {
    socket.close();
    throw error;
  });
  return socket;
}

function receivedTime() {
  return { unixMs: Date.now(), monotonicNs: process.hrtime.bigint().toString() };
}

function decodeEvent(event) {
  if (!(event.data instanceof ArrayBuffer)) {
    throw new Error(`Quadro WebSocket inesperado: ${typeof event.data}`);
  }
  return decodeDataFeedUpdates(event.data);
}

async function inspect(options) {
  const socket = await connect(options.url);
  try {
    const update = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Sem resposta ao PollDataFeed em 10 segundos')), 10_000);
      socket.addEventListener('message', (event) => {
        try {
          const updates = decodeEvent(event);
          if (updates.length > 0) {
            clearTimeout(timeout);
            resolve(updates[0]);
          }
        } catch (error) {
          clearTimeout(timeout);
          reject(error);
        }
      });
      socket.send(encodeDataFeedRequest('poll'));
    });
    const trackers = flattenUpdate(update, receivedTime(), 1);
    console.log(JSON.stringify({
      url: options.url,
      deviceCount: update.devices?.length ?? 0,
      trackerCount: trackers.length,
      trackers: trackers.map((tracker) => ({
        deviceId: tracker.deviceId,
        trackerNum: tracker.trackerNum,
        bodyPartName: tracker.bodyPartName,
        displayName: tracker.displayName,
        imuType: tracker.imuType,
        firmwareVersion: tracker.firmwareVersion,
        hardwareIdentifier: tracker.hardwareIdentifier,
        hasLinearAcceleration: tracker.acceleration !== null,
        hasRotation: tracker.rotation !== null,
        hasRawAcceleration: tracker.rawAcceleration !== null,
        hasRawGyro: tracker.rawAngularVelocity !== null,
      })),
    }, null, 2));
  } finally {
    socket.close();
  }
}

async function capture(options) {
  const directory = path.resolve(options.out ?? path.join('captures',
    `${new Date().toISOString().replaceAll(':', '-')}-${process.pid}`));
  await mkdir(path.dirname(directory), { recursive: true });
  await mkdir(directory);
  const socket = await connect(options.url);
  const samplesPath = path.join(directory, 'samples.csv');
  const writer = createWriteStream(samplesPath, { flags: 'wx' });
  const writerDone = finished(writer);
  const diagnostics = new Diagnostics();
  const startedAt = new Date().toISOString();
  const metadata = {
    formatVersion: 1,
    startedAt,
    serverUrl: options.url,
    requestedIntervalMs: options.intervalMs,
    requestedFields: ['rotation', 'linearAcceleration', 'rawAcceleration', 'rawAngularVelocity'],
    timestampNote: 'Horário da chegada ao cliente; não é o horário da amostragem na IMU.',
    signalNote: 'A aceleração linear e a orientação são processadas pelo firmware/servidor; campos raw podem ficar vazios.',
  };
  await writeFile(path.join(directory, 'metadata.json'), JSON.stringify(metadata, null, 2) + '\n');
  writer.write(CSV_HEADER);

  let frameSequence = 0;
  let stopping = false;
  let stopReason = 'duration_elapsed';
  let failure = null;
  let durationTimer;
  let resolveDone;
  const done = new Promise((resolve) => { resolveDone = resolve; });

  let lastFeedback = 0;
  const startTime = Date.now();
  let accLeft = 0;
  let accRight = 0;
  let impactCount = 0;
  let lastImpactTime = 0;

  const stop = (reason, error = null) => {
    if (stopping) return;
    stopping = true;
    stopReason = reason;
    failure = error;
    clearTimeout(durationTimer);
    process.stderr.write('\n');
    socket.removeEventListener('message', onMessage);
    socket.close();
    writer.end();
    resolveDone();
  };
  const onMessage = (event) => {
    try {
      const received = receivedTime();
      frameSequence += 1;
      diagnostics.frame();
      for (const update of decodeEvent(event)) {
        diagnostics.update();
        for (const record of flattenUpdate(update, received, frameSequence)) {
          diagnostics.add(record);
          writer.write(recordToCsv(record));

          if (record.acceleration) {
            const mag = Math.sqrt(record.acceleration.x ** 2 + record.acceleration.y ** 2 + record.acceleration.z ** 2);
            const bp = record.bodyPartName;
            if (bp === 'LEFT_FOOT' || bp === 'LEFT_LOWER_LEG') {
              accLeft = mag;
              if (mag > 12.0 && Date.now() - lastImpactTime > 300) {
                impactCount += 1;
                lastImpactTime = Date.now();
              }
            } else if (bp === 'RIGHT_FOOT' || bp === 'RIGHT_LOWER_LEG') {
              accRight = mag;
              if (mag > 12.0 && Date.now() - lastImpactTime > 300) {
                impactCount += 1;
                lastImpactTime = Date.now();
              }
            }
          }
        }
      }

      const now = Date.now();
      if (now - lastFeedback >= 100) {
        lastFeedback = now;
        const elapsed = ((now - startTime) / 1000).toFixed(1);
        const barE = '#'.repeat(Math.min(8, Math.floor(accLeft / 2.5))).padEnd(8, '-');
        const barD = '#'.repeat(Math.min(8, Math.floor(accRight / 2.5))).padEnd(8, '-');
        const durStr = options.seconds > 0 ? `/${options.seconds}s` : ' (Ctrl+C encerra)';
        process.stderr.write(`\r  ⏱ Tempo: ${elapsed}s${durStr} | Pé ESQ: ${accLeft.toFixed(1).padStart(4)} m/s² [${barE}] | Pé DIR: ${accRight.toFixed(1).padStart(4)} m/s² [${barD}] | Choques: ${impactCount}  `);
      }

      if (writer.writableLength > 16 * 1024 * 1024) {
        stop('write_buffer_overflow', new Error('O disco não acompanhou a captura (fila > 16 MiB)'));
      }
    } catch (error) {
      stop('decode_error', error);
    }
  };
  const onClose = () => stop('connection_closed', new Error('Conexão com o servidor encerrada durante a captura'));
  const onError = () => stop('connection_error', new Error('Erro na conexão com o servidor'));
  const onWriterError = (error) => stop('write_error', error);
  const onSigint = () => stop('interrupted');
  socket.addEventListener('message', onMessage);
  socket.addEventListener('close', onClose);
  socket.addEventListener('error', onError);
  writer.on('error', onWriterError);
  process.on('SIGINT', onSigint);

  if (options.seconds > 0) durationTimer = setTimeout(() => stop('duration_elapsed'), options.seconds * 1000);
  console.error(`Capturando ${options.url} em ${directory}. Ctrl+C para parar.`);
  try {
    socket.send(encodeDataFeedRequest('start', options.intervalMs));
  } catch (error) {
    stop('request_error', error);
  }
  await done;
  process.off('SIGINT', onSigint);
  socket.removeEventListener('close', onClose);
  socket.removeEventListener('error', onError);
  try {
    await writerDone;
  } catch (error) {
    failure ??= error;
    stopReason = 'write_error';
  }

  const summary = {
    ...diagnostics.summary(),
    startedAt,
    endedAt: new Date().toISOString(),
    stopReason,
    error: failure?.message ?? null,
  };
  await writeFile(path.join(directory, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  console.error(`Captura encerrada: ${directory}`);
  console.log(JSON.stringify(summary, null, 2));
  if (failure) throw failure;
  if (summary.trackerCount === 0) throw new Error('Nenhum tracker físico apareceu na captura');
}

try {
  const options = parseCommand(process.argv.slice(2));
  if (options.command === 'help') console.log(HELP);
  else if (options.command === 'inspect') await inspect(options);
  else await capture(options);
} catch (error) {
  console.error(`Erro: ${error.message}`);
  process.exitCode = 1;
}
