# KOREA REPLAY 무료 정적 배포 운영

현재 공개판은 `095795c4-1dfa-4d53-97d4-fbc74d797608` / 묶음 `e79c3faf906a102e`이며 트래픽 100%를 제공합니다. 전국 자료 릴리스는 `pub-a55c6a081e5782db`로 유지했습니다. 버스·서울 도시철도 조회는 별도 비공개 중앙 서버와 SQLite 캐시를 경유합니다. 미리보기·공개·이전판 롤백·복귀의 HTTP 검사를 각각 67건 통과했고, 최종 공개 주소에서 중앙 호출 제어와 교통·위성·레이더 응답을 확인했습니다. [중앙 교통 서버 배포 증빙](CENTRAL_LIVE_DEPLOYMENT.md)에 검증 범위와 한계를 기록합니다. 전국 GPU 프레임 목표, 연속 운행 이력, 모든 실제 건물의 정확성은 아직 미완료입니다.

현재 지도 배포는 `LIVE_TRANSIT_MODE=broker`와 정확한 Service binding을 요구하는 schema 2 receipt를 사용합니다. 비공개 브로커는 9월 20일 공식 16노선 설정과 정상 0건 응답 처리를 반영한 `270d7204-0f47-4aec-97ef-68841415ca32`이며 지도와 독립적으로 관리합니다. 기상 API용 비밀 설정은 `.local/auth/secrets-live-20260919.json`을 통해 보존하며 실제 키를 산출물에 넣지 않습니다. 기존 브로커·지도 롤백과 구버전 직접 호출의 예외는 [중앙 배포 기록](CENTRAL_LIVE_DEPLOYMENT.md), 새 지도 후보의 검증 상태는 [목록 분할·노선 확장](STREAMING_AND_SUBWAY_EXPANSION.md)을 따릅니다.

최종 receipt 기준 **17,401파일·10,725,168,600 B**입니다. 이전 전국 화면 개선판의 역사적 전체 롤백 검사는 `.local/map-first-20260919/`, 이번 중앙 연결판은 `.local/central-live-20260919/`에 별도로 보존합니다.

## 실행 위치와 비용 경계

| 작업 | 실행 위치 |
|---|---|
| 원본 보관·좌표/높이 검수·타일/검색 색인 생성 | 로컬 Python/Node 배치 |
| 공개 웹·지도 바이너리 제공 | Workers Static Assets |
| 버전 정보·조회 API | Worker |
| 카메라·3D·그림자·화면 품질 조절 | 접속 브라우저/GPU |

게시한 정적 버전은 노트북을 꺼도 제공됩니다. 승인된 현재 관측 API는 Cloudflare에서 요청 시 조회하고 제한된 시간 동안 캐시합니다. 이후 원본 갱신과 새 타일 생성은 배치를 다시 실행하고 새 버전을 업로드해야 합니다. 연속 운행 이력을 상시 수집·저장하는 서버를 배포한 것은 아닙니다.

계정의 Workers Free를 확인하고 Wrangler OAuth를 연결했습니다. 인증 범위는 `account:read user:read workers:write workers_scripts:write`입니다. 비밀 토큰은 문서·Git·배포 파일에 넣지 않습니다. R2 활성화 화면의 초과 사용료 청구 계약에는 동의하지 않았으며 결제수단 등록·요금제 변경을 하지 않습니다.

