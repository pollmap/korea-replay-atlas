# 로컬 정리와 복구·재개발

기준: 2026-09-19 KST. 실제 파일과 배포 증빙을 확인한 운영 문서입니다. 뒤에 생성된 배포는 그 버전의 증빙으로 다시 확인해야 합니다.

**로컬 파일을 지워도 이미 게시된 Cloudflare 웹·지도는 계속 제공될 수 있습니다. 그러나 수정 가능한 소스·원본·복구 증빙까지 없애면 이후 개발과 재배포가 어려워집니다. 현재 프로젝트 전체 삭제 조건은 충족되지 않았으며, 이 감사에서는 삭제나 이동을 하지 않았습니다.**

사용자 조건은 “최종 개발·배포를 끝내고, 이 프로젝트가 만든 로컬 파일만 정리”입니다. 소스와 데이터의 독립 백업 및 실제 복원 검증까지 완료한 뒤 적용합니다. 원격 미리보기 업로드, 공개 전환, 원격 롤백, 로컬 원본의 복구는 서로 다른 검증입니다.

**9월 20일 추가 증빙:** 미리보기 `da3dfd41` / 묶음 `d37dfc7d183b994d`의 변경 300파일·45,530,383 B를 ZIP 9,255,539 B로 저장하고 전부 별도 추출·재해시했습니다. [새 변경분 검증](../.local/recovery/delta-3d35ed6bc071d834-to-d37dfc7d183b994d/delta-verification.json)은 원래 건물 자료와 다른 부분을 정확히 구분합니다. 재사용 base 17,241파일은 이전 eb2 전체 복원 목록과 대조했으며 이번에 base 본문·ZIP을 재해시하거나 새 버전을 전체 복원한 것은 아닙니다. 실제 디스크 파일 삭제는 없었습니다. 새 소스 ZIP의 파일 복원 증빙은 [이번 소스 백업](../.local/national-upgrade-20260919/source-backup-verification.json)에 기록하며 외부 보관·독립 설치 빌드 완료를 의미하지 않습니다. 지도 공개판은 `095795c4`, 비공개 브로커는 `270d7204`입니다. [현재 미리보기·공개 경계](STREAMING_AND_SUBWAY_EXPANSION.md)를 우선합니다.

## 1. 확인한 현재 상태

| 항목 | 확인 결과와 의미 |
|---|---|
| 프로젝트 경계 | `C:\Projects\korea-replay` |
| Git | 감사 시 `git remote`와 `git ls-files` 출력이 없고, `git rev-parse --verify HEAD`도 실패했습니다. `.git` 폴더가 있다는 사실만으로 소스 백업이 된 것은 아닙니다. |
| Git 제외 대상 | `.local/`, `public/data/`, `dist/`, `.venv/`, `node_modules/`, `.wrangler/`, 실제 환경변수 파일은 `.gitignore` 대상입니다. 향후 소스를 Git에 보관해도 데이터는 별도 보관해야 합니다. |
| 현재 공개 배포 | 원격 버전 `095795c4-1dfa-4d53-97d4-fbc74d797608` 100%. 중앙 교통 서버 연결판. 묶음 `e79c3faf906a102e`, 데이터 `pub-a55c6a081e5782db` |
| 공개 묶음 크기 | receipt 기준 정적 파일 17,401개·10,725,168,600 B. Worker·manifest·receipt와 복구 ZIP의 크기는 별도입니다. |
| 바로 이전 공개 기록 | 원격 버전 `ab42e105-8e29-4078-806b-cb20fb799662`, 묶음 `579c2245026b946b`. 중앙 연결판의 실제 롤백·복귀 각 67건에 사용하고 보존합니다. 3b·013 등 이전 증빙도 보존합니다. |
| 이전 eb2 공개 기록 | 원격 버전 `013966a1-e7a0-4154-bf62-9bbe564988d5`, 묶음 `eb2f58d5eaecae4d`. 3b 공개판의 전체 67건 롤백·복귀에 사용했으며 보존합니다. |
| 이전 e4 공개 기록 | 원격 버전 `58cd8d4b-5979-47cd-8742-542ffdbd996d`, 묶음 `e4a7bd1c2eb6d18e`. 당시 증빙과 델타 ZIP은 보존합니다. |
| 이전 core 묶음 | `3d35ed6bc071d834`, 데이터 `pub-b3ce2e7229184cfc`, 원격 버전 `3d42002d-39b2-4ee5-8a4f-190069f41a32`. 현재 델타의 base입니다. |
| core 전국 로컬 복원 | **완료.** 기존 ZIP 12개에서 새 로컬 폴더로 17,423파일·10,723,076,576 B를 복원했고 전체 파일 SHA 검사가 통과했습니다. 수정 가능한 소스·원천 자료·인증 복원은 이 범위에 포함되지 않습니다. |
| 과거 e4 델타 ZIP 검증 | **완료.** 추가/교체 154파일·14,700,421 B의 ZIP 생성, 압축 SHA와 내부 전체 파일의 해제 SHA·크기 검사가 통과했습니다. |
| 과거 e4 전국 전체 복원 | **미실행.** base와 delta를 함께 사용한 17,405파일·10,728,272,476 B의 실제 전체 복원은 진행하지 않았습니다. |
| 과거 e86 델타 ZIP 검증 | **완료.** 추가/교체 154파일·14,705,728 B의 ZIP 생성, 압축 SHA와 내부 전체 파일의 해제 SHA·크기 검사가 통과했습니다. |
| 과거 e86 전국 전체 복원 | **완료.** core base와 e86 delta에서 새 로컬 폴더로 17,405파일·10,728,277,783 B를 복원했습니다. base/delta ZIP 전체 SHA, 추출 파일 전체 SHA, 복원 폴더 전체 재검사와 해당 receipt의 JS 배포 사전 검사가 통과했습니다. |
| 이전 eb2 전국 전체 복원 | **완료.** 17,405파일·10,728,282,724 B를 새 로컬 폴더에 복원했습니다. 전체 ZIP·파일 SHA와 복원본 JS 사전 검사가 통과했습니다. |
| 현재 579 델타 ZIP 검증 | **완료.** 추가/교체 155파일·14,731,968 B, ZIP 3,347,659 B를 생성하고 ZIP·모든 변경 파일의 추출 SHA를 검사했습니다. 기존 eb2 복원 목록과 전국 데이터 17,001파일 및 Worker의 해시가 같습니다. |
| 현재 579 전국 전체 복원 | **이번에는 반복하지 않음.** 재사용 base 17,250파일 본문과 base ZIP의 재해시는 반복하지 않았습니다. 기존 전체 복원 증거와 새 변경분 검증을 구분합니다. |
| 외부 보관소의 다운로드·복원 | **미실행.** 같은 노트북에서 core·e86·eb2의 전국 복원을 검증했습니다. 외부 보관 사본을 내려받아 복원한 증거는 아직 없습니다. |
| 수정 가능한 소스 백업 | 같은 노트북에서 별도 ZIP과 새 소스 폴더 복원은 수행했습니다. 첫 스냅샷은 독립 설치·타입·482개 테스트·빌드까지 통과했지만 이번 화면 변경 전 버전입니다. 최신 스냅샷의 파일·검사 범위는 아래 증빙에서 확인합니다. 외부 보관은 아직 없습니다. |
| 원본·SQLite 독립 백업 | **미실행.** 원천 자료·SQLite·검수 증빙은 배포 및 소스 ZIP과 별도로 보관·복원해야 합니다. |
| 원격 HTTP 검사 | 현재 공개 095의 미리보기·공개 주소·ab42 롤백·095 복귀 각각 67/67 통과. 이전 ab42 전파 직후 66/67과 안정화 후 67/67도 보존합니다. 외부 백업 다운로드·복원과 별도입니다. |
| 현재 공개 버전의 롤백 시험 | **완료.** 095→ab42→095 실제 전환과 각 67건 HTTP 검사 증빙은 중앙 배포 기록에 있습니다. 9월 20일 새 미리보기 da3는 공개 전환·롤백을 아직 하지 않았습니다. |
| 추가 개발 | 전국 기본 화면·패널 제어·이동 중 형상 작업 대기·Worker 속성 전송 개선을 공개했습니다. 전국 P95 33ms·모든 생성 8ms와 원천 정밀도는 미완료입니다. |
| 삭제 판정 | **보류.** 외부 다운로드·복원, 소스와 원본의 외부 백업, 남은 기능·성능 검증이 필요합니다. |

