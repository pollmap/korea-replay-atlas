# 성공 백업 뒤 작업본 자동 정리

이번 변경은 **수집 성공 → CAS 백업/검증본 재사용 → 제한된 원문 이관**을 기존 VPS worker 주기에 연결합니다. 자체적으로 새 데이터 릴리스나 원천 API를 호출하지 않습니다. 운영 실행 여부는 아래 활성화 게이트와 배포 기록으로 별도 판정합니다.

## 기본값과 활성화 조건

자동 원문 삭제는 기본으로 꺼져 있습니다. 다음 조건이 모두 충족돼야 실행됩니다.

1. Collector 환경변수 `KOREA_REPLAY_AUTO_RETIRE_WORKING=1`.
2. 수동 표본 이관을 먼저 검증해 `.working-store.sqlite`가 존재.
3. `/data/working-store-readers.json`에 현재 API·Collector 빌드와 `closed-working-index-v1` reader 계약이 기록됨.
4. 실제 API의 `/api/v1/property/working-store-reader`가 해당 API 빌드로 응답하고, 읽기 전용 색인과 CAS 원문 구간을 실제로 읽어 SHA를 검증함.
5. 이번 worker 실행이 반환한 backup 상태가 `verified` 또는 `unchanged`이고, 그 head가 현재 CAS set head와 같음.

배포 확인 파일의 예시는 다음과 같습니다. 아래 build 값은 실제 검증한 Git 빌드로 바꿔야 합니다.

```json
{
  "schema_version": 1,
  "reader_contract": "closed-working-index-v1",
  "api_build": "abcdef1",
  "collector_build": "abcdef1"
}
```

이 파일을 만든 사실만으로 검증이 끝나지 않습니다. API는 실제 CAS 표본을 읽고 빌드를 응답하며, worker는 자신의 `APP_BUILD`까지 대조합니다. API·Collector가 새 빌드로 바뀌면 마커를 다시 검증·갱신할 때까지 자동 정리가 보류됩니다. 신규 설치에서는 환경변수를 켜더라도 수동 이관 색인이 없으면 삭제하지 않습니다.

## 실행 및 실패 경계

`collect_once()`가 수집·백업의 `bulk-work.lock`을 해제한 다음 자동 정리를 호출합니다. 자식 프로세스의 기존 migration CLI가 공유 잠금과 장부 lease를 다시 확인하므로 잠금 중첩이나 동시에 두 이관을 실행하지 않습니다. 성공한 backup head는 자식 명령의 `--expected-head-sha/--expected-head-bytes`에 고정하며 시작과 잠금 획득 후 다시 대조합니다.

- 기본 최대 **2,000파일 / 120초**, 설정 상한 **5,000파일 / 300초**입니다.
- 환경변수는 `KOREA_REPLAY_RETIRE_MAX_FILES`, `KOREA_REPLAY_RETIRE_MAX_SECONDS`입니다.
- 시간 상한은 manifest 읽기·색인 후보 생성·fsync까지 포함한 자식 실행 전체에 적용합니다. 시간 초과 시 TERM, 5초 내 종료되지 않으면 KILL을 사용합니다. API 사전 확인은 별도 5초, 종료 대기는 최대 10초를 사용합니다.
- 파일별 검사 상한과 CAS/pack 캐시는 기존 이관 계약을 사용합니다. 공유 VPS의 CPU·메모리는 기존 collector 컨테이너 한도 내에서 사용합니다.
- 후보 색인은 닫힌 파일의 원자 교체 후에만 공개되며 그 전에는 원문을 삭제하지 않습니다. 강제종료돼도 read-only API가 이전 정상 색인을 읽을 수 있습니다.
- 백업에 없는 새 원문은 대상이 아닙니다. 배치 도중 손상이 확인되면 검증을 통과하지 않은 원문을 남깁니다. 이미 검증해 이관한 이전 자료를 지우거나 다시 복제하지 않습니다.

원천 일일 한도 대기에서도 기존 정상 backup head를 검증해 재사용하고 정리할 수 있습니다. 원천 재시도나 호출 예산 사용은 하지 않습니다. 실패·잠금 충돌·API 확인 실패·시간 초과는 `deferred`로 기록하며 정상 수집 결과를 `collection-hold`로 바꾸지 않습니다. 자식이 중단된 경우 삭제 수를 0으로 꾸미지 않고 `null`로 둡니다.

## 변경 없는 날과 보관 상한

같은 head의 전체 스캔이 완료되면 `/data/working-store-retirement.json`에 head와 reader 마커 해시를 기록합니다. 다음 동일 입력 실행에서는 자식 프로세스, manifest 전수 스캔, 원본 파싱, 색인 복사와 원문 재생성을 생략합니다. 실행 중 새 원문을 수집하면 이후 정상 백업의 새 head에서 작업을 계속합니다.

상태 파일과 `.next` 파일은 고정된 이름 하나씩만 사용합니다. 배치마다 원문 디렉터리·대용량 로그·후보 백업을 만들지 않습니다. 논리 삭제 바이트와 실제 `df` 확보량은 구분합니다.

## 호스트와 컨테이너 경로

호스트에서 수동 이관한 색인의 `archive_root`는 호스트 복구 경로일 수 있습니다. 컨테이너에서는 명시된 `KOREA_REPLAY_WORKING_ARCHIVE=/backups`와 실제 migration store 마운트가 같은지 확인하고 이 경로를 사용합니다. 객체 SHA와 성공 head는 그대로 검증합니다. 임의 경로를 자동 탐색하지 않습니다.

## 활성화·중지 절차

1. 새 API·Collector를 배포하고 건강 상태 및 실제 거래 응답을 확인합니다.
2. 기존 수동 이관 자료가 실제 API의 reader proof 경로로 읽히는지 확인합니다.
3. 실제 두 빌드로 reader 마커를 작성하고 collector의 opt-in 환경변수를 켭니다.
4. 다음 정상 backup 주기의 `last-run.json`에서 `working_retirement`와 `working-store-retirement.json`을 확인합니다.
5. 첫 배치의 장부 불변·원문 해시·실제 거래 조회를 확인하고 계속 운영합니다.

중지하려면 opt-in 환경변수를 끄거나 reader 마커를 제거합니다. 이미 실행 중인 제한 배치를 즉시 중단해야 하면 collector를 정상 종료/중단합니다. 닫힌 색인 프로토콜로 다음 시작에서 안전하게 재개합니다. 일반 수집을 멈추는 `collection-hold` 파일을 원문 정리 설정으로 대신 사용하지 않습니다.

## 검증 범위

실제 자식 프로세스를 쓰는 worker 접점, 중첩 잠금 방지, opt-in/수동 초기화/두 reader 마커, 실제 API CAS 읽기, 고정 head와 중간 head 변경, 배치 재개·동일 입력 무작업, 한도 대기, 미백업 원문 보존, 손상 실패, 시간 초과 자식 종료, 호스트/컨테이너 경로, 비밀값이 없는 오류 상태를 검사합니다. 운영 활성화와 실제 공간 변화는 이 코드 테스트와 별도로 확인해야 합니다.

검증 기록(2026-10-09): 관련 자동 정리·작업 저장소·worker 검사 98개, 전체 `test_real_estate*.py`와 자동 정리·공유 잠금·백업 재사용·조회본 보관·VPS 검사 **495개 통과**. 실제 Linux 자식 프로세스 실행·중단 테스트를 포함합니다. 테스트는 격리 임시 자료로 수행했으며 운영 활성화·운영 원문 삭제는 실행하지 않았습니다.
