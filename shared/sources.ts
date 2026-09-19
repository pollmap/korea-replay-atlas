import type {Catalog, Place, Source} from './contracts';
export const SOURCES: Source[] = [
  {id: 'overture-transportation', title: 'Overture Maps · Transportation', url: 'https://docs.overturemaps.org/guides/transportation/', license: 'ODbL · 원천별 출처 표시', description: '공개 도로·철도 연결망의 지표 투영입니다. 교량·터널의 실측 높이는 포함하지 않습니다.'},
  {id: 'ghsl', title: 'European Commission JRC · GHS-BUILT-H', url: 'https://data.jrc.ec.europa.eu/dataset/85005901-3a49-48dd-9d19-6261354f56fe', license: 'CC BY 4.0 · European Union', description: '2018년 100m 격자 평균 건물 높이입니다. 개별 높이 자료가 없는 건물의 추정 표현에 사용하며, 실측 높이로 해석하지 않습니다.'},
  {id: 'overture', title: 'Overture Maps · Buildings', url: 'https://docs.overturemaps.org/guides/buildings/', license: 'ODbL 및 원천별 출처 표시', description: '건물 외곽·높이·층수·부분 형상. 원천 속성이 있는 건물만 해당 높이를 사용합니다.'},
  {id: 'osm', title: 'OpenStreetMap contributors', url: 'https://www.openstreetmap.org/copyright', license: 'ODbL 1.0', description: '도로·철도·역·건물의 공개 공간 자료입니다.'},
  {id: 'terrain', title: 'Mapzen Terrain Tiles', url: 'https://registry.opendata.aws/terrain-tiles/', license: '원천별 출처: SRTM·GMTED·ETOPO 등', description: 'EGM96 기준 고도 자료. 정밀 측량 또는 교량·터널의 높이를 의미하지 않습니다.'},
  {id: 'natural-earth', title: 'Natural Earth', url: 'https://www.naturalearthdata.com/about/terms-of-use/', license: 'Public domain', description: '전국 탐색용 소축척 지표·행정 경계입니다. 상세 해안선과 차이가 있습니다.'},
  {id: 'tago', title: '국토교통부 TAGO · 버스 위치', url: 'https://www.data.go.kr/data/15098533/openapi.do', license: '공공데이터포털 이용 조건', description: '노선별 현재 위치를 수집한 시점부터 기록합니다. 관측 시각 미제공 응답은 수집 시각을 따로 표시합니다.'},
  {id: 'korail', title: '한국철도공사 · 열차운행정보', url: 'https://www.data.go.kr/data/15125762/openapi.do', license: '이용허락범위 제한 없음', description: '역별 도착·출발 운행 기록. 역 사이의 연속 GPS 위치가 아닙니다.'},
  {id: 'seoul-depth', title: '서울교통공사 · 역사 심도', url: 'https://data.seoul.go.kr/dataList/OA-13305/F/1/datasetView.do', license: '공공누리 1유형 · 서울교통공사 출처 표시', description: '2024-11-04 심도와 2025-08-14 좌표를 호선·역명으로 연결했습니다. 지형에 심도를 적용한 점이며 실제 지하 시설의 3D 형상은 아닙니다.'},
  {id: 'seoul-stations', title: '서울교통공사 · 역사 좌표', url: 'https://data.seoul.go.kr/dataList/OA-22534/F/1/datasetView.do', license: '공공누리 1유형 · 서울교통공사 출처 표시', description: '1~8호선 역 좌표입니다. 같은 호선·역명으로 심도 자료와 연결하며 연결되지 않은 역은 검토 대장에 남깁니다.'},
  {id: 'kma-satellite', title: '기상청 국가기상위성센터 · GK2A', url: 'https://nmsc.kma.go.kr/enhome/html/base/cmm/selectPage.do?page=static.utilization.software', license: '기상청 국가기상위성센터 제공 · 원천 이용 조건', description: '현재 연결된 영상은 2022-01-01 공식 SW038 예제입니다. 공식 보정표로 계산한 밝기온도이며 주간 반사광 영향이 있습니다. 실시간 영상이나 구름 체적 모델이 아닙니다.'},
  {id: 'kma-radar', title: '기상청 · HSR 레이더', url: 'https://www.weather.go.kr/w/image/radar.do', license: '기상청 제공 · 원천 이용 조건', description: '연결된 공개 영상은 기상청 강수에코 색상을 보존합니다. 숫자 강우량으로 역산하지 않으며, 색이 없는 배경에서는 무에코와 결측을 구분할 수 없습니다. 별도 원시 반사도 어댑터는 두 값을 구분합니다.'},
  {id: 'sun', title: 'SunCalc 1.9.0 · Cesium SunLight', url: 'https://github.com/mourner/suncalc/tree/v1.9.0', license: 'BSD-2-Clause / Apache-2.0', description: '좌표·UTC 시각에 따른 태양 계산과 현재 3D 모델의 그림자입니다.'},
];
export const PLACES: Place[] = [
  {id:'korea',name:'대한민국 전체',region:'전국',lon:127.8,lat:35.2,range:1500000},
  {id:'daejeon',name:'대전',region:'대전역 · 원도심',lon:127.433,lat:36.332,range:4800},
  {id:'sejong',name:'세종',region:'정부세종청사',lon:127.265,lat:36.504,range:6200},
  {id:'cheongju',name:'청주',region:'상당구 · 원도심',lon:127.489,lat:36.643,range:5200},
  {id:'osong',name:'오송역',region:'충북 청주시',lon:127.327,lat:36.620,range:4200},
  {id:'seoul',name:'서울',region:'시청 · 광화문',lon:126.978,lat:37.566,range:6200},
  {id:'busan',name:'부산',region:'부산역 · 북항',lon:129.041,lat:35.115,range:7500},
  {id:'incheon',name:'인천',region:'송도',lon:126.637,lat:37.390,range:7500},
  {id:'daegu',name:'대구',region:'동성로',lon:128.596,lat:35.869,range:6500},
  {id:'gwangju',name:'광주',region:'동구 · 원도심',lon:126.918,lat:35.147,range:6500},
  {id:'jeju',name:'제주',region:'제주시',lon:126.527,lat:33.499,range:11000},
  {id:'ulleung',name:'울릉도',region:'경북 울릉군',lon:130.884,lat:37.489,range:18000},
  {id:'dokdo',name:'독도',region:'경북 울릉군',lon:131.869,lat:37.242,range:3500},
];
export const EMPTY_CATALOG: Catalog = {
  schema_version: 1, release_id: 'unpublished', generated_at: null, base_date: null,
  sources: SOURCES, assets: [], layers: [
    ['terrain','지형',['terrain']], ['buildings','건물',['overture','osm']],
    ['infrastructure','도로·도시 시설',['osm']],
    ['rail','철도',['osm','korail']], ['bus','버스',['tago']],
    ['depth','지하 역사',['seoul-depth']], ['satellite','위성',['kma-satellite']],
    ['radar','레이더',['kma-radar']], ['sun','햇빛과 그림자',['sun']],
  ].map(([id,label,source_ids]) => ({id,label,source_ids,state:id==='sun'?'ready':'unavailable',
    reason:id==='sun'?null:'검수된 자료가 아직 연결되지 않았습니다.',record_count:0,from:null,to:null,updated_at:null})) as Catalog['layers'],
};
