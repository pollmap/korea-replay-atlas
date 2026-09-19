# 대한민국 데이터 커버리지

갱신: 2026-09-17. 원천 정제·상세 병합 지문은 `b908f45777c95a08`, 기하 공간 계층은 `2901e65b57e114fc`, 현재 단계 전환 JSON은 `d17d093917bde3e1`입니다. 전체 GLB 코어·확장 검사와 새 JSON이 같은 4,303개 GLB를 참조하는 해시 증빙은 완료됐습니다. GPU 목표·정적 배포 묶음·원격 공개·복구는 별도 검증 중입니다.

현재 패키징 입력은 `.local/lod-error/d17d093917bde3e1/catalog.json`이며 SHA-256은 `fa4d71606f7688afdb2d4ae8e460d4e5a5b7ec057390ff22c022f8f06aae9dd6`입니다. 기존 `.local/retile/catalog.json`은 앞 단계 후보입니다. 새 공개 릴리스·전체 배포 파일 수·URL은 최종 검증 후 [STATUS.md](STATUS.md)에 기록합니다. 로컬 생성·파일 감사 수량을 원격 공개 완료 수로 읽지 않습니다.

## 현재 원천·생성 대사

| 지표 | 확인값 |
|---|---:|
| 확보한 전국 원천 행 | 3,730,526 |
| 원천 지리 셀 처리 완료 / 전체 | 1,241 / 1,241 |
| 기존 세 도시 자료를 포함한 정규화 입력 자산 | 1,244 |
| 새 상세 3D 모델 | 3,687,673 |
| 새 검토 외곽 행 | 42,853 |
| 모델 + 검토 외곽 | 3,730,526 |
| 중간 LOD 대표 행(여러 단계 중복 포함) | 31,191 |

중간 LOD는 상세 건물의 대표 부분집합이며 3,730,526개 합계에 더하지 않습니다. 원천의 지리 셀 처리율과 생성 완료는 대한민국 모든 실제 건물의 존재·위치·높이·지붕이 검증되었다는 뜻이 아닙니다. 원천 행은 확보한 공개 객체 수이고 전국 실제 건물 총수의 분모가 아닙니다.

### 원천 높이 분류와 검토 외곽 분류

| 분류 | 행 수 | 의미 |
|---|---:|---|
| 원천 높이 속성 | 63,673 | 속성 존재, 개별 실측 인증 아님 |
| 층수×3m 추정 | 146,776 | 개별 높이 추정 |
| GHSL 2018 100m 평균 추정 | 3,477,570 | 원천 전체의 약 93.2% |
| 원천 높이 미확인 | 42,507 | 기존 원천 높이 분류 |
| 추가 검토로 외곽 처리 | 346 | 시작 높이 의미 미확정 335 + 1m 미만 원천 높이 9 + 추가 낮은 표고 2 |
| 최종 검토 외곽 합계 | 42,853 | 원천 미확인 42,507 + 추가 검토 346 |

**42,507은 원천 높이가 없던 분류이고, 42,853은 새 정책을 적용한 최종 외곽 표시 수입니다.** 높이가 있더라도 해석·품질 검토가 필요하면 외곽으로 유지합니다. 낮은 표고 검토 전체 3개 중 하나는 이미 원천 미확인에 들어 있어 추가 수량에 중복 합산하지 않습니다. 상세 규칙은 [HEIGHT_QUALITY.md](HEIGHT_QUALITY.md)에 있습니다.

## 현재 후보가 실제 참조하는 파일

| 대상 | 파일 수 |
|---|---:|
| 상세 건물 GLB | 3,448 |
| 중간 LOD GLB | 855 |
| 전체 GLB | 4,303 |
| root tileset JSON | 1 |
| 3D 전체 참조 | 4,304 |
| 검토 외곽 GeoJSON | 968 |
| 도로·철도 등 geometry | 1,376 |
| 재타일링 범위 합계 | 6,648 |

