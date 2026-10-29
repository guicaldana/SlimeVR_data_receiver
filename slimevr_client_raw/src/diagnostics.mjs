const FIELDS = [
  'received_unix_ms', 'received_monotonic_ns', 'frame_sequence', 'feed_index',
  'device_id', 'tracker_num', 'tracker_device_id', 'body_part', 'body_part_name',
  'display_name', 'custom_name', 'imu_type', 'tracker_status', 'tracker_tps',
  'firmware_version', 'hardware_identifier', 'device_packet_loss',
  'ping_ms', 'rssi_dbm', 'battery_pct', 'packets_lost', 'packets_received',
  'linear_acc_x_ms2', 'linear_acc_y_ms2', 'linear_acc_z_ms2',
  'rotation_x', 'rotation_y', 'rotation_z', 'rotation_w',
  'raw_acc_x_ms2', 'raw_acc_y_ms2', 'raw_acc_z_ms2',
  'raw_gyro_x_rads', 'raw_gyro_y_rads', 'raw_gyro_z_rads',
];

export const CSV_HEADER = `${FIELDS.join(',')}\n`;

function csvCell(value) {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function recordToCsv(record) {
  const a = record.acceleration;
  const q = record.rotation;
  const rawA = record.rawAcceleration;
  const rawG = record.rawAngularVelocity;
  return [
    record.receivedUnixMs, record.receivedMonotonicNs, record.frameSequence,
    record.feedIndex, record.deviceId, record.trackerNum, record.trackerDeviceId,
    record.bodyPart, record.bodyPartName, record.displayName, record.customName,
    record.imuType, record.trackerStatus, record.trackerTps, record.firmwareVersion,
    record.hardwareIdentifier, record.packetLoss,
    record.pingMs, record.rssi, record.batteryPct, record.packetsLost, record.packetsReceived,
    a?.x, a?.y, a?.z,
    q?.x, q?.y, q?.z, q?.w,
    rawA?.x, rawA?.y, rawA?.z,
    rawG?.x, rawG?.y, rawG?.z,
  ].map(csvCell).join(',') + '\n';
}

function sameVector(a, b, dimensions) {
  return dimensions.every((key) => Object.is(a[key], b[key]));
}

function quantile(sorted, fraction) {
  if (sorted.length === 0) return null;
  const index = Math.floor((sorted.length - 1) * fraction);
  return Math.round(sorted[index] * 1000) / 1000;
}

function resumo(values) {
  if (values.length === 0) return null;
  const sorted = values.toSorted((a, b) => a - b);
  return {
    median: quantile(sorted, 0.5),
    p10: quantile(sorted, 0.1),
    p90: quantile(sorted, 0.9),
    maximum: sorted.at(-1),
  };
}

// Tabela curta por tracker: TPS, ping, sinal e bateria (para o terminal)
const NOMES_PT = {
  LEFT_FOOT: 'Pé E', RIGHT_FOOT: 'Pé D', LEFT_LOWER_LEG: 'Canela E', RIGHT_LOWER_LEG: 'Canela D',
  LEFT_UPPER_LEG: 'Coxa E', RIGHT_UPPER_LEG: 'Coxa D', HIP: 'Quadril', WAIST: 'Cintura',
  CHEST: 'Peito', UPPER_CHEST: 'Peito sup.', HEAD: 'Cabeça',
};

export function nomeParte(bodyPartName) {
  return NOMES_PT[bodyPartName] ?? bodyPartName ?? '?';
}

function cel(value, width, digits = 0) {
  const text = value === null || value === undefined ? '-' : Number(value).toFixed(digits);
  return text.padStart(width);
}

export function tabelaRede(linhas) {
  // linhas: [{nome, tps, ping, rssi, bateria}] com números ou null
  const out = [`  ${'Tracker'.padEnd(11)} ${'TPS'.padStart(5)} ${'Ping'.padStart(7)} ${'Sinal'.padStart(8)} ${'Bateria'.padStart(8)}`];
  for (const l of linhas) {
    const alerta = (l.tps !== null && l.tps < 50) || (l.ping !== null && l.ping > 50) ? '  <- ruim' : '';
    out.push(`  ${l.nome.padEnd(11)} ${cel(l.tps, 5)} ${cel(l.ping, 4)} ms ${cel(l.rssi, 4)} dBm ${cel(l.bateria, 6)} %${alerta}`);
  }
  return out.join('\n');
}

export class Diagnostics {
  constructor() {
    this.feedUpdates = 0;
    this.binaryFrames = 0;
    this.trackers = new Map();
  }

  frame() { this.binaryFrames += 1; }
  update() { this.feedUpdates += 1; }

  add(record) {
    const id = `${record.deviceId}:${record.trackerNum}`;
    let state = this.trackers.get(id);
    if (!state) {
      state = {
        deviceId: record.deviceId,
        trackerNum: record.trackerNum,
        bodyPartName: record.bodyPartName,
        displayName: record.displayName,
        firmwareVersion: record.firmwareVersion,
        hardwareIdentifier: record.hardwareIdentifier,
        rows: 0,
        accelerationRows: 0,
        rotationRows: 0,
        rawAccelerationRows: 0,
        rawGyroRows: 0,
        accelerationChanged: 0,
        rotationChanged: 0,
        intervalsMs: [],
        firstNs: null,
        lastNs: null,
        previousAcceleration: null,
        previousRotation: null,
        tps: [],
        ping: [],
        rssi: [],
        battery: null,
      };
      this.trackers.set(id, state);
    }

    state.rows += 1;
    state.bodyPartName = record.bodyPartName ?? state.bodyPartName;
    state.displayName = record.displayName ?? state.displayName;
    state.firmwareVersion = record.firmwareVersion ?? state.firmwareVersion;
    state.hardwareIdentifier = record.hardwareIdentifier ?? state.hardwareIdentifier;

    const now = BigInt(record.receivedMonotonicNs);
    if (state.firstNs === null) state.firstNs = now;
    if (state.lastNs !== null) {
      state.intervalsMs.push(Number(now - state.lastNs) / 1e6);
    }
    state.lastNs = now;

    if (record.acceleration) {
      state.accelerationRows += 1;
      if (state.previousAcceleration &&
          !sameVector(state.previousAcceleration, record.acceleration, ['x', 'y', 'z'])) {
        state.accelerationChanged += 1;
      }
      state.previousAcceleration = record.acceleration;
    }
    if (record.rotation) {
      state.rotationRows += 1;
      if (state.previousRotation &&
          !sameVector(state.previousRotation, record.rotation, ['x', 'y', 'z', 'w'])) {
        state.rotationChanged += 1;
      }
      state.previousRotation = record.rotation;
    }
    if (Number.isFinite(record.trackerTps)) state.tps.push(record.trackerTps);
    if (Number.isFinite(record.pingMs)) state.ping.push(record.pingMs);
    if (Number.isFinite(record.rssi)) state.rssi.push(record.rssi);
    if (Number.isFinite(record.batteryPct)) state.battery = record.batteryPct;
    if (record.rawAcceleration) state.rawAccelerationRows += 1;
    if (record.rawAngularVelocity) state.rawGyroRows += 1;
  }

  summary() {
    return {
      binaryFrames: this.binaryFrames,
      feedUpdates: this.feedUpdates,
      trackerCount: this.trackers.size,
      trackers: [...this.trackers.values()].map((state) => {
        const sorted = state.intervalsMs.toSorted((a, b) => a - b);
        const spanSeconds = state.firstNs === null || state.lastNs === null
          ? 0 : Number(state.lastNs - state.firstNs) / 1e9;
        return {
          deviceId: state.deviceId,
          trackerNum: state.trackerNum,
          bodyPartName: state.bodyPartName,
          displayName: state.displayName,
          firmwareVersion: state.firmwareVersion,
          hardwareIdentifier: state.hardwareIdentifier,
          rows: state.rows,
          accelerationRows: state.accelerationRows,
          rotationRows: state.rotationRows,
          rawAccelerationRows: state.rawAccelerationRows,
          rawGyroRows: state.rawGyroRows,
          accelerationChanged: state.accelerationChanged,
          rotationChanged: state.rotationChanged,
          spanSeconds: Math.round(spanSeconds * 1000) / 1000,
          rowRateHz: spanSeconds > 0 ? Math.round((state.rows - 1) / spanSeconds * 100) / 100 : null,
          tps: resumo(state.tps),
          pingMs: resumo(state.ping),
          rssiDbm: resumo(state.rssi),
          batteryPct: state.battery,
          intervalMs: {
            median: quantile(sorted, 0.5),
            p95: quantile(sorted, 0.95),
            maximum: sorted.length ? Math.round(sorted.at(-1) * 1000) / 1000 : null,
          },
        };
      }),
    };
  }
}
