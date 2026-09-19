# 전국 건물의 제한된 교체 계층

갱신: 2026-09-17. 현재 후보는 상세 건물 3,687,673개를 3,448개 GLB에서 그대로 읽고, 855개 중간 LOD를 통해 국토·지역·도시·동네 순으로 세부 정보를 표시한다. 전체 GLB 형식·메타데이터와 상세 파일 보존 검사는 통과했다. GPU 목표와 실제 공개 배포·원격 롤백은 별도 검증 중이다.

## 수정한 문제

이전 계층은 하나의 1°×0.5° 지역에 상세 GLB를 바로 연결했다. 대전 지역은 143개·342.3MiB, 가장 큰 수도권 지역은 300개·762.4MiB였다. Cesium의 기본 REPLACE 탐색은 부모를 교체하기 전에 보이지 않는 형제도 요청한다. 제한된 캐시에서 이 파일들이 반복 퇴출·재요청되면서 대전 장면의 누적 수신량이 약 39.5GB까지 증가했다. 이는 형식 오류나 원천 파일 용량을 그대로 나타내는 수치가 아니다.

근거는 설치된 `node_modules/@cesium/engine/Source/Scene/Cesium3DTilesetBaseTraversal.js`의 `updateAndPushChildren`과 `.local/audit/production-frame-samples.json`이다. 엔진의 세부 단계 건너뛰기와 형제·비행 목적지 미리 적재 설정을 수정한 실험은 앞 단계 계층에서 요청 종료를 확인했다. 아래 계층은 교체 단위 자체도 작게 만든다. 엔진 실험의 프레임 수치를 새 후보 검증으로 자동 승계하지 않는다.

## 공간 분할과 중간 표현

1. 상세 타일 중심을 실제 지리 거리의 긴 축으로 나누고 두 번의 균등 분할을 묶어 중간 자식을 최대 4개로 구성한다. 같은 중심점이 있어도 파일 이름의 결정론적 정렬로 분할을 끝낸다.
2. 상세를 직접 받는 그룹은 최대 8파일·직렬화 크기 합계 24MiB에서 멈춘다. 현재 실제 최대는 7파일·25,099,556 B이다.
3. 원천 대표 건물 11,947개의 ID를 상세 GLB 메타데이터와 정확히 연결한다. 가까운 타일의 사각형만 보고 배정하지 않는다. 각 하위 지역의 대표를 분산 선택하며 중간 GLB는 384KiB 이하로 제한한다.
4. 원래 상세 GLB를 상대 경로로 참조한다. 버텍스·삼각형·source ID·전체 원천 속성을 다시 인코딩하지 않는다. 각 부모의 로컬 프레임에 맞춘 상대 변환만 계산한다.
5. 모든 부모의 지리 경계가 자식 경계를 포함하고, 단계 전환 오차가 단조롭게 작아지는지 검사한다. 단계 전환 수치는 측량 정확도나 엄밀한 도형 오차로 표시하지 않는다.
6. 중간 콘텐츠가 없는 노드도 펼쳐 REPLACE에 실제 필요한 최초 콘텐츠 집합을 센다. 현재 최대는 8파일·25,099,556 B이다. 이 값은 한 교체 묶음의 파일 바이트이며 브라우저 전체 메모리나 디코딩된 GPU 메모리 상한은 아니다.

중간 LOD는 실제 원천 건물의 **대표 부분집합**이다. 멀리서 모든 건물을 표시하는 모델이 아니며, 여러 단계의 같은 건물이 반복될 수 있다. 전체 상세 건물 수와 31,191개의 중간 행을 합산하지 않는다. 높이 추정과 검토 상태는 원래 계약을 유지한다.

## 대표 간격을 반영한 단계 전환

최초 제한 계층의 `공간 대각선×0.0075` 오차는 대표 건물을 생략한 정도를 반영하지 못했다. 대전 중심의 한 말단은 상세 9,897개를 대표 7개로 표시하면서 오차를 75.79m로 잡았다. low 화면의 SSE가 21.91px라 기준 40px를 넘지 않아 상세 진입이 늦었다. `skipLevelOfDetail`은 이 기준을 강제로 넘기는 옵션이 아니므로 반복 요청을 막는 설정은 유지한다.

