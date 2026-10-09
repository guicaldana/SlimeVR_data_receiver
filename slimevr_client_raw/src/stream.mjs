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
        const bp = rec.bodyPartName || '';
        if (bp !== 'LEFT_FOOT' && bp !== 'RIGHT_FOOT') continue;

        const lado = bp === 'LEFT_FOOT' ? 'ESQ' : 'DIR';

        const acc = rec.acceleration;
        const rot = rec.rotation;
        if (!acc) continue;

        // Calcula pitch (graus) se houver rotacao
        let pitch = 0.0;
        if (rot) {
          const sinp = 2.0 * (rot.w * rot.x - rot.y * rot.z);
          pitch = Math.asin(Math.max(-1.0, Math.min(1.0, sinp))) * (180.0 / Math.PI);
        }

        // Envia linha compacta para o Python via stdout
        // Formato: D <LADO> <AX> <AY> <AZ> <PITCH> <UNIX_MS>
        process.stdout.write(`D ${lado} ${acc.x.toFixed(4)} ${acc.y.toFixed(4)} ${acc.z.toFixed(4)} ${pitch.toFixed(2)} ${received.unixMs}\n`);
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