현재 ab42 증빙은 `.local/seoul-live-20260919/http-preview-release.json`, `public-release-settled.json`, `rollback-roundtrip.json`, `public-live-final.json`입니다. 키·정책은 별도 비공개 설정이며 소스 ZIP에 넣지 않습니다. [공식 연결 기록](LIVE_CONNECTION_20260919.md)을 따릅니다.

이전 3b 공개 증빙은 `.local/map-first-20260919/preview-verification.json`, `public-verification.json`, `rollback-eb2-verification.json`, `restored-public-verification.json`, `rollback-roundtrip.json`입니다. 새 묶음의 receipt는 `.local/deploy/bundles/579c2245026b946b/receipt.json`입니다. [화면·성능·배포 기록](MAP_FIRST_PERFORMANCE.md)에 적용 범위와 한계를 정리했습니다.

현재 변경분 검증은 `.local/recovery/delta-3d35ed6bc071d834-to-579c2245026b946b/delta-verification.json`입니다. delta manifest SHA-256은 `8e782149892e14ba94a2b54a3a0f2fca2c08b93b98acb949285f810f014d32c2`, ZIP SHA-256은 `301eb7f9c149a5df51cddb45b6a8b001467592536853674f2205adfe9bfa664a`입니다. 연결한 기존 전체 복원 증거는 `.local/recovery/delta-3d35ed6bc071d834-to-eb2f58d5eaecae4d/final-recovery-audit.json`입니다. 기존 core·e4·e86·eb2 증빙과 원본도 모두 보존했습니다.

최신 중앙 연결판의 소스 ZIP·크기·SHA·파일 복원 결과는 [소스 스냅샷 증빙](../.local/central-live-20260919/source-backup-verification.json)에 기록합니다. 최신 지도 delta와 별도 브로커 의존성은 [중앙 배포 기록](CENTRAL_LIVE_DEPLOYMENT.md)을 따릅니다. 첫 스냅샷 `.local/source-backup-20260919/snapshot-20260919T112421Z-5b68096f/build-verification.json`의 독립 빌드 성공을 최신 소스의 독립 빌드 성공으로 대신하지 않습니다. 모든 소스 스냅샷은 실제 키·공개 지도·원천 데이터·의존성을 제외합니다.

## 중앙 서버 추가 이후 복구 경계

지도 코드·정적 자료와 중앙 교통 서버의 코드·SQLite 상태는 별도입니다. 브로커는 고정 namespace와 `transit-production` 객체를 유지합니다. 실제 코드 롤백에서 호출 예약 보존을 확인했지만 SQLite 외부 백업·재해 복구는 완료하지 않았습니다. 예산 DB를 0으로 초기화한 새 객체로 바꾸지 않습니다. 최신 증빙은 [중앙 배포 기록](CENTRAL_LIVE_DEPLOYMENT.md)을 따릅니다. 이전 ZIP·원본을 보존하며 외부 백업 전 프로젝트 삭제 조건은 여전히 미충족입니다.

## 2. 무엇을 보존하고 무엇을 재생성할 수 있나

