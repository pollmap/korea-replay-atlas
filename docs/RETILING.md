# 전국 공간 재타일링

이 배치는 확보한 원본 지오메트리와 출처를 보존하면서 브라우저가 화면에 필요한 자료만 읽을 수 있게 재구성한다. 대한민국 모든 실제 건물의 완전성이나 실측 정확도를 증명하는 과정은 아니다.

## 실행과 재개

프로젝트 루트에서 실행한다. 기존 원본·기존 공개 릴리스는 삭제하지 않으며, `public/data/catalog.json`도 이 배치가 변경하지 않는다.

```powershell
.venv/Scripts/python.exe -u -m pipeline.retile --phase buildings --workers 2 --compact
.venv/Scripts/python.exe -u -m pipeline.retile --phase geometry --workers 1
.venv/Scripts/python.exe -u -m pipeline.retile --phase assemble
```

건물과 도로·철도 단계는 서로 다른 산출물을 쓰므로 동시에 실행할 수 있다. 중단 후 같은 입력으로 같은 명령을 다시 실행하면, 원본·산출물 해시가 일치하는 완료 셀의 체크포인트를 재사용한다. 건물은 최대 두 작업 프로세스를 기본값으로 사용한다.

입력 목록은 `.local/retile/<입력 지문>/input-buildings.json`과 `input-catalog.json`에 고정된다. 재타일링 결과는 `public/data/retiled/<입력 지문>/`에 생성된다. 최종 v1 호환 카탈로그는 `.local/retile/catalog.json`이며, 별도 무료 정적 배포 패키징 단계가 이를 V2 공개 목록으로 변환한다.

## 건물 계층과 정밀도

- 원본 높이 검토 정책은 `pipeline/height_quality.py`가 결정한다. 높이나 의미가 확인되지 않은 객체는 외곽선으로 유지한다.
- 상세 모델은 약 0.05° 공간 그룹으로 나누고, 실제 직렬화 크기가 4MiB를 넘으면 피처 경계에서 분할한다. 단일 피처가 이보다 크면 원본 형상을 유지하되 정적 배포의 24MiB 안전 한도에서 거부한다.
- `--compact`는 같은 원본 0.1° 셀 안의 작은 파일을 4MiB 이내로 묶는다. 원래 삼각형·구멍·출처 ID·전체 원본 속성을 유지한다. 모든 정점의 공통 로컬 프레임 변환 후 추가 float32 오차를 측정하며 1cm를 넘으면 실패한다.
- 국토 → 지역 → 도시 → 동네의 공간 계층을 사용한다. 각 단계는 공간의 긴 축을 기준으로 나누고 중간 자식은 최대 4개, 상세 파일을 직접 받는 말단은 최대 8개·합계 24MiB로 제한한다. 중간 LOD는 대표 부분집합이며 별도 개수로 기록한다. 상세 건물 수에 중복 합산하지 않는다.
- 각 상세 타일에 원본 `source_record_id`, 원천값·표시값·높이 의미·품질 플래그를 담는다. 원본 전체 속성은 `original_properties`에 보존한다.
- 모든 값이 빈 문자열인 메타데이터 열도 glTF의 양수 bufferView 길이 요건을 만족하도록 1바이트 패딩을 둔다. 문자열 오프셋은 0이므로 값은 계속 빈 문자열이다. 기존 중간 GLB를 보정할 때도 원본 파일을 덮어쓰지 않는다.

## 기존 상세 파일을 재사용하는 계층 개선

초기 후보의 1°×0.5° 지역은 하나의 REPLACE 노드 아래 상세 파일을 직접 연결했다. 대전 지역은 143개·342.3MiB, 가장 큰 수도권 지역은 300개·762.4MiB였다. Cesium의 기본 REPLACE 탐색은 부모를 교체하기 위해 보이지 않는 형제도 적재하므로, 캐시 예산을 초과한 동일 파일의 반복 요청이 발생했다. 파일 형식 검사를 통과해도 이 작업량 문제가 검증되는 것은 아니다.

`pipeline.hierarchy`는 기존 상세 GLB를 다시 인코딩하지 않고 새로운 해시 경로에 중간 계층만 만든다. 원래 상세 GLB의 `source_record_id`를 읽어 대표 건물을 정확한 하위 지역에 연결하며, 각 대표 LOD 파일은 384KiB 이하이다. 단순히 가까운 사각형에 건물을 배정하지 않는다.

```powershell
.venv/Scripts/python.exe -u -m pipeline.hierarchy --catalog .local/retile/catalog.json --retile-work .local/retile/b908f45777c95a08
```

출력은 `public/data/hierarchy/<지문>/tileset.json`과 `.local/hierarchy/<지문>/catalog.json`이다. 기존 공개 파일과 기존 후보 카탈로그는 변경하지 않는다. 같은 지문 경로에 다른 내용이 이미 있으면 덮어쓰지 않고 실패한다. 이후 전체 재타일링도 `spatial-retile-2`부터 동일한 제한 계층을 기본으로 사용하므로, 과거 출력 지문에 새 구조를 덮어쓰지 않는다.

