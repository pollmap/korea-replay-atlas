# KOREA REPLAY

대한민국의 지역·아파트 거래를 빠른 2D 지도에서 탐색하고, 같은 위치의 기존 3D 지형·건물·교통·기상 화면으로 전환하는 공개 소스 웹 프로젝트입니다. 특정 지역을 기본 서비스의 중심으로 두지 않으며, 확보한 자료와 미수집 자료를 구분합니다.

지역 거래량 → 지역 비교 → 단지·계약월·면적 → 실거래 차트와 원문 표가 기본 흐름입니다. 거리·면적 측정, 행정경계 전환, 기기 내 즐겨찾기, 공유, 패널 접기를 지원합니다. `F`는 지도 집중, `Esc`는 복귀, `/`는 검색입니다. 회원가입 없이 탐색합니다.

2026-09-20 확장판은 통합 검증 중입니다. 새 Pages 프로젝트 `korea-replay`와 `korea-replay-data`를 확보했으며 기존 공개 3D Worker와 자료는 보존했습니다. [구현·자료·성능 상태](docs/ATLAS_IMPLEMENTATION.md)와 [Pages 배포 절차](docs/PAGES_DEPLOYMENT.md)에서 실제 공개 여부를 구분합니다. 모든 건물의 정확성, 전국 60개월 수집, 10배 성능 개선을 완료했다고 주장하지 않습니다.

## 실행

9월 20일 추가 개발: 첫 건물 목록을 4,307노드에서 96노드로 나누고, 도로·시설의 GPU 준비를 장면당 2개로 제한했습니다. 준비 중 도형이 숨김 캐시에서 다음 로딩을 막는 경로도 수정했습니다. 건물 부품 16건과 공식 도시철도 16노선 설정을 반영한 후보의 공개 전환은 별도 검증합니다. [현재 후보와 측정 범위](docs/STREAMING_AND_SUBWAY_EXPANSION.md)

Node.js 24, Python 3.13을 사용해 검증했습니다.

```powershell
npm ci
npm run dev
```

브라우저에서 `http://127.0.0.1:5173/`을 엽니다. 이 작업 폴더에는 이미 수집한 공개 데이터가 있습니다. 새 체크아웃에는 지도 자료가 없으므로 [검증된 배포 백업 복원](docs/RECOVERY_AND_CLEANUP.md) 또는 아래 전국 데이터 처리를 별도로 수행합니다. 새 수집·재가공을 위한 Python 환경은 다음과 같이 준비합니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

지도에서 지역을 선택하고 날짜·시간 슬라이더를 움직입니다. 햇빛 실험은 임의 날짜에 쓸 수 있습니다. 기록 재생은 공개된 실제 기록의 기간을 사용합니다. 건물을 클릭하면 높이의 출처·추정 여부가 나타납니다.

전국 건물은 큰 원본을 한 번에 모델링하지 않고 국가 경계로 분할해 처리합니다. 아래 명령은 시간이 걸리는 실제 데이터 작업이며, 완료 셀은 재개 시 건너뜁니다. 디스크 여유 30GiB를 유지합니다.

```powershell
.venv\Scripts\python.exe -m pipeline.cli terrain --region korea --max-level 8
.venv\Scripts\python.exe -c "from pipeline.buildings import extract; extract('korea')"
.venv\Scripts\python.exe -m pipeline.national partition
.venv\Scripts\python.exe -m pipeline.national process --workers 3
.venv\Scripts\python.exe -m pipeline.national_terrain --level 12 --workers 3
.venv\Scripts\python.exe -m pipeline.cli depth
.venv\Scripts\python.exe -m pipeline.cli audit
.venv\Scripts\python.exe -m pipeline.building_identity_audit
.venv\Scripts\python.exe -m pipeline.coverage_report
node scripts/validate-glb.mjs
```

도로·항만·공항·산업 용지의 추출·등록은 [전국 기반 시설](docs/INFRASTRUCTURE.md), 기상 프레임은 [기상 자료](docs/WEATHER.md), 공식 건물 자료 보강은 [원천 업그레이드](docs/NATIONAL_DATA_UPGRADE.md)를 따릅니다. 지도 검색은 현재 5만 개 이름 색인을 사용하며, 모든 주소·건물 이름을 검색하는 서비스는 아닙니다.