9월 19일 공식 관측 연결 후 추가 소스 스냅샷의 증빙은 `.local/seoul-live-20260919/source-backup-verification.json`을 확인합니다. 브로커 설정·원천 어댑터·전국 부품 모듈·최신 문서를 포함하고, 실제 키와 지도·원본 데이터는 제외합니다. 이는 같은 노트북의 파일 복원 검사이며 새 복원본의 독립 의존성 설치·빌드나 외부 백업 검증은 아닙니다. 공개판 코드가 로컬 최신 소스와 완전히 같다는 뜻도 아닙니다.

| 종류 | 경로 | 없을 때의 영향 / 보존 기준 |
|---|---|---|
| 수정 가능한 소스 | `src/`, `shared/`, `worker/`, `pipeline/`, `scripts/`, `tests/`, `config/`, `migrations/`, `docs/`, 루트 설정·잠금 파일 | UI·서버·수집기·변환 알고리즘을 수정하는 기준입니다. 외부 소스 백업과 새 폴더에서의 설치·검사를 완료하기 전 보존합니다. |
| 원본과 수집 근거 | `.local/raw/`, `.local/catalog.sqlite`, `.local/references/`, `.local/research/` | 당시 원본·출처·수집 시점을 보존합니다. 최신 원천을 다시 받는 것만으로 과거 바이트를 복구할 수 있다고 가정하지 않습니다. 별도 백업이 필요합니다. |
| 정제·체크포인트 | `.local/silver/`, `.local/national/`, `.local/grids/`, `.local/retile/` | 원본과 같은 코드·설정으로 재생성 가능한 항목이 섞여 있습니다. 셀 처리 기록·입력 목록·품질 검토를 잃으면 상당한 재처리가 필요합니다. 폴더 전체를 캐시로 분류하지 않습니다. |
| 검수·선택한 입력 | `.local/audit/`, `.local/hierarchy/`, `.local/lod-error/`, `.local/review/`, `.local/search-v2/` | 정확도·형상 보존·성능 판단과 어떤 후보를 배포했는지에 관한 증빙입니다. 최종 입력과 관련 증빙을 소스/데이터와 함께 보존합니다. |
| 공개 지도 전체 | `public/data/` | 여러 과거·후보 릴리스가 함께 있습니다. 최신 배포에 들어간 파일만 복원해도 해당 버전 서비스는 재개할 수 있지만, 모든 과거 후보와 재생성 입력이 복원되지는 않습니다. |
| 실제 배포 가능한 묶음 | `.local/deploy/bundles/<bundle>/` | 웹·Worker·지도·설정·전체 해시 목록이 함께 있습니다. 정상 운영 버전과 롤백 버전 모두 독립 보관하고 실제 복원해야 합니다. |
| 운영 증빙 | `.local/deploy/*verification.json`, `*deployment.json`, 각 `receipt.json`, 필요한 로그 | 원격 UUID·데이터 릴리스·묶음 ID의 연결을 보존합니다. `static-stage.json`은 가장 최근 로컬 준비 결과를 가리키는 포인터라 과거 묶음의 증빙을 대신하지 못합니다. |
| 다시 설치하는 의존성 | `node_modules/`, `.venv/` | 소스·잠금 파일과 설치 환경이 있으면 다시 설치합니다. 다른 프로젝트의 공용 런타임이나 전역 패키지까지 정리하지 않습니다. |
| 다시 만드는 산출물 | `dist/`, `.pytest_cache/`, 프로젝트 안의 `__pycache__/`, `*.tsbuildinfo` | 최종 소스와 독립 배포 묶음이 보존되고 재빌드가 검증된 뒤 우선 정리할 수 있는 후보입니다. 현재 이 문서는 삭제를 실행하지 않습니다. |
| 인증·로컬 실행 상태 | 실제 `.dev.vars`/`.env*`, `.wrangler/` | 실제 키와 실행 상태를 분리합니다. 환경변수 예제만 소스에 포함하고 키는 안전한 별도 보관·재발급 경로로 관리합니다. `.wrangler`도 점검 없이 전체 삭제하지 않습니다. |

Cloudflare에 올라간 JavaScript와 지도 파일은 원본 TypeScript·Python·테스트·원천 데이터의 백업을 대신하지 않습니다. 복구 ZIP도 **선택한 배포본만** 담으며 원본·소스·계정 인증·비밀키는 담지 않습니다.

### 읽기 전용 용량 조사

2026-09-17 01:25:14–01:27:07 KST에 `scripts/inventory-project.py`로 조사했습니다. 163,649파일·96,876,614,588 B이며 오류와 건너뛴 링크는 0개였습니다. 아래는 큰 범주의 합계입니다. 실행 중 변할 수 있는 파일의 시점별 목록이며 원자적 백업 목록이 아닙니다.

| 경로 | 파일 수 | 논리 바이트 |
|---|---:|---:|
| `public/data` | 63,309 | 36,651,310,379 |
| `.local/deploy` | 51,115 | 32,115,976,394 |
| `.local/retile` | 5,191 | 16,528,814,952 |
| `.local/silver` | 1,941 | 6,689,320,783 |
| `.local/raw` | 12,436 | 2,021,848,133 |
| `.local/audit` | 1,296 | 1,075,886,114 |
| `.venv` | 12,909 | 615,080,335 |
| `node_modules` | 12,038 | 548,037,008 |
| `dist` | 401 | 11,734,347 |

이 값은 실제 확보할 디스크 공간과 같지 않습니다. 파일시스템 압축·희소 파일·하드링크·별도 백업 복사본을 반영한 물리 용량이 아닙니다. 복구 ZIP 생성 후에는 합계가 늘어납니다. 큰 용량 대부분은 데이터와 배포 복사본이며, 의존성 두 폴더는 약 1.16GB입니다.

재조사 명령은 파일을 쓰거나 지우지 않고 표준 출력에 합계만 출력합니다.

