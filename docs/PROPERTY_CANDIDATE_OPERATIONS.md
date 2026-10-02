# VPS 감사 후보 생성

수집과 공개를 분리합니다. `pipeline.property_candidate`는 닫힌 조회본 하나를 고정해 원문 재파싱·SHA·페이지 재현성 검사를 통과한 거래 후보와 월별 요약을 만듭니다. 수집기/API/공개 버전을 변경하지 않습니다.

## 실행

```sh
python3 -m pipeline.property_candidate --data /srv/services/korea-replay/shared/data --output /srv/services/korea-replay/shared/candidates
```

- 수집 원본은 읽기만 하고 원천 API를 호출하지 않습니다. 중복 후보 실행은 파일 잠금으로 거부합니다.
- `status.json`의 고정 조회본을 사용합니다. 중단·실패 후 재실행도 같은 세대에서 재개합니다. 완료된 중간 릴리스는 전수 파일 해시 검증 후 재사용합니다. 미완료 단계는 다시 생성하며 기존 파일을 삭제하지 않습니다.
- raw-candidates → transactions → summaries → summary-packs 순서입니다. 거래 묶음의 모든 ID와 행 수, 월별 요약 묶음의 그룹·거래 건수를 각각 검증합니다. 같은 속성의 서로 다른 원천 행은 유지합니다.
- 30GiB 여유 보호 조건과 각 단계의 파일·개별 자산 크기 제한을 그대로 적용합니다. 자원이 부족하면 실패 상태를 기록하고 이전 `last-verified.json`을 유지합니다.
- `last-verified.json`은 모든 단계가 성공한 뒤 원자적으로 전환합니다. `public_release=false`입니다. Pages 업로드·기존 공개 ID 차이 감사·단지 위치 연결·공개 전환은 별도 단계이며 후보 완료로 대신하지 않습니다.
- 준비된 중간 파일과 과거 후보는 삭제하지 않습니다. 보관량을 확인하지 않고 무제한 일일 생성을 예약하지 않습니다.
- 조회본은 변경 중인 수집 DB가 아닙니다. 현재 manifest가 손상됐으면 새 후보는 시작하지 않습니다. 기존 공개본은 계속 유지됩니다.

## 운영 검증

테스트는 고정 이후 수집 DB 변경, 원본 훼손, 실패 후 마지막 정상 후보 보존, 재시도 동안 현재 조회본 변경, 중복 실행, 원본/공개 출력 금지, 거래 ID 보존을 검사합니다.

실제 최초 실행은 `korea-replay-candidate-20261003` 전용 systemd 작업으로 CPU 1코어, 메모리 2300MiB, 낮은 우선순위를 적용했습니다. 실행 로그는 private operations 디렉터리에 보관합니다. 최초 실데이터 전수 감사와 30GiB 보호 조건 통과 전 자동 공개 게시 완료로 판정하지 않습니다.

## 이전 공개본과의 연속성 검사

후보가 준비되면 `pipeline.property_continuity`로 실제 이전 공개본과 대조합니다.

```sh
python3 -m pipeline.property_continuity \
  --previous /private/previous/publication.json \
  --candidate /private/candidate/publication.json \
  --report /private/continuity-report.json
```

- 원본 월별 파일과 월 묶음 형식 모두 읽고, 참조 파일의 SHA·크기·릴리스·지역·월·거래 ID·행 수를 확인합니다. 동일 속성의 반복 거래도 각각의 ID로 비교합니다.
- 이전의 모든 ID를 검사합니다. 신규 거래가 늘어도 기존 ID 소실을 상쇄하지 않습니다. 전체 지역이 없어져도 검사 대상에서 제외하지 않습니다.
- 완료된 월이 미완료로 바뀌거나 더 오래된 스냅샷으로 되돌아가면 차단합니다. 같은 원천에서 ID가 사라진 경우도 차단합니다.
- 더 최근의 원천 정정으로 ID가 달라진 경우 `requires_source_revision_review`로 기록합니다. 정정 가능성은 자동 게시 승인과 다릅니다. 원문 차이와 취소·정정 근거를 검토해야 합니다.
- 종료 코드 0은 연속성 검사 통과, 2는 보고서가 생성됐지만 자동 전환 불가, 1은 입력·무결성 오류입니다. 보고서는 원문이나 인증키를 포함하지 않습니다.
- `automatic_transition_eligible`은 이 검사만의 결과입니다. 데이터 권한, 위치 연결, Pages 후보·브라우저·복구 검사까지 통과해야 공개할 수 있습니다. 도구 자체는 업로드나 공개 포인터 변경을 하지 않습니다.

2026-10-03 공개 앱 `2c891364`는 지도 전체 보기 검색 복귀 수정(PR68)을 포함합니다. 거래 데이터는 여전히 `property-8879dff1b31ac5f0`입니다. 별도 대규모 원문 감사 실행과 혼동하지 않습니다.
