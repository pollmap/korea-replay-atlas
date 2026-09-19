# Pages 배포와 고정 공유 운영

검토 기준: **2026-09-20 KST**. **[korea-replay.pages.dev](https://korea-replay.pages.dev/)**의 production 공개, runtime·정적 응답 검증과 고정 미리보기의 공유 복원을 확인했습니다. 로컬 회귀, 공개 기능, 성능 인수와 실제 롤백은 별도로 판정합니다. 이동 프레임 P95 목표와 이전 Pages production으로의 실제 롤백은 아직 완료하지 않았습니다.

## 현재 공개 배포

| 항목 | 실제 확인값 |
|---|---|
| 대표 주소 | `https://korea-replay.pages.dev` |
| production 배포 ID | `07e46d75-eb23-4f30-a5e9-0d950079887e` |
| production 응답이 고정한 공유 대상 | `https://cf1933d9.korea-replay.pages.dev` |
| 앱 artifact SHA-256 | `e484a6e49b12671bcc54c71f3987d4b26d22793ee9e4df72c548ef2e8f51c1d6` |
| 기존 3D catalog 릴리스 | `pub-b71d244ced0bff39` |
| 데이터 origin | `https://9009c3fb.korea-replay-data.pages.dev` |
| 공동 manifest 경로 | `/data/atlas/atlas-fc1cca5990aca870/manifest.json` |
| 공동 manifest SHA-256 | `31883558a180db6a4db2b080793c930ed0bcc7e1f208e7d8945a9b35e29b55d4` |
| 지도 / 실거래 릴리스 | `map2d-55cb24b80a1df376c579` / `property-d1a65c142e75d52a` |

production과 공유 목적지 preview는 **서로 다른 실제 배포이며 artifact가 같습니다**. 응답에서 확인한 preview를 사용하고 production ID에서 공유 주소를 만들어내지 않습니다. 배포 성공 응답은 `remote-deployment-07e46d75-eb23-4f30-a5e9-0d950079887e.json`, HTTP 검증은 `verified-production.json`과 `.local/national-visual-20260920/final-production-remote.json`에 별도로 보존했습니다. 개인정보·인증값·로컬 원본은 공개 Git에 포함하지 않습니다.

## 배치

| 대상 | 역할 | 동적 처리 |
|---|---|---|
| `korea-replay.pages.dev` | UI, 기존 3D 자산, 고정 자료 조회 | `/api/*`만 Pages Function |
| `korea-replay-data.pages.dev` | 새 2D 타일·선택 정보·부동산 게시물 | 없음, 정적 HTTP |
| 기존 `korea-replay` Worker | 현재 관측 API와 비공개 브로커 연결 | Service binding으로만 새 Pages에서 호출 |

위 두 Pages 이름은 이번에 실제 생성·배포 응답으로 확인했습니다. 새 환경을 구성할 때도 이름이 사용 중이라는 이유로 임의의 다른 프로젝트를 수정하지 않습니다. 별도로 합의한 프로젝트명과 검증기를 함께 변경하며 다른 계정·서비스의 서브도메인은 보존합니다.

Pages에서는 HTTP Range 요청에 `200` 전체 응답을 반환합니다. 따라서 2D PMTiles는 작은 파일 전체를 내려받아 검증한 뒤 브라우저의 제한된 메모리에서 범위를 읽습니다. archive는 1 MiB를 넘으면 게시를 거부합니다. 원본 지형·건물 3D는 기존 자산 구조를 유지합니다. [공식 정적 제공 동작](https://developers.cloudflare.com/pages/configuration/serving-pages/)

## 배포 도구의 안전 경계

**이 프로젝트에서는 원격 Pages 작업에 `wrangler pages ...`를 사용하지 않습니다.** 설치된 Wrangler 4.132.0의 에이전트 감지·자동 위임 때문에 신규 Pages 생성 명령이 기존 Workers 배포로 바뀐 사고가 있었습니다. [사고와 복구 기록](PAGES_CLI_INCIDENT_20260920.md)

`scripts/pages-api.mjs`는 Cloudflare의 Pages REST 경로만 호출합니다. 허용 프로젝트는 위 두 개로 고정되어 있으며 Workers, 과금, R2, D1, cron, 다른 계정 경로는 호출할 수 없습니다. 토큰은 `CLOUDFLARE_API_TOKEN`, 계정 ID는 `CLOUDFLARE_ACCOUNT_ID` 환경변수로만 받습니다. 오류는 HTTP 상태와 공식 숫자 오류 코드만 기록하며 인증값·응답 메시지는 출력하지 않습니다. 배포 POST는 불확실한 결과를 자동 재시도하지 않습니다. [공식 프로젝트 API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/create/), [공식 배포 API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/subresources/deployments/methods/create/)

다음 `create`는 최초 프로젝트가 없을 때의 절차입니다. 현재 이미 공개된 두 프로젝트를 새로 만드는 단계로 반복하지 않습니다.

```text
node scripts/pages-api.mjs inspect korea-replay
node scripts/pages-api.mjs create korea-replay --execute
node scripts/pages-api.mjs create korea-replay-data --execute
```

`create`는 프로젝트 생성만 요청하며 배포 코드·자산을 함께 보내지 않습니다. 이미 존재하는 프로젝트의 설정을 갱신하지 않습니다. 소스의 REST 업로드는 설치 Wrangler의 공개 소스가 사용하는 BLAKE3(base64본문+확장자) 키와 Pages 자산 API를 따릅니다. 업로드 본문의 SHA-256을 별도로 재검증하며, 최대 2개 요청으로 제한합니다. 프로토콜·Service binding을 변경하면 다음 preview에서 원격 검증을 다시 수행해야 합니다.

## 별도 정적 묶음 생성

기존 정상 `pipeline.static_release` receipt와 `asset-manifest.json`을 먼저 전체 검증합니다. 입력 묶음은 `.local/deploy/bundles/` 내부여야 합니다. 새 산출물은 `.local/pages-release/`에만 생성하며, 존재하는 대상 폴더는 덮어쓰지 않습니다. 심볼릭 링크·junction·상위 경로 이동을 거부합니다.

정적 파일과 기존 Worker 모듈은 감사된 불변 묶음에서 hardlink로 재사용합니다. 다른 볼륨·권한 등의 이유로 불가능하면 배타적 복사를 합니다. 공유된 inode를 수정하면 원본까지 바뀌므로 **stage 이후 파일 편집을 금지**하고 새 stage를 만듭니다. 설정·policy·receipt는 각각 새 파일입니다. 기존 원본, 다른 프로젝트, 이전 배포 묶음을 삭제·이동하지 않습니다.

앱 설정은 `pages_build_output_dir: ./client`, `KOREA_API → korea-replay` Service binding 하나만 허용합니다. `_routes.json`은 `/api/*`만 포함하고 root `404.html`로 SPA의 누락 파일 `200` 응답을 막습니다. 정적 파일은 Function을 경유하지 않습니다. 기존 `_headers`의 hash 경로 장기 캐시와 `catalog.json`, `download-gate.js`의 재검증 정책을 보존합니다. [Pages 라우팅](https://developers.cloudflare.com/pages/functions/routing/), [Service binding](https://developers.cloudflare.com/pages/functions/bindings/#service-bindings)

정적 catalog/search/replay/sources는 `_worker.js/app/`에 담긴 감사된 기존 Worker가 해당 배포의 `ASSETS`에서 읽습니다. 현재관측만 기존 Worker로 전달합니다. 현재 서비스가 바뀌어도 옛 공유 자료가 최신으로 대체되지 않습니다. 현재관측 targets의 bus catalog도 이 배포에 존재하는 고정 참조를 사용합니다. 비밀키나 브로커 상태 바인딩은 Pages에 복사하지 않으며, 기존 API가 실패하면 직접 원천 호출로 우회하지 않습니다.

## 지도와 부동산의 공동 데이터 릴리스

각 게시기는 자기완결 후보에 `publication.json`을 작성합니다.

```json
{
  "schema_version": 1,
  "files": [{"path":"data/map-tiles/map-example/catalog.json","sha256":"<64 hex>","byte_length":123}],
  "map_catalog": {"path":"data/map-tiles/map-example/catalog.json","sha256":"<64 hex>","release_id":"map-example"}
}
```

부동산 후보는 동일 형태에서 `property_release`와 `data/property/<release>/manifest.json`을 사용합니다. 원본 XML, 내부 DB, 키, 체크포인트는 포함하지 않습니다. 두 후보를 검증한 뒤 `/data/atlas/atlas-<16hex>/manifest.json`이 `map_catalog`, `property_release`의 각각 경로·SHA-256·릴리스를 고정합니다. 파일 목록의 누락·추가·중복·해시·길이 불일치를 모두 실패시킵니다.

데이터 Pages는 정적 `Access-Control-Allow-Origin: *`, GET/HEAD/OPTIONS만 표시하고 credential을 사용하지 않습니다. 브라우저는 runtime에서 확인한 **실제 immutable data origin**만 로딩 큐에 허용해야 합니다. 범용 외부 URL 허용이나 mutable production alias 사용은 금지합니다.

개인 로컬 옵션 JSON 예시:

```json
{"mapPublication":".local/map-candidate/publication.json","propertyPublication":".local/property-candidate/publication.json"}
```

```text
node scripts/pages-release.mjs data .local/pages-data-options.json
node scripts/pages-release.mjs check .local/pages-release/<data-stage>/receipt.json
node scripts/pages-api.mjs upload .local/pages-release/<data-stage>/receipt.json --preview
node scripts/pages-api.mjs verify .local/pages-release/<data-stage>/receipt.json https://<actual8hex>.korea-replay-data.pages.dev
```

실제 반환 URL과 배포 ID를 stage 밖으로 추정하지 않습니다. 업로드 성공 응답은 `remote-deployment-<id>.json`에 기록하며, 서비스 응답 확인은 별도입니다.

업로드는 동시 2개 버킷으로 제한합니다. 각 버킷의 업로드 성공 응답을 받은 뒤 **그 버킷의 정확한 해시만** `upsert-hashes`로 등록하고 진행률을 기록합니다. 등록에 실패하거나 응답이 불명확하면 완료로 세지 않습니다. 실패 후 새 버킷을 시작하지 않고 진행 중인 두 작업을 정리한 뒤 종료합니다. 재시작은 전체 후보 해시에 대한 서버의 `check-missing` 결과로 판단하며, 누적 진행 건수로 과거 성공 파일을 추정하지 않습니다. 마지막 전체 해시 등록과 배포 POST 무재시도 원칙도 유지합니다. 이는 재업로드를 줄이기 위한 보존 절차이며 영구 백업은 아닙니다. [공식 등록 API](https://developers.cloudflare.com/api/resources/pages/subresources/assets/methods/upsert_hashes/)

최종 앱 preview는 **새 업로드 5개·재사용 17,749개**, production은 **새 업로드 0개·재사용 17,754개**였습니다. 이 숫자는 자산 API의 고유 키 수입니다. Worker·설정까지 보수적으로 센 stage는 **17,764파일 / 10,730,121,203 B**로 18,000파일 관리 목표 안에 있습니다. 전체 파일 재전송을 줄였다는 증거이며, 원격 전체 파일을 다시 내려받아 검사했거나 원본 백업을 확보했다는 뜻은 아닙니다.

## Runtime v2와 두 단계 앱 게시

`shared/runtime-v2.ts`가 다음을 검증합니다.

- `release_id`: 기존 3D catalog 릴리스.
- `artifact_sha256`: client, Worker 코드, 설정, 데이터 고정 참조를 함께 묶은 SHA-256.
- `snapshot.origin/hash`: API가 실제 반환한 앱 immutable preview 주소와 그 8자리 host hash. Workers UUID나 전체 Pages ID와 혼동하지 않습니다.
- `data.origin/manifest_path/manifest_sha256`: 실제 immutable data preview와 공동 manifest.

첫 앱 후보는 `snapshot_origin: null`로 생성합니다. 해당 preview runtime은 요청 hostname의 실제 hash를 사용합니다. 후보의 HTTP·지도·공유를 검증한 뒤 동일 입력으로 production stage를 만들며 `snapshotOrigin`과 `candidateReceiptPath`를 지정합니다. 도구는 두 묶음의 artifact identity가 같아야 허용합니다. Production runtime이 가리키는 snapshot은 검증한 후보이며 production 자체의 배포 ID를 사칭하지 않습니다.

```json
{
  "receiptPath":".local/deploy/bundles/<verified-bundle>/receipt.json",
  "data":{"origin":"https://<actual8hex>.korea-replay-data.pages.dev","manifest_path":"/data/atlas/<atlas-release>/manifest.json","manifest_sha256":"<64 hex>"}
}
```

```text
node scripts/pages-release.mjs app .local/pages-app-candidate-options.json
node scripts/pages-api.mjs upload .local/pages-release/<app-candidate>/receipt.json --preview
node scripts/pages-api.mjs verify .local/pages-release/<app-candidate>/receipt.json https://<actual8hex>.korea-replay.pages.dev
```

검증된 실제 candidate URL을 `snapshotOrigin`, 후보 receipt를 `candidateReceiptPath`로 추가한 **별도 옵션 파일**로 다시 stage하고 `--production` 업로드합니다. 도구는 후보 폴더의 `verified-preview-<actual8hex>.json`이 없거나 artifact/origin이 다르면 production stage를 거부합니다. 이 자동 HTTP 검사는 runtime, 모든 app JS/CSS, index/catalog, static404를 검증하며 브라우저 UI·현재관측·rollback은 별도 검사입니다. production과 candidate의 index/client/지도/Worker hash가 같고 공유 복원이 동일 화면인지 확인합니다.

Artifact 계산에서 공유 목적지 `snapshot_origin`만 의미상 제외합니다. 자기참조를 피하기 위해 `artifact_sha256` 필드는 빈 값으로 정규화하여 계산합니다. 생성된 policy 파일은 전체 파일 manifest에서 별도로 SHA 검증하므로 나머지 policy 값을 마음대로 바꿀 수 없습니다. config, adapter, 기존 Worker 전체 모듈, 모든 client 파일, data origin·manifest hash는 artifact에 포함합니다.

새 공유는 `deployment=pages:<previewHash>:<artifactSha256>`와 기존 hash의 `release`, 카메라·레이어 상태를 사용합니다. 잘못된 token/host/hash/릴리스를 최신으로 대체하지 않습니다. `/api/v2/runtime`이 **404일 때만** 과거 v1 Workers 계약으로 되돌아갑니다. 기존 Workers UUID 공유는 그대로 보존하므로 옛 공유 주소에 개인 서브도메인이 남을 수 있습니다. 새 대표 주소·새 공유에는 그 이름이 들어가지 않습니다.

## 무료 한도와 검증

앱·데이터 프로젝트 각각 20,000 파일, 파일당 25 MiB가 hard limit이고 18,000 파일이 관리 목표입니다. 내부 Worker 모듈과 설정 파일까지 보수적으로 수에 넣습니다. 앱의 기존 입력 검증은 24 MiB 미만을 유지합니다. PMTiles는 별도 1 MiB hard limit입니다. 무료 계정 Functions 요청 한도를 정적 자산으로 우회하는 것은 아니며 API 호출은 Workers 무료 한도를 사용합니다. [Pages 한도](https://developers.cloudflare.com/pages/platform/limits/)

릴리스 검사와 별도 복구 인수:

1. 소스 type/lint/test/build, 앱·데이터 stage 전체 hash 및 파일 수/크기 검사.
2. data preview의 manifest hash, map/property entry hash, PMTiles 실제 GET, CORS, 누락 파일404.
3. app preview의 runtime v2, index/JS, 고정 release, 정적 조회 API, 2D/3D, 선택/검색/공유 복원.
4. 현재관측 Service binding의 키 비노출·실패시 우회 없음·브로커 중앙 quota 유지.
5. production과 candidate의 artifact·정적 응답·공유 화면 동일성.
6. 이전 정상 **Pages production** 배포로 rollback 후 같은 검사를 하고 최종판으로 복귀. Preview는 rollback 대상이 아닙니다. 현재 첫 Pages 공개판에 대해 이 실제 복구 인수는 **미완료**이며 기존 Worker rollback 결과로 대신하지 않습니다. [공식 롤백 범위](https://developers.cloudflare.com/pages/configuration/rollbacks/)

원격 preview/production 검증, 실제 전경 FPS 개선, 모든 실건물 정확성은 로컬 단위검사로 대신 완료 판정하지 않습니다.

## 공개 소스와 CI

MIT는 자체 코드만 적용하고 자료·타사 라이선스는 [고지](../THIRD_PARTY_NOTICES.md)를 유지합니다. `node scripts/pages-public-audit.mjs`는 추적 파일, `.gitignore`에서 제외되지 않은 새 후보 파일, 모든 Git blob·commit metadata를 검사합니다. 값은 출력하지 않고 경로·줄·종류만 남깁니다. 과거 private 이력은 보존했고 정리한 소스를 [별도 공개 저장소](https://github.com/pollmap/korea-replay-atlas)에 게시해 PR #1·#2·#3을 병합했습니다. `.local`, 원본, 대용량 지도, 인증키, 복구 archive는 공개 Git에 넣지 않습니다. 코드 원격화와 원본 자료의 외부 백업 완료는 다릅니다.

`.github/workflows/ci.yml`은 public repo의 표준 Ubuntu runner만 사용하고 write/deploy 권한이 없습니다. 웹 검증과 Python 회귀는 병렬 job입니다. `property-plan.yml`은 수동 실행의 계획 생성만 하며 키/`--execute`/게시/원본 artifact 업로드가 없습니다. 실제 연속 수집은 승인된 공식 registry, quota 장부, 키 secret, sanitization과 수집기 계약을 확인한 별도 변경으로 연결해야 합니다. 지금의 계획 workflow를 운영 수집 완료로 표현하지 않습니다.

## 완료한 원격 증거와 남은 운영 항목

- **완료:** 실제 Pages production 응답, runtime의 앱·데이터 pin 일치, 앱 JS/CSS와 index/catalog 해시, 정적 누락 404. 데이터 origin의 manifest·추가 저줌 MVT·CORS 검사는 별도 기록했습니다.
- **고정 preview 표본 완료:** 유효 공유의 노원구/2026-08/59.28㎡ 복원, 잘못된 버전의 최신 대체 없는 중단, 같은 artifact에서의 서울 3D·모델 요청·캔버스 1개 확인. 표본 UI 검증을 모든 위치·모든 도구의 전수 검사로 표현하지 않습니다.
- **초기 지도 준비 측정 완료:** 공개 대표 주소, Radeon 860M·20Mbps/50ms에서 최초 진입/재방문 각 10회. P95 1,160.2ms/944.3ms입니다. 별도 20왕복의 최종 최근 600개 이동 프레임 P95는 39.2ms로 33ms 목표 미달입니다. 정의·표본·메모리 관찰은 [검증 현황](ATLAS_RELEASE_STATUS.md)에 있습니다. 10배 개선은 입증하지 않았습니다.
- **미완료:** 실제 Pages production rollback·복귀, 원본과 수집 장부의 완전한 외부 복구 검증, 최신 관측·부동산의 연속 서버 수집, 최근 60개 완료월과 과거 행정 코드 대응, 실거래 단지의 검증된 좌표 연결, 공식 인구·공급·가격지수의 제품 연결.

공개 지도는 개발 노트북을 꺼도 제공됩니다. 로컬에서 도는 수집·재가공까지 서버로 옮긴 것은 아니며, 원본 삭제·정리를 허용할 복구 완료 상태도 아닙니다. 후속 성능 후보의 로컬 구현은 위 공개 artifact의 검증 결과에 포함하지 않습니다.
