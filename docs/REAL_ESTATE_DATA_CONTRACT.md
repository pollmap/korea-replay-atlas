# 아파트 매매·전월세 신고 기록 정규화 계약

명세 확인일: **2026-09-20 KST**. 정규화와 수집은 분리된 모듈입니다. 승인된 공식 매매·전월세 API에서 **2026년 8월과 9월, 현행 법정 시군구 256개, 두 거래유형의 1,024개 조회 스냅샷**을 수집했습니다. 958개는 자료가 있고 66개는 정상 빈 응답이며, 신고 행은 148,678개입니다. 60개월+당월 장부의 나머지 30,208개 작업은 첫 후보 생성 시점에 대기였습니다. 지도 좌표 연결과 공개 배포 완료를 뜻하지 않습니다. 실제 후보·실행법·진행 증거는 [수집·게시 운영 문서](PROPERTY_PIPELINE.md)에 구분합니다. 테스트의 가상 자료는 `public`에 발행하지 않습니다.

## 공식 명세와 의미

원천은 [국토교통부 아파트 매매 실거래가 상세 자료 15126468](https://www.data.go.kr/data/15126468/openapi.do)입니다. 포털의 2026-09-08 수정 메타정보와 페이지 내 Swagger를 확인했습니다. 법정동 코드 앞 5자리와 계약년월 6자리로 신고 자료를 조회하는 XML 서비스입니다. 무료·개발 자동승인이더라도 현재 계정의 해당 서비스 승인 여부는 별도로 확인해야 합니다.

명세 경로는 `https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev`, 인자는 `LAWD_CD`, `DEAL_YMD`, `pageNo`, `numOfRows`, `serviceKey`입니다. `real_estate.py`는 키를 읽지 않는 순수 정규화 모듈이며, 별도 `real_estate_fetch.py`가 HTTPS 수집·호출 예약·원본 보존을 담당합니다. 출력에는 **키 없는 공식 데이터 설명 페이지 URL**과 원천 XML SHA-256만 기록합니다.

| 공식 필드 | 정규화 의미 |
|---|---|
| `dealAmount` | 원천 만원 단위 → 정수 `price_krw`. 문자열 그룹 구분과 안전 정수 범위 검사 |
| `dealYear/dealMonth/dealDay` | `contract_date`, 계약일. 존재하는 달력 날짜·입력 계약월·수집일 이후 여부 검사 |
| `rgstDate` | `registration_date`, **등기일자**. 신고일로 사용하지 않음 |
| `cdealType/cdealDay` | 해제 표시와 해제사유발생일. 계약일·등기일과 분리 |
| `excluUseAr` | `area_m2`, 전용면적. 공급면적·평수로 바꾸어 동일성을 판단하지 않음 |
| `aptSeq/aptNm` | 원천 단지 식별자/단지명. 명세에 거래 고유번호는 없음 |
| `sggCd/umdCd/umdNm/jibun` | 법정 시군구·읍면동·명칭·지번. 형식 검사와 행정코드의 실제 유효성 확인은 별개 |
| `floor/buildYear` | 원천 층·건축연도. 미제공과 잘못된 값을 구분 |

위 내용은 [공식 명세](https://www.data.go.kr/data/15126468/openapi.do)의 필드 설명에 근거합니다. 문서에 신고 제출시각·원천 갱신시각·GPS 관측시각은 없으므로 `reported_at`, `source_updated_at`, `provenance.observed_at`, `position`은 **항상 null**입니다. `retrieved_at`은 수집자가 외부에서 명시한 UTC 수집시각이며 원천 갱신시각이 아닙니다.

## 구현 경로

- [Python 배치](../pipeline/real_estate.py): `normalize_xml_page` → `build_partitions` → `write_dataset`.
- [공용 TypeScript 계약](../shared/real-estate.ts): `parseRealEstatePartition`, `activeRealEstateRecords`, `summarizeTransactions`.
- [수집기](../pipeline/real_estate_fetch.py), [법정코드 검증](../pipeline/real_estate_regions.py), [정적 게시 후보](../pipeline/real_estate_publish.py), [웹 게시 계약](../shared/property.ts).
- [Python 회귀](../tests/test_real_estate.py), [TypeScript 회귀](../tests/real-estate.test.ts): 인증이나 네트워크를 사용하지 않습니다.

### 월·시군구 Partition

`schema_version: 1`, `kind: apartment-sale-report-partition`입니다. 필수 상위 필드는 `lawd_code`, `deal_month`, `source`, `retrieved_at`, `records`, `audit`입니다. 하나의 완전한 지역·월 조회 스냅샷만 담습니다. 다른 날짜에 재수집한 페이지들을 섞거나 과거 버전을 이어 붙이는 저장소는 아닙니다.

각 행은 원천 문자열 `source_fields`, 정규화된 값, 필드별 `issues`, `quality`, `provenance`를 갖습니다. 허용된 원천 필드만 보존하고 미지 필드는 값 없이 이름을 감사에 기록합니다. 오류 메시지는 고정 코드만 사용하므로 원문·키가 포함된 요청 URL·공급자 오류 문장을 로그로 전달하지 않습니다.

| 상태/필드 | 규칙 |
|---|---|
| `issues[].code` | `missing`, `invalid_format`, `out_of_range`, `scope_mismatch`, `unknown_value`, `ambiguous` |
| `quality` | 오류 없음 `valid`; 결측만 있음 `incomplete`; 형식·범위·정합 오류 있음 `invalid` |
| `price_krw` | 양의 안전 정수, 원천 변환상 10,000원 배수. 0·소수 만원·잘못된 쉼표·범위 초과는 오류이며 값은 null |
| `area_m2` | 정규 소수 문자열. 예: `84.9900` → `84.99`. 지수 표기·7자리 이상 소수·0 이하는 거부 |
| `complex_id` | `molit-apt:<조회 시군구>:<aptSeq>`. `sggCd`가 조회 범위와 다르거나 식별자가 없으면 null. `aptSeq` 자체는 불투명한 원천 ID로 취급하여 앞 5자리를 현행 행정코드라고 해석하지 않음. 같은 단지명만으로 병합하지 않음 |
| `legal_dong_code` | 형식이 맞는 `sggCd`와 `umdCd`를 연결한 10자리. 공식 코드표와 지적 경계 연결은 후속 단계 |
| `position` | null 고정. 이름·지번·역 근접성으로 임의 좌표 생성 없음 |

가격 비교에 필요한 면적은 최대 소수 6자리까지 보존합니다. 면적 10,000㎡, 층 -100~1,000, 건축연도 1~수집 연도를 방어적 처리 한도로 둡니다. 이를 공식 제공기관의 실측 허용 범위로 주장하지 않습니다. 한도 밖 값을 자르거나 정상값으로 바꾸지 않고 검토 대상으로 남깁니다.

### 날짜와 취소

계약일은 KST 달력 날짜입니다. 등기/해제일은 `YYYYMMDD`, 4자리 연도의 점/하이픈 형식과 2자리 연도 형식을 처리합니다. 2자리 연도는 **계약일 이상·수집일 이하 범위에서 유일한 세기**만 채택합니다. 여러 후보가 가능하거나 범위를 벗어나면 오류로 남겨 임의로 2000년대를 붙이지 않습니다.

매매 취소 상태는 다음처럼 보수적으로 처리합니다. 공식 Swagger가 플래그의 전체 허용값 표를 제공하지 않으므로 낯선 값은 자동 채택하지 않습니다. 최근 두 달 실제 응답에는 이 규칙으로 분리한 해제 표시 행 1,064개가 있었습니다.

- `O` 또는 `Y`, 또는 비어 있지 않은 해제일 필드: `cancelled`. 해제일 형식이 잘못돼도 기본 분석에 포함하지 않으며 오류를 별도 기록합니다.
- 비어 있지 않은 낯선 플래그만 존재: `unknown`, `unknown_value` 오류. 정상 거래로 우회하지 않습니다.
- 플래그와 해제일 표시가 모두 없음: `not_reported`, 즉 **취소 표시를 받지 못함**. 거래가 미래에도 확정 유지된다는 뜻은 아닙니다.

모든 행은 `records`에 남깁니다. 취소·알 수 없는 해제 상태와 계약 숫자·기간·지역 오류는 기본 통계에서 제외합니다. 웹 게시 계약은 원천 필드 전체의 `quality`와 `statistics_eligible`을 분리합니다. 지번이 `가-`, `BL-2-2` 같은 비표준 문자열인 행은 위치 연결 검토 대상으로 남기지만, 명시적 지역·계약일·면적·금액이 유효하면 거래 통계에서 버리지 않습니다. `invalid_rows`는 전체 필드 오류 행 수이고 `statistics_excluded_rows`는 실제 통계 제외 행 수입니다. 좌표와 구조화된 지번을 임의로 만들지는 않습니다.

기존 매매 전용 `summarizeTransactions`는 v1 호환 규칙으로 `quality: invalid` 전체를 제외합니다. 현재 웹 비교는 필드별 적격성을 분리한 `summarizePropertyTransactions`를 사용합니다. 정확한 단지 비교에는 단지 ID·전용면적·계약일·가격이 필요하고, 이름만 같은 자료를 결합하지 않습니다.

### 거래 ID와 페이지 정합성

원천 단지 ID를 거래 ID로 사용하지 않습니다. `id`는 정규 원천 필드의 SHA와 동일 행의 출현 순번으로 구성한 **스냅샷 행 식별자**입니다. 계약일·면적·층·가격이 모두 같은 별도 실제 계약이 존재할 수 있으므로 동일 행도 원천 출현 횟수를 보존합니다. 감사의 `identical_row_occurrences`는 고유 계약 수가 확인됐다는 뜻이 아닙니다.

동일 페이지의 동일 입력을 반복 전달한 경우만 한 번 처리합니다. 같은 지역/월/페이지에 다른 SHA 또는 메타정보가 들어오면 실패합니다. 전체 페이지 번호, `totalCount`, 페이지 크기, 페이지별 실제 행 수를 대조하고 페이지 누락이나 총수 변경을 거부합니다. 한 월의 페이지 수집시각 차이가 15분을 넘는 입력도 거부합니다. 이 검사는 조회 중 제공기관 데이터가 절대 변하지 않았다는 보장은 아닙니다.

파일 입력 순서와 무관하게 지역·월·페이지·행 ID를 정렬하고 고정 JSON 직렬화를 사용합니다. 같은 XML·범위·수집시각을 재처리하면 같은 JSON과 SHA를 만듭니다. 새 수집시각·취소/등기 갱신으로 달라진 행은 새 스냅샷이며, 이전 스냅샷과 거래 단위로 무조건 합치지 않습니다.

### 같은 면적·기간의 요약

Python `summarize_transactions`와 TS `summarizeTransactions`는 `complex_id`, 정규 `area_m2`, 포함 경계의 `date_from/date_to`를 받습니다. 취소·오류를 제외한 신고 행의 `count`와 정수원 중앙값 `median_price_krw`를 반환합니다. 0건 중앙값은 null이며 0원이 아닙니다. 자료형의 `statistic: reported-row-median`은 감정가·호가·향후 가격 전망이 아님을 명시합니다.

면적을 임의로 반올림해 다른 평형과 합치지 않습니다. 단지 비교는 동일한 면적·기간 조건을 각각의 공식 단지 ID에 적용한 결과를 나란히 표시하는 방식입니다. 동일 행 ID가 중복 입력되면 중복 스냅샷으로 실패합니다. 서로 다른 스냅샷 버전의 합성·대표 평형 연결·취소 정정 이력의 연계는 후속 저장 계약이 필요합니다.

## 전월세 및 게시용 공용 계약

[전월세 15126474](https://www.data.go.kr/data/15126474/openapi.do)는 `RTMSDataSvcAptRent/getRTMSDataSvcAptRent`의 XML을 같은 페이지 검증기로 처리합니다. `deposit`, `monthlyRent`, `preDeposit`, `preMonthlyRent`를 각각 현재·종전 보증금/월세의 정수원으로 보존합니다. 0원 월세는 전세일 수 있으므로 유효하며, 보증금과 월세가 동시에 0이거나 음수·비정규 금액이면 오류로 남깁니다. `contractTerm`, `contractType`, `useRRRight`는 원천 문자열로 보존하고 임의의 계약 해석을 추가하지 않습니다.

전월세 명세에는 매매의 해제 표식이 없으므로 `cancellation: not_provided`로 표시합니다. 매매의 `not_reported`와 구별하며, 취소 확인 완료라고 표시하지 않습니다. 2026-09-20 실제 승인 응답에서 Swagger 기본 필드 외 `aptSeq`와 소문자 `roadnm*` 필드가 관찰되어 허용 목록에 추가했습니다. 실제 `aptSeq`가 있는 행만 같은 조회 지역의 동일한 원천 단지 ID로 묶으며 이름·주소만으로 합치지 않습니다.

초기 정규화의 `aptSeq` 접두어 검사로 실제 16,095행이 과도하게 제외되는 문제를 발견했습니다. 현재 변환 이름은 `apartment-sale-report-v2-opaque-apt-seq`와 `apartment-rent-report-v2-opaque-apt-seq`입니다. `sggCd` 범위 검사는 유지하고 `aptSeq`는 원문 식별자 그대로 보존합니다. 기존 v1 파일도 TypeScript에서 읽을 수 있으며, 현재 원본을 재처리한 새 스냅샷은 별도 SHA로 추가합니다. 원본 XML과 초기 후보를 덮어쓰지 않습니다.

웹은 `shared/property.ts`의 `PropertyRelease → PropertyRegions → PropertyRegionDetail → PropertyTransactions/PropertyComplexes`를 읽습니다. 미수집·실패·부분 응답의 수치와 중앙값은 null이며, 원천이 명시적으로 성공한 0건만 `empty`와 0으로 표시합니다. 각 자산은 SHA와 바이트 수를 검사한 후 디코드합니다. 지역월 한 파일에 매매와 전월세가 함께 들어갈 수 있어 소비자는 `trade_type`으로 분리합니다.

지역 지표의 원/㎡ 중앙값은 취소·계약 오류를 제외한 개별 신고 가격/전용면적 분포이며, 같은 평형 단지 비교나 시세 지수가 아닙니다. 단지 비교용 `summarizePropertyTransactions`는 정확한 단지 ID·거래유형·전용면적·기간을 고정합니다. 전월세에는 보증금/월세 중앙값을 별도로 제공하며 전월세 전환율을 임의 적용하지 않습니다. 디코더는 선언된 `statistics_eligible`을 필드·품질 사유와 다시 계산해 비교하므로 임의로 true를 붙인 잘못된 금액을 허용하지 않습니다.

## 입력·실행·출력

수집자에게 받은 UTF-8 XML과 아래 메타파일을 같은 비공개 폴더에 둡니다. 상대 경로는 메타파일 폴더 안으로 제한하며 심볼릭 링크·정션을 거부합니다. 메타파일에 서비스키나 요청 URL은 넣지 않습니다.

```json
{
  "schema_version": 1,
  "pages": [
    {
      "path": "page-001.xml",
      "lawd_code": "11110",
      "deal_month": "202609",
      "retrieved_at": "2026-09-20T00:00:00Z"
    }
  ]
}
```

```powershell
.\.venv\Scripts\python.exe -B -m pipeline.real_estate --manifest .local\real-estate-input\input.json --output .local\real-estate-normalized-v1
```

출력은 새 폴더의 `11110/202609.json` 등 지역/월 파일과 SHA·바이트·행 수를 담은 `manifest.json`입니다. 기존 출력 폴더가 있으면 덮어쓰지 않습니다. 별도 준비 폴더에 모두 기록·해시 검증한 뒤 폴더를 전환하며, 실패한 준비 폴더는 검사할 수 있도록 남깁니다. `public`·`dist`를 출력 경로로 쓰는 것은 거부합니다. 배포 목록을 수정하는 기능은 없습니다.

방어 한도는 XML 1페이지 8MiB, 배치 입력 64MiB, 페이지당 10,000행, 전체 100,000행, 입력 페이지 1,000개입니다. 전국 원본을 한 번에 메모리에 넣는 도구가 아닙니다. DTD·엔티티 선언, 중복 XML 필드, 잘못된 인코딩, 불완전 성공 응답을 거부합니다. 인증·호출량 오류는 각각 `upstream_auth`, `upstream_quota`로 분리합니다.

## 검증과 남은 연결

2026-09-20 집중 검증에서 **Python 49개, TypeScript 22개**가 통과했습니다. 정수원/정밀 면적, 날짜 의미, 필드별 오류, 취소·낯선 플래그 제외, 이름만 같은 단지, 동일 행 횟수, 중복/누락 페이지, 입력 순서, XML 보안, 출력 해시·덮어쓰기 방지·실패 시 최종 폴더 미노출과 오프라인 CLI를 확인했습니다. 새 TS 2파일의 독립 타입 검사·린트도 통과했습니다. Python이 만든 가상 4행 Partition을 TS로 직접 디코드한 추가 검사에서 요약 2건·중앙값 100,015,000원이 양쪽에서 일치했습니다. 이 숫자는 테스트 값이며 실제 거래 결과가 아닙니다. 전체 저장소의 동시 개발분을 포함한 통합 검사는 별도로 수행합니다.

후속 수집·게시 회귀를 합친 집중 검증은 Python 82개·TypeScript 38개가 통과했습니다. 인증 실패와 정상 빈 응답, 호출 예약의 재시작 보존, 반사된 키의 저장 거부, 원본 변경 감지, 단일 수집 임대와 만료 소유자의 상태 덮어쓰기 거부, 새 변환의 원본 재처리, 월 필터 밖 상태 보존, 파일 기록 중단 시 불완전 최종 파일 미노출, 지번/가격 품질 분리, 결정론적 gzip 스냅샷과 압축 해제 한도·변조 거부 및 저장 공간 하한을 포함합니다. 공용 TS의 독립 타입 검사와 린트도 통과했습니다. 실제 후보의 모든 파일에 대한 재파싱·TS 디코딩·SHA·partition 건수 결과는 운영 문서의 불변 릴리스별 증빙으로 확인합니다.

남은 데이터 작업은 과거 60개월 확장, 폐지·변경 법정코드의 효력일 대조, 공식 단지·건물·필지와 좌표 연결입니다. 재수집 시 이전·현재 스냅샷과 행 변화는 보존하지만, 원천에 거래 고유번호가 없으므로 이를 정정 거래의 확정 연결로 표현하지 않습니다. 실제 API 수집 및 로컬 후보 검증, 화면 통합, 공개 배포는 별도 완료 조건입니다.
