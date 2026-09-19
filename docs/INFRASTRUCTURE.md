# 대한민국 공개 기반 시설 데이터

`pipeline.infrastructure`는 이미 확보한 Geofabrik 대한민국 OSM PBF를 다시 다운로드하지 않고 검사·추출합니다. 모든 형상은 원본의 노드·웨이·멀티폴리곤에서 생성하며 OSM 객체 ID와 출처 링크를 보존합니다.

```powershell
.venv\Scripts\python.exe -m pipeline.infrastructure .local/raw/osm/south-korea-20260916.osm.pbf
.venv\Scripts\python.exe -m pytest tests/test_infrastructure.py
```

## 포함 범위와 의미

| 항목 | 형상 및 의미 |
|---|---|
| 도로 | 고속도로부터 생활도로·보행로·자전거길까지 OSM 선형 |
| 교량·터널 | 해당 도로의 bridge/tunnel/layer 원천 태그만 보존. 높이·심도·입체 구조를 생성하지 않음 |
| 공항 | 공항 경계, 활주로, 유도로, 계류장, 터미널의 등록 형상 |
| 항만 | port/harbour 태그, 부두, 여객선 터미널 및 명시된 웨이 항로 |
| 산업 용지 | landuse=industrial 경계. 법정 산업단지 지정 자료와 구분 |
| 대중교통 | 철도역, 승강장, 버스 터미널·정류장의 정적 위치 |
| 도시 | city/town/village/suburb/quarter 등록 지명과 대표점 |

OSM 미등록 시설, 관측 운행 기록, 실제 교량 높이·터널 심도, 건물 모델은 이 추출기의 검증 범위가 아닙니다. 관계 객체로만 선언된 여객선 노선의 웨이 결합은 포함하지 않습니다. 지표에 투영하는 표시는 실제 고가·지하 공간 배치를 의미하지 않습니다.

## 공간 분할과 성능

- `detail`: 0.25도 격자. 원본 좌표를 보존하며 격자 교차 부분만 잘라 분할합니다.
- 4MiB를 초과하는 detail 격자는 0.05도 격자로 다시 나누어 밀집 도시의 과도한 데이터 수신을 줄입니다. 새 자산은 `grid_degrees=0.05`이고 LOD 종류는 계속 detail입니다. 기존 큰 산출물은 삭제하지 않고 최종 자산 목록에서 대체합니다.
- 서울 등의 0.05도 격자도 4MiB를 넘으면 해당 격자만 0.01도로 한 번 더 분할합니다. `grid_degrees=0.01`에서도 LOD 종류는 detail입니다.
- `overview`: 1도 격자. 간선도로와 주요 시설 중심이며 약 0.0002도 허용오차로 화면용 형상을 단순화합니다.
- 분할 객체는 원본 ID가 동일하므로 화면에서 선택할 때 원본 링크로 연결할 수 있습니다. 총 조각 수는 시설 개수가 아닙니다.
- 지도 높이 60km를 기준으로 두 상세도를 교체하는 것을 권장합니다. 양쪽을 동시에 중복 표시하지 않습니다.
- 추출 과정은 최대 48개 파일 핸들만 사용하며 원본 전체 객체를 메모리에 모으지 않습니다. 최종 변환만 한 격자씩 메모리에 올립니다.

## 통합 계약

산출물은 `public/data/infrastructure/<원본해시>-infrastructure-1/*.geojson`, 감사·자산 명세는 `.local/audit/infrastructure-korea.json`입니다. 자동으로 기존 카탈로그에 등록하지 않습니다.

본체에 독립 `infrastructure` 레이어를 추가하고 감사 파일의 `assets`를 등록해야 합니다. `detail_level`, `min_camera_height`, `max_camera_height`로 현재 뷰에 맞는 자산만 로드합니다. `kind`별 도로·공항·산업 용지 색상을 다르게 표시하되 `height_m`·`depth_m`이 null인 데이터를 임의 돌출하지 않습니다. `observed_at`도 null이며 다운로드 시각을 관측 시각으로 바꾸지 않습니다.

