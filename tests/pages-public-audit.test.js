import {describe,expect,it} from 'vitest';
import {scanPublicText} from '../scripts/pages-public-audit.mjs';
describe('public audit redaction',()=>{
  it('reports only categories and line numbers, never matched credentials or personal values',()=>{
    const credential='gh'+'p_'+'A'.repeat(36),body='credential='+credential+'\nContact individual'+'@mail.test';
    const report=scanPublicText(body);expect(report.some(row=>row.category==='provider-token')).toBe(true);expect(JSON.stringify(report)).not.toContain(credential);expect(JSON.stringify(report)).not.toContain('@');
  });
  it('does not mistake explicit placeholders and public noreply addresses for credentials',()=>{
    expect(scanPublicText('DATA_GO_KR_SERVICE_KEY="fixture-not-a-real-credential"\n123+developer@users.noreply.github.com')).toEqual([]);
  });
});
