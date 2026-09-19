# 검색 색인 v2: 계약과 실제 검증

2026-09-16 기준. 공개 검색 후보 131,235개 중 기존에 게시된 50,000개의 범위를 유지하면서, 한 번에 전체 검색 파일을 받던 경로를 쿼리별 색인 조회로 바꿨습니다. 미게시 후보 81,235개가 새로 검색된다는 뜻은 아닙니다.

## 자료와 처리 흐름

`scripts/build-search-v2.mjs`는 실제 `shared/search.ts`의 정규화·한국어 정렬 함수를 사용합니다. 이름 exact → 이름 prefix → 이름·지역·별칭 substring 순서, NFKC·공백 제거·대소문자 정규화, 동일 ID 중복 제거, 20개 결과 제한을 그대로 보존합니다. 원천 ID, 위치, 지역, 별칭을 다른 시설로 자동 연결하지 않습니다.

| 산출물 | 수 | 실제 크기 |
|---|---:|---:|
| 작은 버전 manifest | 1 | 46,291 B |
| prefix·부분문자 검색 bucket | 128 | 개별 최대 shard 한도 4 MiB |
| 정렬된 결과 행 page | 98 | page당 최대 512행 |
| 전체 | 227 | 23,032,349 B |
| 가장 큰 실제 shard | — | 265,926 B |

기존 단일 파일은 10,773,281 B였습니다. v2는 검색에 필요한 정보 일부를 미리 계산하므로 전체 저장량은 늘지만, 처음부터 전체를 내려받아 정렬하지 않습니다. 각 파일의 경로·SHA-256·바이트 수를 manifest에 고정합니다. 브라우저는 버전 목록과 필요한 shard만 읽고 JSON 해석과 질의 처리는 Web Worker에서 수행합니다.

prefix 후보는 사전에 중복 제거한 상위 20개를 저장합니다. substring 후보는 UTF-16 unigram·bigram·trigram의 정렬된 행 번호 교집합으로 찾고, 마지막에 전체 문자열 포함 여부를 검산합니다. 긴 별칭에서 후보가 4개 page 이하로 줄면 나머지 gram 파일 대신 후보 문자열을 확인합니다. 결과 순서에는 변화가 없습니다.

bucket은 `lookup-lines-v1` JSONL로 저장해 필요한 키의 배열만 해석합니다. record page는 `row-bytes-v1`로 저장하며 UTF-8 바이트 오프셋으로 필요한 행만 디코딩·JSON 해석합니다. 선택 결과가 여러 page에 걸쳐 있어도 모든 page의 512개 객체를 만들지 않습니다. 데이터 전체 SHA-256·길이와 offset 경계는 계속 검사합니다. manifest에 encoding을 명시하고 이전 JSON page도 읽을 수 있습니다.

## 공용 실행 계약

```ts
const search = createPublishedPlaceSearch(fetcher, {maxRequests: 40});
const result = await search({url, sha256}, query, {signal});
// result: {places, metadata, schema_version}
// metadata: {indexed_count, candidate_count, omitted_count, index_limit}
```

`shared/search-v2.ts`는 브라우저 Worker와 조회 API가 함께 사용하는 로더입니다. 정적 URL만 허용하고 상대 경로 이탈·원격 주소를 거부합니다. 없는 파일, 일시적 네트워크 오류, 해시·크기 불일치, 잘못된 자료, 요청량 초과를 각각 `missing`, `unavailable`, `integrity`, `invalid-data`, `budget`으로 구분합니다. 예산 초과를 검색 결과 0개나 부분 성공으로 반환하지 않습니다.

schema v1은 기존 검색 계약과 함께 읽을 수 있습니다. v2에는 올바른 SHA-256이 필수입니다. 없는 고정 버전을 최신 버전으로 대체하지 않습니다. 진행 중인 동일 immutable 파일 요청은 공유하고 실패한 요청은 재시도할 수 있도록 캐시에서 제외합니다. 재사용 캐시는 파일 48개·직렬화 크기 8 MiB로 제한하며 버전 정보는 최대 2개만 유지합니다. 직렬화 크기는 실제 JavaScript heap 크기와 다릅니다.

Worker 검색어가 바뀌면 이전 질의의 후속 shard 요청과 계산을 멈춥니다. 이미 요청한 immutable 파일은 새 질의와 재사용할 수 있습니다. prefix 결과에 필요한 page는 동시 4개 이내로 준비합니다.

## 검증 결과

실제 50,000개 공개 자료에서 고정 지역·시설 검색어와 결정론적 원천 표본을 뽑아 **304개 질의 결과를 기존 함수와 대조했고 불일치가 0개**였습니다. 한국어·영문 별칭·공백·부분검색·중복 ID·잘못된 좌표·빈 질의·없는 질의와 실패 복구를 회귀 검사했습니다.

콜드 검색 캐시·Node.js 로컬 파일 환경의 실제 측정값은 `.local/search-v2/audit.json`에 기록되어 있습니다. 현재 304개 중 최대 질의는 22개 파일·2,606,222 B, wall time P95는 37.34 ms입니다. 이 값은 노트북 파일 접근을 포함한 측정이며, Cloudflare CPU 한도 충족이나 브라우저 FPS를 입증하지 않습니다. 공개 환경에서 별도 확인해야 합니다.

추가로 모든 파일을 먼저 메모리에 읽고 질의별 60회 `process.cpuUsage`를 측정했습니다. `.local/search-v2/cpu-audit.json`은 Node `Response`·body 복사·WebCrypto·GC를 포함하며, `cpu-audit-core.json`은 준비한 ArrayBuffer를 직접 제공해 Node HTTP 객체 생성 비용을 제외합니다. 둘 다 Cloudflare isolate CPU 측정값은 아닙니다.

| 질의 | Node Response 포함 cold CPU 평균 | 준비된 buffer 사용 cold CPU 평균 |
|---|---:|---:|
| 서울 | 3.65ms | 1.57ms |
| 청주 | 4.95ms | 2.60ms |
| 산업단지 | 16.68ms | 4.42ms |
| Incheon | 19.52ms | 6.75ms |
| Dongcheon | 24.98ms | 9.38ms |

서버의 Free CPU 10ms 조건은 현재 로컬 결과만으로 통과 판정하지 않습니다. 특히 영문 별칭 콜드 요청은 실제 Cloudflare에서 자원 초과(1102) 여부를 확인해야 합니다. 전체 JSON을 해석하던 이전 측정에서 `Dongcheon`은 Node Response 포함 56.27ms였고 선택 해석·trigram 적용 후 24.98ms로 줄었습니다.

일반적인 없는 검색어 표본은 manifest와 bucket 2개만 읽고 record page를 읽지 않았습니다. API는 질의당 원본 조회를 40개로 제한할 수 있고, 브라우저 기본 제한은 256개입니다. 8 MiB 재사용 캐시와 별개로 한 질의에서 잠시 참조하는 자료의 양은 질의와 후보 수에 따라 달라집니다.

```powershell
node scripts/build-search-v2.mjs
node scripts/build-search-v2.mjs --audit
node scripts/build-search-v2.mjs --cpu
node scripts/build-search-v2.mjs --cpu-core
npx vitest run tests/search-v2.test.js tests/search.test.ts
```

실제 검색 descriptor는 `.local/search-v2/asset.json`, 생성 보고는 `.local/search-v2/report.json`입니다. 빌더는 해시 경로에 새 자료를 만들며 기존 파일과 공개 catalog 포인터를 변경하지 않습니다. 재타일링의 flat catalog 구성 단계가 검증된 descriptor를 받아 최종 무료 배포 묶음에 포함합니다.