```powershell
.venv\Scripts\python.exe scripts/inventory-project.py --expected-root 'C:\Projects\korea-replay'
```

스크립트는 자기 위치의 프로젝트 루트를 사용하고 `package.json`의 프로젝트 이름을 확인합니다. 경로가 루트 밖으로 벗어나면 탐색하지 않으며 심볼릭 링크·정션·기타 reparse point를 따라가지 않습니다. 삭제 옵션은 없습니다.

## 3. 배포 묶음을 다른 위치에서 복원하기

### 절대경로 문제와 해결

기존 `receipt.json`의 `bundle`, `config`, `catalog_path`, `worker_files[].path`는 이 노트북의 절대경로입니다. `deploy-preflight.mjs`는 특히 `receipt.bundle`에서 실제 파일을 읽습니다. 따라서 예전 폴더를 다른 위치로 단순 복사한 뒤 검사를 실행하면 원래 위치를 읽거나 실패할 수 있습니다.

`pipeline.recovery_bundle`은 이 문제를 다음과 같이 처리합니다.

- export는 `client/`, `worker/`, `wrangler.json`, `asset-manifest.json`, `receipt.json`만 허용합니다. 코드·지도·설정과 전체 파일 목록을 다시 해시 검증합니다.
- ZIP 안의 receipt는 로컬 경로 힌트를 상대경로로 바꿉니다. 코드·지도·설정 해시는 바꾸지 않으며 원본 receipt 파일도 수정하지 않습니다.
- restore는 **존재하지 않는 새 디렉터리**만 받습니다. 모든 ZIP의 SHA·파일 목록·크기를 확인한 뒤 각 파일을 새로 쓰고 내부 SHA도 확인합니다.
- `receipt.original.json`은 압축본에 들어 있던 receipt의 원문입니다. `receipt.json`은 복원 위치를 반영해 새로 만듭니다. 원본의 절대경로를 참조하지 않고 JS 배포 검사에 통과해야 합니다.
- 누락·추가·대소문자 중복·경로 탈출·Windows 장치명/대체 스트림·ZIP 링크·파일/폴더 충돌을 거부합니다. 심볼릭 링크·정션이 포함된 입력과 출력도 거부합니다.
- 실패한 새 디렉터리는 조사할 수 있게 남깁니다. 자동 삭제나 기존 파일 덮어쓰기를 하지 않습니다. 성공한 경우에만 `recovery-verification.json`을 만듭니다.

기본 분할 목표는 비압축 입력 약 900MiB이며, 파일을 중간에서 자르지 않는 독립 ZIP 여러 개입니다. 정적 자산 최대 20,000개, Worker 최대 100파일, 증빙 3파일을 따로 검증합니다. 복원에 모든 ZIP과 `recovery-manifest.json`이 필요합니다. 여유 공간은 필요한 출력 크기 외에 30GiB를 남기도록 검사합니다. Python 3.13 환경에서 검증했습니다.

실행 예시입니다. **export 생성은 백업 준비이며, 같은 노트북 안의 ZIP만으로 독립 백업이 완료되지 않습니다.**

```powershell
.venv\Scripts\python.exe -m pipeline.recovery_bundle export --bundle .local/deploy/bundles/3d35ed6bc071d834 --output .local/recovery/deployable-3d35ed6bc071d834
```

이미 있는 출력 폴더를 재사용하지 않습니다. 완료 manifest가 없는 중단된 결과는 백업으로 채택하지 않습니다. 모든 분할 파일과 manifest를 승인된 별도 보관소에 보관하고, 신뢰할 수 있는 운영 기록에도 manifest SHA-256과 묶음 ID를 남겨야 합니다. 해시가 함께 변조된 목록은 해시 일치만으로 출처를 인증할 수 없습니다.

core 묶음의 전국 로컬 복원은 위 증빙에서 확인했습니다. 다음은 **외부 보관소에서 다시 받은 자료를 복원하는 예시**이며, 이 외부 다운로드·복원은 아직 실행하지 않았습니다. 예시 경로를 실제 새 경로로 바꿔 실행합니다. 아래 위치들은 현재 프로젝트 정리 범위 밖입니다.

```powershell
# 다른 보관소에서 받은 모든 ZIP과 manifest가 같은 폴더에 있어야 합니다.
$recoveryManifest = 'X:\KoreaReplayBackup\deployable-3d35ed6bc071d834\recovery-manifest.json'
$restoredBundle = 'C:\KoreaReplayRecoveryCheck\restored-3d35ed6bc071d834'
.venv\Scripts\python.exe -m pipeline.recovery_bundle restore --manifest $recoveryManifest --destination $restoredBundle
node scripts/deploy-preflight.mjs (Join-Path $restoredBundle 'receipt.json')
node scripts/serve-production.mjs --bundle=$restoredBundle --port=4189
```

`recovery-verification.json`과 JS 검사 성공 후, 해당 서버에서 전국 이동·상세 선택·검색·재생·공유를 검증합니다. 복구 검증은 원본 프로젝트가 남아 있어 우연히 읽히는 경로에 의존하면 안 됩니다. exporter의 상대경로 receipt와 실제 다른 폴더에서의 검사로 이 경로를 확인합니다.

도구 회귀 검사는 임시 fixture로 분할 왕복, 다른 위치의 JS `deploy-preflight`, 변조·누락·경로·정션·디스크 부족을 확인합니다. **fixture 통과와 10GB 이상 실제 묶음의 복원 성공은 별도입니다.**

### 이전 e4의 ZIP 재사용 기록

2026-09-17 추가 확인 기준으로, 기존 `3d35ed6bc071d834`의 전체 ZIP 12개는 로컬에 있으며 `restored-core-3d35ed6bc071d834/recovery-verification.json`에 17,423파일·10,723,076,576 B 복원 성공이 기록되어 있습니다. 같은 노트북의 기록이므로 외부 백업 완료를 뜻하지 않습니다. 이 절에 기록한 델타 대상은 `e4a7bd1c2eb6d18e` / `pub-a55c6a081e5782db`이며, 이후 배포의 기록은 별도로 추가합니다.

