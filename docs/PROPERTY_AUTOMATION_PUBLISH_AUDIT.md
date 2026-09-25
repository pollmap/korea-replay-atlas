# 부동산 자동 게시 구현 감사

기준: 2026-09-26 로컬 코드·복원된 장부의 읽기 전용 검사. 원격 실행, 자격증명
조회·출력, 배포 및 다른 소스 파일 변경은 하지 않았다. 이 문서는 구현 완료 보고가
아니라 **현재 실행 가능한 단위와 자동 게시를 막는 실제 연결 지점**을 구분한다.

같은 날 감사 후 구현 반영: `property_point_join`의 기간 하드코드를 제거했고,
manifest 최신 완료월에서 정확한 12개월을 검증한다. 위치·기본정보 조회는
release별 등록/검증/캐시로 바뀌었으며 신규 `pipeline.property_navigation`은
명시적 입력·출력과 불변 파일 검사를 지원한다. 아래 단일 release·하드코드
설명은 **감사 당시 결함**이다. 이 세 수정만으로 아래 전체 자동 게시 경로가
완성되지는 않는다. 대형 앱 묶음, 증분 게시, 파일 한도와 원격 운영 검증은 남아 있다.

## 결론

수집 자동화는 검증된 private D1 head까지 진행한다. 공개 데이터 생성기와 Pages
후보/승격/검증 함수는 이미 존재하므로 새 서비스를 처음부터 만들 필요는 없다.
다만 이들을 그대로 cron으로 이어 붙이면 다음 문제가 발생한다.

1. 새 부동산 release에서 단지 위치·기본정보가 사라진다. 현재 앱은 특정 release의
   navigation/facts만 포함하며, 버전이 다르면 의도적으로 표시하지 않는다.
2. 서울 단지 연결 생성기의 계약월이 202509–202608로 고정되어 다음 달에 실패한다.
3. 기존 앱 배포 경로가 대용량 3D 정적 묶음 전체와 30 GiB 디스크 여유를 요구한다.
4. 10년 전국 완료 시 현재 지역·월별 JSON 구성은 Pages 파일 한도를 초과할 수 있다.
5. 공개 생성기는 성공/빈 결과의 모든 원본 페이지를 매번 다시 파싱한다. 아직 증분
   게시 캐시나 게시용 선택 복원 orchestrator가 없다.

인증키만 등록하면 전체 공개 갱신까지 끝나는 상태가 아니다. 아래 순서로 실제
연결 코드를 추가해야 노트북을 끈 상태의 자동 갱신을 주장할 수 있다.

## 현재 존재하는 실행 단위

| 단계 | 실제 코드 | 현재 결과와 주의점 |
| --- | --- | --- |
| 최신 원격 장부 읽기 | `RemoteWorkspace` / `load_manifest` | head·manifest·checkpoint·registry를 선택 복원한다. `hydrate(names)`로 필요한 파일만 추가 복원 가능하다. |
| 전체 복원 | `pipeline.real_estate_archive.restore` | 모든 과거 raw/snapshot/run 파일까지 복원한다. 게시 작업에 매번 사용할 필요는 없다. |
| 거래 후보 생성 | `pipeline.real_estate_publish.publish` | 모든 jobs를 읽고 완료·빈 결과의 raw와 snapshot을 대조한다. 지역·월 JSON, 단지 목록, manifest, publication receipt 생성. 공개 포인터를 바꾸지 않는다. |
| 단지 연결 | `pipeline.property_point_join.build` | 서울 자료 + MOLIT 도로주소/이름/단지 ID 연결. 원천 좌표의 정확성이 검증됐다는 뜻은 아니다. 고정 기간·원본 SHA 제약이 남아 있다. |
| navigation 생성 | `scripts/build-property-map-index.mjs` | 특정 GeoJSON 파일명/SHA를 읽어 단일 release navigation·manifest를 `src/data`에 작성한다. 입력 CLI 인자가 없다. |
| 단지 facts 생성 | `pipeline.property_facts.build` | navigation의 SHA·release·원천 연결을 검증하고 공개 필드 allowlist만 출력한다. 새 release navigation으로 반드시 재생성해야 한다. |
| 데이터 묶음 | `stagePagesData` | map/property publication 두 개의 **전체 선언 파일**을 검증·합성해 불변 atlas와 data 후보를 만든다. |
| 데이터 업로드 | `deployPagesStage` | 누락 content hash만 업로드한다. 업로드 캐시가 있어도 로컬 파일 전체 hash 검사·manifest는 필요하다. |
| 앱 묶음 | `frontend_release` → `stagePagesApp` | 기존 immutable client/worker 묶음 전체가 필요하다. 앱의 data origin + manifest SHA를 실제 반환된 data URL에 고정한다. |
| 후보 확인 | `verifyPagesRemote` | runtime, 앱 JS/CSS, index/catalog 또는 data atlas, CORS, 404를 확인한다. 데이터 참조 전체·화면·롤백 검사는 별도다. |
| 공개 전환 | `stagePagesApp(snapshotOrigin, candidateReceiptPath)` → production upload | 같은 후보 artifact와 원격 검증 receipt를 요구한다. 후보와 생산 코드/데이터가 달라지면 거부한다. |
| 복구 | `createPagesApi().rollback(project,id)` | 기존 production 배포로 돌리는 API가 있다. CLI에 rollback 명령은 아직 없다. |

