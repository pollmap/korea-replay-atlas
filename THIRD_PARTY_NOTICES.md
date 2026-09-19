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

## 공개 전 확인

소스 저장소 공개는 데이터 공개와 별개입니다. `scripts/pages-public-audit.mjs`는 현재 추적 파일과 모든 Git 이력의 비밀·개인 식별정보 후보를 값 출력 없이 보고합니다. 자동 패턴 검사만으로 비밀 부재나 모든 데이터 이용권이 입증되지는 않습니다. 검수한 공개 후보, Git 메타데이터, 예제 환경파일, 고지 파일을 함께 확인해야 합니다.
