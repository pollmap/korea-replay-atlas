# 높이·지형 품질 계약과 실제 검증

2026-09-16 기준. `pipeline.height_quality`의 `korea-height-quality-1`은 원천 높이를 바꾸지 않고 입체 표시 채택 여부를 결정합니다. 정밀 측량 자료를 새로 확보한 것 또는 대한민국 모든 건물의 정확도를 인증한 것은 아닙니다.

## 전국 원천에 적용한 실제 결과

SQLite 자산 목록을 고정하고 정규화 파일 1,244개의 SHA-256과 행 수를 확인했습니다. 원천 3,730,526행을 한 번씩 검사했습니다. 결과는 `.local/audit/height-quality.json`에 있습니다.

| 항목 | 실제 정책 판정 수 |
|---|---:|
| 입체 표시 채택 가능 | 3,687,673 |
| 외곽선으로 유지 | 42,853 |
| 기존 높이 미상 | 42,507 |
| 기존 모델 중 추가 검토·외곽선 분리 | 346 |
| 시작 높이의 의미 미확정 | 335 |
| 원천 높이 1m 미만 | 9 |
| 타원체 지표고 −30m 미만 | 3 |

낮은 지표고 3개 중 1개는 이미 높이 미상이었습니다. 따라서 추가 분리 수는 `335 + 9 + 2 = 346`입니다. `3,687,673 + 42,853 = 3,730,526`이며 원천을 삭제하지 않았습니다. 검토 임계값은 실제 오류를 확정하는 기준이 아닙니다.

이 수치는 원천 품질 정책의 판정 수입니다. 후속 `.local/retile/audit.json`에서 실제 생성한 상세 모델 3,687,673개·검토 외곽 42,853개와 일치함을 확인했습니다. 기존 공개 카탈로그의 3,688,019개 모델·42,507개 외곽선은 재타일링 전 감사 시점의 값입니다. 최신 계층 후보의 전체 GLB 코어·확장 감사는 아래처럼 완료했지만, 카탈로그 활성화·실제 공개 배포·프레임 목표는 별도로 확인해야 합니다.

GHSL 2018 100m 격자 평균 3,477,570개(93.22%), 층수×3m 146,776개는 추정으로 유지됩니다. 지표고 검토 2개를 제외한 입체 표시 추정값은 3,624,344개입니다. 추정값이 수치 검사를 통과해도 개별 실측 높이가 되는 것은 아닙니다.

## 새 계층에서도 보존한 품질 계약

2026-09-17 갱신. 현재 패키징 입력은 `.local/lod-error/d17d093917bde3e1/catalog.json`이며 SHA-256은 `fa4d71606f7688afdb2d4ae8e460d4e5a5b7ec057390ff22c022f8f06aae9dd6`입니다. 이전 `.local/retile/catalog.json`을 현재 후보로 혼동하지 않습니다.

상세 GLB 3,448개와 중간 LOD 855개는 모두 이전 파일의 해시를 그대로 재사용합니다. 전체 4,303개 GLB의 코어 오류 0·경고 0, 확장 메타데이터 검사 오류 0은 새 후보의 해시 집합·기존 감사 content digest와 동일함을 확인했습니다. 상세 건물과 source ID 행은 모두 3,687,673이며 검토 외곽 42,853개는 기존 GeoJSON을 유지합니다. 대표 LOD의 31,191행은 여러 단계에서 반복되므로 품질 분류나 상세 건물 수에 더하지 않습니다.

이번 변경은 공간 계층과 표시 단계만 바꿉니다. 93.2% GHSL 추정, 층수 추정, 원천 높이·min_height·해안 표고·심도 기준면의 검토 상태는 변하지 않습니다. 상세 좌표를 재양자화하지 않았고, 새 상대 변환 연산 오차 상한 5.77×10⁻⁹m는 실측 높이·위치 정확도를 뜻하지 않습니다. 중간 LOD도 원천 건물의 대표 부분집합으로 표시합니다.

기하 생성 증빙은 `.local/hierarchy/2901e65b57e114fc/`의 `source-proof.json`, `audit-fda522af162ec358.json`, `gltf-validator.json`, `mesh-metadata-audit.json`입니다. 새 후보 폴더의 `policy-proof.json`과 `validation-reuse.json`은 같은 GLB·변환·경계를 확인하며, 표시 전환은 생략 상세 중심과 유지 대표 중심의 EPSG:5179 최근접 최대거리로 계산합니다. 피처 중심의 간격이지 표면 오차나 높이 정확도가 아닙니다. 이번 JSON 변경의 추가 좌표 변환은 0입니다. GPU 프레임 P95≤33ms, 브라우저 분할 작업≤8ms, 공개 배포·원격 복구는 여전히 최종 검증 대상입니다. 구조와 실행 경로는 [HIERARCHY.md](HIERARCHY.md)를 참고합니다.

## 함수와 저장 계약