기하 생성 기준 `2901e65b57e114fc`는 상세 GLB 3,448개·건물 3,687,673개를 그대로 참조한다. 중간 노드는 859개이고, 855개 대표 LOD를 생성했다. 직접 상세 자식의 실제 최대치는 7개·25,099,556바이트이다. 대표 LOD의 31,191개 행은 여러 단계에 반복되므로 상세 건물 수와 합산하지 않는다. 최초 계층 변환의 추가 부동소수 연산 오차 상한은 5.77×10⁻⁹m이며, 이는 원천 좌표·높이의 실제 정확도를 뜻하지 않는다.

현재 출판 입력은 `.local/lod-error/d17d093917bde3e1/catalog.json`이다. 생략된 상세 건물의 중심과 실제 유지 대표 중심 사이의 EPSG:5179 최근접 최대거리를 오차에 반영한다. GLB 4,303개·변환·경계는 그대로 두고 루트 JSON만 새 해시 경로로 만든다. 높이 정확도나 표면 오차로 표현하지 않는다.

```powershell
.venv/Scripts/python.exe -m pipeline.lod_error --catalog .local/hierarchy/2901e65b57e114fc/catalog.json --add-asset .local/audit/dokdo-landmask-asset.json
.venv/Scripts/python.exe -m pipeline.static_release --catalog .local/lod-error/d17d093917bde3e1/catalog.json --stage
```

첫 명령은 GeoJSON 2,352개의 실제 예산과 모든 GLB SHA도 대조한다. 같은 GLB 집합·메타데이터 감사 digest가 일치하는 `validation-reuse.json`으로 기존 코어·확장 감사를 재사용하며, 새 정책은 `policy-proof.json`에서 별도로 확인한다. 새 GLB를 만들었다면 이 재사용으로 검사를 생략할 수 없다.

브라우저에서는 `skipLevelOfDetail`, `loadSiblings`, 비행 목적지 미리 적재와 메모리 예산을 함께 검증해야 한다. 카메라를 정지한 뒤 새로운 요청이 멈추는지, 도시 왕복 후 동일 파일이 계속 재요청되는지를 성능 완료 조건에 포함한다.

## 도로·철도

상세 GeoJSON의 원본 좌표는 그대로 유지한다. 공통 속성은 `metadata.shared`, 각 피처의 나머지 속성은 `metadata.rows`에 담고, 피처에는 `metadata_index`와 그리기에 필요한 최소 속성을 둔다. 복원 순서는 공통 속성 → 해당 행 → 피처 속성이다. 생성 시 모든 피처의 속성과 좌표를 원본과 비교한다.

각 파일에 바이트 수·피처 수·정점 수·확대 단계·표시 높이 범위를 기록한다. 철도는 200km 이상 시점에만 EPSG:5179에서 100m 허용치로 단순화한 표시용 개략 자료를 사용하며, 상세 자료에는 원본 좌표가 남는다. 개략의 출처 ID는 유지되고 선택 속성에 단순화 사실이 명시된다.

## 감사

```powershell
.venv/Scripts/python.exe -m pytest tests/test_hierarchy.py tests/test_retile.py tests/test_glb_merge.py tests/test_mesh_metadata_audit.py -q
.venv/Scripts/python.exe -m pipeline.mesh_metadata_audit --catalog .local/retile/catalog.json --output .local/audit/retiled-mesh-metadata.json
node scripts/validate-glb.mjs --catalog .local/retile/catalog.json --output .local/audit/retiled-gltf-validator.json
```

계층만 갱신한 후보를 감사할 때는 두 검사 명령의 `--catalog`를 새 `.local/hierarchy/<지문>/catalog.json`으로 지정하고 결과도 같은 작업 폴더에 기록한다. `source-proof.json`은 기존 상세 파일의 해시와 대표 ID 연결을, `audit-<카탈로그 해시>.json`은 전체 상세 참조·부모 경계·중복·상대 변환·교체 작업량을 검증한다.

`audit.json`에는 상세 모델·외곽선의 합계, 원본 셀별 ID·형상 해시, 추가 좌표 오차, 원본 도로·철도 피처 수가 기록된다. 메타데이터 감사는 외부 tileset JSON을 재귀 탐색하고 상세·대표 LOD를 구분한다. Khronos 검사는 glTF 코어 형식을 검사하며, 검사기가 지원하지 않는 메타데이터 확장은 Python 검사가 별도로 검증한다.

체크포인트·병합 전 파일·후보 LOD 파일은 재현을 위해 남기므로 출력 폴더의 전체 파일 수는 배포 파일 수와 다르다. 무료 한도 판정은 최종 카탈로그에서 실제 참조하는 모든 종속 파일을 따라가는 배포 패키징 단계에서 수행한다.
