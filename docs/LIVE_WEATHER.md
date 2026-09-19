# 실시간 기상 원천과 운영 계약

최신 확인일: 2026-09-20 KST, 최초 원천 조사는 9월 17일입니다. 이 문서는 실시간 조회 모듈의 근거와 배포 조건을 기록합니다. 저장된 과거 영상은 [WEATHER.md](WEATHER.md), 무료 서버 운영은 [STATIC_DEPLOYMENT.md](STATIC_DEPLOYMENT.md)를 따릅니다.

## 현재 판정

정식으로 활용승인된 `RadarImgInfoService`를 연결했습니다. 실제 모듈에서 최신 목록과 CMP_WRC PNG의 HTTP 200·제품·시각·파일 무결성을 검증했습니다. **이 영상은 지도 경계선과 범례를 포함하는 635×620 도표이므로, 최신 관측 패널로 제공합니다. 지도 좌표 overlay로 사용하지 않습니다.**

별도 제품인 날씨누리 SFC-HSR 공개 영상은 인증 없는 최신 목록·PNG와 LCC 투영을 확인했지만 운영 재이용 근거를 확정하지 못했습니다. 이 경로의 기본 상태는 계속 `source_not_verified`입니다. 공식 API 승인이 공개 CGI의 모든 제품을 허용한다는 뜻으로 해석하지 않습니다.

공식 위성 `getInsightSatlit`은 **9월 19일 활용승인 완료 후 실제 공개 API에서 HTTP 200·`available`·원본 패널 13개를 확인했습니다.** 같은 공개 origin의 최신 PNG 1장도 HTTP 200, 600×614, 463,938 B이며 청크 CRC와 전체 픽셀 디코딩을 통과했습니다. 최신 표본의 관측은 22:14 KST, 서버 목록 조회는 22:18 KST입니다. 9월 17일의 HTTP 403은 승인 확인 전 기록으로 보존합니다. 과거 예제나 다른 공개 썸네일을 승인 API 대신 표시한 결과가 아닙니다. 위성 지도용 경로는 계속 `projection_unverified`입니다.

| 원천 | 실제 확인 | 현재 연결 판정 |
|---|---|---|
| 날씨누리 SFC-HSR 공개 레이더 | 01:06 KST 조회에서 01:00 KST PNG, 4,090B, 640×640, 유색 픽셀 444개 확인 | 기술 경로·투영 검증, 운영 재이용 근거 미확정 |
| 날씨누리 GK2A 한반도 적외 목록 | 24개 목록, 마지막 01:02 KST 확인 | 최신성 확인, 썸네일의 지도 범위·테두리·범례를 아직 검증하지 않음 |
| 날씨누리 GK2A GIS | 공식 JS가 WMTS `Capabilities.xml`과 타일 matrix를 사용함을 확인 | 단일 이미지의 LCC 범위로 임의 취급하지 않음 |
| 기상청 APIHub HSR | 공식 문서에 인증키를 사용하는 영상 조회와 투영·이미지 URL JSON 계약 존재 | 별도 활용승인·키·실제 응답 검증 필요 |
| 공공데이터포털 레이더영상 | 정식 활용승인 이후 실제 모듈에서 01:25 KST 관측 목록·최신 PNG 31,196B 확인 | 최신 도표 패널 연결, 지도 투영·crop 미확인 |
| 공공데이터포털 위성영상 | 9월 19일 활용승인 후 공개 목록 13패널·최신 PNG 1장 HTTP 200, 무결성·전체 디코딩 통과 | 현재 관측 원본 패널 연결. 정확한 지도 투영·crop과 연속 운영 성능은 별도 검증 |
| 공공데이터포털 레이더관측자료 | 무료, 이용허락범위 제한 없음, 개발/운영 자동승인 표기 확인 | 수치격자 API이며 날씨누리 PNG와 별도 제품 |

위 표의 초기 원천 조사는 9월 17일이며, 위성의 최신 공개 응답은 9월 19일에 별도로 확인했습니다. 개별 목록·영상의 정상 응답과 원천의 연속 운영·전국 기상 정확성은 구분합니다.

