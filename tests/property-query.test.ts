import {describe,it,expect} from 'vitest';
import {parsePropertyQuery,propertyQueryIssues} from '../shared/property-query';
const regions=[{lawd_code:'11710',name:'서울특별시 송파구'},{lawd_code:'11140',name:'서울특별시 중구'},{lawd_code:'26110',name:'부산광역시 중구'}];
describe('explicit apartment search conditions',()=>{
  it('understands region, budget and national exclusive area without choosing a trade',()=>{
    const result=parsePropertyQuery('송파 12억 이하 국평',regions);
    expect(result.valid).toBe(true);expect(result.patch).toEqual({region:'11710',latestPriceMinEok:'',latestPriceMaxEok:'12',area:'84-band',areaBasis:'exclusive'});
  });
  it('handles compound amounts and periods',()=>expect(parsePropertyQuery('서울 송파 전세 8억 5000만 이하 전용 84㎡ 최근 3년',regions).patch).toMatchObject({region:'11710',trade:'rent',rentKind:'jeonse',latestPriceMaxEok:'8.5',area:'84',historyMonths:36}));
  it('requires resolution of namesakes',()=>{const r=parsePropertyQuery('중구 5억 이하',regions);expect(r.valid).toBe(false);expect(r.regions).toHaveLength(2);expect(r.patch.region).toBeUndefined();});
  it.each(['송파 12억 이하 30평','송파 12억 이하 가성비','송파 전용 84㎡ 공급 32평','송파 최근 2년','송파 매매 전세','송파 10억 이하 12억 이하','송파 전용 0㎡','송파 999999999999억 이하','송파 -12억 이하'])('never drops ambiguous or invalid conditions: %s',query=>expect(parsePropertyQuery(query,regions).valid).toBe(false));
  it('preserves literal apartment and address searches',()=>{expect(parsePropertyQuery('헬리오시티',regions).active).toBe(false);expect(parsePropertyQuery('송파 가락동 913',regions).active).toBe(false);});
  it('normalizes fullwidth and decomposed input',()=>expect(parsePropertyQuery('송파 １２억 이하 국평'.normalize('NFD'),regions).valid).toBe(true));
  it('protects input budgets',()=>expect(parsePropertyQuery('x'.repeat(121),regions).valid).toBe(false));
  it('does not infer monthly rent from a deposit constraint',()=>{
    const query=parsePropertyQuery('송파 3억 이하',regions);
    expect(propertyQueryIssues(query,{trade:'rent',rentKind:'monthly'})).toHaveLength(1);
    expect(propertyQueryIssues(query,{trade:'rent',rentKind:'jeonse'})).toHaveLength(0);
  });
  it('provides exclusive-area bound filters',()=>expect(parsePropertyQuery('송파 전용 59㎡ 이하',regions).patch.area).toBe('range::59'));
});