`pipeline.lod_error`는 실제 GLB의 피처별 로컬 AABB 중심을 타일 Z-up → ECEF(EPSG:4978) → EPSG:5179로 변환한다. 현재 대표 GLB가 유지한 `source_record_id`를 상세 집합에서 제외하고, 나머지 상세 중심에서 실제 표시 대표 중심까지의 최근접 거리 최댓값을 계산한다. 부모 오차는 `max(기존 오차, 이 최댓값, 자식 오차)`다. 콘텐츠 없는 4개 노드는 가장 가까운 대표 콘텐츠 조상의 집합을 기준으로 삼는다. 임의의 일괄 최소 거리를 넣지 않으며, 상세 잎의 오차 0은 유지한다.

이 수치는 **투영 좌표계의 피처 중심 간격**이다. 표면 오차·측량 정확도·실제 위치의 정확도를 인증하지 않는다. 좌표·변환·경계·GLB 바이트는 바꾸지 않고 새 루트 JSON만 만든다.

| 기본 카메라의 말단 예 | 기존 GE → 새 GE | 기존 SSE → 새 SSE |
|---|---:|---:|
| 대전 | 75.79 → 3,197.81m | 21.91 → 924.38px |
| 부산 | 98.21 → 6,695.84m | 17.83 → 1,215.85px |
| 송도 | 196.75 → 2,390.72m | 35.36 → 429.65px |
| 서울 | 88.90 → 3,453.50m | 19.24 → 747.38px |

설치된 Cesium Camera/TileBoundingRegion으로 CSS 1280×720, drawing buffer 896×503, pixel ratio 0.7, pitch −60°를 계산했다. 네 지역 모두 low 기본 기준 40px를 넘는다. 동적·메모리 조정 SSE를 제외한 CPU 계산이며 실제 브라우저 성능은 별도 측정한다. 시야와 교차하는 상세의 정점·인덱스 버퍼 합계는 대전 21.79MiB, 부산 31.97MiB, 송도 18.66MiB, 서울 55.44MiB다. 메타데이터·조상·드라이버 메모리를 제외하므로 실제 GPU 점유량으로 표시하지 않는다.

## 현재 산출물

| 항목 | 확인값 |
|---|---:|
| 중간 노드 / 최대 깊이 인덱스 | 859 / 5 |
| 재사용 중간 GLB / 대표 행 | 855 / 31,191 |
| 콘텐츠 없는 중간 노드 | 4 |
| 재사용 상세 GLB / 상세 건물 | 3,448 / 3,687,673 |
| 전체 GLB / 루트 JSON | 4,303 / 1 |
| 이번 정책의 새 3D 파일 / 전체 3D 참조 | 루트 JSON 1 / 4,304 |
| 중간 GLB 합계 | 81,434,052 B |
| 루트 JSON | 3,556,481 B |
| 새 계층의 상세 GLB 재인코딩 | 0 B |
| 최초 계층의 상대 변환 연산 오차 상한 | 5.77×10⁻⁹m |
| 이번 JSON 정책 변경의 추가 좌표 변환 | 0 |

루트 JSON은 노드 4,307개의 트리를 담는다. 초기 트리 파싱 비용은 브라우저 검수 대상이며, 필요한 경우 상세·중간 GLB를 보존한 채 외부 지역 JSON으로 나눌 수 있다. 현재 파일 감사만으로 초기 지연이 없다고 선언하지 않는다.

검토 외곽 42,853개는 기존 968개 GeoJSON에 남는다. 도로·철도 geometry까지 포함한 재타일링 범위는 6,648파일·9,863,088,259 B이다. 지형·검색·기상·웹·공개 목록은 이 합계와 별도로 최종 묶음에서 센다.

## 후보와 재현 명령

- 현재 입력: `.local/lod-error/d17d093917bde3e1/catalog.json`
- 현재 SHA-256: `fa4d71606f7688afdb2d4ae8e460d4e5a5b7ec057390ff22c022f8f06aae9dd6`
- 루트: `/data/hierarchy/d17d093917bde3e1/tileset.json`
- 기하 생성·검사의 기준 후보: `.local/hierarchy/2901e65b57e114fc/catalog.json`
- 앞 단계 후보: `.local/retile/catalog.json` — 보존용이며 최신 계층이 아님

기존 상세 자료로 계층만 다시 만드는 명령:

