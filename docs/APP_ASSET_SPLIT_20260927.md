# 화면 배포와 불변 지도 배포 분리 검토

기준일: 2026-09-27. 상태: **구현 전 설계**. 이 조사에서는 소스·지도 자료·원격 배포를 변경하지 않았다. 현재 진행 중인 배포의 완료 여부를 판정하는 문서가 아니다.

## 결론

Cloudflare를 유지하고 **2D 앱, 2D/부동산 자료, 기존 3D 앱을 각각 배포 단위로 분리**하는 것이 우선이다. 현재 큰 비용은 호스팅 CLI가 아니라 작은 화면 수정에도 기존 3D 정적 자료까지 포함하는 검증 경로에서 발생한다. 이미 게시된 불변 자료는 배포 ID와 감사 증빙으로 고정해 재사용하고, 새 앱에 포함되는 작은 파일은 매번 전부 검증한다.

다만 현재 앱에서 `/data/*`를 제거하고 URL만 다른 호스트로 돌리면 안 된다. 기존 3D는 같은 출처의 상대 경로·서비스워커에 의존한다. **3D 렌더러와 자료를 그대로 보존하는 1차 분리안은 새 2D 화면에서 검증된 기존 3D 앱으로 이동하는 방식**이다. 같은 페이지 안에서 2D↔3D 전환까지 유지하려면 별도의 전송 계층 변경이 필요하다. 이를 무변경 분리로 설명하거나 테스트 없이 공개해서는 안 된다.

## 1. 실제 비용과 현재 구조

확인한 로컬 게시 증빙: `.local/pages-release/korea-replay-ce30b9771010d003-production-54014567/{receipt.json,asset-manifest.json,verified-production.json}`. 이 증빙에는 `https://54014567.korea-replay.pages.dev`와 artifact SHA `ce30b9771010d003b229bc43d0490792c3ae07b63c01521d2627c0fcae170317`이 고정되어 있다. 대표 주소가 이후 배포로 바뀌었는지는 별개다.

| 파일 집합 | 개수 | 바이트 |
| --- | ---: | ---: |
| 현재 앱 게시 묶음 전체 | 18,290 | 10,743,381,599 |
| `data/retiled/` | 5,784 | 9,751,100,042 |
| `data/terrain/` | 9,621 | 803,399,459 |
| 모든 `data/`를 제외한 앱 파일 | 936 | 26,907,186 |
| 아래 보수적인 앱+메타데이터 유지안 | 1,685 | 58,867,186 |

마지막 행은 manifest를 경로로 분류해 합산한 **배포 입력 규모**다. 10.743GB 대비 약 99.45% 작지만, 실제 배포 시간이 같은 비율로 줄어든다는 측정 결과는 아니다. 해당 범위의 기능 호환 검증은 아직 남아 있다.

새 2D 지도와 부동산 자료는 이미 별도 프로젝트다. 확인한 정책은 `https://0282e2a1.korea-replay-data.pages.dev/data/atlas/atlas-d6f6212552ac672c/manifest.json`, SHA `ff5f74ae49d516e11a64eef44f0e442896f0356ebede45faf6edab568c98c168`을 고정한다. 즉 아직 붙어 있는 큰 자료는 새 2D 타일이 아니라 기존 3D 건물·지형이다.

### 같은 자료를 반복해서 읽는 경로

| 코드 | 현재 동작 |
| --- | --- |
| `pipeline/frontend_release.py::restage` | 기존 `data/` 전체를 새 plan에 넣고 `static_release.stage(reuse_bundle=...)` 호출 |
| `pipeline/static_release.py::_verified_reuse_bundle` | receipt/config/manifest와 파일 목록 일치 후 모든 정적 파일 바이트·SHA 재검증 |
| `scripts/pages-release.mjs::stagePagesApp` | 전체 static bundle 검증 후 모든 자산을 Pages 묶음에 포함 |
| `createStage`, `verifyPagesStage` | hardlink/copy 후 해시 및 완전한 파일 목록 확인 |
| `scripts/pages-api.mjs::deployPagesStage` | stage 검증 후 모든 정적 파일을 읽어 SHA와 Cloudflare 자산 해시 계산 |
| `verifyPagesRemote` | 다시 로컬 stage 검증 후 원격 앱·runtime·404 등 검사 |

