# Private archive broker

## 목적과 현재 검증 범위

GitHub 수집기가 Cloudflare 관리 API 토큰 없이 기존 private D1 원본과 장부를 읽고 쓰는 전송 경로입니다. 로컬 Wrangler OAuth는 최초 Worker 배포와 secret 등록에만 사용하고, 이후 수집기는 별도 32바이트 랜덤 토큰으로 이 Worker에 접속합니다. 기존 REST `D1Archive`도 유지합니다.

이 변경은 **수집 원본 보관**을 연결합니다. Pages 공개 게시 권한이나 공개 데이터 자동 갱신을 제공하지 않습니다. 로컬 SQLite 회귀와 Worker 단위 검증은 운영 배포, 실제 D1 응답, Workers Free CPU 측정과 별도입니다.

2026-09-26 운영 읽기 검증: 기존 D1 5개에만 연결한 별도 Worker를 배포하고,
87회 인증된 요청으로 19,082개 파일의 원본 목록을 확인했습니다. 16,719,872바이트
원본 객체는 기존 REST 경로와 바이트가 일치했으며, 최대 4개 chunk의
262,433바이트 응답도 정상 처리됐습니다. 인증 전 401, 미등록 secret의 503,
허용되지 않은 operation의 400, no-store와 CORS 부재를 확인했습니다.
이 검사는 원천 API를 호출하거나 원본·head를 변경하지 않았습니다.
수집기 점유는 0을 유지했습니다. Worker CPU 실측과 실제 수집 쓰기는 별도이며,
이미 소진된 D1 무료 쓰기 한도 초기화 후 검증 전까지 정기 수집은 비활성입니다.

## 배선

* Worker entry: `worker/archive-broker.ts`
* 고정 SQL: `worker/archive-broker-operations.json`의 44개 operation
* Python: `from pipeline.real_estate_archive import BrokerArchive`
* 생성: `BrokerArchive(config, token=token, endpoint=endpoint)`
* 환경: `PROPERTY_ARCHIVE_BROKER_TOKEN`, `PROPERTY_ARCHIVE_BROKER_URL`
* config: 기존 `account_id`, `control_database`, `object_databases` 4개를 그대로 사용합니다.

`PROPERTY_ARCHIVE_BROKER_TOKEN`은 **소문자 hex 64자**입니다. Python 수집기와 Worker에 동일하게 주입합니다. 관리 API 토큰을 이 위치에 사용하지 않습니다. URL과 token 중 하나만 설정됐다면 호출자는 중단해야 하며 REST로 자동 전환하면 안 됩니다. 양쪽이 없을 때만 기존 관리 API 경로를 명시적으로 선택할 수 있습니다.

endpoint는 `https`, 명시적 port 없음, user/password 없음, `/v1/query`, query/fragment 없음을 검사한 뒤 전체 문자열의 SHA256을 코드에 고정한 허용값과 비교합니다. 실제 개인 계정 주소는 공개 파일에 넣지 않습니다. 다른 Worker로 이전할 때는 새 URL 해시 변경을 코드 리뷰하고 Worker와 클라이언트를 함께 배포해야 합니다. 응답의 redirect는 따라가지 않습니다.

기존 5개 DB를 다음 **정확한 순서**로 별도 private Wrangler 설정에 바인딩합니다. 새 DB 생성은 필요 없습니다.

| Worker binding | 기존 config |
|---|---|
| `ARCHIVE_CONTROL` | `control_database` |
| `ARCHIVE_OBJECT_0` | `object_databases[0]` |
| `ARCHIVE_OBJECT_1` | `object_databases[1]` |
| `ARCHIVE_OBJECT_2` | `object_databases[2]` |
| `ARCHIVE_OBJECT_3` | `object_databases[3]` |

별도 Worker만 지정한 private Wrangler 설정은 entry의 절대 경로, 위 5개 `d1_databases` binding, `workers_dev: true`, `preview_urls: false`, `observability: { enabled: false }`를 사용합니다. 앱 Worker 설정을 재사용하거나 앱에 DB를 연결하지 않습니다. 토큰을 `vars`, 소스, 명령 인자 또는 로그에 넣지 않습니다. Worker secret은 stdin으로 전달하고 GitHub secret에도 별도로 등록합니다. 최초 Worker 배포에 secret이 없으면 모든 정상 경로 요청이 503으로 닫힙니다. secret 등록은 해당 Worker의 배포 버전을 변경하므로 정확한 private 설정을 지정해야 합니다.

## 요청 계약과 보존 조건

인증된 `POST /v1/query`만 받습니다. body는 `version: 1`, `operation`, `database`, `params` 네 필드만 허용합니다. `sql` 필드는 허용하지 않습니다. database는 `control`, `object0`~`object3` 중 하나입니다. operation이 지정한 control/object 종류도 일치해야 합니다.

서버는 배포된 JSON의 SQL만 prepare하고 params를 bind합니다. SQL 문자열을 전달하거나 정규식으로 위험 단어만 제외하는 프록시가 아닙니다. 삭제, 임의 UPDATE, 수동 강제 lease 해제, 임의 PRAGMA, arbitrary DB ID는 지원하지 않습니다. 기존 additive schema migration 두 개와 고정 trigger DDL은 허용합니다. SQL 변경이 필요하면 카탈로그와 회귀를 함께 변경해야 하며 모르는 쿼리는 네트워크 호출 전에 중단됩니다.

