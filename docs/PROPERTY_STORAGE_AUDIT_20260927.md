# 무료 수집 대표 실행과 비공개 장기 보존 감사

기준: 2026-09-27. 수집 실행은 완료했으며, 아래 저장 구조 대안은 조사·설계 상태다.
새 저장 서비스 생성, 원본 삭제·공개, R2 활성화, 추가 D1 생성은 수행하지 않았다.

## 1. 대표 실행 결과

[run 36313421610](https://github.com/pollmap/korea-replay-atlas/actions/runs/36313421610)은
PR 44 병합 `a6be336b3b5ae26f9758b4c1ca3590482145c09b`에서 성공했다.
`PROPERTY_COLLECTION_MAX_REQUESTS`는 25→100, `MAX_BYTES`는
16,777,216→67,108,864로 변경했고 enabled=true를 유지했다. 추가 확대는 하지 않았다.

| 항목 | 확인 결과 |
|---|---:|
| 실행 시간 | 10:43:00–10:54:34 UTC, 11분 34초 |
| 원천 요청/응답 | 100회 / 35,534,495 B |
| 최초 확보 작업 | 8,417→8,503, **+86 지역·월·거래유형 작업** |
| 미확보 작업 | 53,535→53,449 |
| 최신 갱신 대기 | 466개 유지 |
| 실패 | 최초 확보 0 / 갱신 0 |
| 원격 확인 | 새 head, parent 연결, owner 해제, SQLite integrity, 100 calls validated |
| 원문 독립 표본 | 성동구·송파구 전월세 / 도봉구 매매 3개 응답 해시·재정규화 통과 |

원격 manifest의 19,530개 파일 참조를 검증하고 checkpoint를 다시 읽었다.
이 수치는 개별 거래 86건이라는 뜻이 아니며, 공개 데이터 갱신도 수행하지 않았다.
기존 공유·공개 릴리스는 그대로다. 원본 데이터·인증정보는 공개 저장소와 Actions artifact에 없다.

Cloudflare GraphQL의 실행 시간창 계정 합계는 D1 읽기 22,253행, 쓰기 2,194행,
readQueries 1,599, writeQueries 1,316이었다. broker 통계는 1,781 requests,
errors=0, CPU P50=1.196ms / P99=3.681ms다. CPU 단위는 공식 GraphQL schema의
microseconds 설명으로 확인했다. 계정 시간창에는 동시 작업이 포함될 수 있고
Workers adaptive 통계와 D1 집계는 동일 모집단이 아니므로 SQL 수와 Worker 수를
억지로 일치시키지 않는다. 마지막 계정 일간 D1은 읽기 1,659,771 / 쓰기 16,509행이었다.

## 2. 실제 저장 증가의 분해

이전/새 manifest의 객체 집합을 비교하고 **새 객체 248개**의 실제 base64 payload
길이를 읽었다. 원문 내용은 출력하지 않았다. 4개 shard 물리 크기 합계는
605,347,840→620,453,888 B, 증가 **15,106,048 B**다.

| 구성 | 새 객체 | 압축 전 객체 B | zlib 저장 B | D1 base64 payload B |
|---|---:|---:|---:|---:|
| 원본 XML | 97 | 34,831,616 | 2,106,388 | 2,808,656 |
| 정규화 snapshot | 86 | 6,624,290 | 6,624,459 | 8,832,732 |
| checkpoint 변경 조각 pack | 1 | 2,969,600 | 694,256 | 925,676 |
| manifest 62개 bucket + root | 63 | 6,050,352 | 1,648,698 | 2,198,344 |
| 실행 보고 | 1 | 558 | 378 | 504 |
| 합계 payload | 248 | — | — | **14,765,912** |
| 물리 증가와 payload 차이 | — | — | — | **340,136** |

마지막 차이는 SQLite record/index/B-tree/page 여유 공간 등의 합성 잔차다.
broker는 PRAGMA/dbstat를 허용하지 않으므로 **인덱스만의 실제 증가량으로 표현하지 않는다**.
control DB의 reservation·budget 증가는 위 object-shard 합계와 별도다.
신규 파일은 raw 99개지만 객체는 97개다. 경로와 객체해시의 단위가 다르고
이미 존재하는 동일 내용 객체를 재사용하기 때문이다.

### 전체 SQLite가 매번 복제되는가

아니다. `pipeline/real_estate_manifest.py:checkpoint_row`는 64KiB 경계의 동일
조각을 이전 객체 offset으로 참조하고, 달라진 조각만 하나의 새 pack으로 만든다.
이번 SQLite는 16,920,576→17,059,840 B였지만 신규 조각은 261개 중 46개,
2,969,600 B뿐이었다. 압축/base64 뒤 새 저장량은 925,676 B였다.
매회 전체 snapshot 압축본이 누적된다는 진단은 틀리다.

다만 새 pack은 변경 조각 전체를 묶어 해시하므로 다른 과거 pack 안의 동일 조각을
전역 재사용하지는 않는다. SQLite page 변경 확산과 pack 구성 변화에 따라 절감률은 변한다.

### 원본 압축과 중복 제거

`D1Archive.put`은 원본 SHA-256을 주소로 사용하고 zlib level 6 압축 뒤
48KiB 조각을 base64 TEXT로 저장한다. 같은 원본 bytes는 다시 쓰지 않는다.
이번 XML 압축률은 약 6.0%라 이미 효과가 크다. 정규화 snapshot은 기존 `.json.gz`
이므로 추가 zlib 압축으로 거의 줄지 않으며, **파생 결과가 물리 증가의 약 58.5%**다.
base64 자체는 압축 bytes보다 약 33% 크다.

원본+파생 snapshot 변동 payload는 11,641,388 B이고 checkpoint+manifest는
3,124,020 B다. 후자는 작업량·장부크기·bucket 변경 수에도 영향을 받는 배치 비용이다.
따라서 전체 증가/86=175,652 B를 모든 미래 작업에 선형 적용하면 안 된다.
이번 비율만 유지할 경우의 위험 시나리오는 추가 약 5,542 작업이며,
우선지역 24,752개 미확보를 모두 저장할 수 있다는 근거는 아직 없다.

## 3. 현재 운영 여유와 중단 조건

현재 shard는 176,123,904 / 136,126,464 / 169,009,152 / 139,194,368 B다.
각 380MiB 자체 cap까지 합계 973,381,632 B 여유가 있다. 해시 분배가 달라
합계 여유보다 먼저 한 shard가 찰 수 있다. 단기 100회/64MiB × 하루 6회는
이번 관측 기준 일일 요청·쓰기 한도 안이지만 장기 저장 완료를 보장하지 않는다.

현재 실제 중단 장치는 다음과 같다.

- 호출 100/응답 64MiB, 서비스별 KST 8,000회 guard, 로컬 disk reserve.
- 후속 로컬 패치의 `raw_budget_preflight`: 원천 reservation 전에 최대 응답량의
  zlib 최악 팽창·base64·chunk/row allowance를 모든 shard에 적용한다. 부족하면
  0호출 `storage_paused`, 기존 head 유지, 정상 lease 해제다. 원격 적용은 별도다.
- 객체 쓰기 전에 shard size와 신규 payload를 계산해 `archive_free_storage_limit` 차단.
- D1 일일 read/write 무료 한도 오류, Worker 무료 요청/CPU 오류는 실패로 처리.
- 소스 quota를 관측한 당일 재호출 차단. source auth/불확실 저장 오류를 정상 빈 결과로 바꾸지 않음.
- 불확실한 reservation·checkpoint·owner는 자동 해제/재시도하지 않음. 실패 시 이전 head 보존.

기존 put cap 검사와 새 raw 사전검사는 **전체 publish 공간 예약 검사**가 아니다. 원천 응답을 받은 뒤
저장 cap에 걸리면 불확실 상태로 중단되고 수동 복구가 필요할 수 있다. 현재
계정 전체 D1/Worker 잔여량을 실행 전 예약하는 구현도 없다. 새 검사는 원본 payload
범위의 부분 위험 감소이며, 파생 결과·SQLite 물리 page overhead·다른 writer는
보장하지 않는다. 100/64 운영 유지와 별개로 전체 저장 계약 보완은 계속 필요하다.

## 4. 원본을 보존하는 개선 순서

1. **파생 snapshot 보관 계약을 분리한다.** 이미 보관한 snapshot은 삭제하지 않는다.
   미래 결과는 raw 해시·페이지 순서·조회시각·normalizer 코드/스키마 버전·결과 해시를
   갖는 재생성 recipe로 보관할 수 있다. 원문에서 같은 snapshot bytes가 결정론적으로
   복구되는 회귀와 기존 reader 호환이 먼저 필요하다. 원본이 없는 파생 결과는 생략 금지다.
   이번 구성에서 줄일 여지가 가장 크지만 아직 구현/절감 달성으로 보고하지 않는다.
2. **manifest를 변경 경로 중심 Merkle tree 또는 append delta+정기 base로 바꾼다.**
   현재 파일명 해시가 64 bucket 전반에 분산돼 100회만으로 62 bucket이 다시 쓰였다.
   과거 파일 목록을 매번 압축·저장하는 2.20MB 비용을 줄일 수 있다. chain 길이·복구 읽기
   상한·누락 검증을 함께 설계해야 하며 CAS 원자성은 유지한다.
3. **checkpoint 조각 dedup 개선은 실측 후 결정한다.** 전역 64KiB 조각 주소화는
   중복 저장을 줄일 수 있지만 객체·SQL 수를 늘린다. 작은 조각을 묶는 pack index와
   4슬롯 읽기 cache를 유지하고 page 단위 과분할은 피한다.
4. **base64→BLOB은 부수 최적화다.** 최대 약 25% payload 절감 여지가 있지만
   D1 broker/REST 바인딩·기존객체 읽기 호환과 새 고정 SQL이 필요하다. 그것만으로
   장기 저장 문제가 해결된다고 보지 않는다. 기존 데이터를 제자리 재작성하지 않는다.

## 5. 인증된 Workers Static Assets archival tier

### 가능성 판단

**기술적으로 검토할 가치가 있는 무료 경로다. 그러나 현재 연결·복구 검증은 없다.**
모든 요청에 `assets.run_worker_first=true`를 지정해 secret 인증 뒤
`env.ASSETS.fetch()`로만 응답할 수 있다. 경로 배열의 예외나 asset-first fallback을
두지 않는다. [공식 인증 선행 예제](https://developers.cloudflare.com/workers/static-assets/routing/worker-script/).

Assets 저장에는 별도 비용이 없지만 인증 요청은 Worker 호출로 계산된다. Free 요청
한도를 넘으면 Worker-first 경로는 429를 반환하며 인증 없는 asset으로 우회하지 않는다.
[요금과 실패 동작](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/).

한 버전은 파일 20,000개·개별 25MiB 제한이다. 내부 목표는 18,000개·24MiB 이하로
두고 raw 파일 하나씩이 아니라 불변 압축 pack으로 묶는다. 파일 한도의 곱을 무제한
영구 저장 SLA로 해석하지 않는다. 인증 Worker는 100,000회/일 공유·10ms CPU·128MB
제한이므로 pack을 stream하고 서버에서 대량 압축/복호화하지 않는다.
[공식 제한](https://developers.cloudflare.com/workers/platform/limits/).

### 비공개 유지와 직접 접근

설계 조건은 별도 private archival Worker, 전 경로 인증, unknown path 404,
GET/HEAD만 허용, secret 미설정 fail-closed, no-store/no-CORS, 인증 전 캐시 조회 금지다.
경로·리다이렉트·HEAD·Range·navigate header에도 동일 인증이 적용돼야 한다.
웹 앱의 public ASSETS·Pages 프로젝트에는 원본을 넣지 않는다.

Version URL은 기본 설정에 따라 공개 접근 경로가 생성될 수 있으므로
`preview_urls=false`를 명시하고 preview branch builds를 끈다. 배포 후보 시험을 위해
버전 URL을 열더라도 같은 인증 Worker가 반드시 먼저 실행돼야 한다. custom domain의
인증만 믿으면 workers.dev/version URL 우회가 생긴다.
[버전 URL 설정](https://developers.cloudflare.com/workers/versions-and-deployments/version-urls/).

추가 방어로 pack은 runner에서 압축 후 독립 key로 암호화하고, 서비스에는 암호문만
저장하는 설계를 권장한다. 같은 이미 생성된 암호문은 재사용하고 재업로드 때마다
nonce를 새로 만들어 dedup을 깨지 않게 한다. 신규 암호화 nonce 재사용은 금지한다.
키와 raw는 공개 git/log/artifact에 넣지 않는다. 암호화가 인증을 대체하지는 않는다.

### 버전 보존·증분 업로드·복구

Worker 버전은 코드·assets·bindings를 포함하지만 연결된 D1 상태를 함께 복원하지 않는다.
과거 배포가 영구 보존된다는 SLA는 이번 공식 문서에서 확인하지 못했다.
따라서 모든 필요한 pack과 과거 root manifest를 **현재 활성 asset manifest에도 포함**한다.
현재 버전의 전체 참조 closure만으로 복구 가능해야 한다.
[버전의 범위](https://developers.cloudflare.com/workers/versions-and-deployments/).

Direct Upload는 전체 파일 manifest의 hash/size를 보내고 서버가 요구한 bucket만
업로드한다. 최근 업로드된 미변경 파일은 생략되지만 과거 모든 파일이 항상 재사용된다고
가정하면 안 된다. 요청된 누락 pack은 private archive에서 다시 읽어야 한다. upload JWT와
완료 token은 각각 1시간 유효라 장기 인증 대용이 아니다.
[증분 업로드 계약](https://developers.cloudflare.com/workers/static-assets/direct-upload/).

GitHub runner는 기존 asset manifest를 읽고 새 pack만 생성→요구된 pack 업로드→
candidate 버전→인증 실패/정상 읽기와 ciphertext·plaintext SHA 검증→복원 fixture→
D1 head CAS 순서로 연결해야 한다. runner 14GB 임시 disk에 전체 원본을 매번 복원하지
않는 uploader가 필요하다. 게시 실패 시 새 객체만 남을 수 있으나 기존 head는 유지한다.
이 tier를 추가해도 매회 같은 raw를 D1에도 계속 누적하면 기존 cap 문제는 해결되지 않는다.
미래 raw를 assets로 먼저 확정 저장하고 D1은 장부/일시 staging을 맡는 계약 변경이 필요하다.
기존 D1 원본은 이번 범위에서 삭제·이동하지 않는다.

### 지속 배포 자격증명 경로

GitHub의 현재 broker secret은 고정 D1 연산 권한뿐이라 Worker 배포에 쓸 수 없다.
GitHub에서 직접 업로드하려면 제한된 Cloudflare 관리 토큰이 별도로 필요하다.
로컬 OAuth의 일회성 upload token을 장기 자동화로 표현하지 않는다.

대안으로 **Cloudflare Workers Builds 관리 배포 + GitHub의 secret deploy hook**이 있다.
Workers Builds는 기본 관리 API token을 생성·재사용하고 build 전용 secret을 지원한다.
공개 repo에는 코드만 두고, build가 private broker/assets에서 필요한 bytes를 읽게 한다.
기본 생성 token 권한이 넓으므로 별도 리소스에 제한한 token으로 축소하는 검토가 필요하다.
[Builds 인증과 secret](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/).

Deploy Hook은 연결된 git branch의 build를 POST로 시작하며 URL 자체가 secret이다.
GitHub에 CF 관리 token을 두지 않을 수 있으나 저장 서비스·git 연결·build secret·hook
구성은 아직 수행하지 않았다. hook 성공이 복구 검증 성공은 아니다.
[공식 deploy hook](https://developers.cloudflare.com/workers/ci-cd/builds/deploy-hooks/).

Free Builds는 월 3,000분, 동시 1개, 회당 20분, disk20GB다. 하루6번×30일×20분은
3,600분이므로 최악 시간으로 매 수집마다 build를 돌릴 수 없다. 하루1회 등 bounded
archival batch와 별도 D1 staging 여유가 필요하다. 현재 GitHub 무료 표준 runner와
Cloudflare Builds의 무료 한도는 별개로 계량해야 한다.
[Builds 한도](https://developers.cloudflare.com/workers/ci-cd/builds/limits-and-pricing/).

## 6. 판정

- 100/64 대표 backfill: 성공, 최초 확보 증가 확인. 현재 예산 유지, 추가 확대 보류.
- 당일 무료 요청·쓰기: 관측치에 여유 있음. 다른 서비스와 공유하므로 무기한 보장은 아님.
- 전국/우선지역 10년 보존: 미완료. raw보다 파생 snapshot·목록 반복 비용부터 줄일 근거 확보.
- 비공개 Static Assets: 공식 기능상 가능, 실제 인증 우회·복구·지속 배포 검증 전 미채택.
- 다음 구현은 저장 계약 fixture→로컬 재생성·복구 검증→비공개 후보 검증 순서다.
  원본 삭제, 유료 전환, 공개 repo 원본 업로드를 대안으로 사용하지 않는다.