합계는 `3,448 + 855 + 1 + 968 + 1,376`이며 표의 소계 행을 중복 합산하지 않습니다. 총 9,863,088,259 B, 최대 파일 4,192,456 B입니다. 지형·검색·기상·웹·추가 공개 목록은 이 합계에 포함하지 않습니다. 최종 배포 파일 수와 무료 한도는 별도 정적 묶음 검사에서 판정합니다.

기준 manifest는 `.local/lod-error/d17d093917bde3e1/retile-reference-manifest.json`이며 SHA-256은 `867914e67f0c7e3a4dfca62bdbf8be76060c7ad5cb9b1e6772bddf5c918ddab4`입니다. 같은 폴더의 `retile-reference-summary.json`에 범위·합계·최대 파일을 기록했습니다. 상세 GLB 3,448개의 SHA를 보존하고 대표 원천 ID 11,947개를 실제 상세 파일의 ID에 연결했습니다. 최종 LOD의 31,191행에는 같은 원천의 여러 단계 표현이 포함됩니다.

전체 GLB 4,303개의 SHA를 새 후보에서 다시 확인했습니다. 이전 `.local/hierarchy/2901e65b57e114fc/`의 `gltf-validator.json`은 코어 오류 0·경고 0, `mesh-metadata-audit.json`은 메타데이터 오류 0 및 상세 건물/ID 3,687,673행 일치를 기록합니다. 현재 후보의 `validation-reuse.json`은 그 전체 해시 집합과 metadata digest가 같은지 검사합니다. 42,853개의 검토 외곽은 GLB가 아닌 기존 GeoJSON에 그대로 남습니다.

기존 상세 병합의 추가 float32 반올림 최대는 0.344861mm, 기존 반올림 포함 상한은 0.556206mm입니다. 최초 계층 갱신은 상세 GLB 재인코딩·좌표 재양자화 없이 상대 변환 연산 오차 상한 5.77×10⁻⁹m를 확인했습니다. 이번 오차 정책 JSON은 그 변환도 그대로 유지하므로 추가 좌표 변환은 0입니다. 이는 데이터 변환의 수치 오차이고 실제 지형·건물의 측량 정확도는 아닙니다.

### 앞 단계 파일 수와의 관계

이전 `.local/retile/audit.json`은 46개 LOD·대표 10,929행, 지역 JSON 46개·root 1개, 3D 참조 3,541개, 재타일링 참조 5,885개·9,806,829,413 B를 기록합니다. 당시 manifest SHA는 `0c44d7245ab0d404b439d65cf0fed81d2b516545050c7eb9f2339a24091d8844`입니다. 현재 후보는 그중 기존 계층 파일 93개를 참조에서 빼고 새 계층 파일 856개를 참조합니다. 원본 파일을 삭제한 것은 아닙니다.

현재 후보로 준비하려면 경로를 명시합니다.

```powershell
.venv/Scripts/python.exe -m pipeline.static_release --catalog .local/lod-error/d17d093917bde3e1/catalog.json --stage
```

이 명령은 로컬 묶음 생성이며 공개 배포나 GPU 목표 통과가 아닙니다. 공간 분할·중간 LOD의 범위는 [HIERARCHY.md](HIERARCHY.md), 재현·재개는 [RETILING.md](RETILING.md), 배포는 [STATIC_DEPLOYMENT.md](STATIC_DEPLOYMENT.md)를 따릅니다.

## 실제 로딩 예산과 독도 표시

현재 목록의 GeoJSON 2,352개는 SHA-256과 실제 피처·정점·바이트를 대조했습니다. 누락 예산 7자산을 보완했으며, 예산이 없거나 실제보다 작으면 정적 출판 단계가 실패합니다. 이 검사는 표시할 데이터의 처리량을 확인하며 지도의 실측 정확도를 인증하지 않습니다.

독도 OSM 원본의 닫힌 해안선 86개·2,355정점은 별도 terrain 자산으로 추가했습니다. 60km 미만 카메라에서 기존 GroundPrimitive로 표시하며 원천 DEM은 변경하지 않았습니다. 공개 파일은 `/data/geography/dokdo-landmask-b3dee7cd1449904abfba.geojson`, 171,324 B입니다. 닫힌 섬·암초의 형상 수를 독도의 공인 섬 개수로 해석하지 않습니다.

