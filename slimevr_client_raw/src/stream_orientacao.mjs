// Orientação (quaternion) de todos os trackers pelo SolarXR, uma linha por amostra no stdout.
// Usado pelo experimento_cervical.py (cabeça e peito). Formato:
//   Q <PARTE_DO_CORPO> <QX> <QY> <QZ> <QW> <UNIX_MS>
import process from 'node:process';
import { decodeDataFeedUpdates, encodeDataFeedRequest, flattenUpdate } from './protocol.mjs';

function decodeEvent(event) {
  if (typeof event.data === 'string') return [];
  let buffer;
  if (event.data instanceof ArrayBuffer) {
    buffer = event.data;
  } else if (ArrayBuffer.isView(event.data) || Buffer.isBuffer(event.data)) {
    buffer = event.data.buffer.slice(event.data.byteOffset, event.data.byteOffset + event.data.byteLength);
  } else {
    return [];
  }
  return decodeDataFeedUpdates(buffer);
}

async function run() {
  const socket = new WebSocket('ws://127.0.0.1:21110');
  socket.binaryType = 'arraybuffer';

  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  socket.send(encodeDataFeedRequest('start', 0));

  socket.addEventListener('message', (event) => {
    const received = { unixMs: Date.now() };
    for (const update of decodeEvent(event)) {
      for (const rec of flattenUpdate(update, received, 0)) {
        const bp = rec.bodyPartName;
        const q = rec.rotation;
        if (!bp || bp === 'NONE' || !q) continue;
        process.stdout.write(`Q ${bp} ${q.x.toFixed(6)} ${q.y.toFixed(6)} ${q.z.toFixed(6)} ${q.w.toFixed(6)} ${received.unixMs}\n`);
      }
    }
  });

  process.on('SIGINT', () => {
    socket.close();
    process.exit(0);
  });
}

run().catch((err) => {
  process.stderr.write(`Erro no stream SolarXR: ${err.message}\n`);
  process.exit(1);
});
