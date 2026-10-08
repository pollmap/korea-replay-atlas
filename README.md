# KOREA REPLAY

**현재는 지정 9개 권역의 아파트 2D 탐색에 집중하며 오피스텔은 후속 단계로 미룹니다.** 검색 → 지도에서 단지 선택 → 면적·기간별 실거래 → 비교 → 관심 단지 저장을 핵심 흐름으로 둡니다. 거래 이력 확보 목표는 **연속 20년(240개 완료 계약월과 당월 잠정 별도)**입니다. 전체 10배 성능 향상 목표는 2026-09-27에 제외했으며 실제 응답 시간·오류·캐시 상한은 계속 검증합니다.

**현재 집중 범위는 경기·서울·인천 → 대전·세종·청주·천안·아산 → 부산 순서입니다.** 다른 지역의 신규 수집·확장은 보류하고 기존 자료를 보존합니다. 데이터 연결 전에도 단지정보·주변의 화면과 필터를 먼저 제공합니다.

**공개 서비스: [korea-replay.pages.dev](https://korea-replay.pages.dev/)** · [제품 범위](docs/PRODUCT_SCOPE.md) · [기능별 인수 기준](docs/PROPERTY_PARITY_ACCEPTANCE_20260927.md) · [지역별 확보 순서](docs/PROPERTY_ACQUISITION_PRIORITY.md)

2026-09-27 확인한 공개판은 서울·인천·경기·대전·세종·충남·충북의 상세 2D 건물·도로와 기존 아파트 실거래 자료를 제공합니다. 지역 선택·경계 강조·국평 필터·전용 평당가·단지 비교·관심 저장·외부 길찾기·지도 집중을 지원합니다. 3D 렌더러·태양 계산·메시·입체 지형 생성은 폐기했습니다. 2D 건물 외곽선·도로·경계와 원천 자료는 유지합니다. 과거 Git 이력과 종료 명령의 범위는 [3D 생성 종료](docs/THREE_D_GENERATION_RETIREMENT_20261009.md)에 기록합니다.

2026-09-28 최신 검증된 앱 artifact는 `1f4d796c585e6fd2bc2a031685ad3cfad4e176fa5143e8146f59ce8ecb2886c2`, snapshot은 `https://7d55c901.korea-replay.pages.dev`입니다. [PR #50](https://github.com/pollmap/korea-replay-atlas/pull/50)까지 병합·공개 검증됐습니다. 지정 9개 권역의 주변 시설 기록과 `property-87d1c67336e97209`의 2,276,757개 거래 행을 제공하며, 자료 누락·미확인 단지 위치는 별도로 표시합니다. 이후 서버 수집 성공과 공개 게시를 혼동하지 않습니다. [주변 시설·거래 보존](docs/NEARBY_FACILITIES_RELEASE_20260928.md), [상세 지도 검증](docs/CAPITAL_DETAIL_RELEASE_20260927.md), [Pages 운영](docs/PAGES_DEPLOYMENT.md).

**전체 제품 완료 또는 경쟁 서비스와 모든 기능이 동등하다는 판정은 아직 아닙니다.** 거래 이력은 게시된 지역·월만 보여주며, 20년 선택 기능이 수집 완료를 뜻하지 않습니다. 매매는 공식 공개체계 2006-01 이후, 전월세 확정일자는 2011-01 이후입니다. 원천 제공 전·미수집·실패·실제 0건을 구분합니다. [원천과 이용조건](docs/PROPERTY_SOURCE_RIGHTS_20260927.md). 오피스텔 공개 연결, 단지 좌표와 상세정보의 전국 검증, 공급·인구·학군 및 연속 장기 이력은 남은 인수 항목으로 관리합니다.

현재 자동 수집은 프로젝트 전용 VPS의 `collector` 서비스에서 실행합니다. 노트북·채팅을 꺼도 체크포인트에서 이어가며, 서비스별 일일 호출 예산과 작업별 출력·임시 사용량 예산을 지킵니다. 공개 후보 생성 중에는 대량 작업 잠금에서 기다린 뒤 자동 재개합니다. 인증 오류·공간 부족은 운영 확인이 필요한 중단 상태입니다. 수집 자동화와 검증된 새 자료의 공개 전환은 별개입니다. 소스 변경·수집 성공·새 자료의 공개 게시가 각각 검증돼야 합니다. 원본 대량 저장의 용량 제약은 [저장 감사](docs/PROPERTY_STORAGE_AUDIT_20260927.md)에 기록하며, 무료 예산을 넘는 경우 이전 검증본을 유지합니다. [자동 수집](docs/PROPERTY_AUTOMATION.md), [자동 게시 감사](docs/PROPERTY_AUTOMATION_PUBLISH_AUDIT.md).

과거 릴리스와 성능 측정은 [릴리스 기록](docs/ATLAS_RELEASE_STATUS.md)에 보존합니다. 특정 장면의 프레임 측정을 모든 지역·회선·기기에서의 성능 보증으로 확대하지 않습니다.

## 실행

이전 3D 성능·건물 부품·도시철도 실험은 [과거 기록](docs/STREAMING_AND_SUBWAY_EXPANSION.md)으로 남습니다. 현재 실행·설치·배포에는 3D 생성기와 전용 패키지가 필요하지 않습니다.

Node.js 24, Python 3.13을 사용해 검증했습니다.

```powershell
npm ci
npm run dev
```

브라우저에서 `http://127.0.0.1:5173/`을 엽니다. 대용량 지도 자료는 Git에 포함하지 않습니다. 현재 PC 2D 개발·배포는 [경량 Pages 운영](docs/LEAN_STORAGE_OPERATIONS.md)을 따릅니다. 폐기한 3D 전체 묶음을 복원하거나 전국 건물·지형을 다시 생성할 필요가 없습니다. 새 수집·재가공을 위한 Python 환경은 다음과 같이 준비합니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

현재 기본 흐름은 지역·단지 검색 → 전용면적·기간 선택 → 실거래 차트·표 → 비교입니다. 2D 화면 개발에는 기존 검증 자료를 재사용합니다. 과거 3D 처리 절차는 기록 문서로 남기며 일반 개발·배포 단계에서 실행하지 않습니다.

도로·항만·공항·산업 용지의 추출·등록은 [전국 기반 시설](docs/INFRASTRUCTURE.md), 기상 프레임은 [기상 자료](docs/WEATHER.md), 공식 건물 자료 보강은 [원천 업그레이드](docs/NATIONAL_DATA_UPGRADE.md)를 따릅니다. 지도 검색은 현재 5만 개 이름 색인을 사용하며, 모든 주소·건물 이름을 검색하는 서비스는 아닙니다.

## 검증

```powershell
npm run typecheck
npm test
npm run lint
npm run build
.venv\Scripts\python.exe -m pytest -q
```

테스트용 입력은 `tests/`에만 있습니다. 공개 데이터 폴더에 모의 관측을 적재하지 않습니다. 테스트 통과와 실제 원천 정확성·운영 환경 검증은 별개입니다.

2026-09-20 [PR #5 병합 후 CI](https://github.com/pollmap/korea-replay-atlas/actions/runs/35470195554)에서 웹 815개·55파일, Python 819개(경고 4개), 타입·린트·프로덕션 빌드·공개 소스/이력 패턴 감사가 통과했습니다. 이는 현재 소스의 검사이며 새 후보의 공개 승격을 뜻하지 않습니다. 공개 환경의 최신 검증은 [릴리스 현황](docs/ATLAS_RELEASE_STATUS.md)에 있습니다. 이전 [검증 기록](docs/VALIDATION.md), [지도 집중 검증](docs/MAP_FIRST_PERFORMANCE.md), [라벨 성능 기록](docs/LABEL_RENDERING_PERFORMANCE.md)은 각각 당시 버전과 조건의 결과입니다. 전국 이동 P95 33ms·모든 생성 작업 8ms 달성을 입증한 자료로 합산하지 않습니다.

## 자료와 배포

- `.local/raw`: 원본, 출처, 조회 시각, SHA-256. 비공개이며 배포하지 않습니다.
- `.local/silver`: 정규화 중간 자료.
- `.local/audit`: 제외·미연결 기록과 검증 보고서.
- `public/data`: 공개 가능한 타일·외곽·기록과 버전별 카탈로그.
- `worker/`: 조회 API와 현재 관측용 비공개 중앙 브로커.
- `migrations/`: 이전 D1 수집 대장 설계. 현재 무료 Pages 운영의 필수 구성이나 활성화 완료 항목이 아닙니다.

공개판은 **Cloudflare Pages 무료 프로젝트**에 앱과 2D·부동산 정적 자료를 나눠 게시합니다. 수집·조회는 프로젝트 전용 VPS 서비스에서 실행하므로 노트북을 꺼도 게시된 지도와 서버 수집을 이용할 수 있습니다. 수집과 새 자료의 공개 전환은 각각 검증합니다. [Pages REST 전용 절차](docs/PAGES_DEPLOYMENT.md)를 따르며 기존 `npm run deploy`는 레거시 Workers 버전 업로드 명령입니다. 결제 플랜·R2·유료 러너는 활성화하지 않았습니다. 자체 코드는 [MIT](LICENSE), 데이터와 외부 라이브러리는 [각 이용조건](THIRD_PARTY_NOTICES.md)을 적용합니다.

로컬 개발 API는 Worker의 같은 요청 처리 함수를 Node에서 실행합니다. 이 Windows 환경에서 workerd 실행이 EPERM으로 차단되어 도입한 개발용 경로이며, Cloudflare 실제 런타임 검증을 대신하지 않습니다.

## 출처 표시

OpenStreetMap contributors / ODbL, Overture Maps 및 원천 제공자, Mapzen Terrain 원천 SRTM·GMTED·ETOPO, Natural Earth / public domain, European Commission JRC GHS-BUILT-H / CC BY 4.0, 서울교통공사 / 공공누리 1유형, 한국철도공사·국토교통부 TAGO·기상청의 개별 이용 조건을 따릅니다. 소스별 설명과 링크는 `shared/sources.ts` 및 앱의 ‘데이터와 출처’에서 확인합니다.

기록 재생의 위성 자료는 2022-01-01 기상청 공식 예제이며, **현재 관측**은 별도로 승인·연결한 천리안2A 최신 적외영상입니다. 최신 위성·레이더는 지도와 범례를 포함한 원본 패널로 제공하며 지도에 정합한 오버레이나 숫자 강우량으로 표현하지 않습니다. [현재 기상 조회](docs/LIVE_WEATHER.md)와 [기록 기상 자료](docs/WEATHER.md)에 각각의 범위를 정리했습니다.

> 공개 소스에서는 과거 개인 계정의 배포 호스트를 `legacy.example.invalid`로 대체했습니다. 버전·측정 이력은 당시 기록이며 현재 서비스 상태와 구분합니다. 실제 과거 URL은 비공개 배포 증빙에 보존합니다.
