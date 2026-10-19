// LogEatsの実routeハンドラーと偽DBを使う契約結合試験。外部通信は常に拒否する。
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import readline from 'node:readline';
import net from 'node:net';
import http from 'node:http';
import https from 'node:https';

const [root, imagePath, pngPath] = process.argv.slice(2);
if (!root || !imagePath || !pngPath) throw new Error('bridge_arguments_required');
let networkAttempts = 0;
const deny = () => { networkAttempts++; throw new Error('bridge_external_network_forbidden'); };
globalThis.fetch = deny as typeof fetch;
net.Socket.prototype.connect = deny as typeof net.Socket.prototype.connect;
http.request = deny as typeof http.request;
https.request = deny as typeof https.request;
console.log = console.warn = console.error = () => {};
const require = createRequire(path.join(root, 'package.json'));
const { NextRequest } = require('next/server');
const load = async (relative: string) => import(pathToFileURL(path.join(root, relative)).href);
const { GET: stateGET } = await load('src/app/api/internal/mina/state/route.ts');
const { GET: requestGET } = await load('src/app/api/internal/mina/requests/[uuid]/route.ts');
const { POST: mealsPOST, PATCH: mealsPATCH } = await load('src/app/api/internal/mina/meals/route.ts');
const { POST: dailyPOST } = await load('src/app/api/internal/mina/daily-share/route.ts');
const { setMinaM1DepsForTests } = await load('src/lib/minaM1/deps.ts');
const { FakeMinaStore } = await load('scripts/lib/mina-m1-fake-store.ts');
const token = 'm'.repeat(40);
const settings = { target_calories: 1900, target_protein: 90, target_fat: 60, target_carbs: 240,
  daily_calorie_target: null, tolerance_pct: 10, share_og_image_enabled: true };
const store = new FakeMinaStore({ users: [{ id: 7, email: 'mina@example.test', settings }] });
const assets = new Map<string, ArrayBuffer>();
const png = fs.readFileSync(pngPath);
const pngBytes = () => Uint8Array.from(png).buffer;
const sha = (value: Uint8Array) => crypto.createHash('sha256').update(value).digest('hex');
let failRender = false;
let corrupt = false;
const calls = { analyze: 0, image: 0, render: 0, requests: 0 };
setMinaM1DepsForTests({ store, env: () => ({ token, accountEmail: 'mina@example.test', analysisAvailable: true, imageStorageAvailable: true }),
  claimQuota: async () => true,
  analyzeImage: async () => { calls.analyze++; return { foods: [{ name: 'ご飯と魚', nameJa: 'ご飯と魚', amount: '1人前', calories: 600, protein: 30, fat: 20, carbs: 75 }], isAmbiguous: false }; },
  storeImage: async (bytes: ArrayBuffer) => { calls.image++; return { url: `https://store.public.blob.vercel-storage.com/meals/synthetic_${calls.image}.webp`, storedSha256: sha(Buffer.concat([Buffer.from('RIFFWEBP'), Buffer.from(bytes)])) }; },
  deleteImage: async () => {},
  ogpIo: () => ({ renderPng: async () => { calls.render++; if (failRender) throw new Error('synthetic_render_failure'); return pngBytes(); },
    putAsset: async (key: string, bytes: ArrayBuffer) => { assets.set(key, bytes); },
    getAssetBytes: async (key: string) => assets.get(key) ?? null,
    deleteAsset: async (key: string) => { assets.delete(key); } }),
  now: () => new Date('2026-10-07T11:00:00.000Z'),
});

async function state() {
  const response = await stateGET(new NextRequest('https://www.log-eats.com/api/internal/mina/state?date=2026-10-08', { headers: { Authorization: `Bearer ${token}` } }));
  if (!response.ok) throw new Error('bridge_state_failed');
  return response.json();
}
function escaped(value: string) { return value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;'); }
async function request(message: any) {
  calls.requests++;
  const url = new URL(message.url);
  if (url.protocol !== 'https:' || url.hostname !== 'www.log-eats.com') throw new Error('bridge_host_forbidden');
  const before = store.counters.writeCount;
  const options: any = { method: message.method, headers: message.headers };
  if (message.body !== null) options.body = Buffer.from(message.body, 'base64');
  const req = new NextRequest(url, options);
  let response: Response;
  if (url.pathname === '/api/internal/mina/state' && message.method === 'GET') response = await stateGET(req);
  else if (url.pathname.startsWith('/api/internal/mina/requests/') && message.method === 'GET') response = await requestGET(req, { params: Promise.resolve({ uuid: url.pathname.split('/').at(-1) }) });
  else if (url.pathname === '/api/internal/mina/meals' && message.method === 'POST') response = await mealsPOST(req);
  else if (url.pathname === '/api/internal/mina/meals' && message.method === 'PATCH') response = await mealsPATCH(req);
  else if (url.pathname === '/api/internal/mina/daily-share' && message.method === 'POST') response = await dailyPOST(req);
  else if (message.method === 'GET' && url.pathname.startsWith('/s/')) {
    // 公開HTMLは合成する。API正本とOGPバイトは実ハンドラーの結果・保存物を使う。
    const body = await state();
    const result = [...body.meals, body.daily_share].filter(Boolean).find((item: any) => item.share?.url === url.href);
    if (!result || result.ogp.status !== 'ready') throw new Error('bridge_public_result_missing');
    const html = `<meta property="og:title" content="食事の解析結果"><meta property="og:description" content="${escaped(result.x.fixed_text)}"><meta property="og:image" content="${escaped(result.ogp.image_url)}">`;
    response = new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  } else if (message.method === 'GET' && url.pathname.endsWith('/og-image')) {
    const body = await state();
    const result = [...body.meals, body.daily_share].filter(Boolean).find((item: any) => item.ogp?.image_url === url.href);
    if (!result) throw new Error('bridge_ogp_missing');
    const asset = store.state.assets.find((item: any) => item.revision === result.ogp.revision);
    const bytes = asset && assets.get(asset.object_key);
    if (!bytes) throw new Error('bridge_ogp_bytes_missing');
    response = new Response(corrupt ? Uint8Array.from(Buffer.from('corrupt')).buffer : bytes, { headers: { 'Content-Type': 'image/png' } });
  } else throw new Error('bridge_route_forbidden');
  if (message.method === 'GET' && store.counters.writeCount !== before) throw new Error('bridge_get_wrote_state');
  return { status: response.status, headers: Object.fromEntries(response.headers), body: Buffer.from(await response.arrayBuffer()).toString('base64') };
}
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  try {
    const message = JSON.parse(line);
    let result: unknown;
    if (message.op === 'request') result = await request(message);
    else if (message.op === 'stats') result = { ...calls, ...store.counters, network_attempts: networkAttempts };
    else if (message.op === 'fail-render') { failRender = true; result = { ok: true }; }
    else if (message.op === 'corrupt-ogp') { corrupt = true; result = { ok: true }; }
    else if (message.op === 'change-daily-comment') { store.state.dailyShares[0].mina_comment = '変更後の一言'; result = { ok: true }; }
    else throw new Error('bridge_operation_forbidden');
    process.stdout.write(JSON.stringify({ ok: true, result }) + '\n');
  } catch {
    process.stdout.write(JSON.stringify({ ok: false, error: 'bridge_operation_failed' }) + '\n');
  }
}
