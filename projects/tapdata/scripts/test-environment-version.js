/* 从当前源码取得版本，使用目标 Launcher 自有编码协议，不伪造发布版本。 */
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const [launcher, core, output] = process.argv.slice(2);
if (!launcher || !core || !output) throw new Error('需要 Launcher、核心源码和输出文件的绝对路径');
if (![launcher, core, output].every(path.isAbsolute)) throw new Error('路径必须为绝对路径');
const crypto = require(path.join(launcher, 'node_modules/crypto-js'));
const basic = require(path.join(launcher, 'basic'));
const version = cp.execFileSync('git', ['-C', core, 'describe', '--always', '--tags', '--dirty'], {encoding: 'utf8'}).trim();
fs.mkdirSync(path.dirname(output), {recursive: true});
fs.writeFileSync(output, crypto.RC4.encrypt(JSON.stringify({app_version: version}), basic.getKey()).toString(), {mode: 0o600});
