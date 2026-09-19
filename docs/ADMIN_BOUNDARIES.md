# 전국 행정경계 원천과 2D 후보

확인일: **2026-09-20 KST**. 전국 시도·시군구와 개략 도로·철도·육지·수역을 MVT/PMTiles 후보로 검증했습니다. 전체 상세 도로·건물 평면·행정동은 후속 범위입니다. 이 문서의 로컬 후보는 공개 배포 완료를 의미하지 않습니다.

## 현재 전국 기본 지도 후보

새 육지 보완 후보의 배포 검토 입력은 **`.local/map-tiles-20260920/sgis-land-candidate/publication.json`**입니다. 아래의 기존 후보와 원본은 모두 보존합니다. 이 문서의 후보 생성·로컬 검증은 원격 배포나 GPU 표시 검증과 구분합니다.

- release: `map2d-f882deab7b81016f2b61`
- catalog: `data/map-tiles/map2d-f882deab7b81016f2b61/catalog.json`
- catalog SHA-256: `5580708797eb810b0f3ca7c694988bc27d2b5830b64ff1d3d9859a325418c992`
- **267파일 / 76,343,640 B**. 기존 227파일 / 65,924,437 B는 바이트·SHA까지 동일하게 재사용했습니다. 도로·철도·수역·행정경계 주제의 ID·형상·속성·줌 설정은 그대로입니다.
- 새 육지는 **SGIS 시도 17개 + 보존된 OSM 독도 해안 86개**, 총 103개 원본 선택 레코드를 사용합니다. z5~12, PMTiles 38개 + 속성 1개입니다. 최고 줌 이후는 overzoom입니다.
- 가장 큰 새 land archive는 **507,148 B**, 전체 지도 archive 최대는 **509,496 B**입니다. 모든 파일·decoded MVT의 1 MiB 상한을 검증했습니다.
- 독립 JS 검증: `.local/map-tiles-20260920/sgis-land-validation-f882deab7b81016f2b61.json`. 실제 TypeScript schema, 전체 파일의 SHA·길이·의존관계, 원본 103개 선택 레코드 완전 일치, official JavaScript PMTiles reader와 Mapbox decoder의 부산·독도·울릉·제주 채움이 통과했습니다.

## 육지 표시의 원천과 정밀도

기존 Natural Earth 국가 윤곽은 소축척 지도용입니다. 부산 북항 범위 `[129.015,35.08,129.09,35.145]`에서 최대 3,496.5 m 직선 구간이 있었고, 보존된 OSM 해안과 EPSG:5179 좌표계의 20 m 간격으로 비교한 최대 차이는 2,269.6 m였습니다. 같은 구역에서 기존 z9 MVT와 Natural Earth 원천 사이의 차이는 약 3.86 m였습니다. 이는 **원천 간 차이와 표시 변환 차이**를 구분한 것이며 현장 측량 정확도가 아닙니다. [Natural Earth 소축척 해안 설명](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-coastline/)

새 land는 **2025-06-30 SGIS 통계 행정경계 기반 육지 표시**입니다. 공식 해안측량, 지적도, 법정동이나 현재 매립지의 완전한 정확도를 의미하지 않습니다. 부산의 SGIS와 OSM 원천은 중앙값 약 2.2 m로 가까웠지만 방향에 따라 최대 283.7~406.0 m 차이가 남았습니다. 원천 기준일과 형태 차이를 숨기지 않습니다. 독도 OSM과 SGIS 도형이 겹치는 부분도 있으며 서로 다른 날짜의 원천을 임의로 일치시키지 않았습니다.