## 검색과 심도 보강

검색은 후보 131,235개 중 게시 50,000개를 유지한 v2 색인입니다. 227파일·46,291 B 초기 manifest이며 기존 결과와 대조한 304개 질의에서 불일치가 없었습니다. 주소 전체와 미게시 후보까지 검색된다고 표현하지 않습니다. [SEARCH_V2.md](SEARCH_V2.md)에 범위와 CPU 한계가 있습니다.

서울 심도는 공식 277행 중 높이 숫자 유효 276행, 좌표 연결 265역입니다. 새 품질 파일은 265개 기존 geometry를 전부 보존하며 원천 높이·+100.3m 오프셋·기준면 미확정 상태를 추가합니다. 역간 보간은 계속 추정이며 전국 지하 시설 실측 모델이 아닙니다.

아래는 기존 자동 범위 보고의 역사적 증거입니다. 지형 원천과 관측 시각의 기준을 보존하기 위해 남기며, 새 타일의 지역별 실기기 검수 결과와 혼합하지 않습니다.

## 이전 릴리스의 탐색 중심점 검사

아래는 재타일링 전 `pub-56cc8fc00c4a79a8`을 2026-09-16T06:34:00.905337Z에 검사한 과거 결과입니다. 자산 ID와 파일 수를 새 재타일링의 결과로 사용하지 않습니다. 각 지역의 지정 탐색 중심점이 당시 파일 범위에 들어가는지를 검사했습니다. 행정구역 전체 커버리지나 그 점의 실제 객체 존재를 증명하지 않습니다. 건물 3D는 catalog의 셀 범위보다 좁은 tileset root 범위를 사용합니다.

| 위치 | 경도, 위도 | 건물 3D 범위 포함 파일 | 존재 확인 지형 level | 기타 범위 포함 형식 |
|---|---|---|---|---|
| 대한민국 전체 (전국) | 127.8, 35.2 | 없음 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 대전 (대전역 · 원도심) | 127.433, 36.332 | buildings-daejeon, buildings-kr-1274-363 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 세종 (정부세종청사) | 127.265, 36.504 | buildings-kr-1272-365, buildings-sejong | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 청주 (상당구 · 원도심) | 127.489, 36.643 | buildings-cheongju, buildings-kr-1274-366 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 오송역 (충북 청주시) | 127.327, 36.62 | buildings-kr-1273-366 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 서울 (시청 · 광화문) | 126.978, 37.566 | buildings-kr-1269-375 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | depth:geojson, infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 부산 (부산역 · 북항) | 129.041, 35.115 | buildings-kr-1290-351 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 인천 (송도) | 126.637, 37.39 | buildings-kr-1266-373 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 대구 (동성로) | 128.596, 35.869 | buildings-kr-1285-358 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 광주 (동구 · 원도심) | 126.918, 35.147 | buildings-kr-1269-351 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 제주 (제주시) | 126.527, 33.499 | buildings-kr-1265-334 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 울릉도 (경북 울릉군) | 130.884, 37.489 | 없음 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |
| 독도 (경북 울릉군) | 131.869, 37.242 | 없음 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | infrastructure:geojson, infrastructure:search-index, radar:imagery, rail:geojson, satellite:imagery, terrain:geojson, terrain:quantized-mesh |

지형의 level은 타일 세분 수준이며 미터 단위 위치 정확도가 아닙니다. 높은 level이 일부 지점에 있어도 전국 고해상도 지형이 완성된 것으로 해석하지 않습니다.

| 지형 자산 | level | 선언 타일 / 파일 존재 / 누락 |
|---|---:|---:|
| terrain-combined | 0 | 2 / 2 / 0 |
| terrain-combined | 1 | 1 / 1 / 0 |
| terrain-combined | 2 | 1 / 1 / 0 |
| terrain-combined | 3 | 1 / 1 / 0 |
| terrain-combined | 4 | 2 / 2 / 0 |
| terrain-combined | 5 | 4 / 4 / 0 |
| terrain-combined | 6 | 9 / 9 / 0 |
| terrain-combined | 7 | 30 / 30 / 0 |
| terrain-combined | 8 | 110 / 110 / 0 |
| terrain-combined | 9 | 160 / 160 / 0 |
| terrain-combined | 10 | 528 / 528 / 0 |
| terrain-combined | 11 | 1866 / 1866 / 0 |
| terrain-combined | 12 | 6906 / 6906 / 0 |

