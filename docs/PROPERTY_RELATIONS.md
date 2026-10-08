# 아파트 관계·테마 데이터 계약

기준일: 2026-10-08. `pipeline/property_relations.py`와 `shared/property-relations.ts`의 구현 계약입니다. 별도 그래프 서버, 임베딩, 유료 API를 추가하지 않습니다. **데이터 구조와 어댑터의 구현이며, API/UI 공개 완료 문서가 아닙니다.**

## 사용 목적

단지 선택 후 공식 기본정보·주소·지역·주변 시설·계획의 근거를 확인하고, 확인된 수치로 후보를 거릅니다. 지도를 대신하는 전체 지식망이나 근거 없는 종합 점수를 만들지 않습니다.

## 식별자와 근거

하나의 SQLite 파일은 하나의 `property-*` 릴리스에 고정합니다. 다른 릴리스로 열면 실패합니다. 기관의 원본 ID를 유지하며 단지명이 같아도 합치지 않습니다.

| 개체 | 식별 예 | 현재 연결 방식 |
| --- | --- | --- |
| 단지 | `molit-apt:11110:11110-102` | 기존 공식 ID·주소 검증 결과만 재사용 |
| 주소 | `reported-address:<sha256>` | 단지 ID와 원문 주소의 해시. 공인 주소 ID인 것처럼 표시하지 않음 |
| 지역 | `legal-region:11110` | 법정 지역 코드. 행정동·과거 경계와 자동 연결하지 않음 |
| 공식 식별자료 | `kapt:A11034001` | K-apt 식별 근거를 가리키는 원천 레코드 |
| 역·학교·시설 | `osm:node/42`, `osm:way/42` | OSM ID와 좌표 의미 보존 |
| 건물·거래·개발계획 | 원천 이름공간 + 원본 ID | 공통 개체 형식 지원. 실제 적재 어댑터는 후속 연결 필요 |

각 근거에는 원천 ID·URL·가공 파일 SHA256·원천 레코드 ID·연결 방법·관찰일·유효 시작/종료일을 저장합니다. 처리 결과 문서의 SHA는 원문 파일의 SHA와 같은 뜻이 아닙니다. 원문 추적은 해당 게시 문서의 기존 원본 해시 목록을 따릅니다.

- `verification=verified`: 해당 연결이나 값이 명시된 원천에 의해 뒷받침됩니다. 원천 전체의 완전성이나 좌표 실측 정확성을 뜻하지 않습니다.
- `unverified`: 기본 관계 조회·숫자 테마에서 제외합니다.
- `inferred`: 기본 관계 조회에서 제외합니다. 사용자가 추론 관계를 명시적으로 포함할 때만 반환합니다.
- `claim_type=source / calculated / inferred`: 원천값·산식 결과·추론을 따로 표시합니다.
- 관찰일 이후, 유효 시작일 이후·종료일 이전의 근거만 해당 날짜 조회에 사용합니다. 현재 자료를 과거 사실처럼 반환하지 않습니다.

## 저장·조회 한도

| 영역 | 구현 |
| --- | --- |
| 관계 | `nodes`, `node_evidence`, `edges`, `evidence` 관계형 테이블 |
| 숫자 필터 | `facts(field,numeric_value,node_id)` 색인 |
| 검색 | FTS5. 검색 문자열은 따옴표로 감싼 최대 10개 토큰으로 변환, SQL 직접 삽입 금지 |
| 공간 | RTree 후보 + 구면 직선거리, 최대 3km. 도보·운전 시간이나 배정 학교가 아님 |
| 그래프 | 최대 2단계·100개 노드·300개 관계·512개 근거 |
| 조회 작업량 | 각 노드의 양방향 후보를 각 300개까지 검사. 초과 시 `truncated=true` |
| 공간 작업량 | 최대 1,000개 공간 후보 검사·100개 반환. 초과 시 `truncated=true` |
| 재실행 | 같은 식별자·내용·근거면 삽입 생략. 충돌하는 동일 ID는 오류 |
| 가져오기 | 한 문서의 실패 시 SAVEPOINT 롤백. SHA 불일치·중복 ID는 실패 |

잘린 결과를 지역 전체 목록이나 시설 완전성으로 표현하지 않습니다. 그래프 응답의 `evidence_ids` 및 각 관계의 `evidence_id`로 근거를 펼칠 수 있습니다.

## 현재 어댑터

### 공식 서울 단지 기본정보

`ingest_official_facts(store, path, expected_sha256)`는 기존 `seoul-apartment-facts-<release>.json` 형식을 읽습니다. 확인된 기관 ID·주소 연결을 가져오고, 다른 이름으로 재검색하거나 명칭만으로 병합하지 않습니다.

