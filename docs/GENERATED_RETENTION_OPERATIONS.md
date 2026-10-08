# 맛동산 생성 후보의 자동 보관 정책

작성: 2026-10-09. CLI·테스트·실행 예시를 구현한 문서입니다. **운영 정책 파일 작성, timer 설치·시작, 운영 파일 삭제는 이번 변경에 포함되지 않습니다.**

## 삭제 범위와 보존 범위

유일한 삭제 대상은 지정 프로젝트의 `.local/pages-release/korea-replay[-data]-<hash>-candidate/client` 또는 `...-production-<hash>/client`입니다. 원본·수집 장부·CAS·조회 DB·다른 Git 작업·Docker 이미지·볼륨은 접근/삭제 대상이 아닙니다. 후보 밖의 출처 묶음은 해시만 검증하고 삭제하지 않습니다.

현재본·직전 정상본·기반 자료·작업 중 후보의 `receipt.json`을 각각 지정해야 합니다. 설정된 역할 하나라도 빠지거나 보호할 payload가 없으면 실행하지 않습니다. 정상 종료된 미참조 후보도 최소 24시간 보관합니다. 후보의 파일 이름·크기·SHA256이 자산 목록과 일치해야 하고, 예상 밖 파일이 있으면 그대로 남깁니다. 영수증·자산 목록·작은 폐기 기록은 보존합니다.

`logical_bytes`는 삭제한 디렉터리 항목의 논리 크기입니다. 하드링크나 파일시스템 압축을 고려한 확보 용량이 아니므로 실제 확보량은 실행 전후 `df -B1`로 별도 확인해야 합니다.

## 비공개 정책 파일

예시 위치: `/srv/services/korea-replay/shared/retention-policy.json`. 운영 값은 저장소에 커밋하지 않습니다. 게시 검증이 성공한 작업이 아래 참조를 새로 확인한 뒤 파일을 원자적으로 바꾸도록 연결합니다. 정리기가 설정의 만료일을 스스로 연장하지 않습니다.

```json
{
  "schema_version": 1,
  "project": "korea-replay",
  "service_root": "/srv/services/korea-replay",
  "project_root": "/srv/services/korea-replay/workspaces/matdongsan",
  "generated_at": "2026-10-09T00:00:00Z",
  "expires_at": "2026-10-10T00:00:00Z",
  "protected": {
    "source": [{"path": ".local/pages-release/korea-replay-aaaaaaaaaaaaaaaa-candidate/receipt.json", "sha256": "<해당 receipt.json SHA256>"}],
    "current": [{"path": ".local/pages-release/korea-replay-bbbbbbbbbbbbbbbb-production-12345678/receipt.json", "sha256": "<해당 receipt.json SHA256>"}],
    "previous": [{"path": ".local/pages-release/korea-replay-cccccccccccccccc-production-23456789/receipt.json", "sha256": "<해당 receipt.json SHA256>"}],
    "active": []
  },
  "public_runtime": {
    "url": "https://korea-replay.pages.dev/api/v2/runtime",
    "artifact_sha256": "<현재 앱 receipt의 artifact_sha256>",
    "data_manifest_sha256": "<현재 앱 receipt의 policy.data.manifest_sha256>"
  },
  "activity_locks": [
    {"path": "/srv/services/korea-replay/shared/data/bulk-work.lock", "mode": "flock"},
    {"path": "/srv/services/korea-replay/workspaces/matdongsan/.local/pages-release/publication.lock", "mode": "presence"}
  ]
}
```

예시의 해시·경로·시간은 반드시 실제 검증본으로 교체해야 합니다. 기반 자료가 기존 Workers 묶음이면 `source`에 `.local/deploy/bundles/.../receipt.json`도 지정할 수 있습니다. 앱 외에도 현재·직전·활성 데이터 후보의 Pages 영수증을 각 역할에 함께 넣습니다. `source/current/previous`는 비울 수 없고 `active`만 빈 배열을 허용합니다.

설정 유효기간은 최대 24시간입니다. 미래 시각·만료·참조 파일 SHA 변경·공개 앱/데이터 해시 변경·네트워크 오류이면 **아무 payload도 삭제하지 않습니다**. 공개 런타임은 고정 HTTPS 읽기 주소만 사용하며, 리다이렉트·내부망 주소·인증정보는 허용하지 않습니다.

## 작업 잠금과 실행

수집기의 기존 `shared/data/bulk-work.lock`을 함께 사용합니다. 정리기는 이 잠금을 획득하고 완료 시 운영체제 잠금을 해제합니다. 파일 자체가 남아 있어도 잠금을 잡은 프로세스가 없으면 다시 실행할 수 있습니다. 잠금 파일을 삭제하거나 PID만 보고 빼앗지 않습니다.

