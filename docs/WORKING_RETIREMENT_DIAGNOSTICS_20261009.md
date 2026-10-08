# 자동 평문 정리의 자식 프로세스 진단 — 2026-10-09

## 관측과 원인 범위

2026-10-09 03:18 KST, API/collector `2428cbe`의 두 번째 자동 정정 배치가 수집·백업·조회본을 완료했으나 평문 정리는 `working_retirement_child_failed`로 미뤄졌습니다. 원문 삭제/이관 재시도, 서비스·환경 변경 없이 읽기 전용으로 조사했습니다.

- 마지막 수집 결과: 50호출·50작업 변경, content revision 104, backup verified, 완료 시각 2026-10-08T18:18:26Z.
- 첫 자동 배치의 정리는 98개·논리 6,966,590바이트로 성공했습니다. 다음 실패를 수집/백업 실패로 합산하지 않습니다.
- `.working-store.lock`와 `.working-store.sqlite`는 UID/GID 10001, 0644입니다. 실패 후 남은 `.next`/journal은 없었습니다.
- 현재 head와 index의 기존 110,284개 매핑을 메타데이터로 대조했습니다. SHA/크기 불일치 0, 같은 자료의 재포장 0, 누락 pack 0입니다. manifest 35,663,067바이트를 읽었으며 20.7초, head 전후 동일입니다. 약 16GB 원문을 재해제한 검사가 아닙니다.
- 신규 100파일은 모두 평문으로 남아 있었으며, 선택한 총 10,327,256바이트의 실제 SHA·크기·UID도 일치했습니다. 기존 index에 없는 파일이 사라진 경우는 없었습니다.
- collector restart 0, cgroup `oom=0`, `oom_kill=0`, pids limit hit 0입니다. memory peak가 1GiB에 닿았지만 그 사실만으로 OOM 종료라고 판단하지 않습니다.
- 운영 준비 작업의 `.local/retention/generated-work.jsonl`에는 18:12:55Z와 18:19:03Z 성공 종료가 있습니다. 평문 정리는 수집의 bulk lock 해제 후 자식이 같은 잠금을 다시 획득하므로, 그 사이 배포 stage가 먼저 잠금을 잡았을 가능성이 있습니다.

**당시 정확한 원인은 확정하지 못했습니다.** 기존 `_execute`가 child의 stderr와 종료 코드를 버렸으며, stage 로그에는 시작 시각/잠금 소유 구간이 없습니다. 따라서 시간대 일치는 잠금 경쟁 가설을 뒷받침하지만 해당 실패의 확정 증거가 아닙니다. 운영 정리를 반복해 추측으로 확인하지 않았습니다.

## 최소 수정

`pipeline/property_working_retirement.py`만 변경합니다.

- 자식 CLI의 정확한 한 줄 `working_store: <code>`가 고정 허용 목록에 있을 때만 해당 오류 코드를 보관합니다.
- traceback·원문·URL·경로·환경변수·알 수 없는 코드·여러 줄 출력은 저장하지 않습니다. 기존 일반 오류 코드만 사용합니다.
- `child_exit_code`를 기록합니다. 예를 들어 -9는 signal 종료 증거이며 OOM의 확정 증거로 바꾸지 않습니다.
- `vps_bulk_work_busy`, `local_archive_writer_active`, `archive_collector_active`는 삭제 전 검사에서 발생하므로 `deferred / reason=busy / retryable=true / retired=0`으로 표시합니다.
- 즉시 재시도나 새 수집을 추가하지 않습니다. 다음 기존 worker 주기에 같은 검증 head로 이어갑니다. 다른 오류·timeout은 실제 부분 처리 여부를 모르므로 `retired=null`을 유지합니다.
- 원문·장부·CAS·API의 닫힌 index 게시 순서는 바꾸지 않습니다.

## 검증

실제 자식 프로세스를 띄운 작은 fixture에서 stage가 bulk lock을 소유하면 삭제 0·장부 유지·API 조회 유지로 미뤄지는 것을 검사합니다. 잠금 해제 뒤 다음 주기에는 같은 head로 정상 완료합니다. 허용 코드·비밀 문자열·여러 줄 trace·비ASCII·출력 상한·signal 종료도 검사합니다. 운영 데이터에 이 시나리오를 실행한 것은 아닙니다.

관련 retirement·working store·VPS runtime·bulk lock 회귀 90개 통과(34.98초), Python 문법·`git diff --check` 통과입니다.
