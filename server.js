const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { createClient } = require("redis");
const { WebSocketServer } = require("ws");

const port = process.env.PORT || 8080;
const pod = process.env.POD_NAME || os.hostname();
const podIp = process.env.POD_IP || "unknown";
const redisUrl = process.env.REDIS_URL;
const page = path.join(__dirname, "public", "index.html");
const scoreKey = "match:demo:score";
const scoreChannel = "match:demo:score-events";
let state = { a: 0, b: 0 };
let redis, pub, sub;

const recent = { cameraA: [], cameraB: [] };
const calibration = { cameraA: false, cameraB: false };
const rally = {
  active: false,
  lastHitter: null,
  lastConfirmedSide: null,
  crossedNet: false,
  bounceCounts: { A: 0, B: 0 },
  pointAwarded: false,
  lastBounceAt: 0,
};

// TUNE LATER: deliberately permissive thresholds for phone/court experiments.
const TH = { state: 0.65, kalman: 0.65, conf: 0.55, doubleMs: 450, historyMs: 2500 };

const server = http.createServer((req, res) => {
  if (req.url === "/healthz") return res.writeHead(200).end("ok");
  if (req.url === "/api/info") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ pod, podIp, redis: Boolean(redis) }));
  }
  if (req.url === "/" || req.url === "/index.html") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    return fs.createReadStream(page).pipe(res);
  }
  res.writeHead(404).end("Not found");
});

const wss = new WebSocketServer({ server });
const send = (ws, data) => ws.readyState === ws.OPEN && ws.send(JSON.stringify(data));
const broadcast = (data) => wss.clients.forEach((client) => send(client, data));
const otherPlayer = (p) => (p === "a" ? "b" : "a");
const sideForPlayer = (p) => (p === "a" ? "A" : "B");

async function getScore() {
  if (!redis) return state;
  const saved = await redis.get(scoreKey);
  state = saved ? JSON.parse(saved) : state;
  return state;
}

async function saveScore(next) {
  state = next;
  if (!redis) return broadcast({ type: "score", state, pod, podIp });
  await redis.set(scoreKey, JSON.stringify(state));
  await pub.publish(scoreChannel, JSON.stringify({ state, pod, podIp }));
}

function resetRally() {
  rally.active = false;
  rally.lastHitter = null;
  rally.lastConfirmedSide = null;
  rally.crossedNet = false;
  rally.bounceCounts = { A: 0, B: 0 };
  rally.pointAwarded = false;
  rally.lastBounceAt = 0;
  for (const id of Object.keys(recent)) recent[id] = [];
  broadcast({ type: "rally", rally });
}

async function awardPoint(player, reason, confidence = 1, source = "server") {
  if (rally.pointAwarded) return;
  rally.pointAwarded = true;
  const next = { ...(await getScore()) };
  next[player] += 1;
  await saveScore(next);
  broadcast({ type: "rallyEvent", event: reason, winner: player, confidence, source });
  setTimeout(resetRally, 800);
}

function keepRecent(cameraId, obs) {
  if (!recent[cameraId]) recent[cameraId] = [];
  recent[cameraId].push(obs);
  const cutoff = Date.now() - TH.historyMs;
  recent[cameraId] = recent[cameraId].filter((x) => x.captureTime >= cutoff).slice(-30);
}

function updateCrossing(obs) {
  const side = obs.court?.side;
  const conf = obs.ball?.confidence || 0;
  if (!["A", "B", "NET"].includes(side) || conf < TH.conf) return;
  if (side === "A" || side === "B") rally.lastConfirmedSide = side;
  if (!rally.lastHitter) return;
  const from = sideForPlayer(rally.lastHitter);
  const to = from === "A" ? "B" : "A";
  if (side === to) rally.crossedNet = true;
}

function localPipelinesAgree(obs) {
  if (obs.force) return true;
  const m = obs.metrics || {};
  return (m.stateChangeScore || 0) >= TH.state && (m.kalmanEventScore || 0) >= TH.kalman;
}

