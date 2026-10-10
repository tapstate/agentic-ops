/* 复用工位 Launcher 已有 mongodb 模块，只读检查副本集与监控事实。 */
const {MongoClient} = require('/driver/mongodb');
const net = require('net');
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
process.stdin.on('end', async () => {
  let client;
  try {
    const request = JSON.parse(input);
    client = new MongoClient(request.uri, {serverSelectionTimeoutMS: 10000, connectTimeoutMS: 5000});
    await client.connect();
    const hello = await client.db().command({hello: 1});
    const members = [...new Set([...(hello.hosts || []), ...(hello.passives || []), ...(hello.arbiters || [])])];
    if (!(await Promise.all(members.map(memberReachable))).every(Boolean)) throw new Error('member unreachable');
    const nodes = [];
    const inventory = request.nodes.length === 0 ? {
      database: client.db().databaseName,
      collection_count: (await client.db().listCollections({}, {nameOnly: true}).toArray()).length
    } : {};
    for (const node of request.nodes) {
      const record = await client.db().collection(request.heartbeat.collection).findOne({[request.heartbeat.uuid_field]: node.uuid});
      const timestamp = record && field(record, request.heartbeat.time_field);
      const numeric = timestamp instanceof Date ? timestamp.getTime() : Number(timestamp);
      const age = (Date.now() - numeric) / 1000;
      nodes.push({node: node.name, registered: Boolean(record), heartbeat_fresh: Boolean(timestamp) && age >= -5 && age <= request.heartbeat.max_age_seconds});
    }
    console.log(JSON.stringify({mongo_reachable: true, replica_members_reachable: true, ...inventory, verified: nodes.length > 0 && nodes.every(node => node.registered && node.heartbeat_fresh), nodes}));
  } catch (_) {
    // 驱动异常可能含原始 URI 或用户信息。
    process.exitCode = 1;
  } finally {
    if (client) await client.close().catch(() => {});
  }
});
