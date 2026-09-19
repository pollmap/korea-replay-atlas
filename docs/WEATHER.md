# 기상 자료와 검증

확인일 2026-09-16. 지도에 연결된 실제 파일과 인증 이후 수집할 원시 자료를 구분합니다.

| 자료 | 현재 연결 범위 | 표현과 한계 |
|---|---|---|
| GK2A SW038 | 2022-01-01 00:00:32 UTC 공식 예제 1장 | 원본 2km. 공식 v3.0 보정표의 밝기온도. 낮에는 반사광 영향이 있어 순수 구름 온도로 단정하지 않음 |
| 기상청 SFC-HSR 공개 영상 | 2026-09-16 13:20~14:20 KST, 5분 간격 13장 | 640×640, 공식 LCC 범위로 재투영. 원래 색상을 유지하며 dBZ/mm/h 숫자로 역산하지 않음 |
| HSR ASCII / CF 위성 원시 어댑터 | 코드·합성 테스트 존재, 인증 수집 미연결 | 발급받은 KMA 키와 사용 조건 필요. 현재 공개 영상과 데이터 의미가 다름 |

## 실제 처리

위성 공식 다운로드 응답은 `.nc` 이름에도 ZIP 컨테이너였습니다. 지정된 NetCDF만 추출하고 원본 SHA-256을 보존했습니다. DN 상위 품질 비트를 분리하며 good=0만 보정합니다. conditional/space/error 표본은 구름처럼 표시하지 않습니다. 공식 엑셀 보정표와 파일 gain/offset을 대조했습니다.

GEOS 투영은 파일의 적도·극 반경, 지구 중심 기준 위성 높이, sub-longitude, CFAC/LFAC/COFF/LOFF를 사용합니다. 북쪽 행 방향과 위성 직하점 위치를 검증했습니다. 지도용 1536×1168 출력은 리샘플링 크기이며 원래 관측 해상도가 좋아진 뜻이 아닙니다.

레이더는 [기상청 공개 지도](https://www.weather.go.kr/w/image/radar.do)가 사용하는 프레임 목록과 이미지, [공식 투영 정의](https://www.weather.go.kr/w/resources/js/kmap.bb.js?ver=202607091110)를 보존했습니다. 이미지 URL의 시각·제품이 목록과 다르거나 범위·CRS·640×640 구조가 바뀌면 공개를 중단합니다. 요청 시각 뒤에 추가로 반환된 1개 프레임은 제외했습니다. 회색 배경에서 무에코와 자료 결측을 구분할 수 없다는 한계를 표시합니다.

보유 기록 날짜 선택은 실제 공개 자산에서 계산합니다. 기록이 없는 날짜를 추가하지 않습니다. 각 프레임은 정해진 유효 구간에만 표시하며 오래된 마지막 영상을 무제한 유지하지 않습니다. 위성 예제의 10분 유효 구간은 제품 표시 간격에 따른 설정입니다.

## 재현

```powershell
.venv\Scripts\python.exe -m pipeline.satellite --official-example
.venv\Scripts\python.exe -m pipeline.public_radar --time 202609161420 --limit 13
.venv\Scripts\python.exe -m pytest tests/test_weather.py tests/test_public_radar.py -q
```

공개 레이더의 오래된 시각은 제공 사이트에서 더 이상 내려주지 않을 수 있습니다. 저장한 원본과 해시가 재현 기준입니다. 수집기는 호출당 최대25개만 허용하며, 이 코드는 전국 장기 관측 수집이 자동 운영되고 있다는 뜻이 아닙니다.

감사 파일: `.local/audit/satellite-gk2a.json`, `.local/audit/public-radar.json`. 공개 프레임 manifest에는 원본 해시·보정/투영 버전·단위·관측 시각·출처가 포함됩니다. [국가기상위성센터 공식 소프트웨어·예제](https://nmsc.kma.go.kr/enhome/html/base/cmm/selectPage.do?page=static.utilization.software)를 기준으로 처리했습니다.
