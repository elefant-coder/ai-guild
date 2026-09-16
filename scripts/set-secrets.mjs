// Interactive: sets the three Worker secrets and writes the collector's sync config.
// Usage: npm run secrets [-- --endpoint https://ai-guild.example.workers.dev]
import { randomBytes } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { chmod, mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline/promises';
import { stdin, stdout } from 'node:process';

const args = process.argv.slice(2);
const flag = name => { const index = args.indexOf(name); return index >= 0 ? args[index + 1] : undefined; };
const rl = createInterface({ input: stdin, output: stdout });
const ask = async (question, hidden = false) => {
  if (!hidden) return (await rl.question(question)).trim();
  stdout.write(question);
  const wasRaw = stdin.isRaw; stdin.setRawMode?.(true);
  let value = '';
  const result = await new Promise(resolve => {
    const onData = chunk => {
      for (const char of chunk.toString()) {
        if (char === '\n' || char === '\r') { stdin.off('data', onData); stdin.setRawMode?.(wasRaw ?? false); stdout.write('\n'); resolve(value); return; }
        if (char === '') process.exit(130);
        if (char === '') value = value.slice(0, -1); else value += char;
      }
    };
    stdin.on('data', onData);
  });
  return result.trim();
};

function wrangler(...command) {
  const run = spawnSync('npx', ['wrangler', ...command], { stdio: ['pipe', 'inherit', 'inherit'], encoding: 'utf8' });
  return run.status === 0;
}
function putSecret(name, value) {
  const run = spawnSync('npx', ['wrangler', 'secret', 'put', name], { input: value + '\n', stdio: ['pipe', 'inherit', 'inherit'], encoding: 'utf8' });
  if (run.status !== 0) { console.error(`Failed to set ${name}. Is wrangler logged in (npx wrangler login) and is the Worker deployed?`); process.exit(1); }
}

console.log('AI Guild secrets\n');
let endpoint = flag('--endpoint');
if (!endpoint) {
  const name = JSON.parse((await readFile(new URL('../wrangler.jsonc', import.meta.url), 'utf8')).replace(/\/\/.*$/gm, '')).name;
  const subdomain = spawnSync('npx', ['wrangler', 'whoami'], { encoding: 'utf8' }).stdout.match(/([a-z0-9-]+)\.workers\.dev/)?.[1];
  const guess = subdomain ? `https://${name}.${subdomain}.workers.dev` : '';
  endpoint = await ask(`Site URL${guess ? ` [${guess}]` : ''}: `) || guess;
}
endpoint = endpoint.replace(/\/+$/, '');
if (!/^https:\/\/[^/\s]+$/.test(endpoint)) { console.error('Need an https:// origin without a path.'); process.exit(1); }

let passphrase = '';
while (passphrase.length < 8) {
  passphrase = await ask('Passphrase to open the site (8+ characters, hidden): ', true);
  if (passphrase.length < 8) console.log('Too short.');
  else if (passphrase !== await ask('Repeat it: ', true)) { console.log('Did not match.'); passphrase = ''; }
}
rl.close();

const session = randomBytes(32).toString('base64url');
const ingest = randomBytes(32).toString('base64url');
console.log('\nSetting Worker secrets…');
putSecret('GUILD_VIEW_PASSWORD', passphrase);
putSecret('GUILD_SESSION_SECRET', session);
putSecret('GUILD_INGEST_SECRET', ingest);

const dir = join(homedir(), '.config', 'ai-guild');
await mkdir(dir, { recursive: true, mode: 0o700 });
const secretFile = join(dir, 'ingest.secret');
await writeFile(secretFile, ingest + '\n', { mode: 0o600 });
await chmod(secretFile, 0o600);
const syncFile = join(dir, 'sync.json');
const existing = existsSync(syncFile) ? JSON.parse(await readFile(syncFile, 'utf8')) : {};
await writeFile(syncFile, JSON.stringify({ ...existing, endpoint: `${endpoint}/api/inventory`, ingestSecretFile: '~/.config/ai-guild/ingest.secret' }, null, 2) + '\n', { mode: 0o600 });
console.log(`\nDone.\n  site:      ${endpoint}\n  collector: ${syncFile}\n\nNext: python3 collector/sync.py --check && python3 collector/sync.py --once`);