- 단지·법정 지역·보고 주소·K-apt 식별자료 관계.
- 세대수·동수·주차 수, 승인일·승인연도.
- 세대수 0 또는 미제공이면 세대당 주차를 계산하지 않습니다. 주차 0은 실제 0으로 유지합니다.
- 세대당 주차는 공식 주차 수 ÷ 세대수이고 `claim_type=calculated`로 표시합니다.
- 이 문서에는 단지명이 없으므로 주소로 표시합니다. 출처 있는 단지명을 연결하는 후속 어댑터가 필요합니다.
- `coordinate_verification=not_performed`를 확인하고 **좌표는 적재하지 않습니다**. XY가 행에 추가되어 있어도 검증된 단지 위치로 승격하지 않습니다.

### 기존 OSM 시설 묶음

`ingest_osm_chunk(store, path, expected_sha256, source=manifest['source'])`는 기존 `src/data/property-poi`의 검증된 manifest에 연결된 묶음을 가져옵니다. 호출자는 manifest 자체의 릴리스/해시를 먼저 고정해야 합니다.

원천 ID, ODbL, 원천 스냅샷 SHA, 원본점·면 대표점·선 중점의 의미를 유지합니다. `status=source_record` 좌표는 공식 검증 단지 좌표와 구분합니다. 시설을 가져왔다는 이유로 인근 단지와 자동 관계를 만들거나 학교 배정을 추정하지 않습니다.

## 테마와 API 연결 예

지원 필터: `households`, `buildings`, `parking`, `parking_per_household`, `approved_year`, `build_year`. `build_year`는 실제 자료가 연결되기 전 `unavailable`입니다. 승인연도를 준공연도로 이름만 바꾸지 않습니다.

```python
from pipeline.property_relations import RelationStore, ingest_official_facts

store = RelationStore(candidate_path, pinned_property_release)
ingest_official_facts(store, published_facts_path, pinned_facts_sha256)

theme = store.theme({
    'households': {'min': 1000},
    'parking_per_household': {'min': 1},
}, as_of='2026-10-08')

graph = store.graph(complex_id, as_of='2026-10-08', hops=2, limit=100)
store.close()
```

운영 조회는 `RelationStore(published_path, pinned_property_release, read_only=True)`로 열어 쓰기를 차단합니다. 이 파일은 수집 DB나 다른 서비스 DB와 분리해야 합니다. 다른 구조의 DB 경로를 잘못 넘기면 테이블을 추가하지 않고 실패합니다.

응답에는 릴리스, 사용 조건, 각 결과의 근거가 함께 있습니다. 필요한 필드가 연결되지 않으면 `unavailable`, 필드가 연결됐지만 조건에 맞는 단지가 없으면 `ready`와 빈 목록입니다. 서로 충돌하는 유효 수치가 존재하는 단지는 해당 필터에서 제외합니다.

예산·면적·기간의 가격 테마는 거래 요약의 동일 조건 계약을 연결한 뒤 추가해야 합니다. 이 모듈의 주차·세대수 테마를 가격 필터의 구현으로 계산하지 않습니다. 개인 메모와 목적지는 이 데이터 계약에 포함하지 않습니다.

## 검증과 후속 게이트

- `tests/test_property_relations.py`: 원본 ID 충돌·동명 개체 분리·원천/계산/추론·기간 유효성·그래프/공간 한도·해시·원자적 적재·재실행·실제 공개 기본정보 적재.
- `tests/property-relations.test.ts`: 릴리스/선택 고정·근거 누락·참조 오류·기간·그래프 크기의 브라우저 수신 계약.
- 실제 공식 기본정보 827개를 임시 DB에 적재하며 같은 문서를 반복해도 변경 행이 늘지 않는지 확인합니다. 테스트 DB는 자동 폐기합니다.

실제 자료 표본 검수: 공개 공식 기본정보 827개, OSM 4묶음 2,324개를 임시 DB에 적재했습니다. 노드 4,830개·관계 2,481개·근거 3,954개, DB 8,294,400바이트, 무결성 `ok`, 동일 입력 재실행 변경 0행입니다. 이는 전체 OSM 자료 또는 전국 관계 연결의 완료 증거가 아닙니다. 사용한 기본정보 문서 SHA256은 `bcc11963a071e28f9bad8d5fdf88de9837d2560b7fc757ba521b959f6f860cff`입니다.

운영 API 라우팅, 후보 DB 생성·조회본 전환, 2단계 관계 화면, 테마 조건 칩, 각 단지의 공식 단지명/위치 연결은 부모 작업에서 통합·브라우저 검수·배포해야 합니다. DB 스키마와 테스트 통과만으로 그 기능이 공개됐다고 표시하지 않습니다.
