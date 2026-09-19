# K-apt·실거래 단지의 좌표 연결 조건

확인일: **2026-09-20 KST**. 기존 원본 33개와 제한된 실거래 표본, 현재 공식 API 명세를 읽어 대조한 결과입니다. 새로운 인증 API 호출·신청·수집·지오코딩·좌표 연결은 실행하지 않았습니다. 수집은 성능 측정을 위해 일시정지 상태를 유지합니다.

**권고는 공식 주소정보의 구조화 주소키를 확보하고, 건축HUB의 대장 관계로 단지 소속을 검증하는 두 단계입니다.** 현재 자료만으로는 `aptSeq ↔ kaptCode` 대응을 확정할 수 없습니다. 같은 이름·법정동·지번·도로명주소만으로 단지를 합치거나, 주소 좌표를 모든 동의 실제 위치로 표시하지 않습니다. 좌표는 검증될 때까지 `null`을 유지합니다.

## 1. 실제 응답에서 확인한 연결 공백

K-apt 목록·기본정보는 [공식 목록 V4](https://www.data.go.kr/data/15057332/openapi.do), [기본정보 V5](https://www.data.go.kr/data/15058453/openapi.do)의 기존 응답입니다. 자세한 수집 범위는 [KAPT.md](KAPT.md)를 따릅니다. 실거래는 [매매 상세 15126468](https://www.data.go.kr/data/15126468/openapi.do), [전월세 15126474](https://www.data.go.kr/data/15126474/openapi.do)의 종로구 `11110`, 2026년 8월 원본을 표본으로 읽었습니다.

| 원천·검사 범위 | 실제 존재하는 연결 관련 필드 | 없는 필드·이번 한계 |
|---|---|---|
| K-apt 전국 목록 23페이지·22,319행 | `kaptCode`, `kaptName`, `bjdCode`, `as1`~`as4` | `aptSeq`, 구조화 지번/도로명 번호, `bdMgtSn`, `mgmBldrgstPk`, 좌표 없음 |
| K-apt 기본정보 10개 | `kaptCode`, `bjdCode`, `kaptAddr`, `doroJuso`, `kaptDongCnt`, `kaptdaCnt`, `kaptUsedate` 등 | 두 주소는 문자열이며 공통 건물 ID·좌표 없음. 주소·법정동코드는 10개 모두 존재, 사용승인일 9개·우편번호 7개. 전국 기본정보 확인 아님 |
| 매매 원본 17행 | `aptSeq`, `sggCd`, `umdCd`, `landCd`, `bonbun`, `bubun`, `jibun`, `roadNmSggCd`, `roadNmCd`, `roadNmbCd`, `roadNmBonbun`, `roadNmBubun`, `roadNmSeq` | `kaptCode`, `bdMgtSn`, `mgmBldrgstPk`, 좌표 없음. `roadNmbCd`는 16/17행만 값 있음 |
| 전월세 원본 132행 | `aptSeq`, `sggCd`, `umdNm`, `jibun`, 소문자 `roadnmsggcd`, `roadnmcd`, `roadnmbcd`, `roadnmbonbun`, `roadnmbubun`, `roadnmseq` | `umdCd`, `landCd`, `bonbun`, `bubun`이 이 응답에는 없음. `roadnmbcd`는 92/132행만 값 있음. K-apt/건물 ID·좌표 없음 |

실거래 표본은 두 응답의 필드 실재 여부를 확인한 범위이며 전국 결측률이 아닙니다. 매매의 현재 공식 Swagger는 `aptSeq`를 **단지 일련번호**, `sggCd/umdCd`를 법정동 시군구/읍면동 코드, `roadNmbCd`를 도로명 지상지하 코드로 설명합니다. 전월세의 추가 `aptSeq/roadnm*`는 실제 응답에서 확인한 필드이며 기존 파서가 원문을 보존합니다. [정규화 계약](REAL_ESTATE_DATA_CONTRACT.md).

K-apt 목록을 이번에 다시 읽어 집계하면 법정동 코드가 **3,042개**, 그중 **2,221개**에 복수 단지가 있고 한 코드 아래 최대 **81개**가 존재합니다. `bjdCode`는 단지 식별자가 아닙니다. K-apt 관리 단위와 실거래 단지 단위가 항상 1:1이라는 공식 근거도 확보하지 못했습니다.

정확히 필요한 추가 근거는 다음 세 가지입니다.

1. **실거래 단지와 K-apt 관리 단위의 대응 증거:** 원천 `aptSeq`와 `kaptCode`를 직접 연결하는 공식 대응표, 또는 같은 공식 건물 집합에 속함을 확인하는 대장 관계가 필요합니다. 현재 두 API에는 직접 대응 ID가 없습니다.
2. **공통 주소·건물 식별자:** 주소정보의 `bdMgtSn`과 구조화 주소키, 건축HUB의 `mgmBldrgstPk`·`mgmUpBldrgstPk` 등입니다. 서로 다른 체계의 ID이므로 값 모양이 같아도 동일 ID로 취급하지 않습니다. 지번/PNU는 필지 식별이고 건축물이나 단지와 1:1이라고 전제하지 않습니다.
3. **좌표 자체의 증거:** 주소/건물 ID와 결합된 원천 X/Y, 좌표계, 점의 의미, 자료 기준시점, 저장·재배포 허용 범위입니다. 좌표 응답을 받았다는 사실과 단지 소속을 확정한 사실은 별도입니다.

`aptSeq`와 `bdMgtSn` 등의 접두어를 현행 행정코드로 추정하지 않습니다. 과거 행정구역 변경 전 번호가 남을 수 있으며, 현재 256개 조회 코드와 다른 시점의 252개 지도 경계를 이름으로 이어 붙이지 않습니다.

## 2. 다음 원천은 두 경로로 제한

### 우선 1: 주소정보 검색 → 구조화 주소키 → 좌표제공

[주소정보 API 연계](https://business.juso.go.kr/jst/jstAddressApiList)의 [도로명주소 검색 명세](https://business.juso.go.kr/jst/jstRoadNmAddrApiSearch)와 [좌표제공 검색 명세](https://business.juso.go.kr/jst/jstCoordApiSearch)를 확인했습니다. 사이트는 SPA여서 일반 페이지 본문 추출 결과는 비어 있었고, 같은 공식 페이지가 로드하는 공개 문서 자산의 표를 직접 읽었습니다. 확인 자산은 `jstRoadNmAddrApiSearch-54034eee.js`, `jstCoordApiSearch-074b2de4.js`입니다. 실제 주소 검색 API를 호출한 것은 아닙니다.

| 단계 | 현재 공식 명세의 계약 | 이 프로젝트 적용 조건 |
|---|---|---|
| 주소 검색 | `https://business.juso.go.kr/addrlink/addrLinkApi.do`; `keyword`, `currentPage`, `countPerPage`, 서비스별 `confmKey`. 응답에 `admCd`, `rnMgtSn`, `bdMgtSn`, `udrtYn`, `buldMnnm`, `buldSlno`, 지번·도로명주소 등 | K-apt 원천 주소로 후보를 조회하더라도 문자열 검색 첫 결과를 채택하지 않음. 법정동·지상/지하·본번/부번 등 전체 키와 원문 주소가 일관된 유일 후보인지 검수 |
| 좌표 검색 | `https://business.juso.go.kr/addrlink/addrCoordApi.do`; `admCd`, `rnMgtSn`, `udrtYn`, `buldMnnm` 필수, `buldSlno` 선택. 응답에 동일 주소키·`bdMgtSn`·`entX`·`entY` | 검증한 구조화 키를 그대로 사용. 응답 건수·반환 키·유한 좌표·CRS가 일치할 때만 주소 위치 근거로 보존 |
| 오류 계약 | 응답 `common.errorCode`, `errorMessage`, `totalCount`, `juso` 목록 | 인증·제한·형식 오류와 정상 0건을 구분. 정상 코드라도 좌표 결측이면 `coordinate_missing` 유지. 원문 오류/승인키 포함 URL 기록 금지 |

실거래 도로명 코드와 JUSO 코드의 자릿수·영역·기준시점·지상지하 값이 같다는 검증이 먼저 필요합니다. `roadNmSggCd + roadNmCd` 같은 문자열 결합을 검증 없이 확정 키로 승격하지 않습니다. 현재 표본의 지상지하 코드 결측을 `0`으로 채우지 않습니다. 주소 검색에서 구조화된 공식 키를 확보하는 경로를 우선합니다.

**비용·승인·좌표계의 확인 수준:** 공식 도움센터의 과거 답변에는 API 무료 제공, 좌표용 별도 운영 승인, UTM-K(GRS80), 5초당 10건 제한 안내가 있습니다. 이번에는 해당 과거 본문의 직접 재열기가 일부 실패하여 검색 색인에 남은 기관 답변을 보조 근거로만 사용했습니다. 현행 명세 표에서 X/Y 필드는 확인했지만, **최신 비용·배치 저장/재배포 조건·정확한 좌표계 정의·점의 의미까지 현재 계약으로 재확인한 상태는 아닙니다.** 따라서 무료 공식 연계의 우선 후보이며, 전국 좌표 정적 게시가 허가·검증됐다고 표시하지 않습니다.

- [2022년 무료 제공 안내](https://eng.juso.go.kr/addrlink/qna/qnaDetail.do?bulletinRefSn=103908&currentPage=437&keyword=&noticeMgtSn=103908&noticeType=QNA&noticeTypeTmp=QNA&page=&searchType=).
- [2024년 좌표계·호출 제한 안내](https://m1.juso.go.kr/addrlink/qna/qnaDetail.do?bulletinRefSn=126722&currentPage=201&keyword=&noticeMgtSn=126722&noticeType=QNA&noticeTypeTmp=QNA&page=&searchType=).
- [2025년 좌표 API의 운영 승인키 안내](https://eng.juso.go.kr/addrlink/qna/qnaDetail.do?bulletinRefSn=138362&currentPage=51&keyword=&noticeMgtSn=138362&noticeType=QNA&noticeTypeTmp=QNA&page=&searchType=).

이후 승인 상태를 확인할 대상은 **도로명주소 검색 API와 좌표제공 검색 API**입니다. 공공데이터포털의 기존 일반 키를 그대로 사용할 수 있다고 전제하지 않습니다. 팝업용·검색용 승인도 구분합니다. 이 조사에서는 로그인·본인인증·신청·키 확인을 실행하지 않았습니다. 좌표를 사용할 때는 원천 CRS와 원값을 보존하고, 검증한 변환 및 기준점 회귀를 통과한 결과만 WGS84로 제공합니다. 단지 중심점·개별 동 중심점·출입구 중 어느 점인지 미확인 상태에서는 그 의미를 만들어 붙이지 않습니다.

### 우선 2: 건축HUB 대장 관계·부속지번으로 동일 건물 집합 확인

[국토교통부 건축HUB 건축물대장정보 15134735](https://www.data.go.kr/data/15134735/openapi.do)의 현재 본문과 HTML 내 공식 Swagger를 직접 확인했습니다. **무료·이용허락범위 제한 없음·개발/운영 자동승인·개발계정 10,000 트래픽**으로 표시됩니다. 과거 건축데이터 PK가 건축HUB 전환으로 변경됐다는 공지도 있으므로 구 PK와 새 PK를 별도 버전으로 보존하고 공식 전환 규칙 없이 합치지 않습니다.

현재 공식 HTTPS 서비스는 `https://apis.data.go.kr/1613000/BldRgstHubService`입니다. 우선 기능은 다음 네 개로 제한합니다.

| 기능 | 검증할 출력 | 연결에 필요한 이유 |
|---|---|---|
| `getBrBasisOulnInfo` 기본개요 | `mgmBldrgstPk`, `mgmUpBldrgstPk`, `regstrGbCd`, `regstrKindCd` | 개별 대장의 상위 관계를 보존. 명칭으로 총괄/개별 동을 합치는 것 방지 |
| `getBrRecapTitleInfo` 총괄표제부 | 대장 PK·종류, 지번, 도로명 코드/번호, `useAprDay` | 단지 전체를 대표하는 대장인지 확인할 근거 |
| `getBrTitleInfo` 표제부 | `mgmBldrgstPk`, `dongNm`, 지번, 도로명 코드/번호, 사용승인일 | 개별 동과 주/부속 건물을 구별하고 해당 총괄 범위와 대조 |
| `getBrAtchJibunInfo` 부속지번 | 대장 PK, 주·부속 지번 관련 필드 | 하나의 단지가 여러 필지에 걸치는 사례를 단일 지번으로 축소하지 않음 |

공식 요청은 `sigunguCd`, `bjdongCd`가 필수이고 `platGbCd`, `bun`, `ji`, 페이지·크기·날짜 범위가 선택입니다. `platGbCd`는 **0:대지, 1:산, 2:블록**으로 설명되어 있습니다. 실거래 `landCd`의 값과 동일하다고 가정하지 않고 각 원천 코드표를 대조해야 합니다. 반환 도로명 키 `naRoadCd`, `naBjdongCd`, `naUgrndCd`, `naMainBun`, `naSubBun`도 구조화 상태로 보존합니다.

대장 PK·상위 PK는 정수 계산 대상이 아닌 **문자열 ID**로 처리합니다. 전 페이지의 목록이 완성되고 대장 관계·필지/주소가 일관된 경우에만 건물 집합 증거를 채택합니다. 이 API가 `aptSeq`나 `kaptCode`의 공식 대응표 또는 좌표를 직접 제공한다고 주장하지 않습니다. 대장 연결만으로 최종 단지 대응이 하나로 결정되지 않으면 계속 후보입니다.

기존 공공데이터포털 키를 인증 수단으로 사용할 수 있는 계열이지만 **15134735의 실제 활용승인·응답은 이번에 확인하지 않았습니다.** 매매·전월세·K-apt 승인 사실을 이 서비스 승인으로 확장하지 않습니다. 인증/권한 코드 20·30·31, 할당량 22·23, HTTP 실패와 정상 빈 결과를 별도 처리하는 검증이 실제 연결 전에 필요합니다.

## 3. 향후 연결 계약 — 구현 전 제안

원천 ID는 아래처럼 각각 보존합니다. 전부 존재하지 않는다고 임의 값이나 다른 ID로 채우지 않습니다.

| 항목 | 의미·확정 조건 |
|---|---|
| `property_complex_id`, `apt_seq`, `query_lawd_code` | 현재 실거래 namespace와 불투명 원천 단지 ID. 현재 행정코드로 접두어 재해석 금지 |
| `kapt_code` | K-apt 관리 단위. 1:1을 강제하지 않고 연결 건수·범위를 기록 |
| `address_key` | 공식 `admCd/rnMgtSn/udrtYn/buldMnnm/buldSlno` 전체 조합과 기준시점 |
| `address_building_id` | `bdMgtSn` 원문. 대장 PK와 분리 |
| `building_register_ids`, `parent_register_ids` | 건축HUB ID·대장 종류·PK 버전·명시적 상위 관계. 복수 건물 허용 |
| `evidence[]` | 제공기관·공식 문서 URL·원본 응답 SHA·조회시각·원천 기준일·검증 규칙 버전. 비밀 요청 URL 제외 |
| `identity_status` | `unlinked / address_candidate / building_set_candidate / verified / ambiguous / conflict` |
| `coordinate_status` | `missing / source_received / crs_verified / publishable`. 단지 식별 상태와 독립 |
| `coordinate` | 원천 X/Y·CRS·대상 ID·점의 의미·변환 후 경위도·허용 사용범위. 확정 조건 미충족 시 null |

확정 여부는 순차적으로 판단합니다.

1. K-apt 주소와 실거래 주소를 각각 공식 구조화 주소키에 대응시킵니다. 검색어 일치·주소 한 줄의 일치만으로 단지 대응을 확정하지 않습니다.
2. 각 주소에 해당하는 공식 건축물대장과 상위 관계·부속 필지를 대조합니다. 같은 주소에 여러 관리 단위/단지가 있거나 한 단지가 복수 주소라면 관계를 그대로 남깁니다.
3. 두 원천의 단지 범위가 같은 공식 건물 집합으로 검증되거나 직접 공식 대응 ID를 확보한 경우에만 `identity_status=verified`로 전환합니다. 일부 건물만 겹치면 다대다 후보입니다.
4. 좌표 대상·CRS·기준시점·사용범위 검사를 별도로 통과해야 공개 지도에 올립니다. 주소 위치를 단지 외곽/전 동의 위치 검증으로 확대하지 않습니다.

이 설계는 원천에 없는 정답을 생성하지 않습니다. 완공·재건축·분할/합병·법정코드 변경으로 관계가 바뀌는 경우 기존 연결과 새 연결의 적용 기간을 분리해야 합니다. 기준일이 없는 원천에는 조회일을 기준일처럼 대입하지 않습니다.

## 4. 다음 실행의 구체적 선행 조건

- JUSO 검색/좌표 API의 현재 무료 이용·저장/정적 재배포 조건과 해당 종류의 승인키 유무를 확인합니다. 이 상태는 현재 미확인입니다.
- 건축HUB 15134735 활용승인 상태와 기존 PK 전환 규칙을 확인합니다. 승인 여부를 키 문자열 존재만으로 판정하지 않습니다.
- 현재 확보한 K-apt 기본정보 10개를 작은 검증 대상으로 삼아, 정확한 주소키→대장→좌표 증거를 원본 보존 방식으로 검증합니다. 성공·0건·좌표 결측·복수 후보를 각각 보고합니다.
- 위 검증으로도 `aptSeq ↔ kaptCode`의 동일 건물 집합이 확인되지 않는 건은 미연결로 유지합니다. 전국 수집과 무관하게 이 연결의 확정률을 별도로 측정합니다.

현재 좌표 연결 0개를 변경하지 않았습니다. 제품 코드·데이터 후보·공개 pin·키·원본·수집 장부도 변경하지 않았습니다.

## 5. 이번 읽기 감사의 재현 근거

- K-apt 원본: `.local/kapt-20260920/collection/raw/<sha256>.json`, 23개 목록 응답과 10개 기본정보 응답. 기존 `verification.json`의 수집시각은 2026-09-19T18:15:11Z~18:15:36Z입니다.
- K-apt 공식 Swagger: `.local/kapt-20260920/swagger-15057332.json`, `swagger-15058453.json`. 현재 포털 페이지의 V4/V5와 대조했습니다.
- K-apt 정규화 후보: `kapt-d5e385e5e588915a/context.json`, SHA `d5e385e5e588915acb6f6b2c3815252a53be55adced180b4ffded662b979240d`.
- 매매 표본 원본 SHA: `c35cd768aff86f330cef11ccbc7b2cceca5d10fb4527353ff122a33b31fe23fc`, 14,909B·17행.
- 전월세 표본 원본 SHA: `0022218b60fa73c719c72df8ea4c7d73c99b6994aef8acd66ff86ac38c73913b`, 92,709B·132행.
- 두 실거래 표본은 `.local/real-estate-20260920/raw/<sale 또는 rent>/11110/202608/`에 있으며, 원장에 기록된 조회시각은 2026-09-19T17:22:24Z입니다. 이번에는 원본을 다시 읽어 태그 존재/결측 수만 집계했습니다.
- 관리사무소 연락처·팩스 등 원본 값, 인증정보, 개별 거래 원문은 이 문서에 복사하지 않았습니다. 공식 문서의 읽기 요청 외 신규 데이터 API 요청은 0회입니다.