`evaluate_feature(feature)`는 입력을 변경하지 않는 순수 함수입니다. `raw_height`는 원천 높이 속성이 있는 경우만 보존하고, 층수·GHSL 값은 `input_height`에 남깁니다. `render_height`는 채택 가능할 때만 숫자이고, 항상 **지면에서 상단까지**를 뜻합니다. `render_min_height`는 지면에서 하단까지입니다. 미채택일 때 두 값은 null입니다.

`height_semantics`, `height_semantics_basis`, `quality_flags`, `quality_state`, `source_record_id`, `upstream_record_id`, `quality_policy`를 함께 전달합니다. 지표고는 현재 EPSG:4979 타원체 높이이며 `ground_accuracy_verified=false`입니다. `quality_state=verified_semantics`는 높이 필드의 의미만 확인했다는 뜻으로 실측 정확도 인증이 아닙니다.

- 원천 높이 1m 미만, 지표고 −30m 미만, 미확정 floating 높이: 외곽선으로 유지.
- 유한하지 않은 값·음수 시작 높이·유효하지 않은 높이: 입체 표시 제외.
- 원천 높이를 층수×3m로 덮어쓰거나 음수 지표고를 0m로 고치지 않음.
- 층수·GHSL 추정: 추정 표시를 유지하며 입체 표현 가능.
- 원본과 기존 카탈로그는 정책 감사 과정에서 변경하지 않음.

## Overture 명세와 OSM 높이 의미 충돌

