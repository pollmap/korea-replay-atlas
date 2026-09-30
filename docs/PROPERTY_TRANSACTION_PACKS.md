# 20년 월별 자료의 정적 파일 수 제한

112개 조회 지역 × 240개 계약월은 거래 유형을 한 파일에 함께 담아도 최소 26,880개 월별 파일입니다. 따라서 기존 “지역·월마다 파일 한 개”를 20년 전체에 그대로 적용하면 Pages Free의 20,000개 한도를 넘을 수 있습니다. **원본 장기 보관 가능성·실제 20년 수집 완료·20년 전체 정적 게시 가능성은 별도 검사**해야 합니다.

## 거래 원문 묶음

`pipeline.real_estate_transaction_pack`는 원본 재파싱을 마친 비공개 게시 후보를 읽어 새 파생 릴리스를 만듭니다. 원본 후보는 변경하지 않습니다. 원천 행, ID, 원본 SHA, 취소·등기·품질 필드를 그대로 보존합니다. 입력·출력의 모든 거래 ID 순서 해시와 행 수가 일치해야 성공합니다. 각 파일은 최대 4MiB이고 한 파일에 여러 계약월을 담습니다.

원래 V1 지역 인덱스의 `partitions[].transactions` 참조 구조를 유지합니다. 여러 월이 같은 참조를 공유할 수 있으므로 캐시와 중복 요청도 공유할 수 있습니다. 새 파일은 아래 형태입니다.

```json
{
  "schema_version": 1,
  "kind": "property-transaction-pack",
  "release_id": "property-<16자리 해시>",
  "lawd_code": "11110",
  "months": [{"deal_month": "202609", "transactions": []}]
}
```

클라이언트는 요청한 정확한 계약월을 먼저 꺼낸 뒤 기존 거래 행 검증을 적용해야 합니다. 파일에 다른 월이 있다는 이유로 해당 월의 거래를 섞으면 안 됩니다. 기존 `property-transactions` 파일과 과거 공유 링크의 파서는 계속 지원합니다. 정상 0건·미수집·원천 제공 전 상태는 지역 인덱스에 보존하며 실패한 월에 성공 참조를 생성하지 않습니다.

```powershell
python -B -m pipeline.real_estate_transaction_pack --publication <기존 publication.json> --output .local/property-packed-candidates
```

원문 재파싱 후보가 18,000개를 넘는 경우 기존 `real_estate_publish --private-packing-input`을 명시해 최대 100,000개 파일의 **비공개 중간 자료**를 생성할 수 있습니다. 기본값 18,000개 제한은 그대로입니다. 이 옵션은 Pages 한도를 늘리지 않습니다. 최종 묶음 후보와 전체 지도·요약 자산을 합친 실제 배포는 다시 18,000개 목표·20,000개 상한을 검사해야 합니다.

## 월별 단지 요약 묶음

`pipeline.real_estate_summary_month_pack`는 기존 단지·면적 요약을 새 불변 요약 버전으로 묶습니다. 단지별 512KiB 조회 묶음과 `complex_chunks`는 보존하고, 월별 지도 조회 파일만 최대 4MiB의 `property-complex-summary-month-pack`으로 재구성합니다. 지역의 `summaries`는 기존처럼 `{deal_month,url,sha256,bytes}` 형태이고 여러 월이 동일 URL을 공유할 수 있습니다. 파일은 `months:[{deal_month,rows}]`를 제공합니다.

입력·출력의 모든 요약 키와 값의 해시, 요약이 대표하는 거래 행 수를 확인합니다. 월별 상태·정정 대기·미수집을 그대로 유지합니다. 단지 요약을 원문 거래나 현재 호가처럼 취급하지 않습니다.

```powershell
python -B -m pipeline.real_estate_summary_month_pack --publication <요약 publication.json> --output .local/property-summary-month-packs
python -B -m pytest tests/test_real_estate_transaction_pack.py tests/test_real_estate_summary_month_pack.py -q
```

이는 파일 수를 줄이는 구현입니다. 현재 부분 수집 자료가 최종 한도에 들어갔다고 해서 20년 전체의 미래 바이트·파일 수가 보장되는 것은 아닙니다. 수집과 정정이 진행될 때마다 전체 파일 수·크기·업로드·복구·브라우저 요청량을 검사합니다.

## 2026-10-01 후보 검증

아래 결과는 후보 생성과 데이터 감사까지의 결과이며 공개 배포 완료와 구분합니다.

| 대상 | 입력 → 묶음 파일 수 | 묶음 바이트 | 보존 검사 |
|---|---:|---:|---|
| 거래 `property-51bfb7ca79924838` → `property-8879dff1b31ac5f0` | 5,850 → 1,406 | 3,245,170,592 | 원천 3,161,421개 거래 ID의 전체 순서 해시 일치 |
| 연결 요약 `summary-010e97b5dc51c794` → `summary-49ad802d41baf9f8` | 7,560 → 2,734 | 2,039,498,074 | 1,558,258개 요약 그룹의 전체 키·값 해시 일치, 적격 원천 3,113,850행 유지 |
| 과거 공개 거래용 요약 `summary-3bd848e301667a3e` → `summary-4a47118c4c45ee05` | 6,450 → 2,313 | 1,646,821,222 | 기존 공개 `property-8deba5b9951e48da`에 고정된 별도 요약 |

신규 원문·요약은 총 4,140개 파일입니다. 가장 큰 파일은 원문 4,190,073B·요약 4,187,821B로 4MiB 상한을 지켰습니다. 과거 공유를 위한 기존 파일, 다른 지도 자산, 배포 설정 파일까지 더한 전체 파일 수는 별도 검증합니다.

거래 전체 ID 해시는 `b6c4066b9d6f9ec778419d7f4fd78a8640c1488b59717178259d17aaf2a5a6a5`, 최종 요약 그룹 해시는 `c5054b2d549f06849d3021cf3c1cfa7a381b5433a314fd52a97fbb07a2667a12`입니다. 파생 작업은 원천 API 호출·장부 쓰기 없이 진행했습니다. 비기본 바이트 예산을 사용하면 다른 불변 릴리스 ID를 생성해 기존 경로를 덮어쓰거나 캐시 결과를 잘못 재사용하지 않습니다.