`pipeline.publication`은 기존 지도 자료 게시 파이프라인이다. 거래 후보는
`pipeline.real_estate_publish`가 담당한다. 이름이 비슷하다고 서로 대체하면 안 된다.

## 최소 자동 게시 흐름

수집은 여러 번 실행하되, 게시는 초기에는 하루 한 번만 시도하는 것이 합리적이다.
다음 실행의 API 성공과 공개 후보 성공을 별도로 기록한다. 수집 실패가 기존 공개
release를 덮어쓰지 않도록 publisher는 항상 하나의 검증된 head SHA를 입력으로 고정한다.

1. **입력 고정 및 변경 판정:** 최근 정상 앱/data 배포 ID·origin·receipt SHA와
   private head를 읽는다. 공개 jobs fingerprint가 이전 게시와 같으면 종료한다.
   private head는 retry 장부만 바뀌어도 바뀔 수 있으므로 head 차이만으로 재배포하지 않는다.
2. **선택 복원:** checkpoint의 complete/empty jobs에서 현재 pages/snapshot descriptor를
   모으고, registry와 함께 hydrate한다. 공유 pack은 object SHA별 한 번 다운로드하도록
   게시 전용 복원 계획을 만든다. 현재 `RemoteWorkspace`는 마지막 pack 하나만 캐시한다.
3. **정규화·후보 생성:** 기존 `publish`를 호출한다. 디스크 사전 검사 후 명시적
   reserve를 주입하고, 누락·해시 불일치·메모리/파일 한도 초과면 공개 전환을 금지한다.
4. **동일 release의 UI 보조 자료:** point join → navigation → facts를 재생성한다.
   새 release 참조 목록에 모두 넣고 기존 release 항목을 보존한다. 이름만으로 재연결하거나
   단순히 기존 JSON의 release 문자열만 바꾸면 안 된다.
5. **정적 후보:** 기존 검증된 map publication + 새 property publication으로 data 후보를
   생성·업로드한다. 실제 반환된 불변 URL과 atlas SHA를 앱 후보에 고정한다.
6. **게이트:** runtime/전체 참조 closure/대표 단지의 매매·전월세·과거 이력/위치·facts/
   현재 월과 과거 공유 링크/누락 404/비밀정보 검사를 통과시킨다. 기존 정상 범위를
   잃으면 후보 실패로 처리한다. 원천 취소·정정에 따른 거래 수 감소는 무조건 오류로
   보지 않고 새 snapshot 근거와 함께 판정한다.
7. **공개 전환:** 기존 검증 함수가 만든 후보 receipt로만 production을 만든다.
   production 검증 실패 시 직전 정상 앱 배포로 rollback한다. 앱 policy에 불변 data가
   함께 고정되어 있으므로 이 방식은 코드와 데이터를 함께 복구한다.
8. **성공 장부:** private head, property/atlas release, 앱 artifact, 실제 배포 ID,
   검증 결과·시각·이전 정상 ID를 작은 운영 receipt로 보관한다. raw는 공개하지 않는다.

기존 명령/함수의 실제 호출 형태는 다음과 같다. 대괄호 부분은 검증한 산출물에서
계산하는 값이며, 지금 그대로 실행하라는 명령이 아니다.

```text
python -m pipeline.real_estate_publish --root [hydrated-checkpoint] \
  --regions [hydrated-registry.json] --output [.local/property-candidates]
python -m pipeline.property_point_join --collection [verified-seoul-source] \
  --checkpoint [hydrated-checkpoint] --property-root [candidate/data/property/release] \
  --points [original-audited-points.geojson] --output [new-points.geojson]
# 현 build-property-map-index는 입력/출력 경로를 파라미터화한 후 사용해야 한다.
python -m pipeline.property_facts --collection [verified-seoul-source] \
  --navigation [new-navigation.json] --manifest [new-navigation-manifest.json] \
  --output [new-facts.json]
node scripts/pages-release.mjs data [data-options.json]
node scripts/pages-api.mjs upload [data-receipt.json] --preview
node scripts/pages-api.mjs verify [data-receipt.json] [actual-data-origin]
node scripts/pages-release.mjs app [app-candidate-options.json]
node scripts/pages-api.mjs upload [app-candidate-receipt.json] --preview
node scripts/pages-api.mjs verify [app-candidate-receipt.json] [actual-app-origin]
# 추가 데이터/UI 게이트 성공 후 snapshotOrigin/candidateReceiptPath를 넣어 production stage
node scripts/pages-release.mjs app [app-production-options.json]
node scripts/pages-api.mjs upload [app-production-receipt.json] --production
node scripts/pages-api.mjs verify [app-production-receipt.json] https://korea-replay.pages.dev
```

