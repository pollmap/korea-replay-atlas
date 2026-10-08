# 압축 CAS를 이용하는 수집 작업본

기준일: 2026-10-09. 이 문서는 구현과 실서버 읽기 전용 조사를 구분합니다.

## 구현 범위

기존 평문 XML/JSON/XZ 파일을 우선 읽고, 평문이 없는 경우 작은 `.working-store.sqlite` 색인에서 **기존 CAS pack의 정확한 파일 구간**을 읽습니다. `LocalArchive.get()`의 XZ·zlib 호환과 `read_file()`의 객체·구간·원문 SHA 검사를 재사용합니다. 장부의 경로·SHA·크기·정규화 snapshot 인코딩은 바꾸지 않습니다.

적용 경로는 Collector의 재처리·새 수집·이전 snapshot 비교, 게시기의 공통 `checked_read` 및 원문 감사, VPS 거래 API, legacy/set 백업, cutover tar.gz 내보내기입니다. 백업/내보내기는 설치별 색인 경로를 담지 않고 원래 논리 파일을 포함합니다. 따라서 과거 복구 형식처럼 평문으로 복원할 수 있습니다. 별도 원본 import CLI와 원격 D1 hydration은 새 평문 작업 디렉터리를 만드는 기존 흐름을 유지합니다.

색인은 원본 저장소가 아닙니다. 현재 장부뿐 아니라 과거 snapshot/폐기되지 않은 원본을 담은 CAS head의 raw·snapshot 항목을 참조합니다. 객체 저장소가 손상되거나 없어지면 성공 또는 0건으로 처리하지 않습니다. registry·runs·changes·history-windows·checkpoint·2D 지도 자료는 이관/삭제 대상이 아닙니다.

## 실서버 조사: 삭제 전 상태

명령은 `--plan`만 실행했습니다. 원본·장부·CAS 객체·서비스 설정에 쓰기 또는 삭제를 수행하지 않았습니다.

| 항목 | 관찰 결과 |
|---|---:|
| CAS set head | `89dfd15d7d451c9424699b79573a7dfd504c8b7c666c0e114ca46da8188aa50a` |
| head의 raw/snapshot 파일 | 110,176 |
| 해당 논리 바이트 | 15,983,798,473 |
| 존재하는 평문 파일 | 110,176 |
| 평문 크기 충돌·누락 | 0 / 0 |
| 현재 장부 참조 | 109,662 |
| head에서 동일 경로/SHA/크기로 찾은 장부 참조 | 109,662 |
| 기존 작업본 색인 | 0 |

위 표는 **참조·크기 조사**입니다. payload 해시 전수 검증이나 실제 공간 확보를 뜻하지 않습니다. 이관 시 각 원문의 실제 바이트와 압축 해제한 원문의 바이트가 일치해야만 색인을 기록하고 삭제할 수 있습니다. 새 수집이 계속되면 이 수치는 달라집니다. 현재 head에 없는 새 파일은 삭제 대상에 포함되지 않습니다.

운영 원본은 `/srv/services/korea-replay/shared/data/collector`, 압축 CAS는 `/srv/services/korea-replay/backups/cas`입니다. 관찰 시 API 이미지 `b85203a`에는 CAS 마운트가 없었고, 수집기 이미지 `94a47b6`은 CAS를 `/backups`로 사용했습니다. **이 코드로 양쪽을 전환하기 전에는 평문을 삭제하면 안 됩니다.**

## 실행 순서

1. 현재 및 복구에 사용할 API/수집기 이미지를 이 dual-reader 코드로 준비합니다. API에 CAS 읽기 전용 마운트를 추가하고 양쪽에 `KOREA_REPLAY_WORKING_ARCHIVE=/backups`를 지정합니다. 같은 Docker 경로를 모든 후보에도 적용합니다. 호스트 CLI는 환경변수를 지정하지 않으면 색인에 기록한 호스트 CAS 경로를 사용합니다.
2. 두 서비스의 `/health`, 같은 지역·월의 실제 거래 응답을 검증합니다. 장부 최신본과 CAS head를 확인합니다. 새 head가 필요한 경우 기존 백업 절차를 사용하고 전체 평문 복제본을 별도로 만들지 않습니다.
3. 수집기를 정상 종료해 진행 중인 작업을 마칩니다. 게시·복원·백업이 동시에 실행되지 않는지 확인합니다. CLI가 `bulk-work.lock`을 비차단 방식으로 획득하고 장부 `BEGIN IMMEDIATE` 및 활성 lease를 검사합니다. 잠금/lease가 있으면 실패하며 강제로 해제하지 않습니다. API는 계속 읽을 수 있습니다.
4. 작은 배치부터 이관하고 응답을 확인한 뒤 최대 10,000파일 배치로 진행합니다. 실행 중단 시 같은 명령을 다시 실행합니다. 기존 색인에는 쓰지 않습니다. `.working-store.sqlite.next` 후보를 배치 단위로 커밋·무결성 검사·종료한 뒤 파일 fsync→원자 교체→디렉터리 fsync를 완료해야 원문을 지웁니다. 후보에 hot rollback journal이 남은 강제종료에서도 API는 이전 정상 색인을 계속 읽습니다.
5. 전체 장부 감사와 표본 공개 가공, legacy/set/cutover 복원 검사를 완료합니다. 실제 `df`의 전후 가용 공간을 측정합니다. 이후 수집기를 재개합니다. 이관 결과의 `retired_logical_bytes`를 실제 확보량으로 대신 보고하지 않습니다.

