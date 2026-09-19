# 배포·운영 절차

> **과거 R2 구조 참고용입니다. 현재 실행 절차는 [Workers Static Assets 무료 배포](STATIC_DEPLOYMENT.md)를 따릅니다.** 2026-09-16에 Wrangler 인증을 완료하고 R2·D1·크론 없는 정적 배포로 전환했습니다. 아래의 리소스 생성·R2 활성화 명령은 현재 결제 없는 운영에 적용하지 않습니다. 수집기의 공개 목록 직접 변경도 제거했으며 검수 대기 후보만 생성합니다.

## 비밀정보와 리소스

키를 대화·Git·공개 JSON에 넣지 않습니다. `.dev.vars`는 로컬 Worker 개발용이며 Python은 프로세스 환경변수를 읽습니다. 공공데이터 키는 **Decoding 키**를 사용합니다. 인증키를 포함한 URL을 로그에 쓰지 않습니다.

| 이름 | 용도 |
|---|---|
| CLOUDFLARE_ACCOUNT_ID | 배포 대상 계정 식별자 |
| CLOUDFLARE_API_TOKEN 또는 Wrangler 로그인 | Worker·D1 리소스 관리 |
| R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY | 해당 R2 버킷으로 제한한 S3 업로드 자격 |
| R2_BUCKET | 공개 타일과 비공개 원본을 분리된 접두사에 저장할 버킷 |
| DATA_GO_KR_SERVICE_KEY | 승인된 TAGO 버스 위치·노선 API |
| KORAIL_SERVICE_KEY | 정기·대량 열차운행 수집 API |
| KMA_AUTH_KEY | 승인된 기상청 API허브 자료 |

## Cloudflare 연결

**현재 상태: 계정 미로그인, 원격 리소스·공개 URL 없음.** 기존 Chrome과 앱 브라우저에서 로그인 화면을 확인했습니다. 사용자의 결제 금지 조건에 따라 요금제 변경, 결제수단 등록, 유료 기능 활성화는 수행하지 않습니다. 아래 명령은 인증과 계정의 실제 무료 사용량을 확인한 후 사용하는 운영 절차입니다.

