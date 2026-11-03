#!/usr/bin/env node
// 建 MongoDB 副本集（rs0），供 aitoearn 服务端与 ai 端使用。
//
// 为什么不沿用上游的 mongosh：
//   这个仓库在这台机器上跑起来后，mongo 官方镜像里的 mongosh 一启动就崩
//   （`Check failed: VerifyChecksum(blob).` → v8::Isolate::Initialize → SIGTRAP，退出码 133），
//   而同一个容器里的 mongod 本身完全正常。也就是说探针/初始化脚本不能依赖那个二进制，
//   否则整个应用层被一个起不来的客户端拖着起不来。
//   这里改用 server 镜像里本来就装好的 mongodb 驱动跑，行为与 mongosh 版本等价。
//
// 幂等：已经初始化就直接等它升主；没初始化才 replSetInitiate。
// 退出码：0 = rs0 已经是我方 primary；1 = 超时；2 = 用法错误。
import { MongoClient } from 'mongodb';

const uri = process.env.MONGO_URI;
const RS = process.env.MONGO_RS || 'rs0';
const MEMBER_HOST = process.env.MONGO_MEMBER_HOST || 'mongodb:27017';
const WAIT_MS = Number(process.env.MONGO_WAIT_MS || 120000);

if (!uri) {
  console.error('mongo_rs_init: MONGO_URI 未设置');
  process.exit(2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const deadline = Date.now() + WAIT_MS;
const log = (...a) => console.log('mongo_rs_init:', ...a);

async function connect() {
  const client = new MongoClient(uri, { serverSelectionTimeoutMS: 5000, directConnection: true });
  await client.connect();
  return client;
}

async function waitForServer() {
  for (;;) {
    try {
      const c = await connect();
      await c.db('admin').command({ ping: 1 });
      return c;
    } catch (err) {
      if (Date.now() > deadline) throw err;
      await sleep(1000);
    }
  }
}

async function rsStatus(client) {
  try {
    return await client.db('admin').command({ replSetGetStatus: 1 });
  } catch (err) {
    // 副本集还没建过：code 94 = NotYetInitialized
    if (err && (err.code === 94 || err.codeName === 'NotYetInitialized')) return null;
    const msg = String(err && err.message);
    if (msg.includes('no replset config has been received') || msg.includes('not yet initialized')) return null;
    throw err;
  }
}

async function waitForPrimary(client) {
  for (;;) {
    const st = await rsStatus(client);
    if (st && st.myState === 1) return true;
    if (Date.now() > deadline) return false;
    await sleep(1000);
  }
}

const client = await waitForServer();
log(`mongod 已可达（${uri.replace(/\/\/[^@]*@/, '//***@')}）`);

if (await waitForPrimary(client)) {
  log(`已初始化且已是 primary（${RS}）`);
  await client.close();
  process.exit(0);
}

log('副本集尚未初始化，执行 replSetInitiate');
try {
  await client.db('admin').command({
    replSetInitiate: { _id: RS, members: [{ _id: 0, host: MEMBER_HOST }] },
  });
  log('replSetInitiate 已接受');
} catch (err) {
  if (err && (err.codeName === 'AlreadyInitialized' || err.code === 23)) {
    log('已经初始化过，跳过');
  } else {
    throw err;
  }
}

await client.close();

// initiate 之后连接会短暂断开，重新连一次等它升主
const c2 = await waitForServer();
if (await waitForPrimary(c2)) {
  log(`副本集就绪：primary 是我方成员（${RS}）`);
  await c2.close();
  process.exit(0);
}
console.error(`mongo_rs_init: ${WAIT_MS}ms 内没等到 primary`);
await c2.close();
process.exit(1);