**자동 삭제 활성화 전 필수 조건:** Pages 생성·업로드·공개 전환·복원도 같은 잠금을 사용해야 합니다. 기존 `scripts/pages-release.mjs` 자체는 잠금을 자동 취득하지 않으므로 다음 실행 래퍼를 통과시킵니다.

```sh
sh deploy/vps/run-generated-work.sh node scripts/pages-release.mjs app /비공개/options.json
```

이 래퍼는 명령어를 셸 문자열로 합치지 않습니다. 인수 배열을 그대로 전달합니다. `publication.lock` 같은 `presence` 잠금은 존재 자체가 정지 조건입니다. 오래된 파일로 보여도 정리기가 임의 제거하지 않으며 실제 작업 종료를 확인한 운영자가 해소합니다.

정리 CLI는 자체 중복 잠금과 수집·게시 잠금을 내부에서 잡으므로 `run-generated-work.sh`로 한 번 더 감싸지 않습니다.

```sh
# 기본은 삭제하지 않는 검수 실행
sh deploy/vps/run-pages-retention.sh --summary

# 검수·보호 참조 확인 후에만 실제 정리
sh deploy/vps/run-pages-retention.sh --apply --summary
```

설정은 실행 시작과 각 후보 삭제 직전에 다시 검사합니다. 작업 중 새로운 presence 잠금이나 설정 변경이 생기면 이후 후보를 보존하고 종료합니다. payload도 삭제 직전 파일·SHA를 다시 대조합니다. 이 검사는 협조하지 않는 게시 프로세스를 원자적으로 멈추는 장치가 아니므로 위 공통 잠금 연결을 건너뛰면 안 됩니다.

## 일일 스케줄러 예시

`deploy/vps/korea-replay-retention.service`, `.timer`는 설치하지 않은 예시입니다. 매일 한국시간 04:20 이후 10분 내 실행합니다. 설정이 만료된 날에는 종료 코드 2로 종료하고 이전 후보를 그대로 둡니다.

설치 전에 운영자가 확인할 항목:

1. 최신 source/current/previous/active와 런타임 pin이 들어간 비공개 정책.
2. 모든 생성·게시·복원이 공통 잠금을 통과하는지 확인.
3. `korea-replay` 실행 계정과 폴더·기존 잠금 inode의 소유권. 계정 생성이나 권한 변경은 예시 파일이 수행하지 않음.
4. `.local/retention` 디렉터리와 `bulk-work.lock` 준비. systemd의 쓰기 허용 경로는 생성 payload·정리 로그·프로젝트 잠금으로 제한.
5. dry-run → 실제 대상 검수 → 적용 → 재실행에서 `retired_count=0`.

로컬 실행 로그는 `.local/retention/retention.jsonl`과 `.1` 두 파일, 각각 최대 1MiB입니다. 원문/개인정보/자산 본문을 로그에 넣지 않습니다. 설정이 잘못되어 로그 경로를 신뢰할 수 없으면 표준 오류에 짧은 중단 이유만 출력합니다. 스케줄러는 요약 출력만 남깁니다.

## Docker 캐시와 조회 DB는 별도입니다

2026-10-09 읽기 검사에서 VPS는 `default` / `docker` 빌더 하나를 사용했습니다. 이 빌더의 4GiB GC 설정은 다른 프로젝트의 빌드 캐시에도 영향을 줄 수 있어 자동 적용하지 않습니다.

Docker 공식 문서는 기본 `docker` 드라이버의 daemon 설정과 별도 BuildKit 설정을 구분합니다. 이 서비스 전용 builder로 실제 빌드가 분리된 후에만 전용 설정의 `maxUsedSpace=4294967296`을 보관 목표로 검토합니다. 캐시가 실행 중이거나 공유 중이면 즉시 그 크기가 되는 보장은 아닙니다. 전역 `docker system prune`, 볼륨 제거, daemon 재시작은 이 도구에 없습니다. [Docker GC 문서](https://docs.docker.com/build/cache/garbage-collection/)

조회 DB는 현재·직전·진행 중 후보 외에도 API가 사용 중인 세대를 정확히 알아야 합니다. **generation pin/사용 종료 추적이 없으므로 이 스케줄러는 조회 DB를 자동 삭제하지 않습니다.** 원본 압축/CAS 수명 관리는 별도 장부와 복구 검증을 담당하는 경로를 따릅니다.

## 완료 판정

테스트는 만료/누락 정책, 보호 참조 변조, 현재 런타임 불일치, 중복 스케줄러, 실제 수집 잠금 경합, symlink, SHA가 다른 동일 크기 파일, 적용 후 무작업 재실행, 로그 상한과 실제 CLI 진입을 확인합니다. HTTP 응답은 고정 fixture이며 운영 네트워크 검증과 timer의 날짜 전환 관찰은 별도입니다.

CLI와 예시 파일의 검증을 운영 활성화로 표현하지 않습니다. 스케줄러 설치·공통 잠금 연결·실제 dry-run/적용·하루 운영 관찰이 남은 적용 단계입니다.