원천: [Geofabrik South Korea](https://download.geofabrik.de/asia/south-korea.html), [OpenStreetMap 저작권 및 ODbL](https://www.openstreetmap.org/copyright). 배포 시 `© OpenStreetMap contributors` 표시와 ODbL 출처를 유지합니다.

실행 결과의 개수·파일 용량·결손 내역은 감사 파일이 기준이며, 실행 전 이 문서에 임의 수치를 기입하지 않습니다.

CLI는 추출 다음에 `validate_staged()`를 실행해 모든 자산 해시, 형상 유효성, 격자 범위, 중복 ID를 검사합니다. 같은 정류장 객체가 `bus_stop`과 `platform`을 동시에 가지면 버스 정류장 표현만 유지하고, 같은 시설의 선·면 표현은 면을 우선합니다. 통합·보고에는 검수 후 `source_feature_counts_unique`를 사용합니다. `source_feature_counts`는 추출 시 원시 분류 횟수, `fragment_count`는 격자·상세도별 지도 조각 수입니다.

## 검색 색인

CLI는 검수 다음에 `pipeline.infrastructure.build_search_index()`를 실행합니다. 각 detail 파일의 SHA-256을 다시 검사하고 이름이 있는 지명·공항·항만·철도역·터미널·산업 용지·정류장을 최대 50,000개까지 담습니다. 도로명만으로 주소를 지오코딩하거나 빠진 시설을 만들어 넣지 않습니다. 별도 호출할 때도 검수 후 실행합니다.

`public/data/infrastructure/<원본해시>-infrastructure-1/search-index.json`의 `entries`는 `id`, `name`, `alt_names`(있을 때), `kind`, `kind_label`, `lon`, `lat`, `range`, `position_evidence`를 제공합니다. 공통 출처와 스냅샷·해시는 최상단 한 번만 저장합니다. 개별 경계·출처 링크·계산 방법을 포함한 전체 색인은 `.local/silver/infrastructure-search-full-<원본해시>-search-index.json`에 보존합니다.

원래 노드는 원천 좌표, 영역은 가장 넓은 격자 조각의 내부점, 선은 가장 긴 조각의 중앙점을 사용합니다. 영역 대표점은 실제 출입구나 정류장 승차 위치라는 뜻이 아닙니다. `candidate_count`, `entry_count`, `omitted_count`로 색인 제한을 명시합니다.

결과 위치와 해시는 `.local/audit/infrastructure-search.json`에 기록합니다. 검색 색인은 GeoJSON 자산이 아니므로 지도로 직접 로드하지 않으며, 배포 업로드 명세에 별도 정적 파일로 포함해야 합니다. 검색 API를 통해 필요한 항목만 반환하면 브라우저가 전체 색인을 받을 필요가 없습니다.

## 지도 통합 시 확인할 사항

- 기존 Overture 도로와 infrastructure 도로를 함께 표시하면 같은 도로가 이중으로 보일 수 있습니다. 새 도로 레이어가 활성화된 뷰에서는 이전 도로 자산을 숨깁니다.
- overview도 도로·밀집 시설(산업 용지/역/버스 터미널)·기타 주요 시설 파일로 나눕니다. 도로와 밀집 시설은 카메라 높이 60~350km, 주요 시설은 60~1500km에서 로드하므로 전국 뷰가 불필요한 도로 JSON을 받지 않습니다. 큰 overview 파일도 0.25도, 필요하면 0.05도로 분할합니다.
- overview/detail의 시설 및 같은 원본이 여러 격자에 존재하므로 클릭·검색 중복 판단은 화면 Feature ID보다 `source_record_id`와 `kind`를 기준으로 합니다.
- `vertical_geometry=unavailable`을 보고 지하철·도로 터널을 지표 경로로 표시하는지 명확히 설명합니다. `layer=-1`을 미터 단위 심도로 바꾸지 않습니다.
- `name`이 없는 시설은 이름을 추측하지 말고 `kind_label`과 OSM ID로 식별합니다.
- 도로 `access=private`와 같은 접근 제한 태그를 보존하지만 이 자료를 길찾기나 출입 가능성 판단에 사용하지 않습니다.

## 확인된 원천 결손

2026-09-15T20:20:37Z PBF에서 [무계로 교량 way/842288033](https://www.openstreetmap.org/way/842288033)은 구성 노드가 1개뿐이므로 선형을 만들 수 없습니다. 2026-09-16에 [공식 OSM 객체 API](https://api.openstreetmap.org/api/0.6/way/842288033/full)를 별도 확인했고 version 2, 수정 시각 2026-07-05T02:40:56Z에서도 노드 1개로 확인됐습니다. 임의의 두 번째 좌표를 만들지 않고 격리했습니다.

격리 기록은 `.local/audit/infrastructure-quarantine.json`, 현재 공식 응답과 해시는 `.local/raw/osm/way-842288033-current.osm` 및 `.meta.json`에 있습니다. 이는 원천 결손이며, 다른 도로 구간의 추출 실패나 전체 무계로가 없다는 뜻은 아닙니다.

## 2026-09-16 로컬 검수 결과

카탈로그 등록·Cloudflare 배포와 구분한 데이터 산출물 완료 상태입니다. 최종 검수 요약은 `.local/audit/infrastructure-ready.json`, 등록할 자산은 `.local/audit/infrastructure-korea.json`의 `assets`가 기준입니다.

| 고유 원천 분류 | 객체 수 |
|---|---:|
| 도로 웨이 구간 | 1,797,717 |
| 버스 정류장 | 110,159 |
| 지명 | 20,243 |
| 산업 용지 | 8,978 |
| 철도역 | 1,365 |
| 버스 터미널 | 894 |
| 승강장 | 2,013 |
| 공항/비행장 노드·영역 | 100 |
| 활주로 / 유도로 / 계류장 / 터미널 | 141 / 5,099 / 119 / 32 |
| 항만 / 부두 | 35 / 1,804 |
| 여객선 터미널 / 웨이 항로 | 465 / 357 |

이 수치는 실제 시설의 공인 전국 총수가 아니라 OSM 객체 ID와 분류 조합의 개수입니다. 공항 노드와 경계 등이 같은 실제 시설을 표현할 수 있고, 산업 용지는 법정 산업단지와 다릅니다.

최종 5,354개 자산, 표시용 조각 2,280,350개, 총 2,436,034,188 bytes입니다. 최대 파일은 4,170,962 bytes로 4MiB보다 작습니다. 모든 원본·세분화 형상과 격자 경계를 검사했고, 세분화 전후 고유 원천 개수가 동일함을 확인했습니다. 첫 검수에서는 중복 표현 103,146개를 정리했으며 마지막 전수 검사에서는 추가 중복이 없었습니다. 유효한 선형을 만들 수 없는 원천 객체 1건은 위와 같이 격리했습니다.

검색은 이름이 있는 후보 131,235개 중 우선순위에 따라 50,000개를 담았고 81,235개는 색인 제한으로 제외됐습니다. 제외된 시설의 지리 형상은 detail 데이터에 남습니다. 공개 compact 색인은 10,773,281 bytes이며 전체 출처를 포함한 검수용 색인은 별도 보존했습니다.

`tests/test_infrastructure.py` 6개 테스트가 통과했습니다. 실제 osmium XML 읽기, 관계 멀티폴리곤 식별, 경계 교차 선형·폴리곤 구멍 보존, 원천 높이·심도 미생성, 카탈로그 미변경, 공간 세분화 전후 원천 수 유지, 검색 개수 제한을 확인했습니다. 생산 자산 검수는 테스트 fixture와 별도로 실제 전국 자료에 실행했습니다.
