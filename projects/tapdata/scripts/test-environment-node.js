/* 在目标发行包已有 Node 上执行；不安装第三方组件。 */
const fs = require('fs');
const cp = require('child_process');
const net = require('net');
const path = require('path');
const settings = JSON.parse(fs.readFileSync('/tapdata/settings.json', 'utf8'));
function processes() {
  return cp.execFileSync('ps', ['-eo', 'pid,args'], {encoding: 'utf8'}).split('\n').map(line => {
    const match = line.trim().match(/^(\d+)\s+(.*)$/);
    return match ? {pid: Number(match[1]), args: match[2]} : null;
  }).filter(Boolean).filter(row => row.pid !== process.pid && row.args.includes('/tapdata/'));
}
function components(rows) {
  return {
    launcher: rows.some(row => /tapdata-agent\s+agent(?:\s|$)/.test(row.args)),
    tm: rows.some(row => /(?:^|[ /])tm\.jar(?:\s|$)/.test(row.args)),
    engine: rows.some(row => /(?:^|[ /])tapdata-agent\.jar(?:\s|$)/.test(row.args)),
    api: rows.some(row => /(?:^|[ /])app\.js(?:\s|$)|apiserver[^ ]*\.jar|com\.tapdata\.apiserver/.test(row.args))
  };
}
function tcp(port) {
  return new Promise(resolve => {
    const socket = net.connect({host: '127.0.0.1', port});
    const finish = value => { socket.destroy(); resolve(value); };
    socket.setTimeout(2000);
    socket.on('connect', () => finish(true));
    socket.on('timeout', () => finish(false));
    socket.on('error', () => finish(false));
  });
}
function yaml(object, indent = 0) {
  return Object.entries(object).map(([key, value]) => {
    const prefix = `${' '.repeat(indent)}${key}:`;
    return value && typeof value === 'object'
      ? `${prefix}\n${yaml(value, indent + 2)}`
      : `${prefix} ${JSON.stringify(value)}\n`;
  }).join('');
}
async function main() {
  if (process.argv[2] === 'configure') {
    const uri = fs.readFileSync('/secret/mongo-uri', 'utf8').trim();
    const config = {
      spring: {data: {mongodb: {username: '', password: '', mongoConnectionString: uri, uri: '', ssl: 'false', authenticationDatabase: ''}}},
      tapdata: {cloud: {accessCode: '', retryTime: '3', baseURLs: ''}, mode: 'cluster', conf: {
        tapdataPort: '3030', backendUrl: settings.backend_url, apiServerPort: '3080', apiWorkerMaxMemory: '512', apiServerErrorCode: 'false',
        mongodbDeployType: 'self-build', mongodbPort: 27017, tapdataJavaOpts: settings.engine_opts, tapdataTMJavaOpts: settings.tm_opts,
        SCRIPT_DIR: 'etc', reportInterval: 20000, Decimal128ToNumber: 'false', uuid: settings.uuid
      }}
    };
    // Launcher 的 yamljs 不接受 JSON 文档；标量以 JSON 转义生成 YAML 双引号值。
    for (const root of ['/tapdata/apps', '/tapdata/work']) {
      fs.mkdirSync(path.join(root, 'etc'), {recursive: true});
      for (const name of ['application.yml', 'etc/application.yml']) {
        fs.writeFileSync(path.join(root, name), yaml(config), {mode: 0o600});
      }
    }
    // 目标 Launcher 的监控进程从 WORK_DIR/uuid.js 读取节点身份。
    fs.writeFileSync('/tapdata/work/uuid.js', `module.exports = ${JSON.stringify(settings.uuid)};\n`, {mode: 0o600});
    return;
  }
  if (process.argv[2] === 'stop') {
    const rows = processes().filter(row => /tapdata-agent\s+agent|tm\.jar|tapdata-agent\.jar|app\.js|apiserver[^ ]*\.jar|com\.tapdata\.apiserver/.test(row.args));
    for (const row of rows) { try { process.kill(row.pid, 'SIGTERM'); } catch (_) {} }
    for (let i = 0; i < 30; i++) {
      if (!rows.some(row => { try { process.kill(row.pid, 0); return true; } catch (_) { return false; } })) return;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    process.exitCode = 1;
    return;
  }
  const state = components(processes());
  if (settings.components.includes('TM')) state.tm_port = await tcp(3030);
  if (settings.components.includes('APIServer')) state.api_port = await tcp(3080);
  const healthFile = `/tapdata/health-${process.pid}.json`;
  fs.writeFileSync(healthFile, JSON.stringify({time: Date.now(), components: state}), {mode: 0o600});
  fs.renameSync(healthFile, '/tapdata/health.json');
  const expected = {TM: 'tm', FE: 'engine', APIServer: 'api'};
  const selected = settings.components.map(component => expected[component]);
  if (!state.launcher || selected.some(name => !state[name]) ||
      Object.values(expected).some(name => !selected.includes(name) && state[name]) ||
      (settings.components.includes('TM') && !state.tm_port) ||
      (settings.components.includes('APIServer') && !state.api_port)) process.exitCode = 1;
}
main().catch(() => { process.exitCode = 1; });
