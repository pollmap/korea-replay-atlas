# 게시 완료 후 생성 후보 보관 정책 갱신

2026-10-09. 코드·fixture 검증 문서입니다. 운영 정책 갱신, timer 활성화, 후보 삭제, 계정·권한 변경은 이 변경에서 실행하지 않습니다.

## 동작

`pipeline.generated_work`는 생성·게시 명령과 후속 정책 검증이 끝날 때까지 기존 `shared/data/bulk-work.lock`을 유지합니다. `pipeline.pages_retention_policy`는 명시한 영수증 경로에서 보호 참조를 만들고, 검증을 통과한 경우에만 작은 비공개 정책을 원자 교체합니다.

1. 게시 트랜잭션은 공통 잠금을 잡고 `publication.lock`을 만듭니다.
2. 지정한 명령이 후보 생성 → 업로드 → 원격 검증 → 공개 전환 → 대표 주소 검증을 수행합니다. 명령은 인수 배열로 전달하며 셸 문자열로 합치지 않습니다.
3. 게시 명령은 실제 생성한 경로를 비공개 inventory에 기록합니다. 명령 종료 코드가 0이어도 검증 영수증·본문·공개 런타임이 맞지 않으면 다음 단계는 실패합니다.
4. 현재·직전·기반·활성 후보의 자산 목록, 파일 수·크기·SHA256을 확인합니다. 현재·직전 앱의 데이터 pin과 일치하는 **로컬 데이터 본문 및 불변 공개 주소 검증 기록**도 함께 보호합니다. 기반 앱이 가리키는 오래된 데이터까지 현재 데이터로 간주하지 않습니다.
5. 현재·직전 앱에는 대표 주소의 `verified-production.json`이 필요합니다. 기반 후보에는 해당 불변 주소 검증 기록이 필요합니다. 미게시 활성 후보는 완성된 로컬 영수증/본문으로 보호할 수 있습니다. 생성 중인 불완전 후보는 공통 잠금과 presence 잠금으로 보호합니다.
6. 고정 공개 런타임 `/api/v2/runtime`의 앱/데이터 해시를 확인하고, 정책을 같은 디렉터리의 0600 임시 파일 → 검증 → fsync → replace로 바꿉니다. 작업 성공 후 자신이 만든 동일 inode의 presence 파일만 제거합니다.

**후보를 만드는 명령이나 원격 검증이 실패하거나 프로세스가 중단되면 `publication.lock`을 남깁니다.** 자동 정리는 이 파일이 있는 동안 중단됩니다. 파일의 나이나 PID만 보고 자동 제거하지 않습니다. 운영자가 실패한 작업과 보호할 후보를 확인하고 재개 여부를 정한 다음 해소해야 합니다. 실제 원본·DB·Docker·서비스에는 이 유틸이 쓰지 않습니다.

## 비공개 inventory

아래 필드는 전부 필요합니다. `active`, `presence_locks`만 빈 배열을 허용합니다. 파일 권한은 0600이며 실행 계정 소유여야 합니다. 파일을 저장소에 커밋하지 않습니다.

```json
{
  "schema_version": 1,
  "project_root": "/srv/services/korea-replay/workspaces/matdongsan",
  "service_root": "/srv/services/korea-replay",
  "current": [".local/pages-release/<현재 앱 production 후보>/receipt.json"],
  "previous": [".local/pages-release/<직전 정상 앱 production 후보>/receipt.json"],
  "source": [".local/pages-release/<재생성 기반>/receipt.json", ".local/pages-release/<현재 앱의 candidate>/receipt.json"],
  "data": [".local/pages-release/<현재·직전·활성 앱이 참조하는 데이터 후보>/receipt.json"],
  "active": [],
  "presence_locks": []
}
```

