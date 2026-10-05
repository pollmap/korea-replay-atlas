import {readFileSync} from 'node:fs';
import {expect,it} from 'vitest';
import {parseRevisionArchive} from '../src/property-source-revisions';
const release='property-ceeff63959643461';
const archive=()=>JSON.parse(readFileSync(new URL('../src/data/property-revisions-ceeff63959643461.json',import.meta.url),'utf8'));
it('preserves every earlier public ID with original record values and keeps source absence distinct from cancellation',()=>{
  const original=archive(),result=parseRevisionArchive(original,release);
  expect(result.records).toHaveLength(635);
  expect(new Set(result.records.map(row=>row.previous.id)).size).toBe(635);
  expect(result.records.filter(row=>row.state==='report_fields_updated')).toHaveLength(609);
  const missing=result.records.filter(row=>row.state==='not_in_latest_snapshot');
  expect(missing).toHaveLength(26);expect(missing.every(row=>row.candidate_id===null)).toBe(true);
  expect(result.records[0].previous).toBe(original.records[0].previous);
});
it('rejects wrong release, duplicated records and archives that imply absence means cancellation',()=>{
  expect(()=>parseRevisionArchive(archive(),'property-0000000000000000')).toThrow();
  const duplicate=archive();duplicate.records[1]=duplicate.records[0];expect(()=>parseRevisionArchive(duplicate,release)).toThrow();
  const changed=archive();changed.absence_policy='cancelled';expect(()=>parseRevisionArchive(changed,release)).toThrow();
});
it('rejects invented replacements, money-unit errors and an older candidate snapshot',()=>{
  const absent=archive();const row=absent.records.find((r:{state:string})=>r.state==='not_in_latest_snapshot');row.candidate_id=absent.records[0].previous.id;expect(()=>parseRevisionArchive(absent,release)).toThrow();
  const amount=archive();amount.records[0].previous.price_krw=123;expect(()=>parseRevisionArchive(amount,release)).toThrow();
  const older=archive();older.records[0].candidate_retrieved_at='2000-01-01T00:00:00Z';expect(()=>parseRevisionArchive(older,release)).toThrow();
});
