# 3D 생성 경로 종료 · 2026-10-09

맛동산은 지정 9개 권역의 아파트 2D 탐색을 유지합니다. 이번 변경은 3D 전용 생성 코드·의존성을 제거하며, 원본·2D 지도·공개 릴리스를 삭제하지 않습니다. 공개 배포와 운영 적용은 별도 확인해야 합니다.

## 종료한 경로

- 메시·GLB 결합·3D Tiles 계층·LOD 검증·스트리밍 계층 생성
- 입체 지형과 DEM·GHSL 높이 다운로드
- 3D 건물 부품의 전국 생성·차분·게시
- 지형에 심도를 적용한 지하 역사·기록 재생 생성
- 태양 계산 및 SunCalc, GLB validator와 Python 3D 전용 패키지

과거 모듈 실행은 공통 `3D generation is retired` 오류와 2D 파이프라인 안내로 종료됩니다. 외부 자료를 요청하거나 출력 파일을 만들기 전에 멈춥니다. `pipeline.national partition`의 원천 외곽선 분할은 남고 `process`는 종료합니다. `scripts/validate-glb.mjs` 역시 짧은 종료 오류를 반환합니다.

삭제한 패키지: `suncalc`, `@types/suncalc`, `gltf-validator`, `mapbox_earcut`, `pygltflib`, `quantized-mesh-encoder`. 의존성 설치본을 새로 복제하거나 기존 공유 `node_modules`를 변경하지 않았습니다. 다음 설치는 갱신된 잠금 파일을 사용합니다.

## 보존한 2D 처리

| 코드 | 보존 이유 |
|---|---|
| `geometry_compaction.py` | 원본 ID·좌표·속성을 유지하는 GeoJSON 압축과 공간별 분할. 기존 `retile`의 2D 부분을 옮김 |
| `building_partitions.py` | 원천 Parquet 외곽선 분할·체크포인트. 기존 `national`의 2D 부분을 옮김 |
| `buildings.py` | 건물 외곽선·원천 ID와 높이/층수의 원천 속성 보존. 지형·GHSL은 요청하지 않음 |
| `height_quality.py`·`building_parts.py` | 2D 타일의 속성 품질 검사 및 원천 부품·외곽선 연결 근거 검사 |
| `depth.py`·`rail.py` | 동일 호선·역명 해석, 원천 심도·시각 검증. 3D 게시 진입만 종료 |
| `map_tiles*`, `water_partition.py`, `geometry_budget.py` | 현행 2D 타일·수역·정점 예산 처리 |

건물 높이가 없더라도 외곽선을 버리지 않습니다. 지면 고도를 0으로 만들거나 새로운 DEM을 받아 채우지 않습니다. 원천 높이와 층수 추정 속성은 의미를 구분해 남깁니다. 기존 압축·공유 버전을 읽기 위한 일반 자료 계약과 출처 기록은 남깁니다.

원천 분할의 과거 고정 30GiB 제한도 제거했습니다. 현재 배치의 비압축 크기 세 배와 체크포인트 최소 공간으로 필요한 작업 여유를 계산합니다. 부족하면 다음 배치 시작 전에 중단합니다.

## 읽기 전용 저장공간·예약 조사

측정은 2026-10-09 KST에 진행했습니다. 아래 값은 파일 바이트이며 삭제 후 확보량이 아닙니다. 이번 작업에서 원본·3D 바이너리·예약 작업은 삭제하지 않았습니다.

| 위치(각 프로젝트 루트 기준) | 확인 결과 | 판정 |
|---|---|---|
| 로컬 `.local/retile/b908f45777c95a08/geometry.sqlite` | 3,388,444,672바이트, `records`·`complete` 테이블 | 2D 도로·철도 스풀. 폴더명만으로 3D 삭제 금지 |
| 로컬 `.local/national` | Parquet 2,626개, 444,230,598바이트 | 2D 원천 외곽선 분할이므로 보존 |
| 로컬 `.local/building-parts-national/2026-08-19.0/national-building-parts-candidate-1/final/meshes` | GLB 16개, 262,072바이트 | 3D 전용 후보. 삭제 자체는 미실행 |
| 로컬 `.local/building-parts-delta/6057f4f7c4c008d4ccd7` | 3D 확장자 8개, 27,113,256바이트 | 차분 후보. 삭제 미실행 |
| 로컬 `.local/compression-probe-20260917/client` | 3D 확장자 4개, 15,291,200바이트 | 과거 압축 실험. 삭제 미실행 |
| 로컬 `public/data/atlas` | JSON 4개, 2,076바이트 | 대용량 3D 본문 없음 |
| VPS 작업공간 `.local/interrupted-transfer-20261003/data/terrain/merged-07474a7c8addf078/12/7014/2866.terrain` | 25,088바이트, 디스크 할당 28,672바이트 | 중단 전송 잔여 3D 파일. 삭제 미실행 |

VPS 프로젝트 경로에서 `.glb/.gltf/.b3dm/.terrain`을 검사했습니다. 심볼릭 링크·의존성 설치 폴더·비밀 설정은 따라가지 않았습니다. 실행 중인 프로젝트 API·collector의 명령에는 3D 생성기가 없었습니다. 확인한 `/etc/systemd/system`, `/etc/cron.d`, `/etc/cron.daily`와 현재 사용자 crontab에는 해당 프로젝트의 3D 예약 실행을 찾지 못했습니다. 로컬의 `KOREA REPLAY Apartment Collector` 예약에도 3D 생성 명령은 없었습니다. 이 범위 밖 사용자 계정의 예약까지 확인한 것은 아닙니다.

## 검증

- 웹: 100파일, 1,156검사 통과
- Python: VPS 기존 환경에서 57검사 통과, 기존 노트북 지리 환경으로 110검사 통과
- 타입·린트·프로덕션 빌드 통과
- 건물 외곽선 좌표와 ID 보존, 원천 높이 미확인 상태 유지, 지형/GHSL 신규 요청 없음
- GeoJSON 무손실 복원, 공간 분할·수역·철도 원천 연결 및 2D 지역 타일 회귀
- 22개 레거시 모듈/명령 종료 경로에서 생성 파일 0개 확인
- 빌드 산출물 1,371개, 72,966,998바이트. GLB·입체 지형·Cesium 파일 0개

검증용 소스 묶음은 약 379KB이며 기존 원본 체크아웃을 변경하지 않았습니다. 실제 PC 화면과 공개 주소 검증은 상위 2D 배포 절차에서 수행합니다. 이 문서는 바이너리 정리로 수GB를 확보했거나 공개 배포를 마쳤다는 주장으로 사용하지 않습니다.