## 2026-09-19 공개 위성 응답

검사 대상은 [실제 공개 위성 API](https://korea-replay.legacy.example.invalid/api/v1/live/weather/satellite)입니다. 새 서울 지하철 인증 미리보기와 별개로 기존 공개 주소에서 위성 목록을 조회했습니다. 이 승인을 확인하기 위해 제품 코드를 바꾸거나 미리보기를 공개 전환하지 않았습니다.

| 항목 | 확인 결과 |
|---|---|
| 공개 목록 | HTTP 200, `available`, `presentation:panel-image`, 패널 13개·지도 프레임 0개 |
| 원천 시각 | 최신 파일명 UTC `202609191314` → `2026-09-19T13:14:00Z` → 22:14 KST |
| 서버 확인·응답 | `checked_at:2026-09-19T13:18:03.730Z`, `served_at:2026-09-19T13:18:03.735Z` |
| 영상 | 동일 공개 origin의 `/api/v1/live/weather/satellite/gk2a-ir105-ko/frames/202609191314.png`, HTTP 200·`image/png` |
| PNG 표본 | 600×614, RGBA, 463,938 B, 서명·60개 청크 CRC·Pillow verify와 전체 픽셀 load 통과 |
| 표현·시각 계약 | 13개 모두 `GK2A_IR105_KO`, `panel-image`, `map_overlay:false`, `projection:null`, UTC 시각 변환 검사 통과 |
| 요청 범위 | 공개 위성 목록 1회·PNG 1회, 재시도 0회. 키·인증 URL 노출 검사 통과 |

증빙은 [원격 검사](../.local/seoul-live-20260919/preview-verification.json)의 `satellite_origin`, `satellite`, `satellite_image`, [전체 픽셀 디코딩 검사](../.local/seoul-live-20260919/preview-image-decode.json)입니다. 전자는 지하철 미리보기 검사도 함께 담고 있지만 위성 origin은 공개 주소로 명시돼 있습니다. 영상 SHA-256은 `9ef0f67cd4a4be5c9f658de5a1c6190132e14c982334ede97579411409dff2a1`입니다. PNG 1장의 정상 해석을 모든 시각의 영상, 지도 위치 정확성 또는 Free Worker CPU 한도 통과로 확대하지 않습니다.

## 원천과 이용 조건

- [공식 레이더 화면](https://www.weather.go.kr/w/weather/radar/radar.do), [공식 위성 화면](https://www.weather.go.kr/w/weather/satellite/gk2a.do).
- [공식 지도 투영 정의](https://www.weather.go.kr/w/resources/js/kmap.bb.js?ver=202607091110). `radar.extent`와 WGS84 LCC 정의를 사용합니다.
- [기상청 저작권 정책](https://www.kma.go.kr/kma/guide/copyright.jsp)은 공공누리 표시와 개별 제한을 확인하도록 하며, 표시 없는 자료는 이용 전 협의를 안내합니다. 실제 레이더 페이지에는 저작권 정책 링크가 있으나 개별 영상의 공공누리 표시는 이번 검사에서 찾지 못했습니다. 이를 곧바로 불법 여부 판정으로 확대하지 않습니다.
- [기상청 APIHub 레이더](https://apihub.kma.go.kr/apiList.do?seqApi=5)는 HSR 자료·영상의 공식 승인 경로입니다. 배경 없는 그래픽 조회는 투영법과 이미지 위치를 JSON으로 제공합니다. 인증 뒤 실제 제품·좌표 범위를 검증하면 현재 계약의 별도 원천 어댑터로 연결할 수 있습니다.
- 이미지 목록의 공식 무료 신청은 [레이더영상 조회서비스](https://www.data.go.kr/data/15056924/openapi.do)와 [위성영상 조회서비스](https://www.data.go.kr/data/15058167/openapi.do)를 우선합니다. 두 API 모두 개발·운영 자동승인과 개발계정 10,000회를 명시합니다. 레이더는 공공누리 제1유형, 위성은 이용허락범위 제한 없음으로 표시되어 있습니다. 레이더 `RadarImgInfoService/getCmpImg`의 제품은 `CMP_WRC`, 위성 `SatlitImgInfoService/getInsightSatlit`은 `sat=G2`, `data=ir105`, `area=ko` 등을 받습니다. 위성 파일명 끝의 관측 시각은 UTC이므로 목록 요청 날짜나 KST 표시 문자열과 혼동하지 않습니다.
- [기상청 레이더관측자료 조회서비스](https://www.data.go.kr/data/15057166/openapi.do)는 `RadarObsInfoService`의 무료·자동승인·이용허락범위 제한 없음·개발계정 10,000회 조건을 명시합니다. 반환 수치격자를 활용하려면 해당 API의 좌표·압축·결측 계약을 별도로 구현해야 하며, 이 허락을 다른 공개 PNG로 자동 이전하지 않습니다.

공식 API 키는 `dataGoKrServiceKey` 옵션으로 서버에만 전달합니다. 정식 키를 이용한 로컬 검증에서는 `.dev.vars`의 값을 메모리에서 읽었으며 출력하지 않았습니다. 배포에서는 Worker secret을 사용하고, `source_url`, `permission_reference`, 로그 또는 캐시 키에 키를 넣지 않습니다. 모듈은 인증 URL을 응답에 포함하지 않습니다.

## 공용 계약과 연결

공용 타입은 `shared/live-weather.ts`, 서버 구현은 `worker/live-weather.ts`입니다.

```ts
import {createLiveWeatherHandler} from './live-weather';

const handler = createLiveWeatherHandler({
  cache: caches.default,
  waitUntil: promise => ctx.waitUntil(promise),
  dataGoKrServiceKey: env.DATA_GO_KR_SERVICE_KEY,
  // 별도 공개 CGI 제품의 재이용 근거는 아직 검증하지 않았습니다.
  radarAccess: 'unverified',
});
const response = await handler(request); // 담당 경로가 아니면 null
```

| 경로 | 반환 |
|---|---|
| `GET /api/v1/live/weather/radar` | 승인 키가 있으면 공식 CMP_WRC 목록, 없으면 별도 공개 CGI의 상태 |
| `GET /api/v1/live/weather/radar/cmp-wrc/frames/YYYYMMDDHHmm.png` | 공식 API 목록에 포함된 원본 도표 PNG |
| `GET /api/v1/live/weather/radar/frames/YYYYMMDDHHmm.png` | 별도 이용 조건이 검증된 경우에만 원본 LCC PNG |
| `GET /api/v1/live/weather/satellite` | 승인된 공식 GK2A 한반도 적외 목록. 9월 19일 공개 HTTP 200·13패널 확인; 이후 인증·원천 오류는 명시적 실패로 반환 |
| `GET /api/v1/live/weather/satellite/gk2a-ir105-ko/frames/YYYYMMDDHHmm.png` | 공식 목록에 포함된 원본 패널 PNG, 경로 시각은 **UTC** |
| `GET /api/v1/live/weather/satellite/frames/YYYYMMDDHHmm.png` | 지도용 위성 경로는 `projection_unverified`, HTTP 503 |

임의 원천 URL·제품·범위를 받는 쿼리는 허용하지 않습니다. 조회 가능한 레이더는 최근 2시간 안의 목록 중 최대 13장입니다. 공식 API는 `CMP_WRC`만 요청하고, 결과의 공식 호스트·고정 파일 경로·KST 파일 시각을 검증합니다. URL에 query나 인증정보가 포함되면 거부하며, 공식 반환의 HTTP 이미지 주소는 실제 접근을 확인한 HTTPS로 정규화합니다. 공식 API의 JSON 배열 및 문자열 안의 URL 배열 두 표현을 명시적으로 처리합니다.

위성은 `sat=G2`, `data=ir105`, `area=ko`만 요청합니다. 최근 2시간에 해당하는 **UTC 날짜만** 조회합니다(보통 1일, UTC 자정 경계는 최대 2일). 명세는 반환 파일의 UTC 시각을 명시하며, 날짜별 조회 동작은 아래 실제 원천 검사로 확인했습니다. 같은 관측 시각·주소를 합쳐 최근 13개만 제공하고, 인증·쿼터·형식 오류는 즉시 중단합니다. 한 날짜의 명시적 자료 없음 응답만 다음 날짜 조회를 허용합니다. 관측 시각은 요청 날짜에서 추정하지 않습니다.

### 2026-09-20 한국 자정 이후 위성 오류 수정

00:55 KST 미리보기 화면에서 위성 목록 실패를 재현했습니다. 기존 UTC/KST 날짜 합집합은 아직 오지 않은 UTC 날짜 `20260920`을 먼저 요청했습니다. 00:57 KST에 같은 승인 API를 날짜만 달리해 2회 확인한 결과, `20260920`은 HTTP 200 안의 `resultCode=01`(오류), `20260919`는 `resultCode=00`과 영상 참조 472개, 최신 파일 시각 `202609191552` UTC를 반환했습니다. 오류를 자료 없음으로 바꾸지 않고, 미래 UTC 날짜 요청 자체를 제거했습니다.

증빙은 [날짜 경계 원천 검사](../.local/national-visual-20260920/satellite-boundary-probe.json)입니다. 원문·인증 URL·키는 저장하지 않았습니다. 한국 자정, UTC 자정, 연도 경계, 원천 `01` 실패 유지 검사를 포함한 기상 테스트 73개가 통과했습니다. 이 절의 최초 작성 시점에는 로컬 수정이며, 배포 상태와 이후 공개 응답은 최신 배포 기록을 확인해야 합니다.

허용 영상은 공식 `www.weather.go.kr`/`www.kma.go.kr` 호스트의 `/[w/]repositary/image/sat/gk2a/KO/gk2a_ami_le1b_ir105_ko020lc_<UTC12>[.thn].png`뿐입니다. 원천에서 받은 파일 변형을 그대로 사용하고 썸네일·원본 이름을 임의로 바꾸지 않습니다. 9월 19일 승인 API 목록에서 선택한 최신 공개 PNG 표본도 600×614임을 확인했습니다. 모든 영상의 크기를 이 값으로 고정하지 않습니다. 위성 목록의 `image_size=null`은 목록만으로 크기를 확정하지 않았다는 뜻이며, 영상 제공 때 실제 PNG 헤더·청크를 검증합니다.

별도 공개 CGI는 현재보다 5분 이전의 완료 경계를 요청하며, 요청 시각 뒤의 프레임을 함께 반환하면 제외합니다. 목록의 `name` 문자열은 다른 프레임에서도 요청 시각으로 반복될 수 있으므로 관측 시각의 근거로 사용하지 않습니다. 엄격히 검사한 `tm`과 이미지 주소의 `tm`을 대조합니다.

시간 필드는 의미가 다릅니다.

- `frames[].time` 또는 `panel_frames[].time`: 실제 영상 관측 시각, UTC.
- `source_time_kst`: 실제 관측 시각의 `YYYYMMDDHHmm` KST. 레이더는 원천 파일명, 위성은 파일명 UTC에서 변환합니다.
- 위성의 `source_time_utc`: 원천 파일명의 `YYYYMMDDHHmm` UTC. `:02`, `:07`도 유효하므로 레이더의 5분 경계 규칙을 적용하지 않습니다.
- `checked_at`: 마지막 성공한 원천 목록 조회 시각. 캐시 적중에도 유지합니다.
- `served_at`: 현재 서버 응답 시각.
- `max_age_seconds`: 최신 관측에 허용하는 최대 지연, 현재 1,200초.

`available`은 유효한 최신 목록이 있다는 뜻입니다. 각 PNG는 요청할 때 다시 검증하며, 개별 영상 404·형식 오류는 명시적 실패 응답으로 전달됩니다. 공식 도표는 `presentation='panel-image'`, `frames=[]`, `panel_frames=[PanelLiveFrame]`으로 반환합니다. `PanelLiveFrame`은 `map_overlay=false`, `projection=null`이며 지도 재투영 Worker에 전달하지 않습니다. 기존 `RawLiveFrame`은 지도용 `frames` 계약을 유지합니다.

`stale` 또는 `unavailable`이면 표시 배열을 모두 비웁니다. 오래된 마지막 영상과 과거 예제를 실시간 영상으로 대체하지 않습니다. 클라이언트는 `isLiveWeatherFresh`로 응답 이후의 시간 경과도 점검하고, 관측 시각과 경과 분을 항상 표시해야 합니다.

## 투영과 영상 의미

CMP_WRC 도표는 상단 관측 시각, 지도 경계선, 우측 mm/h 색상 범례를 포함합니다. 원본을 그대로 패널에 표시하며, 도표에서 지도 영역을 임의로 잘라 경위도 위치를 추정하지 않습니다. 좌표 범위·투영·정확한 crop 근거가 확보된 제품만 추후 지도에 연결할 수 있습니다.

위성 `GK2A_IR105_KO`도 원본 패널만 표시합니다. 승인 API의 성공 영상은 확인했지만 정확한 투영·crop은 아직 확인하지 않았으며, `ko020lc`라는 파일명만으로 지리 범위를 추정하지 않습니다. `background_meaning='source-rendering-not-classified'`로 표시하고 픽셀 색상을 기온·운량·강수량으로 역산하지 않습니다.

별도 SFC-HSR 레이더 원본도 EPSG:4326 정사각 영상이 아닙니다.

```text
+proj=lcc +lat_1=30 +lat_2=60 +lat_0=0 +lon_0=126
+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs

extent (m): [-440000, 3797382.7212162036, 584000, 4821382.721216239]
source image: 640 × 640, origin top-left
```

이 값과 공식 JS 정의가 0.01m 허용 오차 안에서 일치해야 조회를 계속합니다. CRS 문구가 달라지거나 영상 크기가 달라지면 실패합니다. `RawLiveFrame.url`의 원본 PNG를 경위도 사각형에 직접 올리면 안 됩니다.

브라우저의 별도 OffscreenCanvas Worker에서 경위도 출력 격자를 원천 LCC 좌표로 변환하고, 기존 Python 경로와 같은 최근접 표본 방식으로 재투영합니다. 정점·건물 작업 Worker와 분리하여 대형 지도 작업을 막지 않도록 합니다. 투영 lookup은 크기·범위가 같으면 재사용할 수 있습니다. 구현을 통합할 때 pyproj 기준 좌표·상하 방향·소스 범위 밖 투명 처리를 별도로 검증합니다.

원래 레이더 색상은 보존하며 색상에서 mm/h나 dBZ를 역산하지 않습니다. 회색·흰색·투명 배경만으로 무에코와 결측을 구분할 수 없으므로 `background_meaning='no-echo-or-missing-unknown'`을 유지합니다. 숫자 강수량·반사도를 제공하려면 결측 코드와 보정 규칙이 명시된 수치 제품이 필요합니다.

## 서버 자원·실패 경계

| 항목 | 상한/정책 |
|---|---|
| 목록 | 공개 레이더 64KiB·30행, 공식 레이더 64KiB·300파일, 위성 날짜별 256KiB·800파일·최대 2일; 제공 최대 13프레임 |
| 원본 PNG | 레이더 512KiB·635×620 또는 640×640; 위성 2MiB·각 변 1~2048·최대 4,194,304픽셀; 모두 최대 128청크 |
| 투영 정의 | 768KiB, 고정 공식 HTTPS 주소 |
| 원천 대기 | 요청당 8초, 클라이언트 취소 전파 |
| Cache API | 목록 60초, 원본 영상 300초, 투영 검증 3,600초 |
| 반환 JSON | `Cache-Control: no-store`; 내부 원천 조회 캐시와 구분 |
| PNG 검사 | 시그니처·IHDR·IDAT·IEND·청크 범위·CRC, APNG 거부 |
| URL 검사 | 공식 origin·정확 경로·시각·제품·크기·범례·지도 매개변수 고정, 리다이렉트 거부 |

서버는 PNG 픽셀의 압축 해제·재투영·영상 생성과 장기 저장을 하지 않습니다. 같은 01:00 KST 원천 주소의 재조회 크기가 4,090B에서 4,114B로 달라지는 것도 확인했으므로, 시각 기반 원천 URL을 불변 파일로 취급하지 않습니다. Cache API는 지역별 임시 캐시이며 보관소가 아닙니다. R2·D1·유료 기능·수집 cron·노트북 서버를 요구하지 않습니다. 방문이 없으면 수집도 없고, 라이브 프레임의 장기 재생은 보장하지 않습니다. 공유된 고정 데이터 릴리스와 이 경로를 섞지 않습니다.

`source_not_verified`, `projection_unverified`, `projection_changed`, `authentication_required`, `authentication_failed`, `upstream_quota`, `upstream_http`, `upstream_timeout`, `upstream_invalid`, `upstream_unavailable`, `frame_missing`, `stale`, `invalid_request`, `aborted`를 구분합니다. 오류를 투명한 정상 영상으로 바꾸지 않습니다. 캐시 저장 실패는 원천 응답 제공을 막지 않지만, 불완전하거나 실패한 원천 자료를 정상 캐시에 넣지 않습니다.

Workers Free의 실제 CPU 한도 통과는 공개 미리보기 측정으로 따로 판정해야 합니다. 로컬 테스트 통과만으로 원격 CPU 성능이나 연속 원천 접근을 보장하지 않습니다.

## 검증

```powershell
npx vitest run tests/live-weather.test.ts
npx eslint worker/live-weather.ts shared/live-weather.ts tests/live-weather.test.ts
npm run typecheck
```

9월 17일 기상 모듈 독립 검사에서는 회귀 70개와 해당 파일 lint가 통과했습니다. 당시 전체 TypeScript 검사에는 통합 중이던 `src/live-client.ts`의 `TransitCatalogRef` 형변환 오류 1개가 보고됐으며, 기상 모듈의 타입 오류는 없었습니다. 이는 당시 기록이고 이후 전체 코드 검사 상태는 [현재 검증 기록](VALIDATION.md)을 따릅니다. 이번 위성 승인 확인 작업은 코드 검사를 재실행한 작업이 아닙니다.

검사 범위는 이용 조건 설정, 정확 경로·제품·시각 검증, 최신성·캐시 TTL, 요청 시각 이후 프레임 제외, 원천 투영 변경, PNG 손상·대형 응답·리다이렉트 거부, 누락·취소·시간 초과입니다. 공식 승인 경로에는 배열·문자열 목록, 제품·지도 경로 분리, 키 비노출, 인증·쿼터 오류와 도표 크기 검사도 추가했습니다. 실제 원천 조회와 합성 fixture 테스트는 분리했습니다.

위성 추가 회귀는 UTC 파일명→다음날 KST 변환, 윤일·유효하지 않은 날짜, 두 날짜 중 한 날짜의 자료 없음, 인증 거부 즉시 중단, 시각 중복 병합, 실제 크기 미확인 표시, HTML·대형·손상 PNG 거부, 캐시와 취소를 검사합니다. 합성 성공 fixture는 실제 활용승인 또는 원천 성공을 입증하지 않습니다.

승인 API를 실제 모듈로 실행한 검사에서는 목록 1회와 최신 PNG 1회, 총 2회 원천 요청으로 `available`/`panel-image`/13프레임 및 최신 PNG 검증을 확인했습니다. 인증 없는 공개 원천 검사에서도 TypeScript 투영 정의 검증과 PNG CRC 검증이 통과했습니다.

위성의 활용승인·공개 서버 인증 연결·실제 성공 목록·PNG 1장 검증은 9월 19일 확인했습니다. 위성 브라우저 패널의 표시·취소·만료 동작과 Free Worker CPU·연속 운영은 이번 API 검사와 별도로 판정합니다. 지도용 공개 CGI의 이용 근거와 위성의 정확한 투영 검증도 남아 있습니다. 승인된 도표 패널이 연결되어도 미확인 지도용 소스를 자동 활성화하지 않습니다.

> 공개 소스에서는 과거 개인 계정의 배포 호스트를 `legacy.example.invalid`로 대체했습니다. 버전·측정 이력은 당시 기록이며 현재 서비스 상태와 구분합니다. 실제 과거 URL은 비공개 배포 증빙에 보존합니다.
