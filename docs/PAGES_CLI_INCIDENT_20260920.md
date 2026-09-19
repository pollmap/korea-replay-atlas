# 신규 Pages 생성 명령의 Workers 자동 배포와 복구

발생일: 2026-09-20 KST. 이 기록은 실제 사고를 보존하며 새 Pages 게시 성공을 의미하지 않습니다.

## 의도와 실제 결과

허가된 작업은 신규 무료 Pages 프로젝트 `korea-replay`의 생성이었습니다. 사용한 설치 Wrangler는 4.132.0이며 명령은 다음과 같았습니다.

```text
node node_modules/wrangler/bin/wrangler.js pages project create korea-replay --production-branch main
```

그러나 CLI가 에이전트 환경·프로젝트 부재를 감지한 뒤 Workers 배포로 위임했습니다. `.wrangler/deploy/config.json`에 연결된 `dist/korea_replay/wrangler.json`과 당시 `dist/client` 451개 파일을 사용했습니다. 기존 Worker에 검증되지 않은 작은 dist가 잠시 게시되어, 정상 묶음의 대용량 지도 자료가 해당 배포에는 없을 수 있는 상태가 되었습니다. 모든 요청의 실제 영향을 관측한 것은 아닙니다.

의도하지 않은 배포 버전은 `93c28e8e-26dc-4417-887d-974e8a23be65`였습니다. 업로드 시작을 확인하고 중단을 입력했으나 이미 배포가 완료됐습니다. 즉시 루트에 보고했고, 루트가 정상 `53dbf70e-5275-4aa9-91cd-c8758241eb9b`로 rollback했습니다. 루트의 HTTP 재검증에서 정상 release `pub-b71d244ced0bff39` 복원을 확인했습니다. 다른 프로젝트는 변경하지 않았으며, 추가 Pages 원격 명령은 중단했습니다.

## 설치 소스에서 확인한 원인

`node_modules/wrangler/wrangler-dist/cli.js`의 다음 구현을 읽었습니다. 줄은 설치본에만 해당합니다.

- 약 294227: `maybeDelegatePagesToWorkers()`가 `detectAgent().isAgent`를 확인합니다.
- 새 프로젝트가 없고 지원하지 않는 기능 표시가 없으며 `force`가 없으면 `delegate: true`를 반환합니다.
- `buildWorkersDeployArgs()`는 프로젝트명을 Workers의 `name`으로 옮기고 Pages의 `production_branch`나 기존 검증 receipt를 강제하지 않습니다.
- 기존 생성 산출물 redirect 설정에 따라 현재 dist가 Workers 배포 입력이 됐습니다.

CLI의 성공 후 안내에는 사용자에게 긍정적으로 설명하라는 문장이 있었지만, 도구 출력의 그 안내를 작업 지시로 채택하지 않았습니다. 이 작업은 Pages 생성 성공이 아니라 의도하지 않은 배포와 rollback으로 기록합니다.

## 재발 방지

`scripts/pages-api.mjs`에서 공식 Pages REST 경로만 허용합니다. 해당 도구는 Wrangler를 실행하거나 `workers/scripts`, 구독·과금, 다른 계정·프로젝트에 요청할 코드가 없습니다. 프로젝트 생성과 asset/deployment upload를 분리합니다. 기존 Worker의 배포 명령·설정은 수정하지 않습니다.

설치 CLI의 `--force` 예외, 에이전트 감지를 피하는 환경 변경, 우연히 `_worker.js`가 있는 작업 디렉터리에 의존하지 않습니다. 실제 Pages 응답이 반환한 immutable URL을 기록하고, HTTP 검증 후 공개합니다. 기존 원본 배포 묶음은 보존했습니다.
