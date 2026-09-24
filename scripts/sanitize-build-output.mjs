import { lstat, unlink } from 'node:fs/promises';
import path from 'node:path';

const project = path.resolve(process.cwd());
const target = path.resolve(project, 'dist/korea_replay/.dev.vars');
if (!target.startsWith(path.join(project, 'dist') + path.sep)) {
  throw new Error('Refusing to inspect a build path outside dist/.');
}

try {
  const info = await lstat(target);
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error('Refusing to remove a non-regular local environment artifact.');
  }
  await unlink(target);
  process.stdout.write('Removed local-only .dev.vars from the generated Worker bundle.\n');
} catch (error) {
  if (error?.code !== 'ENOENT') throw error;
}
