import {describe,expect,it} from 'vitest';
import {activeRealEstateRecords,parseRealEstatePartition,REAL_ESTATE_SOURCE_ID,REAL_ESTATE_SOURCE_URL,
  REAL_ESTATE_TRANSFORM,summarizeTransactions,type RealEstateRecord,type RealEstatePartition} from '../shared/real-estate';

const inputHash='a'.repeat(64),retrieved='2026-09-20T00:00:00Z';
const filter={complex_id:'molit-apt:11110:11110-999999',area_m2:'84.99',date_from:'2026-09-01',date_to:'2026-09-30'};
function record(index:number,changes:Partial<RealEstateRecord>={}):RealEstateRecord{
  return {id:`molit-sale:${index.toString(16).padStart(64,'0')}:1`,kind:'apartment-sale-report',
    complex_id:filter.complex_id,source_complex_id:'11110-999999',complex_name:'검증용 가상단지',
    lawd_code:'11110',legal_dong_code:'1111017500',legal_dong_name:'가상동',lot_number:'123-4',
    price_krw:100_000_000,source_price_unit:'10,000 KRW',area_m2:'84.99',floor:12,build_year:2008,
    contract_date:'2026-09-02',registration_date:'2026-09-18',reported_at:null,source_updated_at:null,
    cancellation:{status:'not_reported',reason_date:null,source_flag:null},position:null,quality:'valid',issues:[],
    source_fields:{sggCd:'11110',aptSeq:'11110-999999',dealAmount:'10,000'},
    provenance:{source_id:REAL_ESTATE_SOURCE_ID,dataset_id:'15126468',evidence_type:'official_report',
      observed_at:null,retrieved_at:retrieved,input_sha256:inputHash,transform_version:REAL_ESTATE_TRANSFORM},...changes};
}
function partition(records:RealEstateRecord[]):RealEstatePartition{
  const issues=new Map<string,{field:string;code:RealEstateRecord['issues'][number]['code'];count:number}>();
  for(const r of records)for(const i of r.issues){
    const key=`${i.field}:${i.code}`;
    issues.set(key,{...i,count:(issues.get(key)?.count??0)+1});
  }
  return {schema_version:1,kind:'apartment-sale-report-partition',lawd_code:'11110',deal_month:'202609',
    source:{id:REAL_ESTATE_SOURCE_ID,dataset_id:'15126468',page_url:REAL_ESTATE_SOURCE_URL},
    retrieved_at:retrieved,records,audit:{source_rows:records.length,record_count:records.length,
      complete_pages:true,identical_row_occurrences:0,
      quality_counts:{valid:records.filter(r=>r.quality==='valid').length,
        incomplete:records.filter(r=>r.quality==='incomplete').length,invalid:records.filter(r=>r.quality==='invalid').length},
      cancellation_counts:{not_reported:records.filter(r=>r.cancellation.status==='not_reported').length,
        cancelled:records.filter(r=>r.cancellation.status==='cancelled').length,
        unknown:records.filter(r=>r.cancellation.status==='unknown').length},issue_counts:[...issues.values()],
      pages:[{page_no:1,page_size:100,total_count:records.length,input_sha256:inputHash,
        input_bytes:1000,retrieved_at:retrieved,unknown_fields:[]}]}};
}

