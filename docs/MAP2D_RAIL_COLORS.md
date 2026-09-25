# 2D 지하철 노선색 · 2026-09-26

2D 벡터 지도는 모든 철도 선형에 `#97857d`를 사용했습니다. 3D 쪽에 이미 있던 서울교통공사 노선색 계약이 2D에 연결되지 않은 문제를 수정합니다. 3D 렌더러·철도 좌표·원본 타일은 변경하지 않습니다.

## 색상과 노선 식별

- 색상은 [서울교통공사 실시간 열차 운행정보](https://smss.seoulmetro.co.kr/traininfo/traininfoUserView.do)의 1~8호선 메뉴 기호에서 확인한 표시색입니다. 2호선은 `#2fae35`입니다. 색상별 원천 이미지 SHA는 [기존 조사](RAIL_PRESENTATION_AUDIT.md)에 있습니다. 운영기관 웹의 표시색이며 인쇄·시설물의 유일한 표준색이라고 주장하지 않습니다.
- 명시적인 `서울 지하철 N호선`·`수도권 전철 N호선` 이름은 그 노선색으로 표시합니다.
- 기존 지도에는 `2호선`처럼 지역이 생략된 이름이 있습니다. 카메라 위치나 번호로 서울 노선이라고 추정하지 않습니다. 2026-09-16 OSM PBF의 `type=route`, 서울 명시 노선명·ref·`network=수도권 전철`, 공사·계획 상태 없는 관계에서 **직접 소속한 subway way**를 연결했습니다. 경부선처럼 일반 철도와 공용하는 `railway=rail`은 중립색으로 유지합니다.
- 같은 way가 서로 다른 노선에 연결되면 이 추가 표에서는 채택하지 않습니다. 역 이름만으로 노선색을 추정하지 않습니다. 식별되는 역 기호·역 이름에는 선형과 같은 색상 함수를 적용하고, 미연결 역은 중립색입니다.

원본 PBF SHA-256: `3135b6ec7b3d94294735de0aa47d49a58c06b638bf33ba44e30b5728cc5b76c7`.

| 노선 | 원본 식별자로 연결한 지도 피처 수 |
|---|---:|
| 1 | 56 |
| 2 | 89 |
| 3 | 43 |
| 4 | 61 |
| 5 | 48 |
| 6 | 38 |
| 7 | 44 |
| 8 | 24 |

총 403개는 여러 지도 상세도에 걸친 피처 수이며 실제 노선·역 개수가 아닙니다. 운영 route 관계 155개를 감사했고, 방향·운행 구간별 중복 관계가 포함됩니다. OSM의 소속 관계는 커뮤니티 원천 속성이며 운영기관이 검증한 측량선형으로 승격하지 않습니다.

## 재현·표시

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pipeline.map2d_rail_colors
npx.cmd vitest run tests/map2d-rail-style.test.ts tests/vector-style.test.ts
.venv/Scripts/python.exe -X utf8 -B -m unittest tests.test_map2d_rail_colors
```

생성기는 로컬 원본·타일 색인을 읽기 전용으로 열어 `shared/data/map2d-rail-colors.json`에 식별자·관계 근거만 저장합니다. 이 파일은 **© OpenStreetMap contributors, ODbL 1.0** 파생 자료로 코드 MIT 라이선스와 다릅니다. [OSM 이용조건](https://www.openstreetmap.org/copyright)을 유지합니다. 원본 지도 geometry·타일 바이트는 수정하지 않으며, 새 색상 표를 통해 이미 받은 타일을 표시하므로 전체 지도 재다운로드가 필요하지 않습니다.

벡터 선, 역 테두리, 라벨은 동일한 MapLibre 색상 표현식을 사용합니다. GeoJSON 호환 경로에는 동일한 원본 way ID 색상 조회를 제공합니다. 부산·대구·인천·대전 등 다른 도시, 서울 9호선·광역전철 등은 이 1~8호선 계약의 대상이 아닙니다. 별도 운영기관 표시색과 고유 노선 식별을 검증하기 전에는 번호를 서울 색으로 바꾸지 않습니다.
