import flatbuffers from 'flatbuffers';
import solarxr from 'solarxr-protocol';

const {
  Builder,
  ByteBuffer,
} = flatbuffers;

const {
  BodyPart,
  DataFeedConfigT,
  DataFeedMessage,
  DataFeedMessageHeaderT,
  DeviceDataMaskT,
  MessageBundle,
  MessageBundleT,
  PollDataFeedT,
  StartDataFeedT,
  TrackerDataMaskT,
} = solarxr;

export { BodyPart, DataFeedMessage };

export function makeFeedConfig(intervalMs = 0) {
  if (!Number.isInteger(intervalMs) || intervalMs < 0 || intervalMs > 65535) {
    throw new RangeError('intervalMs deve ser um inteiro entre 0 e 65535');
  }

  const trackerMask = new TrackerDataMaskT();
  trackerMask.info = true;
  trackerMask.status = true;
  trackerMask.rotation = true;
  trackerMask.linearAcceleration = true;
  trackerMask.rawAcceleration = true;
  trackerMask.rawAngularVelocity = true;
  trackerMask.tps = true;

  const deviceMask = new DeviceDataMaskT();
  deviceMask.deviceData = true;
  deviceMask.trackerData = trackerMask;

  const config = new DataFeedConfigT();
  config.minimumTimeSinceLast = intervalMs;
  config.dataMask = deviceMask;
  return config;
}

export function encodeDataFeedRequest(kind, intervalMs = 0) {
  const config = makeFeedConfig(intervalMs);
  const header = new DataFeedMessageHeaderT();
  const bundle = new MessageBundleT();

  if (kind === 'poll') {
    const poll = new PollDataFeedT();
    poll.config = config;
    header.messageType = DataFeedMessage.PollDataFeed;
    header.message = poll;
  } else if (kind === 'start') {
    const start = new StartDataFeedT();
    start.dataFeeds = [config];
    header.messageType = DataFeedMessage.StartDataFeed;
    header.message = start;
  } else {
    throw new Error(`Tipo de pedido desconhecido: ${kind}`);
  }

  bundle.dataFeedMsgs = [header];
  const builder = new Builder(1024);
  builder.finish(bundle.pack(builder));
  return builder.asUint8Array();
}

export function decodeDataFeedUpdates(bytes) {
  const raw = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const bundle = MessageBundle.getRootAsMessageBundle(new ByteBuffer(raw)).unpack();
  return bundle.dataFeedMsgs
    .filter((header) => header.messageType === DataFeedMessage.DataFeedUpdate)
    .map((header) => header.message);
}

export function flattenUpdate(update, received, frameSequence) {
  const records = [];
  for (const device of update.devices ?? []) {
    const deviceId = device.id?.id ?? null;
    for (const tracker of device.trackers ?? []) {
      const bodyPart = tracker.info?.bodyPart ?? null;
      records.push({
        receivedUnixMs: received.unixMs,
        receivedMonotonicNs: received.monotonicNs,
        frameSequence,
        feedIndex: update.index,
        deviceId,
        trackerNum: tracker.trackerId?.trackerNum ?? null,
        trackerDeviceId: tracker.trackerId?.deviceId?.id ?? null,
        bodyPart,
        bodyPartName: bodyPart === null ? null : (BodyPart[bodyPart] ?? null),
        displayName: tracker.info?.displayName ?? null,
        customName: tracker.info?.customName ?? null,
        imuType: tracker.info?.imuType ?? null,
        trackerStatus: tracker.status ?? null,
        trackerTps: tracker.tps ?? null,
        firmwareVersion: device.hardwareInfo?.firmwareVersion ?? null,
        hardwareIdentifier: device.hardwareInfo?.hardwareIdentifier ?? null,
        packetLoss: device.hardwareStatus?.packetLoss ?? null,
        acceleration: tracker.linearAcceleration ?? null,
        rotation: tracker.rotation ?? null,
        rawAcceleration: tracker.rawAcceleration ?? null,
        rawAngularVelocity: tracker.rawAngularVelocity ?? null,
      });
    }
  }
  return records;
}