현재/직전 앱은 각각 하나이며 서로 달라야 합니다. 수정시각이 최근이라는 이유로 current나 previous를 고르지 않습니다. 기존 공개 버전에서 새 공개 버전으로 바뀐 게시 작업이 이전 current를 previous로 명시합니다. 검증된 rollback을 수행할 때도 실제 전환 방향으로 지정합니다. 데이터 후보가 여러 개이면 `data`에 모두 나열하며, 앱의 데이터 manifest/불변 origin에 맞는 하나를 엄격히 선택합니다. 일치 후보가 0개 또는 여러 개이면 갱신하지 않습니다.

현재 유틸은 **Pages 본문을 가진 source**만 지원합니다. 과거 Workers 영수증만 남은 source나 본문이 폐기된 source를 검증된 복원본으로 승격하지 않습니다. 해당 경로를 입력하면 중단하며, 원본을 복사해서 임시로 통과시키지 않습니다.

정책 유효기간은 최대 24시간입니다. 참조·증명·공개 pin이 같고 12시간 이상 남으면 정책 파일을 다시 쓰지 않습니다. 정책이 만료되거나 갱신 시점이 되면 본문과 공개 응답을 다시 확인합니다. 변경 없는 실행에서 원본 재파싱·후보 생성·업로드·새 백업은 하지 않습니다. 검증된 generated 자산의 해시는 읽으므로 대형 데이터 후보의 디스크 읽기 비용은 남습니다. 별도 재사용 증명이 없는 상태에서 본문 검증을 건너뛰지는 않습니다.

## 실제 호출 경로

일회성 검증·정책 갱신(삭제 없음):

```sh
python3 -m pipeline.pages_retention_policy \
  --inventory /srv/services/korea-replay/shared/publication-inventory.json \
  --policy /srv/services/korea-replay/shared/retention-policy.json
```

게시 전체 명령에 연결:

```sh
sh deploy/vps/run-pages-publication.sh \
  /srv/services/korea-replay/shared/publication-inventory.json \
  node /srv/services/korea-replay/workspaces/matdongsan/.local/<검증된 게시 오케스트레이터>.mjs
```

위 명령은 실행 예시이며 파일 이름을 자동 추측하지 않습니다. 오케스트레이터는 기존 게시 도구를 순서대로 호출하고 실제 결과 경로로 inventory를 기록해야 합니다. `.local/deploy-vps-auth.mjs`의 **업로드 한 번**만 감싸는 것은 충분하지 않습니다. 공개 원격 검증까지 끝나야 정책이 갱신됩니다. 인증정보를 인수·inventory에 기록하지 않습니다.

후보 생성/복원만 수행하는 기존 명령은 `run-generated-work.sh COMMAND ARG...`를 통과시킵니다. 이 하위 래퍼는 정책을 갱신하거나 후보를 공개하지 않습니다. 전체 게시 래퍼 안에서 중첩 호출하면 검증한 동일 잠금 inode의 상속 FD를 재사용합니다. 환경변수 문자열만 믿고 잠금을 건너뛰지 않습니다. 잘못된 FD·다른 inode·중복 프로세스는 거부합니다. 수집기/CAS처럼 이미 자기 잠금을 잡는 다른 코드를 이 래퍼로 중복 감싸지 않습니다.

대체 프로젝트 경로는 `KOREA_REPLAY_PROJECT_ROOT`, 서비스 경로는 `KOREA_REPLAY_SERVICE_ROOT`로 지정할 수 있습니다. 서비스 이름은 `korea-replay`이고 프로젝트는 그 하위여야 하며, 잠금 경로는 항상 그 서비스의 `shared/data/bulk-work.lock`입니다. 임의 `/tmp` 잠금으로 바꿀 수 없습니다.

## 2026-10-09 읽기 전용 연결 감사

