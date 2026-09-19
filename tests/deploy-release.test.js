import {describe,expect,it,vi} from 'vitest';
import path from 'node:path';
import {deployRelease,parseDeploymentOptions} from '../scripts/deploy-release.mjs';

const configPath=path.resolve('fixture-bundle/wrangler.json');
const config={name:'korea-replay',workers_dev:true,preview_urls:true,vars:{STATIC_RELEASE_ID:'pub-0123456789abcdef'},version_metadata:{binding:'CF_VERSION_METADATA'}};
function fixture(result={code:1,output:'Worker not found [code: 10007]'}){
  return {verify:vi.fn(async()=>({receipt:{bundle_id:'fixture',release_id:config.vars.STATIC_RELEASE_ID},configPath})),
    read:vi.fn(async()=>JSON.stringify(config)),write:vi.fn(async()=>{}),run:vi.fn().mockResolvedValueOnce(result).mockResolvedValue({code:0,output:''})};
}

describe('first Worker initialization guard',()=>{
  it('checks the same configuration and enables only the version preview on first deploy',async()=>{
    const f=fixture();expect(await deployRelease({...f,initialize:true})).toBe(0);
    expect(f.run.mock.calls[0]).toEqual([['deployments','list','--name','korea-replay','--config',configPath,'--json'],{capture:true}]);
    const previewPath=path.join(path.dirname(configPath),'wrangler.preview.json');
    expect(f.write.mock.calls[0][0]).toBe(previewPath);
    expect(JSON.parse(f.write.mock.calls[0][1])).toEqual({...config,workers_dev:false,preview_urls:true});
    expect(f.run.mock.calls[1][0]).toEqual(['deploy','--config',previewPath]);
  });
  it.each([{code:0,output:'[]'},{code:1,output:'Unauthorized [code: 10000]'},{code:null,output:'[code: 10007]'}])('leaves an existing or unverified Worker unchanged: %j',async result=>{
    const f=fixture(result);await expect(deployRelease({...f,initialize:true})).rejects.toThrow('confirmed missing');
    expect(f.write).not.toHaveBeenCalled();expect(f.run).toHaveBeenCalledTimes(1);
  });
  it('uses version upload for subsequent releases without touching routes or public traffic',async()=>{
    const f=fixture({code:0,output:''});expect(await deployRelease(f)).toBe(0);
    expect(f.run.mock.calls[0][0]).toEqual(['versions','upload','--config',configPath,'--message','Verified static bundle fixture; pub-0123456789abcdef']);
    expect(f.run).toHaveBeenCalledTimes(1);expect(f.write).not.toHaveBeenCalled();expect(f.read).not.toHaveBeenCalled();
  });
  it('uploads a broker-mode map release without creating either Worker',async()=>{
    const f=fixture({code:0,output:''});
    f.verify.mockResolvedValue({receipt:{bundle_id:'broker-fixture',release_id:config.vars.STATIC_RELEASE_ID,deployment_contract:{schema_version:1,live_transit_mode:'broker'}},configPath});
    expect(await deployRelease(f)).toBe(0);
    expect(f.run.mock.calls[0][0].slice(0,4)).toEqual(['versions','upload','--config',configPath]);
    expect(f.run).toHaveBeenCalledTimes(1);expect(f.write).not.toHaveBeenCalled();
  });
  it('rejects initialization of a broker-mode map release before any Cloudflare call',async()=>{
    const f=fixture();
    f.verify.mockResolvedValue({receipt:{deployment_contract:{schema_version:1,live_transit_mode:'broker'}},configPath});
    await expect(deployRelease({...f,initialize:true})).rejects.toThrow('separate deployment');
    expect(f.run).not.toHaveBeenCalled();expect(f.write).not.toHaveBeenCalled();expect(f.read).not.toHaveBeenCalled();
  });
  it.each([['--env','preview'],['--config','wrangler.live-broker.jsonc'],['--var','LIVE_TRANSIT_MODE:direct'],['--initialize','--initialize'],['--broker-initialize']])('rejects CLI config and mode overrides: %j',(...args)=>{
    expect(()=>parseDeploymentOptions(args)).toThrow('overrides are not accepted');
  });
  it('accepts only the supported legacy initialization option or normal upload',()=>{
    expect(parseDeploymentOptions([])).toEqual({initialize:false});
    expect(parseDeploymentOptions(['--initialize'])).toEqual({initialize:true});
  });
});
