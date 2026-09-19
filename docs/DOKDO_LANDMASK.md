# 독도 해안선 표시 보완

기준일: 2026-09-17. 원천 검수와 별도 자산 생성은 완료했습니다. 최종 카탈로그 결합·브라우저 확인·공개 배포는 해당 릴리스의 검증 기록으로 판정합니다.

기존 Natural Earth 파일에는 독도 주변의 작은 폴리곤이 하나 있지만 범위가 `[131.860148,37.241606,131.862522,37.244006]`이고 정점이 5개뿐입니다. 독도 프리셋 좌표 `(131.869,37.242)`는 이 폴리곤 밖에 있습니다. 전국 경계를 2048×1600 이미지 한 장으로 그리는 경로도 독도 위도에서 약 325×395m/픽셀로 거칠어 작은 섬의 육지 색을 표현하기 어렵습니다. Natural Earth는 [1:10,000,000 소축척 자료](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/)이며 상세 해안선으로 사용하지 않습니다.

`pipeline/coastline.py`는 기존 Geofabrik 대한민국 OSM PBF에서 독도 주변 해안선을 공간적으로 선택합니다. 메모리에 전국 지점 위치를 적재하지 않고 필요한 노드만 읽습니다. 닫힘, 유효성, 반시계 방향, 영역 내 포함, 원천 ID 중복, 폴리곤 겹침과 누락 노드를 검사합니다. 열린 선을 임의로 닫거나 좌표를 단순화·이동·보정하지 않습니다. [OSM 해안선 규칙](https://wiki.openstreetmap.org/wiki/Tag:natural%3Dcoastline).

| 확인 항목 | 결과 |
|---|---|
| 원천 | `.local/raw/osm/south-korea-20260916.osm.pbf` |
| 원천 취득 | 2026-09-16 04:38:39 UTC |
| 확인한 원천 SHA-256 | `3135b6ec7b3d94294735de0aa47d49a58c06b638bf33ba44e30b5728cc5b76c7` |
| 검사한 전국 해안선 / 선택한 독도 폴리곤 | 5,630개 / 86개 |
| 정점 / 고유 원천 노드 | 2,355개 / 2,269개 |
| 서도 / 동도 원천 | `way/456788679` v168 / `way/456788681` v153 |
| 출력 | `/data/geography/dokdo-landmask-b3dee7cd1449904abfba.geojson` |
| 출력 SHA-256 | `b3dee7cd1449904abfbae16f09c87f9f23b074c265529f2a0b5ba8fe476e0e07` |
| 크기 | 171,324바이트 |
| 화면 조건 | 카메라 높이 0m 이상·60,000m 미만, 공간 범위가 교차할 때 |
| 변경한 원천 좌표 / 만든 높이 / 누락 노드 | 0 / 0 / 0 |

자산의 `source_id=osm`, `layer=terrain`, `format=geojson`, 피처의 `kind=land`, `natural=coastline`을 사용합니다. 원천 태그·OSM way ID·버전·시각·노드 ID를 보존하며 `evidence_type=source_attribute`로 표시합니다. [OSM ODbL 조건과 출처 표시](https://www.openstreetmap.org/copyright)를 적용합니다.

기존 `MapScene`의 배치 지오메트리 경로와 `primitive-renderer.ts`의 `GroundPrimitive(ClassificationType.TERRAIN)`가 지형 위에 `#dce4cd` 색을 입힙니다. 새로운 화면 코드나 DEM 수정은 필요하지 않습니다. 재구성한 좌표나 높이, 실측 해안선으로 표기하지 않습니다. 원래 Natural Earth의 거친 색은 별도 계층으로 남으므로 이 추가 자료가 기존 소축척 표현 전체를 교체했다는 의미는 아닙니다.

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_coastline.py
.venv\Scripts\python.exe -m pipeline.coastline
```

13개 회귀 검사가 통과했습니다. 감사 파일은 `.local/audit/dokdo-landmask.json`, 카탈로그 결합용 descriptor는 `.local/audit/dokdo-landmask-asset.json`입니다. 생성기는 기존 catalog·원본 PBF·기존 공개 파일·지형 바이너리를 수정하지 않습니다. descriptor는 별도 릴리스 조립 단계에서 포함합니다.
