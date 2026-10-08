# 정정 수집 이후 공개 게시 경로 감사 — 2026-10-09

## 판정

정정 수집 → 비공개 CAS 백업 → 조회 DB 전환 → 검증된 평문 정리는 운영에서 확인했습니다. **새 정정분의 Pages 공개와 일일 자동 게시까지 완료한 상태는 아닙니다.** 이번 변경은 동일한 완료 자료의 재조회 시각만으로 공개 release가 바뀌는 문제를 고칩니다. 운영 후보 생성·배포·설정 변경·원문 삭제는 수행하지 않았습니다.

## 실제 운영 증거

관측: 2026-10-09 03:18 KST(2026-10-08 18:18 UTC). 수집기는 계속 실행 중이므로 아래 수치는 그 시점의 마지막 완료 실행 기준입니다.

| 항목 | 확인 결과 | 증명 범위 |
|---|---|---|
| `shared/data/last-run.json` | 완료 시각 18:07:16 UTC, correction content revision 54, `public_release=false` | 비공개 정정 수집·보관 완료 |
| 해당 실행 | 50회 호출, 실제 변경 49작업, 원본 응답 6,167,988바이트 | 이 실행의 소스 응답·처리 결과 |
| 우선 권역 | 가용 48,384작업=complete 47,811+empty 573, pending/partial/failed 0; 원천 미제공 5,824 별도 | 현행 코드·현재 잠정월·보존된 이전 월을 포함한 장부; 모든 과거 행정코드의 완전성 증거 아님 |
| CAS head | `2cd410de3f5fc2dbfed4b4a7a5c8adcb3d7df6be3840c97b4aa5e84dc546e2fd` | 해당 체크포인트의 비공개 검증본 |
| `read-model-status.json` | 18:07:53 UTC, `ready`, correction revision 54 | API용 조회 DB 전환 |
| 자동 원문 정리 | 98개, 논리 바이트 6,966,590 제거, source_calls 0 | 검증된 압축 원문 재사용; 디스크 전체 확보량과 다름 |
| [공개 runtime](https://korea-replay.pages.dev/api/v2/runtime) | app `pub-b71d244ced0bff39`, snapshot `27bf5246` | 관측 시 대표 주소 앱 버전 |
| 공개 atlas | `atlas-7e16e6fe64547834`, data origin `https://0348ea51.korea-replay-data.pages.dev` | 앱이 고정한 정적 자료 |
| [공개 아파트 manifest](https://0348ea51.korea-replay-data.pages.dev/data/property/property-ceeff63959643461/manifest.json) | `property-ceeff63959643461`, generated_at 2026-10-04T22:13:41Z | 10월 9일 서버 정정본과 다른 고정 공개 자료 |

공개 manifest의 좌표 수는 거래 원천 생성기의 값입니다. 별도 navigation/facts의 위치 연결이 있으므로 manifest의 `verified_complexes=0`만으로 서비스 전체의 검증 위치 수를 판단하면 안 됩니다.

## 코드 경로와 남은 장애물

1. **운영 worker는 공개 게시를 호출하지 않습니다.** `pipeline/vps_runtime.py`는 수집·백업·read-model·평문 정리까지 수행합니다. `deploy/vps/compose.yaml`에는 API와 collector만 있고, `.github/workflows/property-collect.yml`도 공개 업로드를 명시적으로 제외합니다. 시스템 timer 목록에서 맛동산 게시 타이머를 찾지 못했습니다. 이것은 확인한 경로의 범위이며 외부 자동화 전체가 없다는 단정은 아닙니다.
2. **무변경 재조회가 새 release를 만들었습니다.** `real_estate_publish.py`의 release fingerprint가 완료 작업의 `MAX(calls.started_at)`를 포함했습니다. 호출 장부가 바뀌면 원본·snapshot이 같아도 전체 버전이 바뀌었습니다. 이번 커밋에서 complete/empty의 운영 조회 시각을 제외하고, 보존 중인 pending/partial/failed의 시각은 유지합니다. 실제 normalizer/verifier 코드 지문도 포함합니다.
3. **기존 candidate가 재사용 경로를 연결하지 않습니다.** `property_candidate.run`은 존재하는 `VerificationCache`를 `publish(..., audit_cache=...)`에 전달하지 않습니다. `compressed_transactions`와 summary `compressed_assets`도 켜지 않습니다. 별도 `RESERVE=30GiB`와 CLI 조절 불가 조건도 남아 있습니다. 이번 수정 범위에 섞지 않았습니다.
4. **전체 월별 산출물이 전역 release에 묶입니다.** transaction pack·지역 색인·summary 본문에 전체 release_id가 들어가므로 한 작업이 바뀌어도 나머지 본문 hash가 바뀝니다. `emit_month_packs`, `real_estate_complex_summary.build`, `real_estate_summary_month_pack.build`는 여전히 모든 대상 거래/요약을 순회합니다. 압축만 켜는 것으로 변경 월만 재생성하는 구조가 되지는 않습니다.
5. **후보 생성과 공개 부속자료·승격이 분리돼 있습니다.** `property_candidate`는 transaction/summary의 비공개 receipt에서 끝납니다. 새 release의 navigation·facts·검색·지역 필터 지표, 이전 공개 거래 보존 감사, Pages 데이터 후보와 앱 고정·검증·승격 연결이 필요합니다. 단지 위치 연결을 새 release 문자열로만 바꾸면 안 됩니다.
6. **후보 보관·실패 복구에도 마지막 성공 상태가 필요합니다.** 현재 후보는 output의 status/last-verified를 사용하지만 생성된 release 디렉터리 자체는 자동 삭제하지 않습니다. 기존 보관 정책과 참조 목록을 통합해야 하며, 공개 중인 자료·직전 정상본·실행 중 후보를 보호해야 합니다.

## 최소 후속 구현 순서

| 단계 | 최소 구현 | 통과 조건 |
|---|---|---|
| 이번 수정 | 완료 자료의 동일 재조회 release 재사용, 실제 코드/입력/상태 무효화 | 호출·queue는 보존하면서 새 원본 파싱·산출물 증가 0; 금액·취소·코드 변경은 새 검사 |
| 후보 접점 | 검증 캐시 주입, 명시적 gzip, 작업별 공간 예산 옵션 | 기존 평문·압축 소비자 호환, 변경 없는 후보 재사용, 과도한 중간본 없음 |
| 월별 증분 계약 | region/month 입력+계산 지문으로 payload 내용 주소화; 전체 release manifest는 그 payload를 참조 | 1개 월 변경 시 관련 payload/요약만 재생성, 이전 공유 유지, 전체 ID 보존 |
| 게시 orchestrator | 하루 1회, 공개 자료 지문이 바뀐 경우만 닫힌 입력 고정→후보→부속자료→검증→승격 | calls/queue 변화만으로 게시 안 함, 실패 시 이전 공개본 유지, 중단 후 같은 입력으로 재개 |
| 운영 검수 | 실제 수정분과 대표 단지·이전 공유·404·복구 확인 | 비공개 DB ready와 정적 공개 반영을 별도 기록 |

현재의 정정 queue는 완료 장부를 pending으로 바꾸지 않습니다. 따라서 이번 지문 수정 이후 변경 없는 정정은 release를 유지하지만, 거래가 실제 변경되면 snapshot/pages/updated_at이 바뀌어 새 release가 생성됩니다. 새로운 소스 SHA를 장부에 갱신하지 않은 채 원본 바이트를 덮어쓰는 일은 허용하지 않습니다. 기존 검증된 공개 산출물 재사용과 원본의 주기적 full audit는 서로 다른 작업입니다.

이번 정책 버전은 `property-publication-v5-stable-complete-rechecks`입니다. 첫 적용 시 기존 v4와 다른 release가 한 번 생성되며 이후 같은 입력·코드는 같은 release를 사용합니다. 수집기 calls/queue와 비공개 CAS head는 이 수정과 무관하게 계속 복구 보관합니다.

## 이번 수정 검증

- publication/candidate/complex summary/summary pack: 47개 통과.
- compressed publication/transaction pack/correction scheduler: 43개 통과.
- 신규 회귀: complete·empty의 재조회 호출/queue 증가에도 같은 release·같은 파일·원본 재파싱 0; 실패 상태의 새 시각은 다른 release; 실제 금액·취소 정정과 normalizer 지문 변경은 새 검사.
- Python 문법 검사, `git diff --check` 통과. UI·운영·공개 배포를 변경하지 않았으므로 화면 동작이나 자동 게시 완료를 주장하지 않습니다.
