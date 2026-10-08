# 2D 종료 전환 배포 감사 — 2026-10-09

## 변경 목적

완료된 3D 종료(PR95), 단지 기본정보 지연 로딩(PR96), 공식 지도 기준점(PR98)을 배포할 때 기존 경량 Pages 스테이징이 새 Worker와 신규 파일을 거절했습니다. 기존 데이터 감사는 유지하고 아래 검토된 변경만 명시적으로 허용합니다.

## Worker: 정확한 한 번의 바이트 전환

| 항목 | 이전 | 이후 |
|---|---|---|
| 경로 | `_worker.js/app/index.js` | 동일 |
| 바이트 | 157,095 | 149,458 |
| SHA-256 | `f2d2d04f607fdca15f50fb863bbb1fab94aef713851c89bc9eeb9dd14f218acf` | `d5547d557c8802ef8de00284844cfc375d1a4d9912f78fbaedd9e736b036c17d` |

실제 컴파일된 파일의 unified diff 218줄 전부를 검토했습니다. 변경은 두 구간입니다.

1. `sun` 출처 및 coverage 항목과 해당 항목의 ready 분기 제거.
2. 사용하지 않는 SunCalc CJS 구현 및 초기화 제거.

라우팅·API 처리기·바인딩·데이터 참조·인증 처리는 이 diff에서 바뀌지 않았습니다. 이후 다른 Worker 바이트는 이 전환으로 승인되지 않습니다. `retire3d=true`, 정확한 이전 SHA, 정확한 새 SHA/크기, 단일 `index.js` Worker 파일 구조를 함께 요구합니다. 이미 새 Worker인 기준본에서는 기존의 동일 바이트 검사 경로가 적용됩니다.

검사만 새 코드에 수행하고 실제 후보에는 옛 Worker를 넣던 경로도 수정했습니다. Python이 검사한 교체 파일과 Node가 후보에 넣는 파일을 동일하게 고정합니다. 후보 파일의 크기·해시는 기존 최종 검사를 다시 통과해야 합니다. Worker는 mutable `dist`와 하드링크하지 않고 별도 복사해 다음 빌드와 분리합니다.

## 추가 프런트엔드 파일

### 기본정보 지연 로딩

8개 JS 모듈의 default export를 각각 기존 정식 `src/data/*.json`과 Node `isDeepStrictEqual`로 비교했으며 모두 동일했습니다. 현재 릴리스 827개 행과 이전 릴리스별 839/842개 행의 값·식별자·원천 메타데이터를 바꾸지 않았습니다. 아래 승인된 JS와 원천 JSON의 해시를 모두 검사하며, 임의의 새 chunk family는 계속 거절합니다. 스테이징 과정에서는 JavaScript 모듈을 실행하지 않습니다.

| 모듈 | JS 바이트 | JS SHA-256 | 원천 JSON SHA-256 |
|---|---:|---|---|
| `seoul-apartment-facts` | 311,999 | `208b830a95d395342a2cc721214ccc56677d6b183c1484ab335845f87f5e617e` | `291e990283b91bb04cb0d7eaa8d73bac5b219c56f8602609cec655d82c9f1dbc` |
| `seoul-apartment-facts-2da3955e5d587c40` | 311,999 | `17844984298e6e491f1084f528b9e366aaee060afc1016d8f7a09170e2ced65a` | `6509648ced57410b61d16b4dd7a3ae9ddf8991fdfa6d21acca48cdf3f62cc782` |
| `seoul-apartment-facts-54bf1817fdcc7bd9` | 311,999 | `65bee6e8619c3141e7a428841b548f5496d899199907e61b7f5d4d911c690c0d` | `77a4a9b6cca3a94de8ed9d6a43d90d652b04010a7636eefabfad2c2d23fea378` |
| `seoul-apartment-facts-87d1c67336e97209` | 311,999 | `51d3dd77b5b3f88c51edb2799d3cf8e35c69ddf51ae30ef64d5d363c4e526a48` | `55f9bbc732b2b79d9ee9674e8c3a7184bb7960ff4126be369e01b7d899857a9e` |
| `seoul-apartment-facts-8879dff1b31ac5f0` | 310,902 | `1448b6208f63fa403a798ac3d20e1e7c0431096eb275a3c8718e58eea4026a16` | `3d90b898455bfa31badc34ac6b51cd2c397ed35044b5ea0cf367b74ec0687d28` |
| `seoul-apartment-facts-8deba5b9951e48da` | 311,999 | `4391f9ff2fb8ff8a24f09a52a3b9dd8de0cf156590b820c7476ec5e0facb4653` | `394976b3e4613806978ac8dd2703e815ad7587fc6bc68a6f1a19edd9ee0fd735` |
| `seoul-apartment-facts-b87eea7c1c03dc21` | 310,902 | `540e585a8c66fada20058be3db9db3bed69b15e8d4167fcecd3be74c14c606dc` | `8d8701a41203a89d41705ca829e125002cb5e3da0a5d5e51a6c91663f2df290e` |
| `seoul-apartment-facts-ceeff63959643461` | 306,466 | `85b756913a4c1da34cc168fca6cdd76f0e3108a4a79d8252766ff3f9661786cb` | `bcc11963a071e28f9bad8d5fdf88de9837d2560b7fc757ba521b959f6f860cff` |

### 공식 지도 기준점 근거

- 파일 계열: `seoul-property-navigation-evidence-ceeff63959643461-<vitehash>.json` 하나.
- 크기: 55,327바이트.
- SHA-256: `0c0109fe1c1bf3c59f33bab47b5e05154c2c25a80865c5b5ddaf6dbaf3e3874f`.
- ID·좌표와 의미 범위 검증은 `SEOUL_OFFICIAL_NAVIGATION_20261009.md` 및 생성기/클라이언트 회귀검사에 기록돼 있습니다. 다른 릴리스·변경된 바이트를 허용하지 않습니다.

## 유지되는 배포 제약

- `data/` 파일·불변 데이터 원점/manifest, 원본·수집 장부는 이 경로에서 가공하지 않습니다.
- 별도 `collection/` 자료는 **입력한 검증 기준본 그대로** 보존합니다. 기준본 artifact를 특정 과거 값으로 강제하지 않아 같은 앱의 다른 승인된 collection 갱신도 유지할 수 있습니다.
- Worker의 adapter·policy·routes는 교체 승인 대상이 아닙니다.
- 숨김 파일·알 수 없는 파일·링크·중복 경로·해시 변경·인증정보 유사 문자열 검사, 파일 수·크기 제한, 후보/대표주소의 동일 artifact 조건은 유지합니다.
- 새 배포를 우선 승인하는 사용자 제공 manifest나 검사 생략 옵션을 추가하지 않습니다. 승인 목록은 코드 검토 대상 상수입니다.

## 확인한 결과와 남은 게이트

- 기존 8141b255 기준본과 실제 새 프로덕션 빌드를 **읽기 전용** 검사: 프런트엔드 1,380파일 / 73,029,709바이트와 위 Worker 한 파일 통과.
- Pages/프런트엔드 Python 회귀 128개 통과. Node Pages 회귀와 린트 통과.
- 실제 새 기준본·후보 스테이징, 후보 공개 조작, 대표주소 전환은 운영 담당의 별도 검증으로 남습니다.
- 브라우저 UI나 데이터 릴리스는 이번 배포 도구 수정에서 변경하지 않았습니다.
