# 노트북 임시 거래 원본 보관

우선 9개 권역의 국토부 거래 수집 원본과 체크포인트는 공개 지도와 별도로 보관합니다. 로컬 보관기는 `pipeline.real_estate_local_archive`이며, 기존 `pipeline.real_estate_archive`의 파일 해시·체크포인트 참조 감사를 그대로 사용합니다. 원본을 공개 저장소나 Pages에 올리는 도구가 아닙니다.

수집 루트 하나에 대해 다음 순서로 실행합니다. 복구 대상은 **새 빈 디렉터리**여야 하며, 운영 중인 수집 루트 위에 복원하지 않습니다.

```powershell
.venv\Scripts\python.exe -m pipeline.real_estate_local_archive backup --root <수집-루트> --store <백업-루트>
.venv\Scripts\python.exe -m pipeline.real_estate_local_archive status --store <백업-루트>
.venv\Scripts\python.exe -m pipeline.real_estate_local_archive restore --root <새-복구-루트> --store <백업-루트>
```

기본값은 백업을 시작할 때 디스크 여유 30GiB를 남깁니다. 객체는 내용 해시로 한 번씩 저장하고, 기존 head와 역사적 head는 덮어쓰지 않습니다. 새 head는 모든 파일·SQLite·참조 감사를 통과한 뒤에만 교체합니다. 같은 백업을 다시 실행해도 기존 원본 파일을 삭제하지 않습니다. 복구 결과는 원본 해시와 체크포인트 감사 결과를 다시 비교해야 합니다.

이 보관 방식은 작업용 체크포인트의 손상이나 실수로 인한 경로 삭제에 대비한 **노트북 내부 복제**입니다. 같은 물리 장치의 디스크 고장·분실까지 복구하는 장기 보관은 아닙니다. 별도 장치나 사용자가 공간을 확인한 VPS에 검증된 복제본을 보관하기 전에는 ‘장기 보관·복구 확정’으로 표시하지 않습니다.

## 8GiB 분할 복원 목록

기존 `backup`의 20GiB 총량 상한은 유지합니다. 더 큰 로컬 자료를 위한 별도 형식은 `pipeline.real_estate_local_archive_set`입니다. 기존 `head.json`을 바꾸지 않고 `set-head.json`과 `set-heads/`에 전체 복원 목록을 남깁니다. 원본 객체 저장소는 공유하므로 기존 백업의 검증된 객체를 복제하지 않고 재사용합니다. 기존 원격 XZ·zlib 객체, v1/v2 manifest와 과거 복원 경로는 변경하지 않습니다.

```powershell
.venv\Scripts\python.exe -m pipeline.real_estate_local_archive plan-set --root <수집-루트> --store <백업-루트>
.venv\Scripts\python.exe -m pipeline.real_estate_local_archive backup-set --root <수집-루트> --store <백업-루트>
.venv\Scripts\python.exe -m pipeline.real_estate_local_archive restore-set --root <새-복구-루트> --store <백업-루트>
```

- 그룹은 기관 작업의 **주택 유형·거래 유형·지역 코드·계약연도**를 기준으로 구성합니다. 원천 응답이 여러 작업에 공유되면 `shared`, 체크포인트는 `control`, 수집 실행 기록·등록부 등은 `metadata`로 구분합니다. 과거 재조회로 현재 장부 참조에서 빠진 원본 XML도 원래 수집 경로의 지역·월을 보존합니다.
- 각 그룹은 원본 바이트 합계뿐 아니라 **참조 객체 전체와 그룹 색인의 합계도 최대 8GiB**가 되도록 분할합니다. 다른 그룹 자료가 함께 든 기존 pack을 재사용할 때도 그 객체 전체 크기를 계산합니다. 작은 XML이 많으면 색인 크기 기준으로 먼저 분할할 수 있습니다. 이 묶음은 **내용 해시 객체를 참조하는 복원 목록**이며, 단일 8GiB ZIP 파일이 아닙니다. 보관·이전 시 `objects/`와 해당 복원 목록을 함께 복사해야 합니다.
- 전체 복원 목록은 그룹별 SHA·파일 수·원본 바이트와 전체 SHA·체크포인트 감사 결과를 담습니다. 복원에 과거 manifest를 순서대로 적용할 필요가 없습니다. 모든 참조 객체를 먼저 확인하고 새 빈 디렉터리에 복원한 뒤 SQLite·원본 SHA·장부 참조를 재검사합니다. 새 set이 아직 없으면 `restore-set`은 기존 백업을 복원합니다.
- 원본 ID나 파일을 삭제하지 않습니다. 이전 복원 목록의 파일이 현재 수집 루트에서 사라졌거나 객체 SHA가 달라지면 새 head를 게시하지 않습니다. 중단된 객체는 완료 head에 들어가지 않으며 다음 실행에서 내용 해시로 재사용할 수 있습니다. 부분 복원 디렉터리도 자동 삭제하지 않습니다.
- SQLite 체크포인트는 기존 검증된 64KiB slice 계약을 재사용합니다. 첫 백업 이후 변경되지 않은 slice는 기존 객체를 참조하고 바뀐 slice만 새 객체로 보존합니다. 시간당 수집에서 매번 전체 체크포인트를 복제하는 누적을 줄이며, 결과의 `checkpoint_changed_bytes`로 실제 새 바이트를 확인합니다. 복원 목록은 필요한 모든 slice의 평면 객체를 직접 참조합니다.
- `plan-set`은 원본 전체 크기, 새 객체의 보수적인 최대 크기, SQLite 사본·색인·한 번의 전체 복원에 필요한 추가 공간을 반환합니다. 백업과 복원은 각각 30GiB 여유를 지키며 부족하면 `disk_reserve`로 중단합니다. 운영 중인 collector의 lease가 있으면 백업을 거부합니다. 수집 종료 뒤에 실행해야 합니다.
- 기존 안전 계약의 **단일 원본 파일 256MiB·전체 파일 300,000개·그룹 색인 16MiB·전체 그룹 색인 128MiB** 상한은 유지합니다. 20년 전체가 현재 저장 공간에 들어가는지는 실제 장부·원본·파생 파일과 이 공간 계획으로 별도 판정합니다. 분할 형식 구현을 전체 수집이나 장기 보관 완료로 표시하지 않습니다.

기본 `backup`/`restore` 명령과 실행 중인 예약 수집기는 자동으로 새 형식으로 바뀌지 않습니다. 검증된 사본의 `backup-set`→새 디렉터리 `restore-set`→감사 결과 일치를 확인한 뒤, 운영 담당자가 다음 예약 실행에 새 백업 호출을 적용해야 합니다. `status`는 기존 `head`와 새 `set_head`를 함께 보여줍니다.

GitHub Actions와 노트북 수집기를 동시에 실행하지 않습니다. 전환 시점에는 원격 head와 모든 원본 파일 해시를 검증하고, 원격 수집 중지·새 작업 루트 생성·첫 로컬 수집·백업에서 새 경로로 복원 시험을 차례로 확인합니다. 노트북이 꺼져도 이미 게시된 Pages 서비스는 열리지만 수집은 재개할 때까지 멈춥니다.