`export-delta`와 `restore-delta`를 추가했습니다. 기존 `export`와 `restore`는 그대로 사용할 수 있습니다. 델타는 **전체 base 한 개와 delta 한 개**의 조합이며, 다른 delta를 base로 연결하지 않습니다.

| 최종 복원 구성 | 파일 수 | 비압축 바이트 |
|---|---:|---:|
| 기존 경로·크기·SHA가 모두 같은 파일 재사용 | 17,251 | 10,713,572,055 |
| 새 경로의 추가 파일 | 146 | 11,418,902 |
| 기존 경로의 새 내용으로 교체 | 8 | 3,281,519 |
| 최종 전체 파일 목록 | 17,405 | 10,728,272,476 |

이 수량에는 정적 자산 17,401개와 Worker 1개, metadata 3개가 포함됩니다. metadata 중 receipt는 절대경로를 제거한 이식 가능한 표현입니다. 최종에서 빠진 옛 경로 164개·6,238,935 B는 **새 복원 폴더에 추출하지 않습니다.** 교체 전 내용 8개까지 포함하면 기존 항목 172개를 재사용하지 않습니다. 원본 ZIP과 기존 복원 폴더를 삭제하거나 고치지 않습니다.

생성한 산출물은 `.local/recovery/delta-3d35ed6bc071d834-to-e4a7bd1c2eb6d18e/`에 있습니다.

| 파일 | 바이트 | SHA-256 |
|---|---:|---|
| `korea-replay-e4a7bd1c2eb6d18e-delta-001.zip` | 3,336,910 | `b04672649ba05048b785f903385b585992532071c1908c8ef40902737a4354d2` |
| `recovery-delta-manifest.json` | 3,091,898 | `a901120d4257f4c30defbce56ae6b403f16ca0925e232d02eabdb819788c3256` |

base의 `recovery-manifest.json` SHA는 `92c05329e53aed1061a3915a016ce11888deab0aedebaf2897f52bc14e27f57d`입니다. 델타 manifest는 이 base manifest와 base ZIP 12개 각각의 이름·크기·SHA, 최종 전체 파일 허용목록, 델타 ZIP을 고정합니다. 델타 ZIP만 보관하면 복원할 수 없습니다. 독립 보관소에는 base ZIP 12개와 base manifest, delta ZIP과 delta manifest, 복구 도구를 포함한 소스 버전, 운영 증빙을 함께 보존해야 합니다. manifest의 기대 SHA는 같은 다운로드 안의 값만 믿지 않고 별도 운영 기록과 대조합니다.

생성 시에는 배포 metadata·파일 목록과 추가/교체 154파일·14,700,421 B를 검증했습니다. base ZIP은 크기와 중앙 파일 목록만 확인했으며, 재사용하는 10.7GB 내용과 기존 ZIP의 SHA를 다시 읽지는 않았습니다. 생성 후 새 델타 ZIP의 압축 SHA와 내부 154파일 전부의 해제 SHA·크기를 다시 확인했습니다. 범위는 `delta-export-verification.json`에 기록되어 있습니다. 이 기록은 **전국 최종 버전 복원이나 외부 백업 성공 기록이 아닙니다.**

생성 때 사용한 명령입니다. 이미 있는 출력 폴더는 거부되므로 재실행하려면 새 출력 이름을 지정해야 합니다.

```powershell
.venv\Scripts\python.exe -m pipeline.recovery_bundle export-delta --base-manifest .local/recovery/deployable-3d35ed6bc071d834/recovery-manifest.json --base-sha256 92c05329e53aed1061a3915a016ce11888deab0aedebaf2897f52bc14e27f57d --bundle .local/deploy/bundles/e4a7bd1c2eb6d18e --output .local/recovery/delta-3d35ed6bc071d834-to-e4a7bd1c2eb6d18e
```

다음은 **아직 실행하지 않은 실제 전국 복원 명령**입니다. 성능 측정 종료 후 실행하며, 외부 백업을 검증할 때는 외부에서 새로 받은 base/delta 파일의 경로를 사용합니다. 아래 예시는 프로젝트 안의 새 폴더를 대상으로 합니다.

```powershell
.venv\Scripts\python.exe -m pipeline.recovery_bundle restore-delta --manifest .local/recovery/delta-3d35ed6bc071d834-to-e4a7bd1c2eb6d18e/recovery-delta-manifest.json --manifest-sha256 a901120d4257f4c30defbce56ae6b403f16ca0925e232d02eabdb819788c3256 --base-manifest .local/recovery/deployable-3d35ed6bc071d834/recovery-manifest.json --destination .local/recovery/restored-final-e4a7bd1c2eb6d18e
node scripts/deploy-preflight.mjs .local/recovery/restored-final-e4a7bd1c2eb6d18e/receipt.json
```

복원은 외부에서 받은 기대 delta SHA와 base 의존관계를 먼저 검사합니다. 사용하지 않는 옛 파일만 담긴 ZIP을 포함해 **base/delta ZIP 전체의 SHA·크기·중앙 파일 목록**을 검증한 뒤, 존재하지 않는 새 디렉터리를 만듭니다. 최종 허용목록에 해당하는 파일만 추출하고 해제 SHA를 검사하며, receipt 경로를 새로 연결한 뒤 전체 배포 파일을 다시 검증합니다. 기존 폴더 덮어쓰기·이동·삭제는 없습니다.

누락·추가·중복·경로 탈출·링크·SHA 불일치 및 의존관계 불일치는 오류입니다. 추출 중 오류가 나면 조사용 새 폴더는 남지만 성공 표시인 `recovery-verification.json`을 발행하지 않습니다. 따라서 실패한 폴더를 완료된 복원으로 채택하면 안 됩니다. 성공 표시는 최종 전체 검사 후에만 작성합니다.