R2 Standard의 무료 범위는 월 10 GB-month, Class A 100만 회, Class B 1,000만 회이며 초과 사용은 과금 대상입니다. 저장량만 작다고 무조건 무료 운영이 되는 것은 아닙니다. 기존 계정의 다른 버킷 사용량과 버전 보관량까지 합산해야 합니다. 무료 범위 보장은 아직 검증되지 않았습니다. [Cloudflare 공식 요금 문서](https://developers.cloudflare.com/r2/pricing/) (2026-09-16 확인).

```powershell
npx wrangler login --scopes account:read user:read workers:write workers_scripts:write d1:write
npx wrangler whoami
npx wrangler r2 bucket create korea-replay-data
npx wrangler d1 create korea-replay-catalog
```

이미 같은 리소스가 있다면 재생성하지 않고 사용합니다. 출력된 실제 D1 ID와 R2 버킷 이름을 `wrangler.jsonc`의 `d1_databases`/`r2_buckets`에 넣습니다. 바인딩 이름은 각각 `DB`/`DATA`입니다. 현재 체크인된 설정에는 존재하지 않는 리소스 ID를 넣지 않았습니다.

배포 대상 확인 후 `vars.ENVIRONMENT`를 `production`으로 변경합니다. `npm run deploy:check`는 실제 바인딩과 검증된 업로드 계획이 없는 배포를 차단합니다. 이는 로컬 설정 검사이며 원격 인증·업로드 완료·요금 상태를 보증하지 않습니다. R2 S3 자격은 버킷에 한정하고 로컬 환경변수로만 전달합니다.

```powershell
npx wrangler d1 migrations apply korea-replay-catalog --remote
npx wrangler secret put DATA_GO_KR_SERVICE_KEY
```

## 데이터 준비·전환

```powershell
.venv\Scripts\python.exe -m pipeline.cli audit
.venv\Scripts\python.exe -m pipeline.publication prepare
.venv\Scripts\python.exe -m pipeline.publication upload
```

`prepare`는 `.local/deploy/upload-plan.json`에 파일·해시·총용량을 기록합니다. `upload`는 동일 해시의 파일을 건너뛰어 재개하고 기존 불변 경로에 다른 내용이 있으면 중단합니다. 업로드만으로 현재 공개 버전이 바뀌지 않습니다.

모든 파일이 검증된 뒤에만 `private/publication/<release>.staged.json` 완료 증빙을 조건부 생성합니다. 버킷·릴리스·카탈로그 해시와 전체 의존 파일 목록을 묶습니다. `promote`는 이 증빙과 원격 카탈로그 본문 해시, 각 파일의 크기·SHA 메타데이터를 다시 확인하고 현재 공개 목록을 조건부 교체합니다. 업로드 도중 종료된 버전은 공개로 전환할 수 없습니다. 전환할 때마다 의존 파일 수만큼 R2 HEAD 조회가 추가됩니다.

```powershell
.venv\Scripts\python.exe -m pipeline.publication promote --release <검증한-pub-버전>
npm run deploy
```

배포 후 `/api/v1/health`, `/api/v1/catalog`, 3D 타일, 지형, 날짜·지역·공유 복원과 브라우저 오류를 확인합니다. 실제 URL과 R2·D1 동작을 확인하기 전에는 배포 완료로 보고하지 않습니다.

## TAGO 수집

`config/tago.example.json`을 `.local/config/tago.json`으로 복사하고 실제 도시코드·노선 ID를 공식 API에서 조회하여 넣습니다. 확인하지 않은 임의 ID를 운영 설정에 넣지 않습니다. `enabled=true`로 설정한 파일을 R2의 `private/config/tago.json`에 업로드합니다.

```powershell
npx wrangler r2 object put korea-replay-data/private/config/tago.json --file .local/config/tago.json --remote
```

매분 수집기는 최대 지정 노선을 순환합니다. 노선 수가 많아지면 각 노선의 실제 재조회 간격도 늘어납니다. 초기 2노선/회 설정은 각 노선이 한 페이지일 때 하루 2,880회입니다. 페이지가 늘면 그만큼 추가됩니다. 개발 기본 한도 10,000회의 80%에서 새 요청을 중단합니다. 실제 승인 한도가 다르면 설정을 수정합니다.

D1 `collection_runs`에서 `status`, `accepted_count`, `rejected_count`, `reason`을 확인합니다. `observations`의 최신 `retrieved_at`과 신규 행 수, R2 원본, 공개 replay 파일을 함께 확인해야 ‘수집 중’으로 판단합니다. 키 미설정·비활성·실행 중·한도 소진은 성공과 구분합니다.

## 되돌리기

```powershell
.venv\Scripts\python.exe -m pipeline.publication promote --release <이전-pub-버전>
```

이전 버전은 R2에 이미 완전히 업로드되어 있어야 합니다. 전환 이력은 `.local/deploy/promotion-*.json`, 수집기 공개 이력은 D1 `publication_history`에 남깁니다. 수집기를 잠시 멈추려면 노선 설정의 `enabled`를 false로 바꾼 뒤 같은 비공개 경로에 다시 올립니다.

이전 릴리스의 불변 데이터와 원격 완료 증빙을 함께 보존해야 되돌릴 수 있습니다. 보존 정책이나 용량 정리를 이유로 임의 파일 삭제를 수행하지 않습니다.

## 알려진 운영 제한

- 실제 Cloudflare 계정 연결과 공개 URL 검증은 아직 남아 있습니다.
- 로컬 테스트는 D1과 동일 SQL을 SQLite에서 실행하지만 Cloudflare 원격 런타임을 대신하지 않습니다.
- 건물은 원천 높이·층수 추정·GHSL 격자 추정·높이 미확인을 구분합니다. 현재 처리 범위는 자동 생성되는 `docs/COVERAGE.md`를 확인합니다. 3개 우선 지역도 사진 기반 정밀 모델은 아닙니다.
- 지형은 총 9,620개 타일로 L12 상세 영역을 포함합니다. 원천 해상도와 건물 높이 정확도는 별개입니다.
- 로컬 `npm run dev`는 나중에 생성된 데이터도 동적으로 읽습니다. 빌드 산출물만 정적 서버로 열면 R2 자료가 자동으로 포함되지 않습니다.
- KMA·KORAIL 정기 수집의 실제 승인·응답·관측 검증이 필요합니다.
- 데이터 수집이 시작되기 전 시각의 버스 기록을 생성하지 않습니다.