async function handleBounce(obs) {
  if (!localPipelinesAgree(obs) || (obs.ball?.confidence || 0) < TH.conf) return;
  const side = obs.court?.side;
  const inside = obs.court?.inside;
  const now = obs.captureTime || Date.now();
  if (!["A", "B"].includes(side)) return;

  if (inside === false && rally.lastHitter) {
    return awardPoint(otherPlayer(rally.lastHitter), "OUT", obs.ball.confidence, obs.cameraId);
  }
  if (inside !== true || now - rally.lastBounceAt < TH.doubleMs) return;

  rally.lastBounceAt = now;
  rally.bounceCounts[side] = (rally.bounceCounts[side] || 0) + 1;
  broadcast({ type: "rallyEvent", event: "BOUNCE", side, confidence: obs.ball.confidence, source: obs.cameraId });
  if (rally.bounceCounts[side] >= 2) {
    await awardPoint(side === "A" ? "b" : "a", "DOUBLE_BOUNCE", obs.ball.confidence, obs.cameraId);
  }
}

async function handleNetFault(obs) {
  if (!rally.lastHitter || rally.crossedNet || !localPipelinesAgree(obs)) return;
  const hitterSide = sideForPlayer(rally.lastHitter);
  const side = obs.court?.side;
  const nearNet = side === "NET" || obs.court?.nearNet;
  const returnedOrStayed = side === hitterSide || rally.lastConfirmedSide === hitterSide;
  if (nearNet && returnedOrStayed) {
    await awardPoint(otherPlayer(rally.lastHitter), "NET_FAULT", obs.ball?.confidence || 0.7, obs.cameraId);
  }
}

async function handleObservation(obs) {
  if (!obs.cameraId || !obs.captureTime) return;
  keepRecent(obs.cameraId, obs);
  updateCrossing(obs);
  broadcast({ type: "observationAck", cameraId: obs.cameraId, side: obs.court?.side, candidate: obs.candidate });
  if (obs.candidate === "bounce") await handleBounce(obs);
  if (obs.candidate === "net") await handleNetFault(obs);
}

async function handleMessage(ws, raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  if (msg.type === "ping") return send(ws, { type: "pong", id: msg.id });
  if (msg.type === "registerCamera") return send(ws, { type: "registered", cameraId: msg.cameraId, pod, podIp });
  if (msg.type === "calibrationStatus") {
    calibration[msg.cameraId] = Boolean(msg.valid);
    return broadcast({ type: "calibrationStatus", cameraId: msg.cameraId, valid: calibration[msg.cameraId] });
  }
  if (msg.type === "hit") {
    rally.active = true;
    rally.lastHitter = msg.player;
    rally.crossedNet = false;
    rally.bounceCounts = { A: 0, B: 0 };
    return broadcast({ type: "rally", rally });
  }
  if (msg.type === "newRally") return resetRally();
  if (msg.type === "observation") return handleObservation(msg);
  if (msg.type === "eventCandidate") return handleObservation({ ...msg, force: true });

  const next = { ...(await getScore()) };
  if (msg.type === "inc" && msg.player === "a") next.a += 1;
  if (msg.type === "inc" && msg.player === "b") next.b += 1;
  if (msg.type === "reset") {
    next.a = 0;
    next.b = 0;
    resetRally();
  }
  if (["inc", "reset"].includes(msg.type)) await saveScore(next);
}

wss.on("connection", async (ws) => {
  send(ws, { type: "score", state: await getScore(), pod, podIp });
  send(ws, { type: "rally", rally });
  broadcast({ type: "clients", count: wss.clients.size });
  ws.on("message", (raw) => handleMessage(ws, raw).catch(console.error));
  ws.on("close", () => broadcast({ type: "clients", count: wss.clients.size }));
});

async function main() {
  if (redisUrl) {
    redis = createClient({ url: redisUrl });
    pub = redis.duplicate();
    sub = redis.duplicate();
    await Promise.all([redis.connect(), pub.connect(), sub.connect()]);
    await redis.set(scoreKey, JSON.stringify(state), { NX: true });
    await sub.subscribe(scoreChannel, (raw) => {
      const event = JSON.parse(raw);
      state = event.state;
      broadcast({ type: "score", state, pod: event.pod, podIp: event.podIp });
    });
    console.log(`Connected to Redis at ${redisUrl}`);
  }
  server.listen(port, () => console.log(`Listening on ${port}`));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