작은 fixture로 기존 full 검사 40개와 delta 검사 38개가 통과했습니다. delta 검사에는 원래 소스 폴더를 읽지 않는 복원, 최종 파일 목록 일치, 다른 위치에서 JS preflight, 사용하지 않는 base ZIP의 변조, 추출/압축 도중 실패와 기존 파일 보존이 포함됩니다. **현재 미완료 항목은 e4 묶음의 base+delta 전체 실물 복원, 외부 보관·재다운로드 복원, 수정 가능한 소스 및 원본 자료의 독립 백업·복원입니다.** core 묶음의 기존 전국 로컬 복원 완료 상태는 유지됩니다. 이 절은 로컬 정리를 승인하거나 공개 배포 검증을 대체하지 않습니다.

### 이전 e86의 성능·카메라·모바일 검색 변경 묶음

2026-09-17에 새 묶음 `e86d5fd5532edd57` / 데이터 `pub-a55c6a081e5782db`의 델타를 추가 생성했습니다. 배포 receipt와 자산 manifest가 가리키는 정적 파일 17,401개·10,725,144,635 B가 일치함을 확인했습니다. 델타 생성 당시에는 공개 전환이 대기 중이었으며, 이후 `363089fb`의 공개 전환과 HTTP 검사 결과를 상단 현재 상태에 반영했습니다. 아래에는 델타 생성 검사와 이후 실제 전국 복원을 구분해 기록합니다.

새 산출물 위치는 `.local/recovery/delta-3d35ed6bc071d834-to-e86d5fd5532edd57/`입니다. 기존 core 전체 ZIP과 e4 델타 기록은 보존했습니다. e86 복원에는 **core base와 e86 delta**를 사용하며, e4 delta를 먼저 적용할 필요는 없습니다.

| e86 최종 복원 구성 | 파일 수 | 비압축 바이트 |
|---|---:|---:|
| core에서 경로·크기·SHA가 같은 파일 재사용 | 17,251 | 10,713,572,055 |
| 새 경로의 추가 파일 | 146 | 11,423,429 |
| 기존 경로의 새 내용으로 교체 | 8 | 3,282,299 |
| 추가/교체 파일 합계 | 154 | 14,705,728 |
| 최종 전체 파일 목록 | 17,405 | 10,728,277,783 |

최종 전체 수량에는 정적 자산 17,401개, Worker 1개, metadata 3개가 포함됩니다. core의 옛 전용 경로 164개·6,238,935 B는 새 복원 트리에 추출하지 않습니다.

| 새 파일 | 바이트 | SHA-256 |
|---|---:|---|
| `korea-replay-e86d5fd5532edd57-delta-001.zip` | 3,338,556 | `0ad370a9d1de8bd6093bc93b191704b65a439648b5d4b2e0e279b9e557eefbde` |
| `recovery-delta-manifest.json` | 3,091,898 | `4615668e7c77a62758f7ab952c6f4b7642577184b8ac70c6020ad15552fdc11d` |

base manifest의 고정 SHA는 기존과 같은 `92c05329e53aed1061a3915a016ce11888deab0aedebaf2897f52bc14e27f57d`입니다. 생성 후 새 ZIP의 압축 SHA, 중앙 파일 목록, 내부 154파일 전부의 해제 SHA·크기를 검증했고 `delta-export-verification.json`에 `passed: true`로 기록했습니다. **델타 생성 단계에서는** 기존 base ZIP의 크기·중앙 파일 목록을 확인했으며 전체 압축 SHA나 재사용하는 10.7GB 내용을 다시 읽지 않았습니다. 이후의 전체 복원에서는 이 입력 ZIP과 최종 복원 파일 모두의 SHA를 검증했습니다.

**e86의 base+delta 전국 전체 로컬 복원과 JS 배포 사전 검사는 완료했습니다.** 외부 업로드·재다운로드 복원, 수정 가능한 소스·원본의 독립 백업은 아직 수행하지 않았습니다. 복구 도구와 핵심 소스는 변경하지 않았습니다. 아래는 이번에 실행한 복원·검사 명령입니다. 대상 폴더는 이제 존재하므로 같은 복원 명령을 반복하면 덮어쓰기를 거부합니다.

```powershell
.venv\Scripts\python.exe -m pipeline.recovery_bundle restore-delta --manifest .local/recovery/delta-3d35ed6bc071d834-to-e86d5fd5532edd57/recovery-delta-manifest.json --manifest-sha256 4615668e7c77a62758f7ab952c6f4b7642577184b8ac70c6020ad15552fdc11d --base-manifest .local/recovery/deployable-3d35ed6bc071d834/recovery-manifest.json --destination .local/recovery/restored-final-e86d5fd5532edd57
node scripts/deploy-preflight.mjs .local/recovery/restored-final-e86d5fd5532edd57/receipt.json
```

실제 복원 결과는 다음과 같습니다. 복원 대상이 없고 같은 대상의 복원 프로세스가 실행 중이지 않은 것을 먼저 확인했으며, 시작한 프로세스를 중복 실행하거나 재시작하지 않았습니다.