기존 guard 예약 trigger, 하루 서비스별 8,000 호출 제한, head CAS, owner/generation fencing, 미확정 reservation의 해제 금지를 그대로 사용합니다. D1 `results`, `meta.changes`, `meta.size_after`를 보존합니다. 새 transport는 write batch 2개, read batch 4개를 사용합니다. 기존 REST는 8개/30개입니다. read 종료 조건도 같은 batch 크기를 사용하므로 네 개 이후의 원본을 자르지 않습니다. 객체 SHA/크기/readback과 head 승격 규칙은 기존 구현을 그대로 따릅니다.

전송 실패, Worker CPU 중단, 응답 초과 등 **쓰기 결과가 불확실한 경우 요청을 자동 재시도하지 않습니다.** 기존 run guard가 owner와 reservation을 유지하며 수동 증거 확인 후 복구하게 됩니다. 토큰 교체나 transport 교체는 소유권을 해제하지 않습니다. 하루 D1 쓰기 제한은 `archive_daily_write_limit`, 읽기 제한은 `archive_daily_read_limit`으로 정제해서 전달합니다. 원래 exception, SQL, DB ID, stack은 반환하거나 로그에 기록하지 않습니다.

## 상한과 무료 운영

| 항목 | broker 상한 |
|---|---|
| 요청 body | 160 KiB, content-length와 스트림 모두 검사 |
| body 읽기 | 5초 |
| 응답 JSON | 384 KiB; 넘으면 실패하고 부분 결과를 반환하지 않음 |
| 개별 문자열 parameter | UTF-8 65,536 bytes |
| 수치 parameter | 안전한 정수만 |
| SQL 실행 | 요청당 고정 prepared statement 1개 |
| chunk 묶음 | 쓰기 2개 약 128 KiB / 읽기 4개 약 256 KiB |

인증 실패는 body/D1보다 먼저 처리합니다. 브라우저 Origin, 압축 요청 body, GET/OPTIONS, 예상하지 않은 필드를 거부하며 모든 응답은 `Cache-Control: no-store`입니다. CORS 헤더가 없으며 공개 조회 route가 없습니다. 소스 API 키는 이 Worker에 전달하지 않습니다.

Workers Free의 100,000 요청/일·10ms CPU/호출 및 D1의 계정 공통 100,000 rows-written/일, 5,000,000 rows-read/일을 별도로 지켜야 합니다. 작은 묶음은 CPU 부담을 줄이지만 HTTP 요청 수는 늘립니다. guard와 원본 저장은 기존 D1 사용량을 계속 소비하며 Worker를 추가한다고 D1 예산이 늘어나지 않습니다. 같은 계정의 다른 서비스 사용량도 포함되므로 초기 25회 source 호출의 실제 Worker 요청/CPU·D1 rows를 관측한 후 source 예산을 조정합니다. 네트워크/D1 대기시간은 Worker CPU와 구분합니다. 무료 한도 실패를 유료 전환으로 해결하지 않습니다. [Workers 한도](https://developers.cloudflare.com/workers/platform/limits/), [D1 요금과 일일 한도](https://developers.cloudflare.com/d1/platform/pricing/), [D1 쿼리 한도](https://developers.cloudflare.com/d1/platform/limits/).

일회성 baseline import는 기존 코드가 최대 10,000개 day/service 행을 한번에 조회합니다. 이미 import된 운영 DB는 그 조회를 반복하지 않습니다. 아주 큰 신규 baseline은 384 KiB 응답 상한에 걸릴 수 있습니다. 이때 부분 baseline을 성공으로 처리하지 않으며, REST로 최초 import를 검증하거나 별도 페이징 구현 후 재시도해야 합니다. 이 문서의 broker는 원본 SQL의 LIMIT을 조용히 바꾸지 않습니다.

## 검증과 운영 전환 순서

1. `python -m pytest tests/test_real_estate_archive_broker.py tests/test_real_estate_archive.py tests/test_real_estate_run_guard.py tests/test_real_estate_remote.py tests/test_property_automation.py -q`
2. `npx vitest run tests/archive-broker.test.ts`
3. `npx tsc -b` 및 변경된 TS 파일 lint.
4. 별도 Worker 배포 후 무인증 401/secret 미등록 503, 캐시/CORS 부재 확인.
5. 인증된 head/status와 원본 하나의 readback을 확인합니다. 이 단계는 source API를 호출하지 않습니다. 기존 head digest/장부와 REST 결과가 동일해야 합니다.
6. GitHub 수집 환경에 broker token/URL을 주입하고 source 최대 25회 첫 실행을 관측합니다. 원본 업로드 실패 시 이전 head 보존, 소유권 유지, 원문·비밀값의 로그 미노출을 확인합니다.
7. UTC D1 예산 초기화 이후 정상 수집→검증된 새 head→guard release까지 확인한 후에만 schedule을 켭니다. CPU 초과 또는 예산 실패 시 현재 스케줄을 중지하고 원본/owner를 임의 삭제하지 않습니다.

로컬 회귀는 실제 SQLite에 operation SQL을 실행하여 원본 복원, 새 달 자동수집, quota trigger, CAS, pending reservation 해제 거부, 4개를 넘는 chunk read, 네트워크 실패 후 재호출 없음, request/response cap과 malformed 입력을 검사합니다. 운영 Worker의 CPU·D1 응답 한계 통과를 로컬 테스트만으로 주장하지 않습니다.
