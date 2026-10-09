import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import flatbuffers from 'flatbuffers';
import solarxr from 'solarxr-protocol';
import { CSV_HEADER, Diagnostics, recordToCsv } from '../src/diagnostics.mjs';
import { decodeDataFeedUpdates, encodeDataFeedRequest, flattenUpdate } from '../src/protocol.mjs';

const {
  BodyPart, DataFeedMessage, DataFeedMessageHeaderT, DataFeedUpdateT,
  DeviceDataT, DeviceIdT, HardwareInfoT, MessageBundle, MessageBundleT,
  QuatT, TrackerDataT, TrackerIdT, TrackerInfoT, Vec3fT,
} = solarxr;

function fixturePacket() {
  const left = new TrackerDataT();
  left.trackerId = new TrackerIdT(new DeviceIdT(7), 1);
  left.info = new TrackerInfoT();
  left.info.bodyPart = BodyPart.LEFT_FOOT;
  left.info.displayName = 'Pé esquerdo';
  left.linearAcceleration = new Vec3fT(1, -2, 3);
  left.rotation = new QuatT(0, 0, 0, 1);

  const right = new TrackerDataT();
  right.trackerId = new TrackerIdT(new DeviceIdT(8), 0);
  right.info = new TrackerInfoT();
  right.info.bodyPart = BodyPart.RIGHT_FOOT;
  right.info.displayName = 'Pé direito';
  right.linearAcceleration = new Vec3fT(-1, 2, 3);
  right.rotation = new QuatT(0, 0, 0, 1);

  const device = new DeviceDataT();
  device.id = new DeviceIdT(7);
  device.hardwareInfo = new HardwareInfoT();
  device.hardwareInfo.firmwareVersion = 'teste';
  device.trackers = [left];

  const otherDevice = new DeviceDataT();
  otherDevice.id = new DeviceIdT(8);
  otherDevice.trackers = [right];

  const update = new DataFeedUpdateT();
  update.devices = [device, otherDevice];
  const header = new DataFeedMessageHeaderT();
  header.messageType = DataFeedMessage.DataFeedUpdate;
  header.message = update;
  const bundle = new MessageBundleT();
  bundle.dataFeedMsgs = [header];
  const builder = new flatbuffers.Builder(512);
  builder.finish(bundle.pack(builder));
  return builder.asUint8Array();
}

function websocketFrame(payload) {
  const bytes = Buffer.from(payload);
  if (bytes.length < 126) return Buffer.concat([Buffer.from([0x82, bytes.length]), bytes]);
  const header = Buffer.alloc(4);
  header[0] = 0x82;
  header[1] = 126;
  header.writeUInt16BE(bytes.length, 2);
  return Buffer.concat([header, bytes]);
}

async function fakeServer(packet) {
  const server = createServer();
  const sockets = new Set();
  server.on('upgrade', (request, socket) => {
    sockets.add(socket);
    const accept = createHash('sha1')
      .update(`${request.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest('base64');
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`);
    let timer;
    let started = false;
    socket.on('data', (bytes) => {
      if ((bytes[0] & 0x0f) === 8) {
        socket.end();
        return;
      }
      if (started) return;
      started = true;
      socket.write(websocketFrame(packet));
      timer = setInterval(() => {
        if (!socket.destroyed) socket.write(websocketFrame(packet));
      }, 20);
    });
    socket.on('close', () => {
      clearInterval(timer);
      sockets.delete(socket);
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    url: `ws://127.0.0.1:${server.address().port}`,
    close: () => {
      for (const socket of sockets) socket.destroy();
      return new Promise((resolve) => server.close(resolve));
    },
  };
}

function runCli(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.resolve('src/cli.mjs'), ...args], { cwd: process.cwd() });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

test('pedido SolarXR solicita o feed e os sinais disponíveis', () => {
  const packet = encodeDataFeedRequest('start', 0);
  const bundle = MessageBundle.getRootAsMessageBundle(new flatbuffers.ByteBuffer(packet)).unpack();
  const header = bundle.dataFeedMsgs[0];
  assert.equal(header.messageType, DataFeedMessage.StartDataFeed);
  const config = header.message.dataFeeds[0];
  assert.equal(config.minimumTimeSinceLast, 0);
  assert.equal(config.dataMask.trackerData.linearAcceleration, true);
  assert.equal(config.dataMask.trackerData.rotation, true);
  assert.equal(config.dataMask.trackerData.rawAngularVelocity, true);
});

test('decodificação, CSV e diagnóstico preservam trackers físicos', () => {
  const updates = decodeDataFeedUpdates(fixturePacket());
  assert.equal(updates.length, 1);
  const records = flattenUpdate(updates[0], { unixMs: 1000, monotonicNs: '1000000000' }, 1);
  assert.equal(records.length, 2);
  assert.equal(records[0].bodyPartName, 'LEFT_FOOT');
  assert.equal(records[1].bodyPartName, 'RIGHT_FOOT');
  assert.equal(records[1].deviceId, 8);
  assert.equal(records[0].acceleration.x, 1);
  assert.equal(records[0].rawAngularVelocity, null);
  const csv = recordToCsv(records[0]);
  assert.equal(csv.split(',').length, CSV_HEADER.trimEnd().split(',').length);
  const diagnostics = new Diagnostics();
  diagnostics.add(records[0]);
  diagnostics.add({ ...records[0], receivedMonotonicNs: '1010000000' });
  const summary = diagnostics.summary().trackers[0];
  assert.equal(summary.rowRateHz, 100);
  assert.equal(summary.accelerationChanged, 0);
  assert.equal(summary.intervalMs.median, 10);
});

test('inspect e capture funcionam com servidor WebSocket de teste', async () => {
  const server = await fakeServer(fixturePacket());
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'slimevr-capture-test-'));
  try {
    const inspected = await runCli(['inspect', '--url', server.url]);
    assert.equal(inspected.code, 0, inspected.stderr);
    assert.equal(JSON.parse(inspected.stdout).trackers[0].bodyPartName, 'LEFT_FOOT');

    const output = path.join(temporary, 'session');
    const captured = await runCli(['capture', '--url', server.url, '--seconds', '0.25', '--out', output]);
    assert.equal(captured.code, 0, captured.stderr);
    const csv = await readFile(path.join(output, 'samples.csv'), 'utf8');
    const summary = JSON.parse(await readFile(path.join(output, 'summary.json'), 'utf8'));
    assert.ok(csv.trimEnd().split('\n').length >= 3);
    assert.equal(summary.trackerCount, 2);
    assert.ok(summary.trackers[0].accelerationRows >= 2);
    assert.equal(summary.trackers[0].rawGyroRows, 0);
  } finally {
    await server.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
