import {describe,expect,it} from 'vitest';
import {SceneFrameAudit} from '../shared/scene-frame-audit';
describe('scene frame audit',()=>{
  it('keeps loading stalls separate from warmed navigation and preserves long frames',()=>{
    const audit=new SceneFrameAudit();
    for(let i=0;i<30;i++)audit.record({at:i*350,sceneMs:270,renderMs:250,frameMs:350,moving:true,loading:true});
    for(let i=0;i<60;i++)audit.record({at:15000+i*16,sceneMs:10,renderMs:7,frameMs:16,moving:true,loading:false});
    expect(audit.snapshot().groups).toMatchObject({movingLoading:{count:30,sceneP95Ms:270,frameP95Ms:350},movingReady:{count:60,sceneP95Ms:10,frameP95Ms:16}});
  });
  it('does not treat minutes between requested renders as dropped navigation frames',()=>{
    const audit=new SceneFrameAudit();
    for(const at of [10,60000,180000])audit.record({at,sceneMs:6,renderMs:4,frameMs:null,moving:false,loading:false});
    expect(audit.snapshot().groups.settledReady).toEqual({count:3,sceneP95Ms:6,renderP95Ms:4,frameP95Ms:null});
    expect(audit.snapshot().groups.movingReady.count).toBe(0);
  });
  it('bounds memory while preserving monotonic sequence numbers for overlapping captures',()=>{
    const audit=new SceneFrameAudit(3);
    for(let at=0;at<5;at++)audit.record({at,sceneMs:at+1,renderMs:at,frameMs:null,moving:false,loading:true});
    const first=audit.snapshot();expect(first.count).toBe(5);expect(first.samples.map(s=>s.sequence)).toEqual([3,4,5]);
    first.samples[0].sceneMs=999;
    expect(audit.snapshot().samples[0].sceneMs).toBe(3);
  });
  it('rejects non-finite samples without clipping valid outliers',()=>{
    const audit=new SceneFrameAudit();
    const sample={at:1,sceneMs:999,renderMs:800,frameMs:1200,moving:true,loading:true};
    audit.record(sample);audit.record({...sample,at:NaN});audit.record({...sample,renderMs:Infinity});audit.record({...sample,frameMs:0});
    expect(audit.snapshot().count).toBe(1);expect(audit.snapshot().groups.movingLoading.frameP95Ms).toBe(1200);
  });
});
