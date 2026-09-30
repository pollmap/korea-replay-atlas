# 단지·월·전용면적 요약 자료

검증 대상은 국토교통부 아파트 매매·전월세의 **한 가지 고정된 공개 데이터 릴리스**입니다. 전체 거래 원문을 먼저 내려받지 않고 단지 차트와 지도 가격 조건을 처리하도록 별도 요약을 생성합니다. 이 파이프라인은 수집 장부, 공개 버전 포인터, 단지 좌표를 변경하지 않습니다.

## 생성과 감사

`pipeline.real_estate_complex_summary`는 기존 `real_estate_publish`의 원본 재파싱·해시 감사 결과를 요구합니다. 입력 파일 전체의 크기·SHA-256, 지역·계약월, 거래 ID 중복, 공개 원천 행 수, 통계 적격 행 수를 검사합니다. 요약의 거래 건수 합계가 적격 원천 행 수와 맞는지도 확인합니다. 같은 속성의 거래 여러 건은 별도 거래 ID를 유지해 여러 건으로 셉니다.

요약 키는 **원천 단지 ID × 계약월 × 거래 유형 × 전세/월세 × 정확한 전용면적**입니다. 84.99㎡와 85㎡는 합치지 않습니다. 정수 원화로 중앙값·최저·최고·최근 계약의 금액을 계산합니다. 평당가는 전용면적 기준이며 공급면적이나 추정한 32평을 사용하지 않습니다. 동일 날짜의 최근 거래는 수집시각·거래 ID 순으로 결정합니다. 이를 현재 호가나 공식 가격지수로 표시하지 않습니다.

매매는 신고 취소·비적격 행을 제외한 원천의 `statistics_eligible` 조건을 따릅니다. 전월세는 보증금과 월세가 모두 원문에 있을 때만 전세/월세로 나눕니다. 원문 월세 0원은 전세로 구분하지만 누락을 0원으로 바꾸지는 않습니다. 단지 ID 미연결 행과 금액 누락 행은 감사에 별도로 남깁니다. 이 파생 자료는 새로운 좌표 연결 근거가 아닙니다.

## 조회 계약

경로는 `/data/property-summary/summary-<16자리 해시>/` 아래에 고정합니다.

| 자산 | 계약 |
|---|---|
| `manifest.json` | `property-complex-summary-release`: 정확한 `property_release_id`, 기간, 지역별 해시 참조, 전수 감사 |
| `regions/<코드>.json` | `property-complex-summary-region`: 월별 수집 상태, 해당 월 `summaries` 참조, 단지별 `complex_chunks` 참조 |
| `rows/<코드>/<계약월>-<순번>.json` | `property-complex-summaries`: 한 계약월의 단지·면적별 요약, 최대 4MiB |
| `complexes/<코드>/<순번>.json` | `property-complex-summary-pack`: 여러 계약월의 요약을 단지 ID 순으로 묶은 최대 512KiB 파일 |

지역 `summaries`는 `deal_month`와 자산의 URL·크기·해시를 포함합니다. `complex_chunks[complex_id]`는 그 단지가 들어 있는 묶음만 가리키며 각 참조의 `from_month`·`to_month`로 기간 밖 묶음을 제외할 수 있습니다. 묶음 안에는 다른 단지도 있을 수 있으므로 **단지 ID와 면적·거래 유형 조건을 다시 적용**합니다. 참조 범위는 해당 단지의 실제 수록 월이고 묶음 본문의 범위는 전체 묶음의 실제 수록 월입니다.

월별 상태는 준비된 정상 0건(`empty`), 미수집, 부분 수집, 실패, 원천 제공 전(`source_unavailable`)을 구분합니다. 실패한 정정 조회가 있더라도 이전에 감사된 거래가 공개 릴리스에 보존됐다면 `refresh` 상태도 유지합니다. 자료 없는 월을 다른 월의 가격으로 채우거나 차트 선으로 잇지 않습니다.

행에는 `transaction_count`, 최근 계약 ID·일자·수집일, 매매 총액 또는 전월세 보증금·월세, 매매 중앙값·최저·최고, 전용 ㎡당·평당 중앙값이 있습니다. 전월세 단위가격의 `price_basis`는 `deposit`이며 월세를 매매 총액으로 바꾸지 않습니다.

## 실행

```powershell
python -B -m pipeline.real_estate_complex_summary --publication <비공개 후보의 publication.json> --output .local/property-complex-summaries
python -B -m pytest tests/test_real_estate_complex_summary.py -q
```

기본 파일 수 18,000개와 자산별 크기 상한을 넘으면 후보 생성을 실패시킵니다. 20년 중간 월별 요약이 한도를 넘는 경우 `--private-packing-input`을 명시해 비공개 중간 자료만 최대 100,000개로 만들고 `real_estate_summary_month_pack`으로 재구성합니다. 기본 모드로 다시 검증할 때 캐시된 중간 후보도 파일 수 제한을 적용합니다. 최종 요약팩은 18,000개 제한을 유지합니다. 원문·기존 후보는 보존하며 **지도·거래 원문과 합친 실제 Pages 배포의 20,000개 한도는 배포 단계에서 다시 검사**해야 합니다.

요약 전체를 브라우저가 다운로드하면 성능 개선으로 볼 수 없습니다. 지도는 선택한 월·지역만, 단지 상세는 `complex_chunks`의 필요한 묶음만 받아야 합니다. 큰 단지의 모든 면적·20년 자료가 언제나 2MiB 안에 들어간다고 보장하지 않습니다. 조건별 실제 다운로드량·캐시를 측정하고 요청 예산 초과를 명확히 처리합니다.

## 출처와 이용조건

공식 [아파트 매매 신고상세](https://www.data.go.kr/data/15126468/openapi.do)와 [아파트 전월세 신고·확정일자](https://www.data.go.kr/data/15126474/openapi.do)에서 확보한 검증본의 파생 통계입니다. 거래 원문·출처·취소 상태의 계약은 기존 [데이터 계약](REAL_ESTATE_DATA_CONTRACT.md)을 유지합니다. 코드의 MIT 라이선스를 외부 원천 데이터의 이용조건으로 대신하지 않습니다.