## 검증

```powershell
npm run typecheck
npm test
npm run lint
npm run build
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pipeline.static_release --catalog .local/performance-20260917/catalog.json
```

테스트용 입력은 `tests/`에만 있습니다. 공개 데이터 폴더에 모의 관측을 적재하지 않습니다. 테스트 통과와 실제 원천 정확성·운영 환경 검증은 별개입니다.

2026-09-19 웹 541개 테스트·38개 파일, Python 394개 검사와 타입·린트·프로덕션 빌드가 통과했습니다. 중앙 브로커 등 새 로컬 코드의 검증과 공개 배포 여부는 구분합니다. 실제 명령·데이터·브라우저 검증 결과와 미완료 항목은 [검증 기록](docs/VALIDATION.md)에 정리했습니다. 공개판의 화면·Worker 전송 결과는 [지도 집중 검증](docs/MAP_FIRST_PERFORMANCE.md), 이전 013966a1 판의 초기 렌더 CPU·라벨 생성 결과는 [당시 라벨 성능 기록](docs/LABEL_RENDERING_PERFORMANCE.md)에 있습니다. 이번 변경의 전경 FPS 개선과 전국 P95 33ms·모든 생성 8ms 달성은 아직 입증하지 못했습니다.

## 자료와 배포

- `.local/raw`: 원본, 출처, 조회 시각, SHA-256. 비공개이며 배포하지 않습니다.
- `.local/silver`: 정규화 중간 자료.
- `.local/audit`: 제외·미연결 기록과 검증 보고서.
- `public/data`: 공개 가능한 타일·외곽·기록과 버전별 카탈로그.
- `worker/`: 조회 API와 TAGO 예약 수집기.
- `migrations/`: D1 수집 대장·호출량·관측·배포 이력.

새 확장판은 **Cloudflare Pages 무료 프로젝트 두 개**에 앱·기존 3D 자료와 2D·부동산 자료를 나눠 게시합니다. [Pages REST 전용 절차](docs/PAGES_DEPLOYMENT.md)를 따르며 기존 `npm run deploy`는 레거시 Workers 버전 업로드 명령입니다. 결제 플랜·R2·유료 러너를 활성화하지 않습니다. 자체 코드는 [MIT](LICENSE), 데이터와 외부 라이브러리는 [각 이용조건](THIRD_PARTY_NOTICES.md)을 적용합니다.

로컬 개발 API는 Worker의 같은 요청 처리 함수를 Node에서 실행합니다. 이 Windows 환경에서 workerd 실행이 EPERM으로 차단되어 도입한 개발용 경로이며, Cloudflare 실제 런타임 검증을 대신하지 않습니다.

## 출처 표시

OpenStreetMap contributors / ODbL, Overture Maps 및 원천 제공자, Mapzen Terrain 원천 SRTM·GMTED·ETOPO, Natural Earth / public domain, European Commission JRC GHS-BUILT-H / CC BY 4.0, 서울교통공사 / 공공누리 1유형, 한국철도공사·국토교통부 TAGO·기상청의 개별 이용 조건을 따릅니다. 소스별 설명과 링크는 `shared/sources.ts` 및 앱의 ‘데이터와 출처’에서 확인합니다.

기록 재생의 위성 자료는 2022-01-01 기상청 공식 예제이며, **현재 관측**은 별도로 승인·연결한 천리안2A 최신 적외영상입니다. 최신 위성·레이더는 지도와 범례를 포함한 원본 패널로 제공하며 지도에 정합한 오버레이나 숫자 강우량으로 표현하지 않습니다. [현재 기상 조회](docs/LIVE_WEATHER.md)와 [기록 기상 자료](docs/WEATHER.md)에 각각의 범위를 정리했습니다.

> 공개 소스에서는 과거 개인 계정의 배포 호스트를 `legacy.example.invalid`로 대체했습니다. 버전·측정 이력은 당시 기록이며 현재 서비스 상태와 구분합니다. 실제 과거 URL은 비공개 배포 증빙에 보존합니다.