[공식 릴리스표](https://docs.overturemaps.org/release-calendar/)에서 `2026-08-19.0`은 `v1.18.0`을 사용합니다. 해당 태그 commit은 `79016c3a56e5c0d7660f911117d59801451e918d`입니다. [고정 버전 정의서](https://github.com/OvertureMaps/schema/blob/79016c3a56e5c0d7660f911117d59801451e918d/schema/buildings/defs.yaml)는 height를 최저점~최고점 거리라고 설명하지만 실제 표본은 OSM 원천 값을 그대로 보존합니다.

[OSM 공식 정의](https://wiki.openstreetmap.org/wiki/Key:min_height)는 height를 지면~상단 높이로 정의합니다. 다음 정확한 과거 버전 API 값과 로컬 Overture 값을 대조했습니다.

| OSM 기록 | height | min_height | 채택하는 지면 대비 범위 |
|---|---:|---:|---|
| [w711239221@3](https://api.openstreetmap.org/api/0.6/way/711239221/3.json) | 18m | 15m | 15~18m |
| [w1214950322@1](https://api.openstreetmap.org/api/0.6/way/1214950322/1.json) | 112m | 100m | 100~112m |

일괄 덧셈하면 상단이 각각 33m·212m로 잘못 커집니다. 따라서 위 기록의 Overture ID·릴리스·값이 모두 일치하는 경우만 의미 확인 상태를 부여합니다. ID만 같고 버전·값이 바뀌면 다시 미확정으로 처리합니다.

전국 원본의 `min_height>0`은 404행이며, 채택된 정규화 ID에 해당하는 336개는 모두 OSM 출처입니다. 개별 과거 OSM 버전을 모두 조회한 것은 아닙니다. 이 중 위 건물 1개만 검증된 계약으로 채택되고 335개는 외곽선으로 분리됩니다. 위 부품 1개는 현재 전국 부모 건물 정규화 범위에 포함되지 않습니다.

## 해안 지표고: 독립 자료와 같은 수직 기준으로 비교

공개 AWS Copernicus GLO-30 COG 3개와 PROJ EGM2008 지오이드 그리드를 실제 확보했습니다. Copernicus EPSG:3855(EGM2008) 높이를 WGS84 타원체 높이로 변환한 뒤 기존 Mapzen EGM96→타원체 변환값과 비교했습니다. 볼파크 변환은 허용하지 않았고 픽셀 중심 보간·해시·좌표·변환명을 기록했습니다.

| 표본 | 기존 Mapzen 타원체고 | Copernicus 타원체고 | 차이 |
|---|---:|---:|---:|
| 고성 삼산면 미룡리 | −78.748m | 32.693m | 111.441m |
| 서해대교 안전센터 | −33.124m | 35.004m | 68.128m |
| 시흥 인근 원천 외곽 | −30.409m | 24.794m | 55.203m |

이는 서로 다른 지오이드 기준만으로 설명되지 않는 큰 자료 차이입니다. 그러나 Copernicus는 건물·수목을 포함하는 **DSM**이므로 값을 실측 지면으로 자동 교체하지 않았습니다. 지표면 기준과 원천 관측 시기·도형 정합의 추가 검증이 필요합니다. 원본 DEM·모델은 변경하지 않았습니다.

실제 결과: `.local/audit/ground-quality-comparison.json`. 원본 COG: `.local/raw/ground-quality/`. 그리드: `.local/grids/us_nga_egm08_25.tif`.

공식 근거: [AWS 공개 접근](https://registry.opendata.aws/copernicus-dem/), [Copernicus DSM·EGM2008·품질 설명](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM). 서로 다른 자료를 비교했지만 원천 보간 자료가 일부 공유될 수 있어 완전히 독립된 측량 검증으로 표현하지 않습니다.

## 서울 지하철 심도

[서울교통공사 공식 설명](https://data.seoul.go.kr/dataList/OA-13305/F/1/datasetView.do)에서 지반고·레일면고는 `해발고도+100.3m`입니다. 로컬 277행 모두 텍스트 칸은 채워져 있으나 **276행만 숫자**이며 암사역사공원 1행은 `-`입니다. 숫자 행의 지반고−레일면고와 기록 심도는 0.06m 허용 오차 안에서 일치했습니다.

`depth.py`는 원천 지반고·레일면고, 100.3m 오프셋, 산술 검산 잔차를 보존합니다. 정확한 측량 기준면이 확인되지 않아 `absolute_vertical_datum_verified=false`이며 지형고−기록 심도로 계산한 기존 위치를 유지합니다. EGM96 또는 EGM2008이라고 임의로 선언하지 않습니다.

실제 기존 265역에 정보만 추가한 `.local/review/depth-vertical/seoul-a1854bf19cdf0a27.geojson`을 생성했습니다. 위치 변경은 0건, 공개 카탈로그 변경은 0건입니다. 감사는 `.local/audit/depth-vertical-quality.json`입니다. 역 사이 높이는 여전히 보간 추정이며 실제 터널 종단면이 아닙니다.

배포 묶음에 포함할 준비로 동일 내용을 `/data/depth/seoul-quality-542ae10dceed8896c2c4.geojson`에 해시 고정 복사했습니다. 파일은 489,957 B이며, 기존 265개 geometry와 전부 대조해 변경이 없음을 다시 확인했습니다. `.local/audit/depth-quality-asset.json`은 재타일링 catalog 조립 단계에 전달하는 descriptor입니다. 품질 감사 원본의 `private_review_only`는 감사 당시 상태를 보존하며, 이 복사 자체가 공개 배포 또는 절대 수직 기준 검증 완료를 뜻하지 않습니다.

## 부품과 공식 건물 자료

서울 기존 표본을 새 규칙으로 재검사했습니다. 186개 부분 건물 중 119개는 원천 검토 후보이고, 높이 의미와 단순 입체화 조건을 통과한 것은 **51개**입니다. 이 51개도 지형 배치 전입니다. 지붕 모양이 평면이 아닌 33개는 별도 모델링이 필요합니다. 검토 이유는 중복될 수 있습니다. 대전 표본 18,053개 부모 건물에서 parts는 0개였습니다. 일부 표본에 부품이 없다는 결과를 전국 부품 부재로 확대하지 않습니다.

`building_parts`의 `candidate=true`는 검토 자료 보존을 뜻합니다. `render_eligible=false`, `extrusion_candidate_pending_ground`를 별도로 저장하여 후보를 공개 모델로 오해하지 않도록 했습니다. 부모와 부품을 함께 중복 체적으로 그리거나 미확인 형상을 만들어 넣지 않습니다.

공식 GIS건물통합정보 **실제 SHP 원본은 여전히 미확보**입니다. 2026-09-16 VWorld 파일 페이지의 다운로드 함수가 로그인 검사 후 반환하는 것을 다시 확인했습니다. 키 없이 보낸 1건짜리 공식 API 요청은 `PARAM_REQUIRED: key`를 반환했습니다. 옛 NSDI 파일 페이지는 연결되지 않았습니다. 검증 기록은 `.local/audit/official-building-access.json`입니다. 인증 우회·키 발급·결제·제공 신청은 하지 않았습니다.

공식 CSV/GeoJSON 어댑터도 `raw_height_m`와 `render_height_m`를 분리하며 A16 값이 존재해도 측정 정의·개체 연결이 미확정이면 렌더링 높이를 채우지 않습니다. SHP/GPKG 직접 판독과 실제 공식 자료 공간 결합은 원본 확보 후 남은 작업입니다.

## 재현 명령과 검증

```powershell
.venv\Scripts\python.exe -m pipeline.height_quality
.venv\Scripts\python.exe -m pipeline.height_quality --ground-review
.venv\Scripts\python.exe -m pipeline.depth
.venv\Scripts\python.exe -m pipeline.building_parts --sample all
.venv\Scripts\python.exe -m pytest tests/test_height_quality.py tests/test_ground_quality.py tests/test_official_buildings.py tests/test_building_parts.py tests/test_depth_vertical.py tests/test_subway_depth.py -q
```

높이 관련 독립 검사 당시 58개가 통과했습니다. 이는 최신 전체 Python 테스트 수가 아니며 최종 전체 실행 결과는 [VALIDATION.md](VALIDATION.md)에서 별도로 확정합니다. Copernicus 픽셀 보간·결측 거부, 원천 불변, 해시 불일치 보고 거부, 원천 0.01m 보존, 잘못된 수치 제외, OSM 과거 버전의 높이 의미, 추정 등급 보존, 공식 오프셋 산술·기준면 미확정 처리를 검증했습니다. 실제 측량 오차를 검증하는 테스트는 아닙니다.