```powershell
.venv/Scripts/python.exe -u -m pipeline.hierarchy --catalog .local/retile/catalog.json --retile-work .local/retile/b908f45777c95a08
```

동일 입력·규칙은 동일 해시 경로를 사용한다. 다른 내용이 이미 있으면 덮어쓰지 않고 실패한다. 새 전체 재타일링도 `spatial-retile-2`부터 제한 계층을 기본으로 사용하며 과거 출력 지문에 덮어쓰지 않는다.

기존 4,303개 GLB를 재사용하는 최종 오차 정책과 목록 생성:

```powershell
.venv/Scripts/python.exe -m pipeline.lod_error --catalog .local/hierarchy/2901e65b57e114fc/catalog.json --add-asset .local/audit/dokdo-landmask-asset.json
```

이 단계에서 모든 GeoJSON 2,352개를 실제 계수하고 누락 예산 7자산을 보완했다. `osm-water-korea`는 97,352정점이다. 독도 OSM의 닫힌 해안선 86개·2,355정점은 별도 terrain GeoJSON이며 기존 DEM은 바꾸지 않는다. `static_release`는 실제 피처·정점·바이트와 다른 선언을 거부한다.

최신 후보 검사와 로컬 배포 묶음 생성:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_lod_error.py tests/test_static_release.py -q
.venv/Scripts/python.exe -m pipeline.static_release --catalog .local/lod-error/d17d093917bde3e1/catalog.json --stage
```

`static_release`에서 `--catalog`를 생략하면 앞 단계 후보를 읽는다. 위 명령은 비용을 발생시키는 서비스 활성화나 원격 업로드를 수행하는 명령이 아니다. 실제 배포와 롤백은 [STATIC_DEPLOYMENT.md](STATIC_DEPLOYMENT.md)를 따른다.

## 검증 증빙과 남은 조건

기하 생성과 기존 전체 GLB 검사의 아래 파일은 `.local/hierarchy/2901e65b57e114fc/`에 있다. 새 후보의 `validation-reuse.json`은 GLB 4,303개 SHA 집합과 메타데이터 감사의 전체 content digest가 같은지 검증한다. 기하가 바뀌지 않은 이번 단계에서 전체 GLB 형식 검사를 중복 실행하지 않았다.

| 증빙 | 확인한 내용 |
|---|---|
| `source-proof.json` | 기존 상세 GLB 해시·대표 ID의 정확한 연결·원래 월드 변환 |
| `audit-fda522af162ec358.json` | 모든 상세 참조, 중복·누락, 부모 경계, 상대 변환, 작업량 |
| `local-frame-bounds.json` | 3,448개 상세 파일의 로컬 위치 범위 최대 7,226.834m로 변환 오차 검사의 보수적 범위 안에 있음 |
| `replacement-budget-audit.json` | 빈 중간 노드의 후손까지 포함한 실제 교체 묶음 |
| `gltf-validator.json` | GLB 4,303개 코어 오류 0·경고 0 |
| `mesh-metadata-audit.json` | GLB 4,303개 메타데이터 오류 0, 상세 ID 3,687,673행 |
| `retile-reference-manifest.json` | 최초 제한 계층의 상세·외곽·도로·철도 참조 목록 |
| `retile-reference-summary.json` | 최초 제한 계층의 파일·바이트 대사 |

새 정책 증빙은 `.local/lod-error/d17d093917bde3e1/`의 `policy-proof.json`, `validation-reuse.json`, `glb-reuse-manifest.json`, `camera-policy-proof.json`, `geometry-budget-audit.json`이다. 같은 폴더의 새 `retile-reference-manifest.json`과 `retile-reference-summary.json`은 루트를 바꾼 6,648파일·9,863,088,259 B를 대사한다. 지형에 속한 독도 보완 파일은 이 부분합에 더하지 않는다.

새 정책·정적 출판 회귀 23개가 통과했다. 기존 계층·재타일링·높이 관련 59개와 중복 합산하지 않으며 최종 전체 웹/Python 검사 수는 통합 실행 후 별도로 확정한다. Radeon 860M 일반 탐색의 프레임 P95≤33ms와 브라우저 분할 생성≤8ms는 아직 목표 달성으로 판정하지 않았다. 도시 왕복 메모리·지역별 장면·공개 API/캐시/검색/공유·원격 롤백 검증이 남아 있다.