| 검증 단계 | 실제 결과 |
|---|---|
| 시작 전 공간 | 여유 65,516,871,680 B. 복원 예상 10,728,277,783 B 외에 30GiB(32,212,254,720 B) 여유 하한을 확보했습니다. |
| 복원 입력 | 고정 base/delta manifest SHA 일치. base ZIP 12개와 delta ZIP 1개 전체의 SHA·크기·중앙 파일 목록 검사 통과. |
| 전국 실물 복원 | 새 폴더 `.local/recovery/restored-final-e86d5fd5532edd57/`에 17,405파일·10,728,277,783 B 복원. core 재사용 17,251개와 추가/교체 154개이며, 옛 전용 경로는 추출하지 않았습니다. |
| Python 전체 검사 | 추출 파일 전체 SHA와 복원 폴더 전체 재검사 통과. `recovery-verification.json`의 `passed`, `base_archive_sha256_verified`, `delta_archive_sha256_verified`가 모두 `true`입니다. |
| JS 배포 사전 검사 | 복원된 `receipt.json`으로 실행해 exit 0. 정적 파일 17,401개와 Worker의 코드·지도 해시 일치를 확인했습니다. |
| JS 검사 완료 시각 | 2026-09-17 05:10:19 KST. `deploy-preflight-verification.json`에 실행 결과, 사용 receipt와 검사 도구의 SHA를 기록했습니다. |
| 검사 후 공간 | 여유 54,909,878,272 B로 30GiB 하한을 유지했습니다. |

`recovery-verification.json` SHA-256은 `8a4dd497729b95977aa4ed24a46b634030b418c7839f791f0867f5d7d8eee990`입니다. 복원된 receipt의 SHA-256은 `c7c9956803743448a25245014e1f77c8042fbab64d9c4b3d53c28d5c05b80437`이며, 로컬 경로를 복원 위치로 바꾼 표현이므로 원래 묶음 receipt와 바이트가 다른 것이 정상입니다. 코드·지도·설정의 내용 해시는 유지합니다.

이 결과는 같은 노트북의 새 폴더에서 완전한 배포 묶음을 재구성한 증거입니다. 외부 사본 다운로드·복원, 수정 가능한 소스와 당시 원천 자료의 독립 백업을 대신하지 않습니다. 기존 묶음·복구 ZIP·원본과 공개 배포는 변경하지 않았습니다.

## 4. 소스 수정과 새 배포를 다시 시작하는 절차

### 소스와 실행 환경

먼저 별도 보관소에서 **수정 가능한 소스 전체**를 새 작업 폴더로 복원합니다. 현재 원격 Git 주소는 없으므로 임의 주소로 clone이 가능하다고 안내하지 않습니다. 로컬 소스 ZIP을 외부에 보관할 때도 소스·문서·테스트·잠금 파일의 정확한 버전과 SHA를 함께 남기고, 실제 키와 원천 데이터는 분리합니다.

현재 검증 환경은 Node 24 계열과 Python 3.13입니다. 새 작업 폴더에서 다음을 실행합니다.

```powershell
npm ci
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
npm run typecheck
npm run lint
npm test
.venv\Scripts\python.exe -m pytest -q
npm run build
```

`python`이 Python 3.13을 가리키는지 먼저 확인합니다. 다른 운영체제·도구 버전의 재빌드는 기존 번들과 바이트가 같다고 보장하지 않으므로, 과거 버전 그대로 복구할 때는 검증된 배포 묶음을 사용합니다.

### 이미 게시한 데이터를 활용해 UI·Worker 수정하기

원천 자료를 다시 수집하지 않고도 복원한 배포 묶음의 `client/data/`를 새 개발 작업공간의 `public/data/`로 **복사**해 사용할 수 있습니다. 기존 개발 자료가 있는 위치에는 덮어쓰지 않습니다. 원본 복구 묶음은 그대로 보관하고, 복사한 데이터의 해시·전체 의존 관계를 다시 검사합니다.

V2의 작은 공개 목록만으로 파이프라인 입력을 대체하면 안 됩니다. 묶음에 포함된 `client/data/releases/<release>.v1.json`이 전체 자산을 가진 v1 입력입니다. 현재 코드의 `pipeline.static_release`는 이 형태를 받습니다. 정확한 릴리스와 모든 참조 파일이 복원되어야 합니다.

```powershell
# 복원한 v1 목록을 명시합니다. 실제 복원한 release ID를 사용합니다.
npm run deploy:stage -- --catalog public/data/releases/pub-b3ce2e7229184cfc.v1.json
npm run deploy:check
```

높이·LOD 검수 입력은 `.local/lod-error/d17d093917bde3e1/catalog.json`이고, 현재 배포 입력은 여기에 전국 버스 노선 목록을 결합한 `.local/performance-20260917/catalog.json`입니다. `--catalog`를 생략하면 기본값인 `.local/retile/catalog.json`을 읽어 이전 후보로 돌아갈 수 있으므로 생략하지 않습니다. 다른 위치에 복원했다면 원래 노트북의 경로를 사용하지 말고 복원한 해당 버전의 v1 목록을 지정합니다. 자세한 생성 단계는 [HIERARCHY.md](HIERARCHY.md)와 [RETILING.md](RETILING.md)를 따릅니다.

### 재배포와 원격 복구

현재 `npm run deploy`는 로컬 묶음을 검사한 후 **버전 업로드**를 수행합니다. 공개 전환까지 자동으로 끝냈다는 의미가 아닙니다. `--initialize`는 기존 Worker의 재배포·복구에 사용하지 않습니다.

```powershell
npx wrangler whoami
npm run deploy
```

복원한 기존 묶음을 그대로 재업로드한다면, 복원된 receipt로 `deploy-preflight`를 먼저 실행하고 그 묶음의 `wrangler.json`을 Wrangler에 명시합니다. 루트 `dist/client`만 배포하면 지도 데이터가 빠집니다. API 키는 코드나 ZIP에 넣지 않고 승인된 비밀 설정으로 다시 연결합니다.

원격 미리보기 검사는 해당 묶음의 receipt를 지정합니다.

```powershell
node scripts/verify-remote-release.mjs --url=<실제-미리보기-주소> --version=<실제-UUID> --receipt=<복원된-receipt.json-경로>
```