`pipeline/map_tiles_land.py`는 원본 좌표와 선택 속성을 보존한 채 타일 교차 후 실제 8,192 정수 격자에서 GEOS `valid_output` 정밀도 축소를 수행합니다. 격자보다 좁은 부분은 분리·병합·축소될 수 있습니다. 형상 전체가 격자보다 작으면 원본 ID를 가진 `subpixel_anchor`로 표시합니다. 0 tolerance로 일직선 중간 정점만 제거하고 그 전후 채움의 완전 일치를 검사합니다. 기존의 invalid polygon을 전체 outline으로 전환하는 경로는 이 육지 후보에 적용하지 않습니다. [GEOS/Shapely 정밀도 축소의 실제 동작](https://shapely.readthedocs.io/en/stable/reference/shapely.set_precision.html)

매 타일에서 원본 ID 유지, 유효한 폴리곤·구멍, encode/decode 후 채움 동등성과 크기를 검사했습니다. z12의 부산·독도·울릉·제주 표본에서는 원본 경계의 **2.389 m(Web Mercator) 폭** 밖에 생긴 채움 변화 면적이 0이었습니다. 이는 네 표본의 정수 격자 변환 검사이며 전국 실제 해안 오차 상한을 뜻하지 않습니다.

추가 저줌 검사에서 z5~9의 90타일 중 21개는 GEOS의 엄격한 공유 변 정점 일치 조건을 충족하지 않았습니다. 그중 18개는 면적 겹침 0, 나머지 3개는 최대 0.8 정수 격자 제곱의 겹침이었습니다. 512 px 타일의 해당 원래 줌에서 0.003125 px²에 해당합니다. 개별 폴리곤 채움·유효성 검사는 통과했지만 **모든 공유 경계가 완전 무오차라고 주장하지 않습니다.** 세부 증빙은 `.local/map-tiles-20260920/sgis-land-topology-f882deab7b81016f2b61.json`입니다.

새 catalog의 `sources`는 실제로 남은 OSM·SGIS만 포함합니다. 교체된 Natural Earth 및 사용하지 않는 Overture를 현재 후보의 사용 출처로 표시하지 않습니다. 기존 원본과 이전 릴리스에는 해당 출처가 그대로 보존됩니다.

```powershell
.venv\Scripts\python.exe -m pipeline.map_tiles_land --baseline .local/map-tiles-20260920/candidate --source-work .local/map-tiles-20260920/work/aed5a621760281b23b30 --output .local/map-tiles-20260920/sgis-land-candidate
node .local/map-tiles-20260920/verify-sgis-land.mjs .local/map-tiles-20260920/sgis-land-candidate .local/map-tiles-20260920/candidate .local/map-tiles-20260920/sgis-land-validation-f882deab7b81016f2b61.json
```

출력 후보와 검증 JSON이 이미 있으면 명령은 거부합니다. 재실행 검토에는 새 출력 이름을 사용해야 합니다. 후보의 `work/land.sqlite`와 줌별 증빙은 생성 이력을 보존하며 공개 파일 목록에는 포함하지 않습니다.

## 기존 전국 기본 지도 후보 — 보존

이전 입력은 `.local/map-tiles-20260920/candidate/publication.json`입니다. 소축척 Natural Earth 육지 표시의 한계가 확인되어 위의 새 후보를 만들었으며 이 230개 파일은 덮어쓰지 않았습니다.

- release: `map2d-aed5a621760281b23b30`
- catalog: `data/map-tiles/map2d-aed5a621760281b23b30/catalog.json`
- catalog SHA-256: `98a9103cf177d8317da3fcb8e7bddff1743fe1ca5acaaa9cc5543d50e22f06eb`
- **230파일 / 66,077,646 B**, 가장 큰 PMTiles **509,496 B**. 개별 1 MiB 상한을 통과했습니다. 일부 저줌 단일 타일은 128 KiB 목표를 넘으며 이 목표를 하드 상한으로 표현하지 않습니다.
- 독립 재검사: `.local/map-tiles-20260920/candidate-validation-aed5a621760281b23b30.json`. 실제 TypeScript schema, 230개 전체 파일의 SHA·길이·의존관계, 모든 선택 속성 수와 16자리 표시 키의 주제 내 충돌 없음이 통과했습니다.

| 주제 | 포함 원본 피처 | 줌 | PMTiles / 속성 파일 | 범위 |
|---|---:|---|---:|---|
| 시도 | 17 | 5~9 | 25 / 1 | SGIS 2025-06-30 전국 |
| 시군구 | 252 | 8~11 | 39 / 1 | SGIS 2025-06-30 전국 |
| 육지 | 87 | 5~9 | 1 / 1 | 기존 국가 윤곽 및 독도 보완 원천 |
| 철도 | 15,497 | 7~12 | 15 / 4 | 기존 공개 개략 형상. 실시간 위치 아님 |
| 도로 | 185,489 | 6~13 | 76 / 58 | 기존 공개 개략 형상. 전체 상세 도로 아님 |
| 수역 | 540 | 6~12 | 7 / 1 | 기존 전국 수역 원천 |

도로의 OSM 분류별 시작 줌은 motorway 6, trunk 9, primary 10, secondary 11, 기타 12입니다. 수도권의 한 z7 타일이 34,989개 조각·decoded 1,753,019 B로 1 MiB 상한을 초과한 실물 근거에 따라 조정했습니다. 최고 줌에서는 포함 도로 185,489개 모두의 ID 표현을 검증했습니다. 이 태그를 도로법상 등급이나 공식 노선 번호로 변환하지 않습니다.

**건물 평면 3,730,526개, 전체 상세 기반시설 2,080,786개, SGIS 행정동 3,559개는 이번 후보에 포함되지 않습니다.** 해당 원천은 보존되어 있으며 새로운 후보로 확장해야 합니다. 이 후보를 전국의 모든 건물·도로 상세가 완료된 것으로 표시하지 않습니다.

## 원천과 권한

채택한 자료는 [국가데이터처_SGIS 행정구역 통계 및 경계_20250630](https://www.data.go.kr/data/15129688/fileData.do)입니다. 공식 항목은 무료·이용허락범위 제한 없음을 명시하고, 2025년 경계와 2024년 통계를 함께 제공합니다. 항목 수정일은 2026-07-23이며 갱신 주기는 반기로 표시됩니다. 이는 2026년 현재의 모든 행정구역 개편을 반영한 경계라는 뜻이 아닙니다.

[SGIS 자료제공 목록](https://sgis.mods.go.kr/view/pss/openDataIntrcn)은 통계 경계 기준을 해당 연도 6월 30일, 좌표계를 UTM-K/GRS80(EPSG:5179)로 설명합니다. 이번 ZIP 내부의 `BASE_DATE`는 세 수준 모두 **20250630**입니다.

SGIS는 [공식 이용 안내](https://sgis.mods.go.kr/view/pss/dataProvdIntrcn)에서 행정동 기준이며 법정동 경계를 제공하지 않는다고 명시합니다. 따라서 이 자료의 코드 체계는 `sgis`, 경계 종류는 `census_administrative`로 보존합니다. **행정안전부 법정동 코드나 부동산 지번 주소로 변환·추론하지 않습니다.** 예를 들어 원본 부산 시도 코드는 `21`이며 이를 다른 기관 코드처럼 해석하면 안 됩니다.

대안으로 확인한 [국토교통부 센서스 행정동경계](https://www.data.go.kr/data/15125055/fileData.do)는 공개 항목에서 공공누리 제4유형 조건을 표시하므로, 이번처럼 간략화·재게시하는 경로의 원천으로 채택하지 않았습니다. SGIS 원천의 개별 허락을 배경지도·위성영상·다른 기관 자료의 허락으로 확대하지 않습니다.

## 실제 취득

- 원본: `.local/admin-boundaries-20260920/raw/sgis-20250630.zip`
- 원본 크기: **269,032,521 B**
- SHA-256: `f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6`
- 공개 포털 다운로드 메타데이터와 공개 다운로드 절차를 사용했습니다. 키·계정·로그인 세션은 사용하지 않았고, 보안문자 필요 여부 응답은 `false`였습니다.
- 최초 부분 요청에 서버가 HTTP 200과 전체 길이를 반환하여 128 B 확인 후 닫았습니다. 원래 150 MB 한도를 넘으므로 다운로드를 중지한 뒤, **이 공식 ZIP 1개에 한해 300 MB 상한 승인을 받고** 전체를 한 번 받았습니다. 전체 전송은 44.34초에 끝났으며 재시도는 없었습니다.
- 출처·허락·전송 크기·해시·ZIP 구성 기록: `.local/admin-boundaries-20260920/acquisition.json`
- 원본 속성표/도형 색인 수 대조: `.local/admin-boundaries-20260920/initial-proof.json`

원본 ZIP 안의 통계·설명 문서와 행정동 SHP는 그대로 보존합니다. 가공은 한 수준씩 메모리로 읽으며 전체 ZIP을 풀어 별도 복제하지 않습니다. 경계 선택 항목의 비압축 읽기 예산은 1 GB, 초기 경계 후보 산출물은 20 MB, 디스크 잔여는 30 GiB 이상입니다. 확장 MVT 경로의 개별 PMTiles는 저줌 128 KiB·상세 512 KiB 목표, **1 MiB 상한**을 따릅니다.

| 수준 | DBF 행 수 | SHX 도형 색인 수 | 원본 코드·이름 필드 | 현재 범위 |
|---|---:|---:|---|---|
| 시도 | 17 | 17 | `SIDO_CD`, `SIDO_NM` | z5~9 MVT·선택 속성 후보 완료 |
| 시군구 | 252 | 252 | `SIGUNGU_CD`, `SIGUNGU_NM` | z8~11 MVT·선택 속성 후보 완료 |
| 읍면동 | 3,559 | 3,559 | `ADM_CD`, `ADM_NM` | 원본 확보·속성/색인 대조. 전체 도형 가공은 아직 미수행 |

252는 이 기준일·분류 체계의 **SGIS 시군구 경계 피처 수**입니다. 이를 기초자치단체 개수나 모든 현재 시군구 개수로 바꿔 부르지 않습니다.

## 변환 계약

`pipeline/admin_boundaries.py`는 원본 2D Polygon SHP와 DBF/PRJ/CPG만 읽습니다. 새로운 의존성을 추가하지 않고 저장소의 NumPy·Shapely·pyproj를 사용합니다.

1. ZIP 경로 탈출·중복 이름·심볼릭 링크·암호화 항목, 과대 항목을 검사합니다. 실제 원본 ZIP은 별도 보존합니다.
2. SHP 레코드·링 폐합·유한 좌표·원본 폴리곤 유효성, DBF 수·코드 중복·명칭·기준일을 검사합니다. 잘못된 형상은 자동 복구하거나 버리지 않습니다.
3. 모든 원본 속성 필드를 유지합니다. `id`에는 원천·기준일·수준·원본 코드를 사용하며 `admin_code`, `name`, `code_namespace`, `boundary_kind`, `boundary_reference_date`를 추가합니다. 법정동 코드 필드는 `null`입니다.
4. PRJ를 pyproj가 EPSG:5179로 식별하는지 확인하고 `always_xy=True`로 WGS84 경도·위도 순서로 변환합니다. 변환 뒤 도형 유효성과 한국 경위도 범위를 검사합니다. 원본점→WGS84→원본 좌표 왕복 오차는 표본임을 명시해 기록합니다.
5. 낮은 줌의 간략화는 먼저 원본이 공유 변을 일치시킨 유효한 coverage인지 확인하고 GEOS coverage simplification을 사용합니다. 공통 변을 함께 처리하여 이웃 구역의 틈·겹침을 피합니다. 섬/구멍의 링 개수가 바뀌거나 결과가 유효하지 않으면 채택하지 않습니다.
6. 간략화된 링의 각 선분과 그에 대응하는 **모든 원본 정점**을 원본 미터 좌표계에서 비교합니다. 연속 원본 선분에 대한 볼록성·투영 연속성을 이용한 보수적 양방향 곡선 변위 상한을 계산합니다. 새 정점·매칭 불명·링 누락은 거부합니다. 이는 현장 측량의 정확도나 수직/건물 위치 정확도가 아닙니다.
7. 새 MVT 경로의 `bounded_coverage`는 공통 tolerance를 낮추며 측정 오차를 다시 검증합니다. 예산을 증명하지 못하면 명시적으로 실패합니다. 전국 전체 형상을 브라우저로 보내는 원본 GeoJSON 대체 경로는 사용하지 않습니다. 이전 GeoJSON 실험 함수의 원본 복귀 동작과 구분합니다.
8. MVT는 Web Mercator(EPSG:3857), 8,192 정수 격자와 타일 경계의 정확한 평면 교차를 사용합니다. 경계의 공유 선을 먼저 함께 간략화하고 타일 격자로 양자화합니다. 같은 원본 행정구역 ID를 가진 내부 대표점을 라벨 위치로 함께 제공합니다. 이는 공식 주소 좌표가 아닙니다.
9. GeoJSON 원천 메타데이터는 `source_metadata`로 이름을 분리합니다. 기존 compact 속성 사전의 예약 필드 `metadata.shared/rows`와 혼동하지 않습니다.

**간략화의 `tolerance`는 면적 기반 알고리즘의 매개변수이며 최대 위치 오차와 같지 않습니다.** 첫 25 m 매개변수 실행에서 최대 변위 150 m 조건을 충족하지 못해 후보가 거부됐습니다. 간략화 결과의 채택 여부는 별도 산정한 실제 보수적 오차와 용도에 따라 판정합니다. 간략판은 행정 경계의 낮은 줌 표시용이며 주소 판정·필지 판정에 사용하지 않습니다.

## 검증된 시도 후보

- 후보: `.local/map-tiles-20260920/admin-candidate/publication.json`
- 진입 파일: `data/map-tiles/map2d-13f67c414b47471c7192/catalog.json`
- 진입 SHA-256: `dd83501f635b37c6dcdf3fd66bb8955cc5050f82d63921df24a9326d56c08d80`
- **27파일, 2,329,499 B**: PMTiles 25개, 선택 속성 1개, 카탈로그 1개.
- 모든 시도 17개 원본 ID·명칭·기준일·속성과 라벨 위치를 포함합니다. 현재 후보에는 `admin-sido`만 기재합니다. z9 이후는 overzoom이며 측량용 형상이 아닙니다.
- 실제 생성 카탈로그를 TypeScript `validateMapCatalog2D`로 다시 읽어 통과했습니다. HTTP와 GPU 표시 검증은 별도입니다.

| 줌 | 허용 곡선 변위 상한(m, Web Mercator) | 측정된 보수적 상한(m) | 원본/표시 정점 수 |
|---|---:|---:|---:|
| 5 | 978.394 | 532.724 | 5,433,279 / 243,984 |
| 6 | 489.197 | 331.513 | 5,433,279 / 404,306 |
| 7 | 244.598 | 96.216 | 5,433,279 / 670,285 |
| 8 | 122.299 | 45.667 | 5,433,279 / 1,094,623 |
| 9 | 61.150 | 45.667 | 5,433,279 / 1,707,070 |

허용 상한은 512 CSS px 타일에서 0.4 px에 해당합니다. 정수 격자의 추가 양자화 상한은 약 **0.0442 px**입니다. Web Mercator 미터는 지표 실거리나 측량 정확도와 같지 않습니다. 원본 대비 곡선 변위와 화면 축척의 관계만 검증했으며 overzoom하면 화면 오차도 확대됩니다.

## 2D 타일과 선택 속성 계약

`shared/map-tiles.ts`의 `MapCatalog2D`는 주제별 min/max zoom, 원본 피처 수, 정렬된 TileID 구간의 PMTiles 및 stable ID 구간의 속성 shard를 가리킵니다. 원본 선택 레코드의 `stable_id`는 원천과 원본 ID의 SHA-256입니다. 기존 도로 데이터에서 동일 원본 ID에 다른 잘린 도형이 실제 존재하므로 도로 조각에는 원본 geometry hash를 추가하며, 선택 응답에는 원본 ID를 그대로 돌려줍니다. MVT 표시 키는 이 해시의 16자리 접두부이며 전체 주제에서 충돌이 없음을 검사한 후 `display_id_hex_length:16`으로 선언합니다. 64자리 기존 후보도 계속 읽을 수 있습니다.

`src/map-tiles-protocol.ts`는 Range 요청 없이 **전체 GET → 길이·SHA 검증 → custom PMTiles Source** 순서로 읽습니다. 본문 캐시는 데스크톱 64 MiB/모바일 32 MiB, 동시 전송 4개, PMTiles directory cache는 64항목/보수적 추산 4 MiB를 제한합니다. Source와 archive handle은 원본 본문을 별도로 보유하지 않습니다. 페이지의 지도·부동산 공통 전송 제한은 루트가 주입하는 `atlasFetch`를 사용합니다.

주제 ID와 source-layer는 `admin-sido`, `admin-sigungu`, `admin-dong`, `land`, `water`, `roads`, `rail`, `facilities`, `buildings`입니다. 후보가 아직 포함하지 않은 주제는 catalog에 기재하지 않습니다. 행정구역 라벨은 같은 source-layer의 Point geometry에 `name`을 표시합니다. 부동산의 법정동 5자리 코드와 SGIS 코드를 자동 연결하지 않습니다. ZIP 안의 코드 설명·격자 매핑 표에서도 이 공식 crosswalk는 확인되지 않았습니다.

MVT 인코딩 뒤 매 타일을 다시 디코드해 포함하기로 한 stable ID 집합이 같음을 확인합니다. 주제 최고 줌에서는 포함한 원본 전체 ID의 표현을 확인합니다. 작은 폴리곤이 화면 격자에서 퇴화하면 원본을 버리지 않고 명시적인 외곽선/대표점 표현을 사용합니다. 유효성 검사는 실제 정수 타일 좌표에서 수행합니다. 미터 좌표로 반올림한 후 정수로 다시 반올림하면 원래 유효한 수역이 자기 접촉을 만들 수 있어 이중 반올림을 제거했습니다. 원본 좌표와 속성은 기존 원천 파일에 보존하고 모든 선택 속성은 불변 shard에 포함합니다.

## 실행과 검증

저장소 루트에서 실행합니다. 기존 후보 디렉터리가 있으면 새 이름을 사용해야 하며 덮어쓰지 않습니다.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_admin_boundaries.py tests/test_map_tiles.py tests/test_map_tiles_publication.py tests/test_map_tiles_land.py -q
npm exec vitest run tests/map-tiles.test.ts -- --maxWorkers=2
.venv\Scripts\python.exe -m pipeline.map_tiles --proof --levels sido,sigungu --output .local/map-tiles-20260920/new-seoul-proof
```

현재 검증된 전국 개략 후보를 생성한 명령은 다음과 같습니다. 기록된 원천·체크포인트를 유지해야 하며, 별도 변형을 시험할 때는 새로운 출력 이름을 사용합니다.

```powershell
.venv\Scripts\python.exe -m pipeline.map_tiles --basemap --levels sido,sigungu --reuse-admin-work .local/map-tiles-20260920/work/f33db36f7c588aff21da --reuse-index-work .local/map-tiles-20260920/work/117fdd41fac5b84726dd --output .local/map-tiles-20260920/candidate
```

전국 후보는 `--proof`를 제외합니다. `--basemap`은 이미 게시된 개략 도로·철도와 전국 land/water만 우선 사용하며, 전체 상세 도로·건물 평면을 포함한 후보와 구분합니다. 입력·알고리즘 해시별 SQLite/RTree 작업 색인은 `.local/map-tiles-20260920/work`에 남아 중단 후 재사용됩니다. 기존 산출물을 덮어쓰지 않습니다.

현재 관련 Python **50개**가 통과했습니다. 이전 프런트 계약 검증의 TypeScript **11개**, 전체 typecheck·관련 파일 lint 통과 기록도 유지됩니다. 이번 육지 보완은 프런트 파일을 변경하지 않았고 실제 새 후보를 기존 JS 계약으로 다시 검사했습니다. 이는 전체 프로젝트의 최종 검사 수가 아닙니다. 검사에는 공식 Python writer→공식 JavaScript PMTiles reader의 실제 바이너리 호환, 해시 오류·취소·공유 요청·캐시 제한, 모든 선택 속성 복원, 공간 조회·원본 ID 중복 조각 보존, 읽기 전용 체크포인트 재사용, 정수 격자에서 퇴화한 폴리곤의 보존과 조기 후보 승격 제한이 포함됩니다.

64자리 표시 키를 사용한 최초 서울 실험 `.local/map-tiles-20260920/seoul-density-proof.json`에서 건물 z12(30,152피처), z13(14,612피처)는 decoded tile 1 MiB 상한을 넘겨 실패했습니다. z14(4,376피처)는 gzip 246,259 B로 모든 ID를 유지했습니다. 이후 16자리 표시 키가 도입됐으므로 이를 현 정책의 z13 실패 증거로 확대하지 않습니다. 전국 건물 후보의 현재 시작 줌 설정은 14이며 모든 전국 타일이 통과한 상태는 아닙니다. 상한을 임의로 올리지 않고 실제 밀도를 검증해야 합니다.

## 남은 범위

- 행정동 3,559개는 원본이 확보됐지만 전체 도형 검수·분할·간략화는 후속입니다.
- 법정동과 행정동의 연계는 기준시점이 맞는 공식 연계표를 확보한 뒤 별도 계약으로 구현해야 합니다.
- 2025-06-30 이후 개편은 최신 원천 릴리스로 갱신해야 하며 현재 명칭을 원본 기록에 임의 덮어쓰지 않습니다.
- 도로·시설·건물의 정식 지번 주소는 별도 공식 주소 자료의 정확한 연결 없이는 채우지 않습니다.
- 공개 포인터·브라우저·빌드·배포·Git·키는 변경하지 않았습니다.
