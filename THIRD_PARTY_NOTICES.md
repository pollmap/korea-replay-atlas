# 타사 소프트웨어와 데이터

프로젝트에서 새로 작성한 코드는 루트 `LICENSE`의 MIT 조건을 따릅니다. 지도 원본, 공식 API 응답, 타사 코드와 라이브러리의 권리를 MIT로 변경하지 않습니다. `public/data`, 원본 수집물, 비밀 설정 및 배포 백업은 소스 저장소와 분리합니다.

## 직접 실행 의존성

2026-09-20 설치된 패키지의 라이선스 파일·메타데이터 기준입니다. 정확한 배포 버전은 `package-lock.json`이 우선하며, 번들에 포함된 원래 저작권·라이선스 주석을 보존합니다.

| 소프트웨어 | 현재 버전 | 라이선스 | 원문 |
|---|---|---|---|
| CesiumJS | 1.145.0 | Apache-2.0 | https://github.com/CesiumGS/cesium/blob/main/LICENSE.md |
| MapLibre GL JS | 6.10.0 | BSD-3-Clause | https://github.com/maplibre/maplibre-gl-js/blob/main/LICENSE.txt |
| PMTiles JS | 4.5.0 | BSD-3-Clause | https://github.com/protomaps/PMTiles/blob/main/LICENSE |
| React / React DOM | 19.3.0 | MIT | https://github.com/facebook/react/blob/main/LICENSE |
| SunCalc | 1.9.0 | BSD-2-Clause | https://github.com/mourner/suncalc/blob/v1.9.0/LICENSE |

전이 의존성도 각 패키지의 LICENSE/NOTICE를 따릅니다. 위 표는 전체 전이 의존성의 저작권 대체물이 아닙니다. 배포 단계에서 라이브러리가 제공하는 `ThirdParty.json`, 라이선스 파일 및 번들 고지를 제거하지 않습니다.

## 공간·관측 자료

| 자료 | 적용 경계와 공개 조건 | 공식 근거 |
|---|---|---|
| OpenStreetMap | © OpenStreetMap contributors, ODbL 1.0. 데이터베이스의 공개·변환 배포 시 ODbL 의무를 별도 검토합니다. 코드 MIT와 다릅니다. | https://www.openstreetmap.org/copyright |
| Overture Buildings / Transportation | 테마·릴리스 및 개별 원천 조건을 보존합니다. 출처별 ODbL 등 조건을 한 라이선스로 뭉뚱그리지 않습니다. | https://docs.overturemaps.org/attribution/ |
| Natural Earth | Public domain. 소축척 표시자료이며 지적·측량 경계가 아닙니다. | https://www.naturalearthdata.com/about/terms-of-use/ |
| GHSL GHS-BUILT-H | European Union / JRC, CC BY 4.0. 격자 추정 높이와 개별 실측 높이를 구분합니다. | https://data.jrc.ec.europa.eu/dataset/85005901-3a49-48dd-9d19-6261354f56fe |
| Mapzen Terrain Tiles | SRTM, GMTED, ETOPO 등 제공 원천별 attribution·조건을 유지합니다. | https://registry.opendata.aws/terrain-tiles/ |
| 서울교통공사 역사 좌표·심도 | 각 공식 제공 페이지의 출처 표시 및 이용 조건을 따릅니다. | https://data.seoul.go.kr/dataList/OA-13305/F/1/datasetView.do |
| TAGO, 서울 지하철, 기상청 및 국토부 실거래 | 신청 서비스별 이용 조건·호출량·재배포 허용 범위를 따릅니다. 인증키와 원본 응답의 비공개 항목은 게시하지 않습니다. | https://www.data.go.kr/ · https://data.seoul.go.kr/ |

실제 게시 목록의 `sources`, 원천 URL, 취득·관측 시각, 검증 상태가 해당 자료의 상세 출처입니다. 원천 페이지의 조건을 확인하지 않은 신규 자료는 공개 목록에 넣지 않습니다. 공공데이터라는 이유만으로 무조건 무제한 재배포 가능하다고 단정하지 않습니다.

## 서울시 공동주택 기본정보 파생 자료