꺾쇠 부분은 실제 값으로 바꿔야 합니다. 이 검사는 HTTP/API/표본 SHA를 검증하며 전체 원격 자산의 다운로드 복원을 대신하지 않습니다. 공개 전환·롤백 명령과 재검사는 [STATIC_DEPLOYMENT.md](STATIC_DEPLOYMENT.md)를 따릅니다. 과거 R2 구조의 [OPERATIONS.md](OPERATIONS.md)에 남은 리소스 생성 명령은 현재 무료 정적 배포의 복구 절차가 아닙니다.

새로 업로드한 버전은 새 배포 UUID를 받습니다. 과거 UUID를 고정한 공유 링크까지 같은 주소로 되살리는 것은 로컬 ZIP 복원의 보장 범위가 아닙니다. 기존 링크는 원격의 해당 배포 버전과 데이터가 함께 남아 있는지 확인해야 하며, 없는 버전을 최신으로 바꾸지 않는 공유 정책을 유지합니다.

### 원본부터 지도를 다시 만들기

배포 묶음에는 가공된 3D·지형·지도와 그 표시용 출처 속성이 있습니다. 원래 수집한 모든 파일, 제외한 객체, 정제 DB, 감사 로그가 전부 들어 있지는 않습니다. 원본부터 재생성하려면 `.local/raw/`와 원본 SHA·출처, SQLite 목록, 선택한 입력 후보와 관련 검수 증빙을 별도로 복원해야 합니다.

당시 원본으로 작은 대표 지역을 재처리해 좌표·원본 ID·높이 계약·출처·타일 검사를 통과한 뒤 전국 처리를 진행합니다. 신규 원천 수집은 데이터 제공 시점이나 라이선스가 바뀔 수 있으므로 과거 버전의 복구와 구분합니다. 전국·지형 생성 명령은 [README.md](../README.md), 원본 정밀도는 [HEIGHT_QUALITY.md](HEIGHT_QUALITY.md)를 기준으로 확인합니다.

## 5. 삭제 전에 반드시 확인할 것

아래 조건이 하나라도 미완료이면 해당 범주의 로컬 파일은 남깁니다. 조건을 충족했다고 다른 프로젝트나 개인 파일까지 정리 범위가 넓어지는 것은 아닙니다.

| 검증 | 통과 증거 |
|---|---|
| 최종 기능·데이터 범위 | 사용자가 요구한 현재 범위의 완료/제한 보고, 브라우저 검수, 오류 및 성능 실측. 미달한 목표를 통과로 바꾸지 않음. |
| 운영 배포와 복구 | 실제 공개 URL에서 최종 버전 확인, 정상 버전으로 원격 전환 후 다시 최종 버전으로 복귀한 검증 기록. |
| 수정 가능한 소스 | 프로젝트 밖 독립 보관소의 소스 버전·해시, 다른 새 폴더에서 설치·타입·린트·테스트·빌드 통과. |
| 실제 게시 데이터 | 모든 ZIP·manifest를 독립 보관소에서 읽어 전국 묶음 복원, 전체 SHA·파일 수·코드/데이터 일치, 다른 폴더에서 JS preflight 통과. |
| 원본과 감사 증빙 | 원본·DB·입력 후보·필요한 검사 기록의 별도 백업과 검증. 원본 재생성을 포기했다면 그 영향을 별도로 명시하기 전 삭제하지 않음. |
| 비밀정보와 운영 연결 | 승인된 키의 재연결 경로, 사용량·무료 운영 조건, 실시간 수집/관측의 실제 연결 범위. 키를 공개 파일에 복사하지 않음. |
| 정리 경계 | 아래의 정확한 루트 확인, 실제 대상 목록·생성 근거·보존 목록, 실행 중인 해당 프로젝트 작업 확인. |

순서는 **소스 백업 → 배포/원본 백업 → 다른 위치의 실제 복원 → 재개발 검사 → 공개/롤백 확인 → 정리 대상 확정**입니다. 같은 디스크에 ZIP 하나를 추가한 것, 미리보기 화면 한 번 열린 것, 파일 수만 맞춘 것은 삭제 전 복구 증거로 충분하지 않습니다.

## 6. 정리 범위의 정확한 경계

허용 범위의 기준 루트는 다음 하나입니다.

```text
C:\Projects\korea-replay
```

- `게임` 부모 폴더 전체, `korea-replay-backup` 같은 이름이 비슷한 형제 폴더, 별도 복구 폴더, 다른 프로젝트, Downloads/Documents의 개인 자료는 범위 밖입니다.
- 사용자 전역 `.codex`, 브라우저 프로필·로그인·캐시, 전역 Wrangler 인증, Python/Node 설치, 전역 패키지 캐시는 범위 밖입니다. 서버의 Worker·버전·데이터도 로컬 정리 요청에 포함하지 않습니다.
- 파일 경로 문자열이 루트로 “시작한다”는 검사만 사용하지 않습니다. 절대경로를 정규화하고 디렉터리 구성요소 경계로 하위 여부를 검사합니다. 파일과 모든 상위 경로의 symlink/junction/reparse point도 확인해야 합니다.
- 경로 안에 있다는 것만으로 프로젝트 생성물이라고 단정하지 않습니다. 사용자 반입 원본·개인 설정·미분류 파일은 보존하고, 생성 기록이나 소스 관리 목록으로 분류합니다.
- 먼저 재생성 가능한 빌드·의존성·시험 캐시를 분리하고, 대용량 중복 배포본·과거 타일은 참조 관계와 독립 백업을 확인한 뒤 별도로 판정합니다. `.local` 또는 `public/data` 전체를 일괄 캐시로 취급하지 않습니다.
- 장시간 진행되는 수집·타일 생성·검사·개발 서버가 해당 경로를 사용하는 동안 정리를 실행하지 않습니다. 최종 목록은 실행 직전에 다시 대조합니다.

이 문서와 inventory 도구에는 삭제 기능·명령이 없습니다. 현재 구현과 배포 검수가 계속되는 동안에는 보존 상태를 유지합니다.
