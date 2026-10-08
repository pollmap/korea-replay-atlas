import {describe,expect,it} from 'vitest';
import {parsePropertyRelationGraph,type PropertyRelationGraph} from '../shared/property-relations';
const release='property-ceeff63959643461',root='complex:a',evidenceId=`evidence:${'a'.repeat(64)}`;
function graph():PropertyRelationGraph{return {schema_version:1,release_id:release,root_id:root,as_of:'2026-10-08',truncated:false,
  nodes:[{id:root,kind:'complex',label:'단지',attributes:{},evidence_ids:[evidenceId]}],edges:[],
  evidence:[{id:evidenceId,source_id:'fixture',source_url:'https://example.org/source',source_sha256:'a'.repeat(64),
    record_id:'1',method:'official_id',observed_on:'2026-09-01',valid_from:null,valid_to:null,verification:'verified',claim_type:'source'}]};}
describe('property relation contract',()=>{
  it('pins release, root, and evidence while allowing valid empty results',()=>{
    expect(parsePropertyRelationGraph(graph(),release,root).nodes).toHaveLength(1);
    expect(parsePropertyRelationGraph({...graph(),nodes:[],evidence:[]},release,root).nodes).toEqual([]);
    expect(()=>parsePropertyRelationGraph(graph(),'property-0000000000000000',root)).toThrow();
    expect(()=>parsePropertyRelationGraph(graph(),release,'complex:b')).toThrow();
  });
  it('rejects dangling references and duplicated IDs',()=>{
    const value=graph();value.nodes[0].evidence_ids=['missing'];expect(()=>parsePropertyRelationGraph(value,release,root)).toThrow();
    const duplicate=graph();duplicate.nodes.push(duplicate.nodes[0]);expect(()=>parsePropertyRelationGraph(duplicate,release,root)).toThrow();
    const broken=graph();broken.edges=[{id:`relation:${'b'.repeat(64)}`,subject:root,predicate:'in_region',object:'region:missing',evidence_id:evidenceId}];
    expect(()=>parsePropertyRelationGraph(broken,release,root)).toThrow();
  });
  it('rejects future or expired facts and unverified identity promotion',()=>{
    for(const change of [{observed_on:'2026-11-01'},{valid_to:'2026-09-30'},{verification:'unverified'},{claim_type:'inferred'}]){
      const value=graph();Object.assign(value.evidence[0],change);expect(()=>parsePropertyRelationGraph(value,release,root)).toThrow();
    }
  });
  it('preserves explicitly inferred relations and calculated facts',()=>{
    const value=graph();Object.assign(value.evidence[0],{verification:'inferred',claim_type:'inferred'});
    expect(parsePropertyRelationGraph(value,release,root).evidence[0].verification).toBe('inferred');
    value.evidence[0].verification='verified';value.evidence[0].claim_type='calculated';
    expect(parsePropertyRelationGraph(value,release,root).evidence[0].claim_type).toBe('calculated');
  });
  it('rejects graph bounds before rendering',()=>{
    const value=graph();value.nodes=Array.from({length:101},(_,i)=>({...value.nodes[0],id:`complex:${i}`}));
    expect(()=>parsePropertyRelationGraph(value,release,root)).toThrow();
  });
});