`src/data/seoul-apartment-facts.json`은 서울특별시 열린데이터광장의 [서울시 공동주택 아파트 정보](https://data.seoul.go.kr/dataList/OA-15818/A/1/datasetView.do)를 2026-09-23 수집해 가공한 자료입니다. 공공누리 제1유형(출처 표시)을 따르며 MIT 코드 라이선스와 구분합니다. 원천에서 현재 사용 중인 아파트·주상복합 중, 기존 국토부 아파트 식별자와 도로명주소·이름으로 고유하게 연결된 842개 단지만 포함합니다. 관리사무소 전화·팩스·홈페이지 및 미검증 좌표는 포함하지 않습니다. 단지 식별자 연결은 좌표 정확성을 보증하지 않습니다. 세대당 주차는 제공 주차대수/세대수로 계산합니다. 원천 취득일·개별 수정일·응답 해시를 보존합니다.

## 공개 전 확인

소스 저장소 공개는 데이터 공개와 별개입니다. `scripts/pages-public-audit.mjs`는 현재 추적 파일과 모든 Git 이력의 비밀·개인 식별정보 후보를 값 출력 없이 보고합니다. 자동 패턴 검사만으로 비밀 부재나 모든 데이터 이용권이 입증되지는 않습니다. 검수한 공개 후보, Git 메타데이터, 예제 환경파일, 고지 파일을 함께 확인해야 합니다.

`shared/data/map2d-rail-colors.json`은 OSM 노선 관계에서 추출한 파생 식별표이며 ODbL 1.0이 적용됩니다. 자체 코드의 MIT 라이선스와 별개입니다. 원본 버전·SHA와 노선별 관계 근거는 해당 JSON 및 `docs/MAP2D_RAIL_COLORS.md`에 보존합니다.

`src/data/region-selection*.json`과 경계 자산 폴더는 SGIS 2025-06-30 행정구역 원본의 표시용 파생자료입니다. 원천 공개 조건(이용허락범위 제한 없음), 원본 SHA·기준일·표시 오차는 `docs/SELECTED_REGION_BOUNDARIES.md`에 기록하며 자체 코드 MIT와 구분합니다.

## 주변 시설 파생 데이터베이스

`src/data/property-poi/`는 © OpenStreetMap contributors의 2026-09-15T20:20:37Z 한국 스냅샷에서 추출한 파생 데이터베이스입니다. **ODbL 1.0**으로 제공하며 루트 MIT는 이 데이터에 적용되지 않습니다. 원본은 Geofabrik 한국 PBF이며 SHA, 출처 URL, 시설 원본 ID, 위치 산정 방식 및 분류 근거를 manifest와 각 레코드에 보존합니다. 이 폴더의 JSON 및 `pipeline/property_poi.py`가 공개된 파생 데이터와 재생성 방법입니다. [라이선스 전문](https://opendatacommons.org/licenses/odbl/1-0/), [출처 표시 안내](https://www.openstreetmap.org/copyright).

경기·서울·인천·천안·아산·세종·청주·대전·부산의 SGIS 표시 경계로 범위를 선택했습니다. 완전한 시설 명부, 최신 영업 상태, 통학 배정 또는 도보 경로를 보증하지 않습니다. 점은 원천 노드, 면 내부 대표점 또는 선의 중간점으로 구분하며 단지 연결이 불확실하면 사용자가 선택한 지도 기준점의 직선거리를 표시합니다.
# 공식 학교·관리비 자료 (2026-10-10 연결)

- 한국교육시설안전원 전국학교위치정보: https://www.data.go.kr/data/15021148/standard.do
  - 원천 이용허락범위: 제한 없음. 기준일 2026-10-01.
  - 지정 권역의 운영 중인 초·중·고교 위치 5,871건을 원천 학교 ID로 유지합니다.
  - 위치 기록은 학교 배정·통학구역·도보 시간을 의미하지 않습니다.
- 서울시 아파트 관리비 명세: https://data.seoul.go.kr/dataList/OA-15822/S/1/datasetView.do
  - 서울특별시, 공공누리 제1유형(출처표시). 2026년 7월 CSV를 단지 코드별로 정리했습니다.
  - 2,205개 코드의 항목별 원화 금액이며 세대별 청구액·㎡당 관리비로 변환하지 않습니다.
  - 단지 이름만으로 결합하지 않습니다. 현재 공식 단지 기본정보와 코드가 일치하는 683개에 연결됩니다.
  - 자체 코드의 MIT 라이선스가 외부 자료의 이용조건을 변경하지 않습니다.