## 이전 릴리스의 교통·기상 파일 검사

| 구분 | 공개 파일 / 로컬 존재 | 형식 | 재생 유효 시간 범위 (UTC) |
|---|---:|---|---|
| rail | 7 / 7 | {"replay": 2, "geojson": 5} | 2026-09-08T21:01:00Z → 2026-09-09T15:32:00Z |
| bus | 0 / 0 | {} | 없음 → 없음 |
| depth | 2 / 2 | {"geojson": 2} | 없음 → 없음 |
| satellite | 1 / 1 | {"imagery": 1} | 2022-01-01T00:00:32Z → 2022-01-01T00:10:32Z |
| radar | 13 / 13 | {"imagery": 13} | 2026-09-16T04:20:00Z → 2026-09-16T05:25:00Z |
| infrastructure | 5355 / 5355 | {"geojson": 5354, "search-index": 1} | 없음 → 없음 |

- **rail**: GeoJSON 선로/역과 replay 운행 기록을 구분합니다. KORAIL 역별 시각과 역간 계산 위치는 전국 열차의 연속 GPS 관측이 아닙니다.
- **bus**: 공개 자산이 없으면 버스 실시간 위치가 연결된 상태가 아닙니다. API 키 존재 여부는 이 보고서에서 읽지 않습니다.
- **depth**: 공식 역사 심도 점과 역간 보간 선입니다. 실제 지하 시설의 전체 3D 측량 모델이 아닙니다.
- **satellite**: 프레임의 관측 날짜를 확인해야 합니다. 2022년 공식 예제는 현재 실시간 위성 영상이 아닙니다.
- **radar**: 공개 영상의 색상을 보존한 프레임입니다. 영상 갱신 시각과 수집 날짜를 분리하며 숫자 강우량/결측을 역산하지 않습니다.
- **infrastructure**: 도로·시설의 원천 형상입니다. 자산의 bbox가 넓다고 모든 위치의 시설이 포함되었다고 판단하지 않습니다.

- **satellite 프레임 실제 관측 시각**: 2022-01-01T00:00:32Z → 2022-01-01T00:00:32Z (UTC). 재생 유효 종료 시각과 다릅니다.
- **radar 프레임 실제 관측 시각**: 2026-09-16T04:20:00Z → 2026-09-16T05:20:00Z (UTC). 재생 유효 종료 시각과 다릅니다.

위 과거 스냅샷에서는 정적 공개 파일 존재를 검사했습니다. 이 실행은 실시간 API 호출, 자동 수집 지속 실행, 브라우저 렌더링 성공, 외부 배포를 검증하지 않습니다.

## 이전 스냅샷의 증거

- 원천 셀 합계 대사: True; 3,730,526행 기준 일치: True
- 로컬 자산 파일 누락: 0; 검사한 JSON manifest 해시 불일치: 0
- catalog SHA-256: `19e27cfa33eaa9368faf8152b9bec1274fd487f46b6f3879faa027e47a82b051`
- cells manifest SHA-256: `a74533dc541e9427ddc7f7a167c373a09516d504723e2291d8b2c5d7bb8217ad`
- 당시 공개 immutable catalog 한 버전과 SQLite 읽기 트랜잭션 한 번을 고정했습니다. 서로 다른 파일의 완전한 동시 시점은 아니므로 변환 완료 수와 공개 모델 수가 일시적으로 어긋날 수 있습니다.
- GeoJSON 본문·모든 GLB의 건물 ID 중복·측량 정확도를 전수 검증한 보고서가 아닙니다. 지형은 선언된 타일의 실제 파일 존재까지 확인합니다.
- 셀별 상태, 원천 행 수, 지역별 자산 ID, 영상 프레임 시각, 지형 level별 타일 수와 누락은 `.local/audit/coverage.json`에 보존됩니다.

