# 실거래 수집기의 원격 보존과 자동 운영 연결

## 목적과 완료 경계

`pipeline.real_estate_archive`는 공공 API 수집 장부와 원본을 **비공개 Cloudflare D1**에 보존하고 새 폴더로 복구합니다. 웹사이트 데이터 게시기나 자동 수집 스케줄러가 아닙니다. 원격 복구가 검증되지 않은 상태에서 로컬 원본을 삭제하거나 자동 수집을 켜지 않습니다.

자동 운영은 원격 복구 → 호출 예산 예약 → 제한된 수집 → 원본·장부 확정 → 자료 감사 → 불변 게시 순서로 연결합니다. 이 모듈은 그중 원격 복구와 보존을 구현합니다. 키가 포함된 인증 파일, 브라우저 상태, 게시 후보, 내부 로그는 백업 대상에서 제외합니다.

## 저장 계약

- 제어 DB 1개와 객체 DB 4개를 사용합니다. 기존 서비스 DB를 재사용하지 않습니다. 객체 SHA-256의 첫 바이트를 4로 나눈 나머지로 저장 위치를 정합니다. **배열 순서와 DB ID를 바꾸면 기존 객체를 찾을 수 없습니다.**
- SQLite online backup으로 일관된 장부를 복사하고 무결성·현재 참조·과거 스냅샷 파일의 해시를 검사합니다. 진행 중인 collector lease가 있으면 거부합니다.
- `checkpoint.sqlite`, `raw`, `snapshots`, `registry`, `runs`, `changes`, `history-windows`만 허용합니다. 내용 주소 파일명, 경로 탈출, 심볼릭 링크·junction, 최대 파일 수·크기를 검증합니다.
- 파일을 원본 상태로 묶어 zlib로 무손실 압축한 뒤 48KiB 조각을 저장합니다. 복구 시 압축 해제 상한, 객체 SHA, 각 파일 SHA를 모두 검사합니다. 재수집·원본 변경·공개 게시를 수행하지 않습니다.
- 작은 SQL 묶음과 제한된 조회 페이지를 사용합니다. **새 버전 등록 전에 모든 객체를 원격에서 다시 읽어 검증**합니다. 네트워크 실패·중간 종료는 새 정상본으로 등록되지 않습니다. 부분 객체는 같은 내용으로 재개할 수 있습니다.
- 기존 정상본의 파일이 로컬에서 누락되면 증분 백업을 거부합니다. 새 파일 때문에 과거 전체를 다시 묶지 않고 기존 객체 주소를 유지합니다.
- 마지막 정상본 포인터는 이전 SHA·크기가 같은 경우에만 갱신합니다. 다른 백업이 먼저 끝났으면 오래된 작업의 갱신을 거부합니다. 이 비교는 **백업 포인터 보호**이며 수집 호출의 분산 잠금이 아닙니다.
- 객체 DB당 380MiB에서 추가 쓰기를 중지합니다. 삭제·유료 전환·R2 활성화는 하지 않습니다. 장기 정정본 축적은 유한한 무료 용량을 사용하므로 잔여 용량을 운영 지표로 관리해야 합니다.

[D1 공식 한도](https://developers.cloudflare.com/d1/platform/limits/)에 따르면 Free는 DB당 500MB, 계정 전체 5GB, 최대 10개 DB이며 [무료 일일 한도](https://developers.cloudflare.com/d1/platform/pricing/)도 공유됩니다. 다른 서비스의 사용량을 포함한 계정 한도는 별도로 확인해야 합니다. 모듈의 자체 중지 기준은 무료 요금제 확인을 대체하지 않습니다.

## 실행과 복구

Python 표준 라이브러리만 사용합니다. 인증정보는 `CLOUDFLARE_API_TOKEN` 환경변수로 전달하며 명령행 인자·Git·출력에 넣지 않습니다. 설정 JSON은 아래 구조이고 토큰은 포함하지 않습니다.

```json
{
  "account_id": "ACCOUNT_ID",
  "control_database": "CONTROL_DATABASE_UUID",
  "object_databases": ["OBJECTS_0_UUID", "OBJECTS_1_UUID", "OBJECTS_2_UUID", "OBJECTS_3_UUID"]
}
```

```text
python -B -m pipeline.real_estate_archive initialize --config PRIVATE_CONFIG_PATH
python -B -m pipeline.real_estate_archive backup --config PRIVATE_CONFIG_PATH --root PRIVATE_COLLECTOR_ROOT
python -B -m pipeline.real_estate_archive status --config PRIVATE_CONFIG_PATH
python -B -m pipeline.real_estate_archive restore --config PRIVATE_CONFIG_PATH --root NEW_RESTORE_DIRECTORY
```

복원 대상은 존재하지 않는 새 디렉터리여야 합니다. 실패한 복원 폴더는 자동으로 지우지 않으며, 다음 시도에는 다른 새 디렉터리를 사용합니다. 원격 버전의 전체 파일을 복구한 뒤 SQLite 무결성·작업 상태별 수·호출 수·스냅샷 수·참조 해시를 다시 검사합니다. 복원 중간 결과는 수집기나 게시기에 연결하지 않습니다.

로컬 장비를 잃었으면 Cloudflare 계정에서 프로젝트 이름의 control, objects-0~3 DB를 확인해 같은 순서로 설정을 복원할 수 있습니다. 저장소 이름은 `korea-replay-property-control`, `korea-replay-property-objects-0`~`3`입니다. 공개 HTTP 조회 경로를 만들지 않았으며 읽기에도 Cloudflare 인증이 필요합니다.

## 새 달 추가

`real_estate_fetch --advance-window --months 121`은 현재월이 앞으로 진행할 때 새 작업을 추가하고, 이전 모든 월·원본·스냅샷·호출 기록을 유지합니다. 최근 완료월을 우선하도록 작업 우선순위만 조정합니다. 121개월에서 다음 달로 넘어가면 122개월이 되며 오래된 월을 삭제하지 않습니다. 시간을 뒤로 돌리거나 현재 조회 기간과 이전 최신월이 겹치지 않는 큰 도약은 거부합니다. 동일 명령 재실행은 작업을 중복 생성하지 않습니다.

이 옵션은 이미 완료된 최근월의 정정 재조회 정책을 대신하지 않습니다. 자동 수집을 연결할 때 최근월 재조회와 과거월 순환 검사를 별도 일정으로 정해야 합니다.

## 남은 자동 운영 관문

1. 전체 실제 장부·원본의 원격 업로드 및 독립 폴더 복원 검증.
2. 자동 실행용 최소 권한 인증, 분산 단일 소유권, 호출 전 원격 예산 예약. 로컬 장부별 한도만으로 여러 실행기의 합산 호출을 보장할 수 없습니다.
3. 무료 실행 환경에서 전체 10년 원본을 매번 복구하지 않도록 필요한 작업의 파일만 내려받는 증분 실행.
4. 새 달 추가·최근 정정·과거 순환 갱신과 과거 유효 지역 코드 대응표.
5. 후보 데이터 감사 및 앱/데이터 릴리스의 원자적 공개·롤백.

이 관문이 남아 있는 동안 GitHub의 `property-plan.yml`은 계속 계획만 생성합니다. 노트북 없이 게시된 웹사이트를 이용하는 것과 자료가 자동으로 새로 수집되는 것은 각각 별도 상태입니다.