describe('reported apartment sale contract',()=>{
  it('decodes a complete snapshot with no invented observation or location',()=>{
    const result=parseRealEstatePartition(partition([record(1)]));
    expect(result.records[0].provenance.evidence_type).toBe('official_report');
    expect(result.records[0].reported_at).toBeNull();
    expect(result.records[0].position).toBeNull();
    expect(parseRealEstatePartition(partition([])).audit.source_rows).toBe(0);
  });

  it('uses exact area and period, preserving distinct report rows with identical values',()=>{
    const rows=[record(1),record(2,{price_krw:300_000_000}),record(3,{price_krw:300_000_000}),
      record(4,{area_m2:'59.99',price_krw:999_990_000}),
      record(5,{contract_date:'2026-08-31'}),record(6,{complex_id:'molit-apt:11110:11110-999998'})];
    expect(summarizeTransactions(rows,filter)).toEqual({...filter,count:3,median_price_krw:300_000_000,
      price_unit:'KRW',cancellation_policy:'exclude',statistic:'reported-row-median'});
    expect(summarizeTransactions([record(1,{price_krw:100_010_000}),record(2,{price_krw:100_020_000})],filter)
      .median_price_krw).toBe(100_015_000);
    expect(summarizeTransactions([],filter)).toMatchObject({count:0,median_price_krw:null});
  });

  it('excludes cancelled, unknown and invalid rows without discarding their provenance',()=>{
    const rows=[record(1),record(2,{cancellation:{status:'cancelled',source_flag:'O',reason_date:'2026-09-10'}}),
      record(3,{cancellation:{status:'unknown',source_flag:'?',reason_date:null},quality:'invalid',
        issues:[{field:'cdealType',code:'unknown_value'}]}),
      record(4,{floor:null,quality:'invalid',issues:[{field:'floor',code:'invalid_format'}]}),
      record(5,{floor:null,quality:'incomplete',issues:[{field:'floor',code:'missing'}]})];
    expect(activeRealEstateRecords(rows).map(r=>r.id)).toEqual([rows[0].id,rows[4].id]);
    expect(summarizeTransactions(rows,filter).count).toBe(2);
    expect(rows).toHaveLength(5);
    expect(parseRealEstatePartition(partition(rows)).records).toHaveLength(5);
  });

  it.each([
    {price_krw:1.5},{price_krw:10_001},{price_krw:Number.MAX_SAFE_INTEGER+1},
    {area_m2:'NaN'},{area_m2:'84.9900'},{area_m2:'0'},
    {contract_date:'2026-02-30'},{contract_date:'2026-08-31'},
    {complex_id:'same-name-only'},{complex_id:'molit-apt:26110:11110-999999'},
    {quality:'invalid',issues:[]},
    {position:{lon:127,lat:37}},{reported_at:'2026-09-18'},
  ])('rejects malformed or incompatible normalized fields: %j',changes=>{
    const value=partition([record(1)]);
    Object.assign(value.records[0],changes);
    expect(()=>parseRealEstatePartition(value)).toThrow('invalid_real_estate_partition');
  });

  it('rejects incomplete pages, mismatched source receipts and duplicate IDs',()=>{
    const cases=[partition([record(1)]),partition([record(1)]),partition([record(1)]),partition([record(1),record(1)])];
    cases[0].audit.source_rows=2;
    cases[1].audit.pages[0].input_sha256='b'.repeat(64);
    cases[2].audit.pages[0].page_no=2;
    for(const value of cases)expect(()=>parseRealEstatePartition(value)).toThrow('invalid_real_estate_partition');
    expect(()=>summarizeTransactions([record(1),record(1)],filter)).toThrow('duplicate_snapshot_records');
  });

  it('preserves old transform decoding and opaque source apartment identifiers',()=>{
    const value=partition([record(1,{source_complex_id:'99999-opaque',complex_id:'molit-apt:11110:99999-opaque'})]);
    expect(parseRealEstatePartition(value).records[0].source_complex_id).toBe('99999-opaque');
    value.records[0].provenance.transform_version='apartment-sale-report-v1';
    expect(parseRealEstatePartition(value).records).toHaveLength(1);
  });

  it.each([{area_m2:'84.9900'},{area_m2:'0'},{date_from:'2026-02-30'},
    {date_from:'2026-10-01'},{complex_id:'검증용 가상단지'}])('rejects ambiguous comparison filters: %j',changes=>{
    expect(()=>summarizeTransactions([record(1)],{...filter,...changes})).toThrow('invalid_summary_filter');
  });
});
