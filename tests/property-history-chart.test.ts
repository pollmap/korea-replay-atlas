import {expect,it} from 'vitest';
import {historyDay,historyMonths} from '../shared/property-history';
import {historyChartData,historyChartSelection} from '../src/property-history-chart';
import {historyRecordWindow,historySelectionIndex} from '../src/property-history-selection';

type Row={id:string;contract_date:string|null;value:number|null;};
const from=historyDay('2006-09-01'),to=historyDay('2026-08-31'),price=(row:Row)=>row.value;
function twentyYears():Row[]{return historyMonths('202608',240).flatMap((month,i)=>[0,1000,5,6,7,8,10,9].map((value,j)=>({
  id:`${month}-${j}`,contract_date:`${month.slice(0,4)}-${month.slice(4)}-${String(j+1).padStart(2,'0')}`,value:value+i*10,
})));}

it('keeps every report at or below 1000 points, including equal dates and prices with distinct IDs',()=>{
  const rows=Array.from({length:1000},(_,i)=>({id:`report-${String(i).padStart(4,'0')}`,contract_date:'2026-08-01',value:100}));
  const chart=historyChartData(rows,from,to,price);
  expect(chart.points).toHaveLength(1000);expect(new Set(chart.points.map(row=>row.id)).size).toBe(1000);
  expect(chart.points.every(row=>rows.includes(row))).toBe(true);expect(chart).toMatchObject({min:100,max:100});
});

it('covers all 240 months using their actual minimum, maximum, lower-median and latest reports',()=>{
  const rows=twentyYears(),chart=historyChartData(rows,from,to,price);
  expect(chart.rows).toHaveLength(1920);expect(chart.points).toHaveLength(960);
  expect(new Set(chart.points.map(row=>row.contract_date!.slice(0,7))).size).toBe(240);
  for(const month of historyMonths('202608',240)){
    expect(chart.points.filter(row=>row.id.startsWith(month)).map(row=>row.id).sort()).toEqual([0,1,4,7].map(i=>`${month}-${i}`).sort());
  }
  expect(chart.points.every(row=>rows.includes(row))).toBe(true);
  expect(chart.points[0].contract_date).toBe('2026-08-08');expect(chart.points.at(-1)?.contract_date).toBe('2006-09-01');
  expect(historyChartData([...rows].reverse(),from,to,price).points.map(row=>row.id)).toEqual(chart.points.map(row=>row.id));
});

it('uses full-period extremes for axes and preserves old extreme reports rather than only recent prices',()=>{
  const rows=twentyYears();rows[0].value=0;rows[1].value=90_000_000_000;
  const chart=historyChartData(rows,from,to,price);
  expect(chart).toMatchObject({min:0,max:90_000_000_000});expect(chart.points).toContain(rows[0]);expect(chart.points).toContain(rows[1]);
  const normalized=historyChartData(rows,from,to,row=>row.value===null?null:row.value/2);
  expect(normalized).toMatchObject({min:0,max:45_000_000_000});
});

it('adds an unrepresented old table row to the chart and preserves chart-to-table and previous/next navigation',()=>{
  const rows=twentyYears(),chart=historyChartData(rows,from,to,price),selected=rows[2];
  expect(chart.points).not.toContain(selected);
  const points=historyChartSelection(chart,selected.id);
  expect(points).toHaveLength(961);expect(points).toContain(selected);expect(points).toContain(chart.points[0]);
  expect(points.length).toBeLessThanOrEqual(1000);expect(new Set(points.map(row=>row.id)).size).toBe(points.length);
  expect(historyRecordWindow(chart.rows,selected.id,{scope:'chart',start:0,expanded:false},'chart')?.start).toBeGreaterThan(1000);
  const index=historySelectionIndex(points,selected.id);expect(index).toBeGreaterThan(0);expect(index).toBeLessThan(points.length-1);
  expect(historyChartSelection(chart,points[index+1].id)).toContain(points[index+1]);
  expect(historyChartSelection(chart,'removed-by-filter')).toBe(chart.points);
  expect(historyChartSelection(chart,selected.id).map(row=>row.id)).toEqual(points.map(row=>row.id));
  expect(chart.rows).toHaveLength(1920);expect(chart.points).toHaveLength(960);
});

it('retains selected duplicate-looking report IDs without creating new rows or claiming missing months',()=>{
  const rows=twentyYears();rows[2]={...rows[0],id:'second-source-row'};
  const chart=historyChartData(rows,from,to,price),selected=historyChartSelection(chart,'second-source-row');
  expect(chart.byId.has(rows[0].id)).toBe(true);expect(chart.byId.has('second-source-row')).toBe(true);
  expect(selected.some(row=>row.id==='second-source-row')).toBe(true);
  const missing=rows.filter(row=>!row.contract_date!.startsWith('2018-04'));
  expect(historyChartData(missing,from,to,price).points.some(row=>row.contract_date!.startsWith('2018-04'))).toBe(false);
});

it('excludes undated, out-of-window and nonnumeric prices without changing the caller table rows',()=>{
  const rows:Row[]=[{id:'null-date',contract_date:null,value:5},{id:'outside',contract_date:'2006-08-31',value:999},
    {id:'null-price',contract_date:'2026-08-01',value:null},{id:'infinite',contract_date:'2026-08-02',value:Infinity},
    {id:'valid',contract_date:'2026-08-03',value:0}];
  const chart=historyChartData(rows,from,to,price);expect(chart.points).toEqual([rows[4]]);expect(chart).toMatchObject({min:0,max:0});expect(rows).toHaveLength(5);
});