| 진입점 | 관찰 | 필요한 연결 |
|---|---|---|
| `scripts/pages-release.mjs` | 자체 공통 잠금 없음 | 생성 호출을 하위 래퍼로 실행 |
| `scripts/pages-api.mjs` | 자체 공통 잠금 없음 | 업로드/검증/공개 전환 전체를 게시 래퍼 안에서 실행 |
| `.local/stage-2d-app.mjs`, `.local/stage-history-app.mjs` | 공통 잠금 호출 없음 | 현재 실제 사용하는 2D 생성 호출만 하위 래퍼 연결 |
| `.local/deploy-vps-auth.mjs` | 공통 잠금 호출 없음 | 인증 방식 유지, 검증까지 포함한 상위 게시 트랜잭션에 연결 |
| `.local/restore-pages-bundle.mjs`, `.local/restore-data-map.mjs`, `.local/restore-data-property.mjs` | 공통 잠금 호출 없음 | 현재 필요한 복원 명령을 하위 래퍼로 실행 |
| 수집·candidate·CAS | 기존 bulk-work 잠금 사용 | 임의 교체/중복 래핑하지 않음 |
| retention timer | 설치된 항목 0개 | 아래 활성화 조건 확인 후 운영자가 설치 |

이 표는 코드 경로와 예약 작업의 관찰입니다. 나열한 모든 private 스크립트가 지금 실행 중이라는 뜻은 아닙니다. private 파일을 이 변경에서 덮어쓰지 않았습니다. raw Pages CLI를 직접 호출하는 경로가 남으면 협조적 잠금이 완성되지 않았으므로 timer 활성화 조건을 충족하지 않습니다.

## 운영 활성화의 남은 조건

1. 진행 중인 CAS 이관의 공통 잠금이 정상 해제될 때까지 기다립니다. 잠금을 빼앗거나 삭제하지 않습니다.
2. 실제 사용하는 생성·게시·복원 호출을 위 래퍼에 연결하고, dry-run/fixture가 아니라 해당 실제 게시가 새 inventory를 생성하는지 확인합니다.
3. 정책을 발행하는 계정과 정리 계정의 읽기/쓰기 권한을 맞춥니다. 감사 시 `korea-replay` 계정이 없었고 workspace/Pages와 shared는 root 소유, `shared/data`와 기존 bulk lock은 UID/GID10001이었습니다. 기존 잠금 inode를 유지한 채 필요한 최소 권한만 별도로 설정해야 합니다. 재귀 chown이나 실행 중 서비스 계정 변경은 이 코드가 수행하지 않습니다.
4. 현재·직전·기반·데이터 본문과 원격 검증 증명이 실제로 남아 있는지 확인합니다. 정책 생성 성공 후 `load_policy`와 공개 런타임 검증이 다시 통과해야 합니다.
5. `run-pages-retention.sh --summary`의 삭제 예정 목록을 확인한 뒤 승인된 범위만 적용하고, 같은 입력의 재실행에서 추가 후보/백업이 늘지 않는지 확인합니다.
6. 별도 발행 작업이 24시간 안에 정책을 갱신하도록 연결한 뒤 timer를 설치합니다. 정리기가 자체 만료를 연장하지 않습니다. 게시할 데이터가 없는 날에도 작은 정책 검증/갱신만 수행할 수 있으며, DB 복사·앱 재빌드·업로드는 불필요합니다.
7. 게시 실패, 날짜 전환, 잠금 경합, 공개 버전 변경 때 자동 삭제가 중단되는지 운영에서 관찰합니다.

조회 DB는 generation pin이 없어 자동 삭제하지 않습니다. Docker 4GiB 정책도 공유 default builder에 적용하지 않습니다. CAS 객체 수명 관리는 별도 구현과 장부의 책임입니다.

## 검증 범위

fixture 테스트는 생성→정책 교체→같은 입력 무작업, 만료 갱신, 본문/출처 누락, 동일 크기 변조, 잘못된 검증 증명, 데이터 pin 불일치/중복, 원자 쓰기 실패, private 권한, 경로 링크, 실패 시 이전 정책 보존, presence 차단, 실제 subprocess 중첩 잠금 재사용과 중복 실행 거부를 검증합니다. 실제 공개 배포/운영 삭제/일일 timer 검수와 구분합니다.
