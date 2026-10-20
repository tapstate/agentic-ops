/* 复用工位 Launcher 已有 mongodb 模块，只读检查副本集与监控事实。 */
const {MongoClient} = require('/driver/mongodb');
const net = require('net');
const http = require('http');
const https = require('https');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', data => { input += data; });
function memberReachable(member) {
  const match = member.match(/^(\[[^\]]+\]|[^:]+):(\d+)$/);
  if (!match) return Promise.resolve(false);
  return new Promise(resolve => {
    const socket = net.connect({host: match[1].replace(/^\[|\]$/g, ''), port: Number(match[2])});
    const end = value => {socket.destroy(); resolve(value);};
    socket.setTimeout(3000);
    socket.on('connect', () => end(true));
    socket.on('timeout', () => end(false));
    socket.on('error', () => end(false));
  });
}
function field(object, name) { return name.split('.').reduce((value, key) => value && value[key], object); }
function tmReachable(url) {
  return new Promise(resolve => {
    // 仅验证 HTTP 服务可达；401/403 不代表认证或业务验收通过。
    const transport = url.startsWith('https:') ? https : http;
    const request = transport.get(url + '/api/', {timeout: 5000}, response => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.on('timeout', () => {request.destroy(); resolve(false);});
    request.on('error', () => resolve(false));
  });
}
async function observation(db, node, spec, maxAge, engine = false) {
  const query = {[spec.uuid_field]: node.uuid};
  if (engine) query[spec.type_field] = spec.type_value;
  // 不读取无关业务字段；重复身份不能随便挑一条当成功。
  const projection = {[spec.time_field]: 1};
  if (engine) {
    projection[spec.deleted_field] = 1;
    projection[spec.stopped_field] = 1;
    projection.deleted = 1;
  }
  const records = await db.collection(spec.collection).find(query, {projection}).limit(2).toArray();
  const record = records.length === 1 ? records[0] : null;
  const raw = record && field(record, spec.time_field);
  const timestamp = raw instanceof Date ? raw.getTime() : Number(raw);
  const age = (Date.now() - timestamp) / 1000;
  const active = Boolean(record) && (!engine || (
    !field(record, spec.deleted_field) && !field(record, spec.stopped_field) && !record.deleted));
  return {registered: records.length === 1, timestamp,
    heartbeat_fresh: active && Boolean(raw) && Number.isFinite(timestamp) && age >= -5 && age <= maxAge};
}
process.stdin.on('end', async () => {
  let client;
  let stage = 'mongo_connect';
  try {
    const request = JSON.parse(input);
    client = new MongoClient(request.uri, {serverSelectionTimeoutMS: 10000, connectTimeoutMS: 5000});
    await client.connect();
    stage = 'mongo_topology';
    const hello = await client.db().command({hello: 1});
    const members = [...new Set([...(hello.hosts || []), ...(hello.passives || []), ...(hello.arbiters || [])])];
    if (!(await Promise.all(members.map(memberReachable))).every(Boolean)) throw new Error('member unreachable');
    const external = Boolean(request.external_tm_url);
    stage = 'tm_http';
    if (external && !await tmReachable(request.external_tm_url.replace(/\/$/, ''))) throw new Error('TM unreachable');
    stage = 'monitoring';
    const nodes = [];
    const inventory = request.nodes.length === 0 && !external ? {
      database: client.db().databaseName,
      collection_count: (await client.db().listCollections({}, {nameOnly: true}).toArray()).length
    } : {};
    if (external && request.nodes.length) {
      const sample = async () => {
        const values = [];
        for (const node of request.nodes) values.push({
          launcher: await observation(client.db(), node, request.heartbeat, request.heartbeat.max_age_seconds),
          engine: await observation(client.db(), node, request.fe_heartbeat, request.heartbeat.max_age_seconds, true)
        });
        return values;
      };
      const before = await sample();
      // Launcher 运行配置 reportInterval 为 20 秒；失败样本无需等待。
      if (before.every(row => row.launcher.heartbeat_fresh && row.engine.heartbeat_fresh)) {
        await new Promise(resolve => setTimeout(resolve, 21000));
        const after = await sample();
        for (let i = 0; i < request.nodes.length; i++) {
          const row = {node: request.nodes[i].name};
          for (const kind of ['launcher', 'engine']) row[kind] = {
            ...after[i][kind],
            heartbeat_advancing: after[i][kind].timestamp > before[i][kind].timestamp
          };
          row.registered = row.launcher.registered && row.engine.registered;
          row.heartbeat_fresh = ['launcher', 'engine'].every(kind =>
            row[kind].heartbeat_fresh && row[kind].heartbeat_advancing);
          nodes.push(row);
        }
      } else {
        for (let i = 0; i < request.nodes.length; i++) nodes.push({
          node: request.nodes[i].name, ...before[i], registered: false, heartbeat_fresh: false
        });
      }
    } else for (const node of request.nodes) {
      const record = await client.db().collection(request.heartbeat.collection).findOne({[request.heartbeat.uuid_field]: node.uuid});
      const timestamp = record && field(record, request.heartbeat.time_field);
      const numeric = timestamp instanceof Date ? timestamp.getTime() : Number(timestamp);
      const age = (Date.now() - numeric) / 1000;
      nodes.push({node: node.name, registered: Boolean(record), heartbeat_fresh: Boolean(timestamp) && age >= -5 && age <= request.heartbeat.max_age_seconds});
    }
    console.log(JSON.stringify({mongo_reachable: true, replica_members_reachable: true,
      ...(external ? {tm_http_reachable: true, authentication_verified: false, database_policy: 'preserve'} : {}),
      ...inventory, verified: nodes.length > 0 && nodes.every(node => node.registered && node.heartbeat_fresh), nodes}));
  } catch (error) {
    // 驱动异常可能含原始 URI 或用户信息。
    console.log(JSON.stringify({probe_error: error && error.code === 18 ? 'mongo_auth' : stage}));
    process.exitCode = 1;
  } finally {
    if (client) await client.close().catch(() => {});
  }
});
