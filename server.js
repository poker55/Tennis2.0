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
let state = { a: 0, b: 0 };
let redis, pub, sub;
const page = path.join(__dirname, "public", "index.html");
const scoreKey = "match:demo:score";
const scoreChannel = "match:demo:score-events";

const server = http.createServer((req, res) => {
  if (req.url === "/healthz") return res.writeHead(200).end("ok");
  if (req.url === "/api/info") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ pod, podIp, redis: Boolean(redis) }));
  }
  if (req.url === "/" || req.url === "/index.html") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    fs.createReadStream(page).pipe(res);
    return;
  }
  res.writeHead(404).end("Not found");
});

const wss = new WebSocketServer({ server });

function send(ws, data) {
  if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(data));
}

function broadcast(data) {
  for (const client of wss.clients) send(client, data);
}

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

wss.on("connection", async (ws) => {
  send(ws, { type: "score", state: await getScore(), pod, podIp });
  broadcast({ type: "clients", count: wss.clients.size });

  ws.on("message", async (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }

    if (msg.type === "ping") return send(ws, { type: "pong", id: msg.id });

    const next = { ...(await getScore()) };
    if (msg.type === "inc" && msg.player === "a") next.a += 1;
    if (msg.type === "inc" && msg.player === "b") next.b += 1;
    if (msg.type === "reset") {
      next.a = 0;
      next.b = 0;
    }
    await saveScore(next);
  });

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