첫 publish CLI는 현재 30 GiB reserve를 바꾸는 옵션이 없다. 표준 runner용 orchestrator는
`publish(..., reserve_bytes=검사한_운영값)` 함수를 호출하거나 검증된 CLI 옵션을 추가해야
한다. 디스크 검사를 제거하는 방식은 사용하지 않는다.

## release와 달 변경의 구체적 수정 지점

- `src/property-map-points.ts`: `manifest.release_id`와 요청 release가 다르면 null을 반환한다.
  단일 global pending 캐시를 release별로 분리하고, 검증된 release→URL/SHA 목록을 사용해야 한다.
- `src/ApartmentFacts.tsx`: 빌드에 포함된 한 개 `seoul-apartment-facts.json`만 읽는다.
  새 release별 facts를 연결하지 않으면 단지 기본정보가 조용히 사라진다. 이전 공유 버전도
  같은 release 자료를 읽어야 하며 최신으로 대체하면 안 된다.
- `scripts/build-property-map-index.mjs`: 기존 고정 파일명·SHA와 단일 출력 대신 입력
  GeoJSON/출력 디렉터리/예상 source SHA를 받게 하고 release 일치 검사는 유지해야 한다.
- `pipeline/property_point_join.py`: `_sale_rows(...start='202509',end='202608')`,
  `recent_sale_markers`의 end/start, 300개 complete 요구가 고정되어 있다. 새 manifest의
  최신 완료 계약월에서 12개월 범위를 계산하되, 새 달의 모든 원천이 아직 없으면 기존
  검증된 식별자 연결을 출처와 함께 보존하고 새로 확인되지 않은 연결만 보류해야 한다.
  빈 결과(`empty`)를 정상 완료로 인정할지 역시 데이터 계약/테스트로 명시해야 한다.
- 기존 point builder는 원본 provider point를 요구한다. 이미 결합된 과거 point를 그대로
  입력으로 재사용하면 제거된 매칭 속성의 잔존 위험이 있으므로 매번 원본에서 생성한다.
- `shared/property-view.ts`의 기본 월은 이미 `period.latest_complete_month`를 사용한다.
  UI에 202608을 고정한 것은 아니다. 다만 새 달이 원천 미수집이면 기본 화면이 미수집이
  될 수 있으므로 **달력상 최신 완료월**과 **실제 수집 완료월**을 구분해 안내해야 한다.
  이전 공유 링크의 명시적 month를 임의로 최신으로 바꾸면 안 된다.
- `pipeline/frontend_release.py`: point SHA/filename/navigation SHA allowlist도 현재 고정이다.
  새 데이터를 허용하려고 검사를 없애지 말고 검증된 release manifest 입력 계약을 추가한다.
- 3D의 2026-08-19 지형·건물 dataset 버전은 다른 의미다. 거래월 rollover 때문에 3D를 변경하지 않는다.

## 17,051개 입력과 증분 처리의 경계

읽기 전용으로 확인한 로컬 원격 복원 장부는 complete 8,077 / empty 288 / failed 1 /
pending 53,586이다. 현재 `publish.verify_snapshot`이 요구하는 고유 descriptor는
**raw 8,686개 + snapshot 8,365개 = 17,051개, 2,712,454,236 bytes**다.
이 수치는 해당 로컬 head 사본 기준이며 이후 수집으로 달라진다.

모든 과거 archive 파일을 복원할 필요는 없다. 하지만 **현 publisher를 그대로 사용하면
현재 공개 대상의 17,051개 파일은 필요하며, raw 8,686개를 재파싱**한다. run 로그,
superseded snapshot, 참조하지 않는 과거 raw까지 복원하는 full restore는 불필요하다.

증분 생략을 하려면 이전에 검증한 정규화 산출물의 입력 raw SHA 목록, snapshot SHA,
normalizer 정책/코드 버전, registry SHA를 키로 한 검증 receipt가 먼저 있어야 한다.
단순히 “snapshot이 있으니 raw 검증 생략”은 현재 재현성 계약을 약화한다. 초기 버전은
하루 한 번 selective hydrate + 전체 재현성 검사로 시작하고 실제 시간·메모리·D1 reads를
측정한 뒤 캐시를 도입하는 것이 가장 적은 변경으로 검증 가능한 경로다.

