import {mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {build} from 'vite';
import {describe,it,expect} from 'vitest';
import {downloadGatePlugin} from '../scripts/download-gate-vite';

describe('download gate deployment artifact',()=>{
  it('publishes the classic root worker and the matching content version without public-directory copying',async()=>{
    const fixture=await mkdtemp(path.join(tmpdir(),'korea-download-gate-build-'));
    try{
      await writeFile(path.join(fixture,'index.html'),'<script type="module" src="/entry.js"></script>');
      await writeFile(path.join(fixture,'entry.js'),'document.body.dataset.version = __DOWNLOAD_GATE_VERSION__;');
      await build({configFile:false,root:fixture,logLevel:'silent',publicDir:false,plugins:[downloadGatePlugin()],build:{minify:false}});
      const script=await readFile(path.join(fixture,'dist','download-gate.js'),'utf8');
      const version=script.match(/const DOWNLOAD_GATE_VERSION = '([a-f0-9]{16})'/)?.[1];
      expect(version).toMatch(/^[a-f0-9]{16}$/);expect(script).not.toContain('__DOWNLOAD_GATE_VERSION__');
      const html=await readFile(path.join(fixture,'dist','index.html'),'utf8');
      const entry=html.match(/src="([^"]+\.js)"/)?.[1];expect(entry).toBeTruthy();
      expect(await readFile(path.join(fixture,'dist',entry!.slice(1)),'utf8')).toContain(version!);
    }finally{
      const resolved=path.resolve(fixture),base=path.resolve(tmpdir());
      expect(path.dirname(resolved)).toBe(base);expect(path.basename(resolved).startsWith('korea-download-gate-build-')).toBe(true);
      await rm(resolved,{recursive:true,force:true});
    }
  });
});