정적 요청과 저장에는 별도 요금이 없지만 Free의 파일 한도가 적용됩니다. 이 프로젝트는 **한 버전 20,000파일 미만 또는 이하**, 개별 파일 **24MiB 미만**을 검사합니다(공식 파일 상한은 25MiB). `_headers` 등 실제 업로드에서 제외되는 파일도 로컬 개수에 포함해 보수적으로 계산합니다. `/api/*`는 Free Worker 요청·CPU 한도를 사용하므로 사용량 제한과 연결 실패를 실제 응답으로 판정합니다. [공식 정적 요금·제한](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/), [Workers 한도](https://developers.cloudflare.com/workers/platform/limits/).

## 코드와 데이터를 함께 준비

전국 재타일링의 원래 입력은 `.local/retile/catalog.json`이고, 실제 생략 거리로 LOD 전환을 보정하고 독도 해안선과 실측 정점 예산을 결합한 감사 입력은 `.local/lod-error/d17d093917bde3e1/catalog.json`입니다. 여기에 검수한 현재 버스 노선 목록을 결합한 최신 배포 입력은 `.local/performance-20260917/catalog.json`입니다. 아래처럼 입력 경로를 명시해야 합니다. 원본과 이전 공개 파일은 보존하며 새 해시 경로를 생성합니다. 새로운 색인이나 심도 자료는 검수한 descriptor를 flat v1 입력에 포함합니다. 계층을 다시 생성하는 명령과 감사 기준은 [HIERARCHY.md](HIERARCHY.md)에 있습니다.

```powershell
npm run typecheck
npm test
npm run lint
.venv\Scripts\python.exe -m pytest -q
npm run deploy:stage -- --catalog .local/performance-20260917/catalog.json
npm run deploy:check
```

`deploy:stage`는 프로덕션 빌드 후 `pipeline.static_release`를 실행합니다. 필요한 데이터만 복사하므로 `public/data`의 이전 릴리스·원본·임시 파일 전체를 배포하지 않습니다. 외부 tileset JSON, 하위 GLB, 지형 가용 타일, 기상 프레임, 검색 bucket/page를 재귀적으로 대조합니다. 검색 shard는 manifest의 SHA와 바이트 길이도 일치해야 합니다.

| 파일 | 의미 |
|---|---|
| `.local/deploy/static-release-plan.json` | 선택한 카탈로그·전체 의존 파일·해시·파일 수 감사 |
| `.local/deploy/bundles/<bundle>/client` | 공개 웹·지도 파일만 포함한 실제 업로드 디렉터리 |
| 같은 묶음의 `worker` | 해당 지도 버전과 함께 배포할 Worker 빌드 |
| 같은 묶음의 `wrangler.json` | R2/D1/크론 없는 독립 배포 설정 |
| 같은 묶음의 `asset-manifest.json` | 업로드 직전 재검사할 파일·크기·SHA 목록 |
| 같은 묶음의 `receipt.json` | 후속 준비 작업 뒤에도 보존하는 해당 버전의 복구 검증 증빙 |
| `.local/deploy/static-stage.json` | 복사·해시 확인까지 완료한 묶음 증빙. 원격 업로드 증빙은 아님 |

`deploy:check`는 증빙의 설정/manifest 해시, 실제 파일 수, 각 파일 크기와 SHA, Worker 코드를 재확인합니다. 누락·추가·변경 파일이나 20,000개 초과는 실패합니다. 묶음 ID에 웹·지도뿐 아니라 Worker 코드와 캐시 설정도 들어가므로 서버 코드만 바꿔도 다른 묶음이 됩니다.

Worker만 고치거나 지도 파일 대부분이 같으면 이전 **완성된 불변 묶음**을 선택해 재복사를 줄일 수 있습니다.

```powershell
npm run deploy:stage -- --catalog .local/performance-20260917/catalog.json --reuse-bundle .local/deploy/bundles/<이전-검증된-bundle>
```

이전 receipt·manifest·설정·Worker와 전체 정적 파일을 먼저 검증하고, target·SHA·바이트가 모두 같은 `client/` 파일만 최대 4개 작업으로 새 묶음에 하드링크합니다. 다른 볼륨 등으로 링크할 수 없으면 새 파일로 복사합니다. `public/data/` 원본은 링크하지 않으며 Worker·설정·증빙은 새 파일로 작성합니다. 원본과 새 target의 해시, 전체 파일 수 검사는 유지합니다. receipt의 `staging`에 링크·복사·정상 재개 수량과 이전 receipt 해시를 기록합니다.

**하드링크는 같은 파일 내용을 공유하므로 어느 묶음도 직접 편집하지 않습니다.** 새 자료는 새 묶음으로 준비합니다. 기존 target이 불완전하거나 변경됐으면 덮어쓰지 않고 중단하며, 실패한 새 디렉터리는 점검용으로 남깁니다. 링크 묶음은 독립 백업을 대신하지 않습니다. 다른 환경의 복원용 ZIP과 이전 정상 배포 증빙도 보존합니다. 디스크 검사는 복사 fallback을 고려해 전체 파일 바이트와 30GiB 여유를 계속 요구합니다.

실제 서버 코드 수정 배포 준비에서는 17,395개·약 10.72GB를 재사용하고 변경된 client 3개·4,539,195 B만 복사했습니다. 전체 해시 검사를 유지하므로 준비 시간은 해당 실행에서 623.39초였으며, 디스크 절감과 검증 시간 절감을 같은 성과로 해석하지 않습니다. `.local/performance-20260917/compatible-stage-result.json`에 기록했습니다.

Vite는 서버 빌드 폴더에 개발용 `.dev.vars`도 만들 수 있습니다. 단계 도구는 숨김 파일·로컬 설정을 제외한 컴파일 결과만 Worker 묶음에 넣습니다. 공식 키는 정적 client나 복구 묶음에 포함하지 않고, 묶음 밖의 비공개 파일을 Wrangler `versions upload --secrets-file <비공개파일>`로 전달합니다. 키 값은 명령행·로그·Git에 쓰지 않습니다.

프로덕션 빌드를 로컬에서 확인할 때:

```powershell
node scripts/serve-production.mjs --port=4173
# 실제 준비한 배포 디렉터리는 다음 방식으로 검증합니다.
node scripts/serve-production.mjs --bundle=.local/deploy/bundles/<bundle> --port=4173
```

이 Node 서버는 프로덕션 웹/Worker 빌드와 실제 지도 파일을 사용합니다. Cloudflare 원격 런타임 자체의 검증은 아닙니다.

## 버전 업로드·미리보기·공개 전환

```powershell
npx wrangler whoami
npm run deploy
```

`deploy`는 완성된 묶음을 재검사하고 **`wrangler versions upload`**를 호출합니다. 프로젝트 루트의 `dist/client`만 직접 배포하면 지도 데이터가 빠지므로 사용하지 않습니다. 출력의 실제 버전 UUID와 고정 미리보기 URL을 보관합니다. [버전 미리보기 공식 동작](https://developers.cloudflare.com/workers/versions-and-deployments/preview-urls/).

새 Worker는 Wrangler가 최초 `deploy`를 요구합니다. `korea-replay`가 아직 없음을 확인한 최초 실행에 한해 `npm run deploy -- --initialize`를 사용합니다. 검증된 설정에서 `workers_dev:false`, `preview_urls:true`만 파생한 설정으로 전체 묶음을 올립니다. 이 단계는 기본 공개 주소를 비활성화하고 버전 미리보기만 제공합니다. 기존 운영 Worker에는 초기화 옵션을 사용하지 않습니다.

미리보기에서 다음을 확인한 뒤 공개 트래픽을 전환합니다.

- `/api/v1/health`, `/api/v1/runtime`의 데이터 릴리스·배포 버전.
- `/data/catalog.json`은 CatalogV2, `/api/v1/catalog`는 v1 호환 응답.
- 3D·지형·검색·기상 의존 파일의 실제 응답과 오류 로그.
- 해시 경로의 장기 캐시, 최신 목록 재검증, 없는 지도 파일의 404.
- 선택·검색·시간 재생·지역 이동과 데이터/배포 버전을 고정한 공유 복원.

HTTP 계약과 배포 파일의 표본 SHA는 `node scripts/verify-remote-release.mjs --url=<미리보기주소> --version=<UUID>`로 재현합니다. 이 검사는 네트워크 경과 시간을 기록하며 이를 Worker CPU 시간으로 해석하지 않습니다. 지역별 브라우저 조작·GPU 성능은 별도로 검증합니다.

```powershell
npx wrangler versions deploy <검증한-UUID>@100% --config .local/deploy/bundles/<bundle>/wrangler.json --yes
# 최초 배포의 비활성 기본 주소를 검증 후 연결합니다.
npx wrangler triggers deploy --config .local/deploy/bundles/<bundle>/wrangler.json
```

고정 공유 URL은 `<버전앞8자리>-korea-replay.<계정>.workers.dev`와 데이터 릴리스를 함께 사용합니다. 없는 릴리스나 다른 배포는 최신 데이터로 바꾸지 않고 오류를 표시합니다. 미리보기 URL은 공개 URL이며 영구 보존 서비스로 보장하지 않습니다.

## 복구

공개 전환 전 정상 버전 UUID와 카탈로그 해시를 기록합니다. 문제가 생기면 그 버전을 다시 100%로 전환하고 같은 health/catalog/파일/공유 검사를 반복합니다.

```powershell
npx wrangler versions deploy <이전-정상-UUID>@100% --config .local/deploy/bundles/<bundle>/wrangler.json --yes
node scripts/verify-remote-release.mjs --url=<공개주소> --version=<이전-정상-UUID> --receipt=.local/deploy/bundles/<이전-bundle>/receipt.json
```

Static Assets는 코드와 함께 버전에 포함되므로 코드만 이전으로 돌아가고 지도 파일이 섞이는 복구를 피합니다. 기존 자료·다른 Worker·이전 묶음을 임의로 삭제하지 않습니다. 실제 복구 성공은 명령 성공뿐 아니라 원격 버전과 데이터 응답까지 일치한 경우에 기록합니다. [공식 버전·배포 구조](https://developers.cloudflare.com/workers/versions-and-deployments/).

## 관측 소스와 별도 조건

`COLLECTORS_ENABLED=false`가 기본값이며 cron도 없습니다. 별도 요청형 현재 관측 경로는 승인된 TAGO 버스, 서울 1호선 역 상태, 기상청 레이더·위성 API를 사용합니다. 전국 공식 도시 선택 → 해당 도시의 노선 검색 → 위치 조회 순서이며, 미선택·위치 미지원 상태에서 다른 지역을 자동으로 선택하지 않습니다. 최신 ab42 공개 응답은 22:30 KST에 1호선 61건(partial)·부산 19대·레이더/위성 각각 13패널이었습니다. [공식 연결 증빙](LIVE_CONNECTION_20260919.md)에 전송 방식과 좌표·시각의 한계를 기록했습니다. 이전 3b 공개판의 21:34 표본 부산 21대·인천 21대·제주 1대·레이더 13패널은 `.local/map-first-20260919/public-live.json`에 보존합니다. 수량은 각 응답 시점의 값이며 전국 연속 관측을 뜻하지 않습니다.

TAGO·KORAIL·KMA의 별도 상시 수집에는 승인된 키, 허용된 보관·재배포 조건, 실제 원본 응답 검증이 필요합니다. 현재 미연결 운행 기록을 실제 관측으로 표시하지 않습니다.

기존 TAGO 수집 코드도 공개 목록을 직접 바꾸지 않습니다. 명시적으로 활성화된 별도 수집 환경에서 원본과 정규화 기록을 보관한 뒤 `private/staged`의 검수 대기 후보만 만듭니다. 감사 및 전체 배포 과정을 통과해야 공개할 수 있습니다. 종전 R2 운영 문서는 과거 구조 참고용이며 현재 무료 배포의 실행 절차가 아닙니다.

## 중앙 브로커를 명시하는 새 배포 계약

2026-09-19부터 새로 준비하는 지도 묶음은 `vars.LIVE_TRANSIT_MODE="broker"`와 다음 Service binding을 함께 선언합니다. 루트 `wrangler.jsonc`뿐 아니라 `pipeline/static_release.py`가 생성하는 실제 업로드 설정에도 적용합니다. **이 절의 설정·검사는 로컬 구현 결과이며, 브로커 최초 생성과 지도 연결의 원격 성공은 별도 증빙으로 판정합니다.** 앞서 공개한 `ab42e105`의 직접 조회 결과를 중앙 예산 운영 완료로 바꾸지 않습니다.

```json
{
  "services": [
    {"binding": "LIVE_TRANSIT_BROKER", "service": "korea-replay-live-broker"}
  ],
  "vars": {"LIVE_TRANSIT_MODE": "broker"}
}
```

위 코드는 추가되는 항목만 보여줍니다. 실제 설정에는 기존 정적 저장 모드·수집 비활성·자료 릴리스 등의 필수 값도 있습니다. 새 receipt의 `schema_version`은 2이며 다음 `deployment_contract`를 필수로 보존합니다. **설정 전체와 계약이 묶음 식별자에 포함되며 설정 파일 바이트는 `config_hash`로 별도 검증**합니다. 설정만 달라도 새 불변 묶음으로 준비합니다. 자료 catalog와 복구 manifest의 스키마는 이 변경과 별개입니다.

```json
{
  "schema_version": 2,
  "deployment_contract": {
    "schema_version": 1,
    "live_transit_mode": "broker",
    "broker": {"binding": "LIVE_TRANSIT_BROKER", "service": "korea-replay-live-broker"}
  }
}
```

Python 복구·재사용 검사와 JavaScript 배포 사전 검사는 같은 정책을 적용합니다. 브로커 바인딩 누락, 다른 서비스 이름, 추가 서비스·서비스 환경/entrypoint, `env` 재정의, `LIVE_TRANSIT_MODE=direct` 또는 모드 삭제, 계약 삭제·변경을 거부합니다. 지도 설정에는 DO class·migration을 넣지 않습니다. R2·D1·cron을 요구하지 않는 기존 무료 정적 경계도 유지합니다. 배포 도구의 CLI로 `--config`, `--env`, `--var`를 추가해 감사한 설정을 우회할 수 없습니다.

과거 schema 1 receipt에는 이 계약이 없으므로 원래 브로커 없는 설정으로 계속 검사·복원할 수 있습니다. 과거 묶음에 새 서비스만 끼워 넣어 기존 검증을 재사용하는 경로는 거부합니다. 새 schema 2 receipt에서 계약·모드·서비스를 함께 지워도 과거 배포로 처리하지 않습니다. full ZIP과 base+delta 복원 모두 새 계약을 보존하며, 복원 후 JavaScript 사전 검사까지 작은 fixture에서 확인했습니다. 이 검사는 브로커 코드·DO 저장 상태·비밀값을 지도 ZIP이 함께 복구한다는 뜻이 아닙니다.

### 브로커 최초 생성과 지도 버전 업로드의 분리

현재 설치된 Wrangler 4.132.0의 `deploy --help`, `versions upload --help`와 미적용 DO migration 거부 코드를 확인했습니다. 새 SQLite DO의 최초 생성은 별도 브로커 설정의 `deploy` 경로로 처리하고, 지도 Worker에는 기존 `versions upload` 경로를 사용합니다. **현재 계정에서는 최초 생성이 이미 끝났습니다.** 아래는 재현 예시이며 기존 namespace를 새로 만들거나 현재 호출 예산을 초기화하는 명령이 아닙니다. 최초 생성이 필요한 별도 환경에서는 계정·존재 여부·무료 사용 범위를 먼저 확인합니다. [공식 배포 관리](https://developers.cloudflare.com/workers/versions-and-deployments/deployment-management/)

```powershell
# 기존 지도 Worker의 --initialize와 별개의 새 비공개 브로커입니다.
npx wrangler deployments list --config wrangler.live-broker.jsonc --json
# 브로커가 아직 없고 최초 SQLite migration이 필요한 경우에만 수행합니다.
npx wrangler deploy --config wrangler.live-broker.jsonc --secrets-file .local/auth/secrets-broker-20260919.json
```

브로커 설정의 `workers_dev:false`, `preview_urls:false`, 빈 routes를 유지합니다. 조회 실패나 인증 오류를 “브로커 없음”으로 간주해 생성하지 않습니다. 실제 키는 위 비공개 입력에서 전달하며 값은 코드·명령행·로그·정적 자산에 적지 않습니다. class·namespace를 만든 뒤 코드를 갱신할 때는 같은 설정으로 `wrangler versions upload`와 검증한 브로커 버전의 공개 전환을 별도로 수행합니다. 적용되지 않은 migration이 남아 있으면 해당 업로드는 중단됩니다.

브로커를 먼저 검증한 뒤 지도 묶음을 준비하고 기존 지도 Worker의 미리보기 버전만 업로드합니다.

```powershell
npm run deploy:stage -- --catalog .local/performance-20260917/catalog.json --reuse-bundle .local/deploy/bundles/<이전-검증된-bundle>
npm run deploy:check
npm run deploy
```

새 broker 계약이 있는 receipt에 `npm run deploy -- --initialize`를 지정하면 **어떤 Cloudflare 호출보다 먼저 거부**합니다. 지도 배포 도구는 브로커를 자동 생성하거나 공개 트래픽을 자동 전환하지 않습니다. 지도의 실제 새 버전·브로커의 배포 버전과 계약·비공개 바인딩 성공·원천 예산·실패 시 우회가 없는지를 함께 기록한 뒤 지도 공개 전환을 판정합니다.

Service binding은 현재 브로커 서비스 배포를 참조하므로 지도 UUID만 되돌려도 브로커의 상태가 과거로 복원되지는 않습니다. 지도와 브로커의 롤백은 따로 검증하며, 이미 소비한 호출 예산을 초기화하거나 저장소를 재생성해서 복구하지 않습니다. 브로커 없는 과거 지도 버전으로의 롤백은 정적 지도 복구와 중앙 호출량 보장을 구분합니다. [중앙 예산과 상태 복구의 경계](LIVE_BROKER_PLAN.md)