## 무료 runner와 저장·파일 예산

현재 `D1Archive`는 압축 객체를 48 KiB chunk로 나누고, 한 번에 30행을 읽는다.
선택 복원의 D1 reads 예산은 파일 수가 아니라 고유 object별 chunk 수 + manifest/checkpoint
읽기로 계산해야 한다. 공유 pack 중복 다운로드를 제거하지 않으면 이 예산이 커진다.
복원·공개 생성은 D1 object를 추가 쓰지 않아도 된다. 수집/운영 장부 쓰기와 별도로
계측하고, 같은 계정의 다른 작업까지 합친 실제 플랫폼 예산을 확인해야 한다.

코드에는 shard당 380 MiB 보수적 cap이 있고, 일일 쓰기 한도 오류를 그대로 실패 처리한다.
새 publisher에서 이 cap을 늘리거나 과금 기능을 활성화하지 않는다. 이미 확인된
무료 플랫폼 한도와 코드의 보호값을 혼동하지 말고 실제 runner `disk free`, 최대 RSS,
경과 시간, D1 read/write 행수, 출력 파일 수를 run receipt에 기록한다.

현재 앱 stage는 기존 static bundle 전체를 로컬에서 재검증한다. GitHub 소스 checkout에는
이 대형 `.local` 묶음이 없다. 자동게시 전에 다음 중 하나가 필요하다.

1. **초기 검증 경로:** 이전 공개 배포의 검증 manifest·immutable URL과 비밀정보 없는
   배포 receipt로 파일을 선택 복원한다. 기존 Pages asset hash로 업로드는 재사용해도
   현재 stage 코드상 로컬 hash 검사는 남는다. 실제 디스크/시간 예산 통과가 전제다.
2. **지속 운영 경로:** 검증된 이전 asset manifest의 불변 항목과 새 frontend/policy만
   합성하는 작은 앱 stage를 구현한다. 기존 hash가 Pages에서 누락되면 원래 immutable
   origin에서 해당 파일만 가져와 SHA 확인 후 복구한다. 이는 현재 없는 기능이며,
   receipt 서명/출처·asset closure·누락 복구·후보/생산 동등성 검사가 필요하다.

3D 타일을 다시 생성할 이유는 없다. 기존 자산을 불변 참조로 재사용하는 배포 개선이다.
표준 runner의 실제 디스크 예산이 기존 전체 stage를 감당하지 못하면 위 2번 구현이
선행되어야 하며, 무료 runner라고 단정하고 30 GiB 보호조건을 무조건 없애면 안 된다.

`real_estate_publish`는 파일 18,000개에서 실패하고 Pages stage는 20,000개/25 MiB를
검사한다. 121개월×256지역은 월 파일만 최대 **30,976개**이며 지역 목록·단지 목록·지도
파일은 별도다. 한 달에 거래가 0건이어도 완료 partition의 빈 배열 파일이 생성될 수 있다.
따라서 10년 전국 완료 전, 작은 월들을 읽기 예산 내의 분기/연간 shard로 묶거나
검증된 다중 data origin 계약을 추가해야 한다. 과거 월이나 지물을 삭제해 한도를 맞추면 안 된다.

## 실패 시 공개 유지와 남은 완료 조건

- source/restore/hash/정규화/point/facts/빌드/후보 검증 중 하나라도 실패하면 production
  요청을 보내지 않는다. 현재 대표 주소는 이전 검증본을 계속 제공한다.
- deployment POST의 응답이 불확실하면 자동 재시도하지 않는다. 기존 코드 원칙대로
  읽기 전용 배포 상태 확인 후 같은 artifact의 성공 여부를 판단한다.
- 성공한 data 후보만 있고 앱 후보가 실패한 경우에도 기존 앱의 pin은 바뀌지 않는다.
- production 검증 실패 시 직전 정상 production ID로 앱 rollback 후 runtime의 data pin과
  대표 단지·과거 공유 링크를 다시 검증한다. 새 후보나 기존 사용자 원본은 삭제하지 않는다.
- concurrent publication에는 collection과 별도의 단일 publisher lock과 최신성 검사가
  필요하다. 오래 걸린 과거 head 후보가 더 최신 공개 release를 덮어쓰면 안 된다.
- 영구 토큰 등록, 최초 실제 scheduled collection 성공, public candidate 성공, production
  검증, 자동 다음 주기 성공, rollback 실증을 각각 기록해야 한다. 단위 테스트 통과나
  workflow 파일 존재만으로 어느 단계도 대신하지 않는다.

이 감사의 산출물은 문서 하나다. multi-release UI/생성기/증분 stage/게시 workflow 자체는
여기서 구현하지 않았으며, 원격 상태 확인 또는 배포 성공을 주장하지 않는다.
