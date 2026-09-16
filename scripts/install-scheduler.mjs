// Install (or remove) the background sync job: launchd on macOS, a systemd --user service on Linux.
// Usage: node scripts/install-scheduler.mjs [--uninstall] [--python /path/to/python3]
import { spawnSync } from 'node:child_process';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, platform } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = dirname(fileURLToPath(new URL('../package.json', import.meta.url)));
const args = process.argv.slice(2);
const uninstall = args.includes('--uninstall');
const pythonFlag = args[args.indexOf('--python') + 1];
const home = homedir();
const state = join(home, '.local', 'state', 'ai-guild');
const run = (command, argv, options = {}) => spawnSync(command, argv, { encoding: 'utf8', ...options });

function findPython() {
  if (args.includes('--python') && pythonFlag) return pythonFlag;
  for (const candidate of ['python3.13', 'python3.12', 'python3.11', 'python3']) {
    const which = run('sh', ['-c', `command -v ${candidate}`]);
    const path = which.stdout.trim();
    if (!path) continue;
    const version = run(path, ['-c', 'import sys;print(sys.version_info>=(3,11))']).stdout.trim();
    if (version === 'True') return path;
  }
  console.error('Python 3.11+ not found. Pass --python /path/to/python3.'); process.exit(1);
}

const python = uninstall ? '' : findPython();
const pathEnv = [dirname(python || '/usr/bin/python3'), join(home, '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin'].filter(Boolean).join(':');
const render = template => template.replaceAll('__PYTHON__', python).replaceAll('__REPO__', repo).replaceAll('__STATE__', state).replaceAll('__PATH__', pathEnv);

if (platform() === 'darwin') {
  const label = 'com.ai-guild.sync';
  const target = join(home, 'Library', 'LaunchAgents', `${label}.plist`);
  const domain = `gui/${run('id', ['-u']).stdout.trim()}`;
  if (uninstall) {
    run('launchctl', ['bootout', `${domain}/${label}`]);
    await rm(target, { force: true });
    console.log(`Removed ${target}`);
  } else {
    await mkdir(state, { recursive: true });
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, render(await readFile(join(repo, 'launchd', `${label}.plist.template`), 'utf8')));
    run('launchctl', ['bootout', `${domain}/${label}`]);
    const boot = run('launchctl', ['bootstrap', domain, target]);
    if (boot.status !== 0) { console.error(boot.stderr || 'launchctl bootstrap failed'); process.exit(1); }
    console.log(`Installed ${target}\nCheck: launchctl list | grep ${label}\nLogs:  ${state}/`);
  }
} else if (platform() === 'linux') {
  const unit = 'ai-guild-sync.service';
  const target = join(home, '.config', 'systemd', 'user', unit);
  if (uninstall) {
    run('systemctl', ['--user', 'disable', '--now', unit]);
    await rm(target, { force: true });
    run('systemctl', ['--user', 'daemon-reload']);
    console.log(`Removed ${target}`);
  } else {
    await mkdir(state, { recursive: true });
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, render(await readFile(join(repo, 'systemd', `${unit}.template`), 'utf8')));
    run('systemctl', ['--user', 'daemon-reload']);
    const enable = run('systemctl', ['--user', 'enable', '--now', unit]);
    if (enable.status !== 0) { console.error(enable.stderr || 'systemctl enable failed'); process.exit(1); }
    console.log(`Installed ${target}\nCheck: systemctl --user status ${unit}\nTip:   loginctl enable-linger $USER keeps it running after logout`);
  }
} else {
  console.error('Automatic install supports macOS (launchd) and Linux (systemd --user). Run `python3 collector/sync.py --run` in any process manager instead.');
  process.exit(2);
}