Cloudflare `check-missing` 때문에 **모든 10GB가 매번 네트워크로 업로드되는 것은 아니다**. 이미 서버에 있는 파일도 업로드 필요 여부를 판단하기 전에 로컬에서 읽고 해시를 계산한다. 위 검사는 현재의 단일 배포 단위에서는 유효하므로 제거하지 않는다. 새 배포 단위를 도입해 검사의 대상 자체를 바꾼다.

## 2. 단순 외부 주소 치환이 깨뜨리는 부분

- `src/catalog.ts`의 catalog/index와 `shared/search-v2.ts`의 검색 descriptor는 `/data/` 상대 경로만 허용한다. 모든 URL을 절대 주소로 치환하면 현재 검증 계약에 걸린다.
- `src/MapScene.tsx`는 terrain/3D Tiles/GeoJSON 등을 `asset.url`로 받는다. tileset과 terrain 내부 참조, JSON에서 재발견한 루트 상대 경로까지 고려해야 한다.
- `src/download-gate.worker.js`는 가로챈 요청을 `mode: 'same-origin'`으로 보낸다. 외부 302는 일반 fetch뿐 아니라 이 경로를 깨뜨릴 수 있다.
- 확인한 기존 앱 `_headers`에는 `Access-Control-Allow-Origin`이 없다. 기존 불변 배포의 헤더를 나중에 수정할 수 있다고 가정해서는 안 된다.
- Pages의 200 rewrite는 같은 사이트의 상대 URL만 지원한다. `_redirects` 한 줄로 외부 불변 호스트를 투명하게 프록시할 수 없다. [Cloudflare redirects](https://developers.cloudflare.com/pages/configuration/redirects/)
- `/data/*` 전체를 Pages Functions로 프록시하면 같은 출처 동작은 만들 수 있지만 모든 지도 요청이 Workers 요청 예산을 사용한다. 정적 자산과 `/api/*`를 분리한 현재 무료 운영 설계와 달라지므로 기본안으로 쓰지 않는다. [Pages Functions 요금](https://developers.cloudflare.com/pages/functions/pricing/)

이번 조사에서 Python HEAD로 기존 앱·자료 불변 주소를 재조회한 요청은 모두 HTTP 403을 받았다. 따라서 **현재 원격 CORS/404 성공을 새로 확인했다고 주장하지 않는다**. 위 헤더 판단은 실제 stage의 `_headers`와 기존 검증 증빙에 근거한다. 구현 전 배포 도구의 정상 접근 경로와 브라우저에서 실제 헤더를 다시 검사해야 한다.

## 3. 권장 1차 분리안: 기존 3D는 불변 앱으로 보존

```text
korea-replay.pages.dev / <앱 불변 배포>
  ├─ 2D UI·검색·SGIS 선택 경계: 앱 자체의 작은 검증 묶음
  ├─ 2D 타일·부동산: 기존 korea-replay-data 불변 배포 직접 조회
  ├─ /api/v1/live/*: 기존 KOREA_API 서비스 바인딩
  └─ 3D·기록·햇빛: 검증된 기존 앱 불변 배포로 명시적으로 이동
                       └─ 기존 코드·동일 출처 데이터·공유 계약 유지
```

3D의 코드와 데이터는 건드리지 않는다. 새 앱의 진입/탐색 연결만 변경한다. 새 2D UI에 없는 3D 자산을 요청하게 놔둔 뒤 실패할 때 옛 앱으로 보내는 방식은 금지한다. `view=3d`, `mode=replay|sun`은 지도 엔진을 만들기 전에 기존 앱으로 연결한다. 현재 관측 중 3D 의존 화면도 같은 원칙을 적용한다.

위치·시각·레이어·선택 상태는 대상 앱이 지원하는 공유 필드로 전달한다. 목적지의 release와 deployment는 대상 불변 앱의 실제 runtime에 맞춰 새로 구성한다. 새 앱의 배포 ID를 옛 앱에 복사하지 않는다. 원래 2D 위치·필터는 현재 URL에 보존하고 브라우저 뒤로가기로 복원한다. 기존 앱이 이해하지 못하는 단지 선택을 3D에서 복원했다고 표시하지 않는다. 임의의 `returnTo` 외부 URL을 허용하지 않는다.

### 얇은 앱에 남길 정확한 파일 집합

초기에는 빌드 청크를 과하게 덜어내지 않고 현재 검증된 비데이터 파일 전체를 보존한다. 이후 사용되지 않는 3D 청크 제거는 독립 변경이다.

| 남길 경로 | 용도·검증 |
| --- | --- |
| `index.html`, `download-gate.js` | 시작점·기존 다운로드 제어; 실제 새 앱 바이트 전부 검사 |
| `assets/**` | JS/CSS/Worker/폰트/아이콘과 SGIS 521개 JSON; 임의 glob 신뢰가 아니라 Vite 산출물+정식 경계 색인의 명시적 목록 검사 |
| `cesium/**` | 1차에 현재 vendor를 그대로 보존; 향후 제거는 번들 의존성 감사 후 별도 |
| `_worker.js/**`, `_routes.json` | adapter, 정적 API 코드, policy; 함수 경로는 `/api/*`만 |
| `_headers`, `404.html`, `.assetsignore` | 장기 캐시·누락 파일 404·배포 제어 |
| `data/catalog.json`, `data/releases/**` | 기존 릴리스 식별과 검색 메타데이터; 허용 릴리스의 exact manifest 목록만 |
| `data/indexes/**` | coverage 및 기존 작은 인덱스; 각 외부 geometry 의존성은 legacy pin에 속함을 명시 |
| `data/search-v2/**` | 검색 manifest와 전체 shard 폐쇄집합, 현재 23,032,349B |
| `data/live-transit/**` | 현재 pinned bus catalog와 route 폐쇄집합, 현재 5,212,400B |

이 집합이 위 1,685개 / 58,867,186B 산정 기준이다. 경계 JSON은 `assets/`에 이미 포함한다. 앱별 새 상한은 예를 들어 2,000파일/80MiB로 별도 설정하되 실제 빌드로 확정한다. 플랫폼 20,000파일/25MiB 단일 파일 제한과 내부 24MiB 제한도 계속 검사한다. 신규 경로가 나오면 승인된 생성기/의존성 계약을 추가해야 하며 unbounded allowlist로 통과시키지 않는다.

`data/building-parts`, `building-streams`, `depth`, `geography`, `hierarchy`, `osm`, `retiled`, `terrain`, `transportation`, `water-partitions`, `replay`, `weather`는 **새 앱 묶음에서만 제외**한다. 기존 로컬 원본·검증 번들·불변 원격 배포는 그대로 둔다. 남겨진 catalog가 외부 자료를 가리킨다는 사실을 새 publication manifest에서 명시하고, 로컬 완전 폐쇄집합으로 거짓 판정하지 않는다.

### API 경로와 호환 범위

| 경로 | 새 앱 처리 |
| --- | --- |
| `/api/v2/runtime` | 새 앱 pin, 기존 2D/property pin, legacy app pin 반환; no-store |
| `/api/v1/live/*` | 기존 `KOREA_API`만 호출; 키를 브라우저로 전달하지 않음. targets의 bus catalog는 앱에 포함된 동일 감사본 |
| `/api/v1/search`, `/api/v1/sources/*`, `/api/v1/health` | 포함된 작은 정적 자료와 기존 코드로 유지 |
| `/api/v1/catalog`, `/api/v1/coverage`, `/api/v1/replay`, `/api/v1/runtime` | 옛 불변 앱에서는 그대로 유지. 새 대표 주소의 v1 호환은 아래 별도 게이트 |
| 알 수 없는 `/api/*` | 기존처럼 404, 잘못된 입력 400, 허용하지 않는 메서드 405 |
| 없는 정적 자료 | HTML 200 대체 없이 404 |

**중요한 호환 제한:** 기존 v1 catalog가 반환한 `/data/...`를 새 대표 주소에서 그대로 내려받는 외부 클라이언트까지 보존하려면 위 1차 파일 집합만으로는 충분하지 않다. API JSON을 유지하는 것과 그 JSON의 모든 상대 자산 URL을 유지하는 것은 다르다. 옛 불변 공유 링크 보존은 가능하지만 canonical origin의 모든 v1 자산 URL까지 완전히 보존했다고 보고해서는 안 된다.

따라서 공개 전 다음 중 하나를 확정해야 한다. (a) 3D·v1 사용은 기존 불변 앱으로 연결하는 제품 분리를 채택하고 문서·공유 진입을 전환, (b) canonical v1 완전 호환을 필수로 삼아 후술하는 전송 계층 작업을 먼저 완료, (c) 그때까지 기존 전체 배포를 유지. 자동으로 v1의 release를 최신 자료로 대체하거나 조용히 빈 catalog를 돌려주는 안은 제외한다.

## 4. 감사 단위와 새 게시 증빙

기존 `frontend_release.py`의 모든 바이트 검증을 끄거나 receipt의 mtime/size만 믿는 옵션을 넣지 않는다. 별도 `app-only` staging 계약을 추가한다.

1. 자료 배포 때에는 기존처럼 전체 파일 내용·목록·참조 폐쇄집합·크기·라이선스·비밀정보를 검사한다. 원격 게시와 실제 불변 URL 검증을 끝낸다.
2. 이 검증이 끝난 배포만 `PublishedAssetPin`으로 승격한다. 필드: project, actual deployment ID/origin, release ID, artifact SHA, closure manifest SHA, file count/bytes, schema, 검증 도구 버전, 이전 전체 감사 증빙 hash, 원격 검증 증빙 hash. 계정·키·로컬 절대 경로는 공개 pin에서 제외한다.
3. pin은 검토된 저장소 파일로 고정한다. 앱 개발자가 임의 origin이나 로컬 receipt를 주면 감사가 통과하는 입력 형식은 금지한다. 초회 pin 등록 시 전체 증빙과 불변 주소를 대조한다. 기존 배포에 공개 closure manifest가 없다면 로컬 감사 증빙의 digest를 검토된 registry에 고정하고 존재하지 않는 원격 manifest URL을 만들어내지 않는다.
4. 앱-only staging은 앱의 모든 실제 바이트와 exact inventory를 검사하고, 새 파일 목록에 큰 자료를 넣지 않는다. 참조된 pin/manifest/runtime과 소수의 canary를 조회해 배포 존재·내용·CORS·404를 확인한다. 참조 데이터의 전체 감사는 재사용한 이전 감사로 별도 표시한다. **canary 검사가 전체 자료 재검증을 대신했다고 표현하지 않는다.**
5. 앱 artifact fingerprint는 새 앱 목록+설정+API adapter+2D/property pin+legacy pin을 포함한다. 데이터 pin이 바뀌면 앱 artifact도 달라져야 한다.
6. candidate 업로드→실제 immutable URL→runtime/화면 검증→같은 artifact의 production 전환은 현 순서 그대로다. `pages-api.mjs`의 엄격한 프로젝트/호스트 allowlist, 비밀키 비노출, non-idempotent deployment POST 무재시도 정책도 유지한다.
7. 기존 full stage 명령은 그대로 둔다. `app-only` receipt는 명시적인 별도 kind로 구분하고 full-data receipt처럼 통과시키지 않는다. 앱 stage에 포함되지 않은 자료의 로컬 payload는 열지도 않도록 테스트한다.

공통 descriptor/SGIS/검색 자산은 처음에 59MB 앱 묶음으로 검증하면 충분하다. 더 줄일 필요가 입증될 때 이들도 별도 불변 자료 묶음으로 옮기되, 처음부터 범위를 넓혀 분리를 지연하지 않는다.

## 5. 같은 페이지의 3D까지 유지하려면 필요한 후속

3D 렌더러를 고치지 않고도 전송 계층을 바꾸는 안은 가능하지만 별도 구현·검증이 필요하다.

- 기존 geometry 바이트를 그대로 가진 **CORS 허용 정적 legacy-data 배포를 한 번 게시**한다. 최초 게시에 한해 전체 약 10.7GB 검증이 필요하다. 현재 2D data 프로젝트와 합쳐 파일 상한을 넘기지 않도록 별도 프로젝트를 선택한다. 이번 조사에서는 생성하지 않았다.
- `/data/*` fetch를 엄격히 고정된 원격 출처로 해석하는 공통 전송 계층을 마련한다. Cesium 요청, Web Worker, 이미지, 초기 서비스워커 미제어/미지원 상태까지 모두 다뤄야 한다. 상대 JSON 참조와 tile URL의 해석 기준을 보존한다.
- 기존 download gate의 same-origin 요청과 사용자 정의 진단 헤더를 그대로 외부로 보내면 CORS/preflight 문제가 생긴다. 임의 origin 허용이나 인증정보 전달 없이 별도 처리해야 한다. 초기 미제어 브라우저까지 통과하지 못하면 이 안은 미완료다.
- CORS는 공개 무자격 요청만 허용하고 credentials를 보내지 않는다. SHA/byte 검증, 4슬롯, 취소, 압축 해제 후 length/encoding 정리, 404, Range 가정 금지 정책은 그대로 유지한다.
- V2 현재 validator는 특정 Pages origin만 허용한다. 새 자료 종류의 엄격한 pin 계약과 하위호환 해석을 추가해야 한다. origin regex를 모든 HTTPS로 넓히는 방법은 금지한다.

이는 3D 자료 재생성도 렌더러 품질 변경도 아니지만 **전송 동작 변경**이다. 현재 “3D를 건드리지 않음”을 렌더러뿐 아니라 모든 3D 동작 무변경으로 해석하면 이 후속은 범위 밖이며, 1차의 별도 앱 이동안을 사용한다.

## 6. 공유·복원·롤백

- 기존 `shared/share.ts`의 Workers UUID 링크, `shared/runtime-v2.ts`의 `pages:<snapshot>:<artifact>` 링크, `src/property-release-archive.ts`의 검증된 과거 property 주소를 그대로 보존한다.
- 새 앱 공유는 새 앱 immutable origin과 앱 artifact를 고정한다. 2D/property/legacy pin은 그 artifact에 포함된다. 없는 release는 404/명시적 오류이고 latest로 fallback하지 않는다.
- 새 2D 앱에서 과거 `view=3d` 링크를 받으면 실제 대상 legacy 배포 pin과 그 release로 연결하는 명시적인 호환 표를 사용한다. 알려지지 않은 조합은 실패 처리한다.
- 롤백 단위는 `앱 artifact + 모든 자료 pin + legacy app pin + API adapter`다. 데이터 latest alias나 최신 bus catalog를 다시 읽어서 조립하지 않는다.
- 이전 정상 앱의 production 복구 후 runtime·old/new 공유·검색·자료 누락·canary를 검사한다. 데이터 프로젝트를 되돌리거나 원본을 지울 필요가 없어야 한다.
- 불변 배포는 백업이 아니다. 원본과 생성 설정·감사 증빙·로컬 bundle·공개 소스는 계속 보관한다. 원격 배포 삭제/접근 제한/계정 문제는 별도 장애로 탐지해야 한다. Pages의 활성 preview 개수 제한 없음은 영구 보존 보증과 다르다. [Pages 한도](https://developers.cloudflare.com/pages/platform/limits/)

## 7. 필수 회귀 검사

| 검사 위치 | 통과 조건 |
| --- | --- |
| 새 app-only staging + `tests/test_frontend_release.py` | 앱 한 바이트 변조, 누락/추가 파일, traversal, symlink/junction, 자격정보, 미등록 SGIS, 잘못된 pin 거부. 큰 data payload의 파일 open을 막은 fixture에서도 앱-only stage 통과; full stage는 그대로 전체 변조를 탐지 |
| `tests/pages-release.test.js` | 앱 receipt kind, 입력 집합, pin 포함 artifact 계산, 후보/production 동일 artifact, 파일/바이트 제한, 외부 data closure 구분 |
| `tests/pages-api.test.js` | app-only 업로드 해시 계산이 앱 파일만 읽음. 잘못된 프로젝트/preview origin/원격 proof 거부. 파일 변경 시 즉시 실패. deployment POST 무재시도 유지 |
| `tests/pages-adapter.test.ts` | GET/HEAD/405, runtime no-store, unknown API 404, KOREA_API만 live 호출, upstream redirect 차단, bus catalog pin, 키/자격정보 전파 금지 |
| `tests/runtime-v2.test.ts`, `share.test.ts`, property archive | 앱/자료/legacy pin 각각 불일치 시 복원 실패; 기존 Workers·Pages·과거 property 링크 복원; arbitrary return URL 거부 |
| catalog/search/atlas client | 검색 shard 폐쇄집합, 2D CORS fetch redirect:error 유지, SHA 불일치·자료 없는 월·404를 다른 데이터로 대체하지 않음 |
| 다운로드 gate/브라우저 | 첫 방문 미제어, 새 SW 활성화, 이전 앱 탭, 뒤로가기, 동시 새/옛 배포 탭; 2D에서 GLB/terrain 요청 0. 기존 3D 앱의 요청·4슬롯·404 유지 |
| 실제 서비스 E2E | 2D 지역→단지→공유→새 탭, 위치/시각 전달 후 3D→뒤로가기, 이전 공유 직접 열기, live 조회, 모바일, candidate→production→rollback |
| 속도 증빙 | 단계별 local bytes read/hash time, stage/upload/remote 시간, missing/cached 업로드량을 따로 기록. 서버 dedup과 로컬 검증 감소를 혼동하지 않음 |

호환과 CORS 게이트가 통과하기 전에는 기존 전체 배포를 계속 사용한다. 테스트 통과와 공개 배포 완료를 분리해 보고한다.

## 8. Vercel 판단

Vercel로 **얇은 UI만** 배포하는 것은 이후 선택지다. 그러나 현재 전체 묶음은 Vercel CLI Hobby source upload 100MB 및 source file 15,000개 제한과 맞지 않는다. 이 제한은 CLI 입력 제한이며 모든 Vercel 정적 출력의 절대 용량 제한이라고 일반화하지 않는다. [Vercel 공식 limits](https://vercel.com/docs/limits)

얇은 앱을 만든 뒤에도 현재 `RuntimeV2.platform`, origin 검증, immutable 공유, `KOREA_API` Cloudflare Service Binding, 404/CORS/캐시·배포 증빙을 Vercel용으로 구현해야 한다. 서비스 바인딩이 타사 호스트에서도 그대로 동작한다고 가정할 수 없다. 원격 API를 공개 프록시로 바꾸려면 별도 허용 정책과 예산 검증이 필요하다.

따라서 이번 병목의 해결 순서는 **배포 단위 분리→측정→필요하면 UI 호스트 비교**다. CLI 교체만으로 동일한 검증·복원·무료 운영을 얻는다는 결론은 근거가 없다.

### 다른 무료 경로와의 비교

2026-09-27 공식 원문 확인 기준이다. 각 한도는 서로 다른 상품/과금 단위이므로 합쳐서 하나의 저장 한도로 해석하지 않는다.

| 후보 | 현재 프로젝트에서의 판단 |
| --- | --- |
| Cloudflare Pages 유지 | 현재 공개 서비스와 API binding, 불변 공유가 이미 동작한다. 우선 앱 배포 단위를 분리한다. |
| Vercel Hobby UI | 개인·비상업 용도에 한정된다. 분리한 UI는 검토 가능하지만 API binding과 배포/공유 계약의 별도 이식이 필요하다. |
| Vercel Blob Hobby 자료 보관 | 월평균 저장 1GB와 전송 10GB는 현재 대용량 자료를 통째로 옮기는 해법이 아니다. 일반 앱의 Fast Data Transfer와 Blob 전송 한도는 다르다. |
| Netlify 신규 Free | 월 300 credits 안에서 production 배포당 15, 전송 GB당 20, 웹 요청 10,000회당 2 credits를 공유한다. 자주 배포하고 지도 타일을 전달하는 현재 용도에 무료 여유가 더 크다고 판단할 근거가 없다. 잔액 소진 시 프로젝트가 일시중단된다. |
| GitHub Pages | 게시 사이트 최대 1GB와 서버 실행 기능 부재로 전체 자료·현재 조회 API의 직접 대체안이 아니다. 공개 소스·CI 저장소 역할은 계속 유지한다. |

출처: [Vercel Hobby](https://vercel.com/docs/plans/hobby), [Vercel Blob](https://vercel.com/docs/vercel-blob/usage-and-pricing), [Netlify 크레딧 계산](https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-credit-based-plans/how-credits-work/), [GitHub Pages 한도](https://docs.github.com/en/enterprise-cloud@latest/pages/getting-started-with-github-pages/github-pages-limits).

대용량 자료의 로컬 재검증/변환, 호스트의 업로드·전달, 브라우저의 지도 렌더링은 별도 비용이다. 호스트 교체만으로 브라우저 프레임 시간이 개선됐다고 표현하지 않는다. 10년 원본 장기 보존은 [별도 저장 감사](PROPERTY_STORAGE_AUDIT_20260927.md)의 미해결 범위이며 UI 호스팅 이전으로 해결됐다고 간주하지 않는다.