읽기 전용 조사:

```sh
python -m pipeline.real_estate_working_store --plan \
  --root /srv/services/korea-replay/shared/data/collector \
  --store /srv/services/korea-replay/backups/cas
```

색인 준비만 수행하며 원문을 보존하는 배치:

```sh
python -m pipeline.real_estate_working_store \
  --root /srv/services/korea-replay/shared/data/collector \
  --store /srv/services/korea-replay/backups/cas --limit 10000
```

양쪽 서비스의 reader 전환 검증 이후 실행하는 배치:

```sh
python -m pipeline.real_estate_working_store \
  --root /srv/services/korea-replay/shared/data/collector \
  --store /srv/services/korea-replay/backups/cas --limit 10000 \
  --retire-plaintext --readers-deployed
```

`--readers-deployed`는 운영자가 서비스 배포를 확인했다는 명시적 조건입니다. 자체적으로 컨테이너 배포 여부를 증명하는 옵션은 아닙니다. 삭제는 이 명령에서만 가능하며 API/수집기는 자동으로 평문을 지우지 않습니다. 같은 입력에 재실행하면 새 객체·색인 행·평문 복사본이 증가하지 않습니다. 진행 중인 `.next` 후보와 그 journal만 재개 시 정리하며 현재 색인의 journal을 임의 제거하지 않습니다. 새로 수집한 원문은 이후 CAS 백업에 포함된 뒤 다음 배치에서 이관합니다.

## 상한과 복구

- 객체 압축 해제 상한은 기존 `MAX_FILE=256MiB`, XZ decoder 메모리는 64MiB입니다. 원문별 실제 허용 상한은 게시/API 등 호출부에서 추가로 제한합니다.
- 읽기 pack 캐시는 최대 32MiB/16개이고 파일 inode·크기·수정시각을 키에 포함합니다. 같은 pack에 있는 많은 작은 XML을 반복 해제하지 않습니다.
- 작업본 색인은 삭제 또는 교체 전까지 CAS 객체를 참조하는 추가 root입니다. **현재·이전 set head와 색인이 참조하는 객체를 임의로 정리하지 마십시오.** 이번 변경은 CAS 객체 GC를 구현하지 않습니다.
- 이관 뒤 옛 평문 전용 이미지를 그대로 롤백하면 안 됩니다. dual-reader 정상본으로 롤백하거나, 충분한 공간을 확보한 별도 작업 디렉터리에 검증된 set/cutover를 복원한 후 전환해야 합니다.
- 색인이 유실돼도 검증된 CAS set 복원 목록은 원래 파일의 독립 복구 근거로 남습니다. 같은 디스크의 CAS를 디스크 고장에 대비한 별도 복구본으로 표현하지 않습니다.

## 검증

의미 있는 회귀는 평문·압축 읽기, 장부/SHA 보존, 중단·재개, 배치 상한, 동일 입력 무증가, 활성 수집기 거절, source/CAS 손상·누락·경로 변조 실패, cache 재사용·상한, 기존 JSON/XZ, 재처리/갱신, API 거래 조회, 두 백업 형식과 cutover 복원입니다. 실제 운영 원문 이관과 서비스 배포는 별도 게이트로 남습니다.

초기 검증은 관련 Python 회귀 230개 통과였습니다. 후속 리뷰에서 read-only API의 hot rollback journal 복구 결함을 발견해 후보 원자 교체 방식으로 수정했습니다. 실제 SIGKILL을 후보 미커밋 hot journal 존재 시점·교체 직후·첫 원문 삭제 뒤에 보내 이전 API 응답, 열린 이전 inode와 재개를 검사합니다. 일반 예외 후 정상 close만으로 강제종료 안전성을 주장하지 않습니다. 실제 운영 원본은 삭제하지 않았습니다.

운영 배치 권장: `nice -n 10 ionice -c 2 -n 7`로 첫 100파일을 수행하고 API·복원 확인 후 1,000~10,000파일로 늘립니다. 고정 디스크 여유량 조건이나 전체 원문 재복제는 추가하지 않았습니다.

후보 색인을 만들 때에는 현재 색인 크기와 후보 쓰기 journal 만큼의 일시 공간이 추가로 필요합니다. 원문을 복제하지 않으며 닫힌 현재 색인과 활성 후보 하나만 둡니다. 디스크 부족 등 후보 생성 실패 시 원문과 기존 정상 색인은 그대로 유지됩니다. 옛 live-index writer로 만든 hot journal이 이미 있는 설치는 별도 RW 복구 후 전환해야 합니다. 이번 운영 조사에서 기존 색인은 없었으며 이관을 실행하지 않았습니다.

강제종료 수정 후 최종 검증: 관련 Python 회귀 **233개 통과(88.09초)**, compileall·git diff --check 통과. 별도 코드 리뷰에서도 후보 한정 쓰기·종료·fsync·원자 교체·이전 매핑 유지 조건을 재확인했습니다.
