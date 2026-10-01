# KOREA REPLAY VPS 운영

기존 PC 지도는 `https://korea-replay.pages.dev/`에서 유지하며, 3D 코드·자료는 변경하지 않습니다. VPS는 공식 아파트 원본 보관, 장부, 단일 수집기, 검증 snapshot 조회를 담당합니다. 수집 성공이 공개 앱의 자료 갱신을 의미하지 않습니다. Cloudflare의 기존 불변 지도·거래 배포와 관측 조회는 아직 별도입니다.

## 격리와 실행

- SSH 별칭: `chanhee-vps`. 호스트 키 검사를 유지합니다.
- 프로젝트: `/srv/services/korea-replay`; Compose project `korea-replay`, private bridge network.
- 릴리스: `releases/<release-id>/`; 검증한 소스와 `deploy/vps/release.env`의 `APP_BUILD`로 고정합니다.
- 현재 코드: `current` 링크. 데이터는 링크 밖의 `shared/data/collector/`에 유지합니다.
- API: localhost `8437` → 전용 컨테이너 `8330`, 외부 HTTPS `https://korea-replay.62-171-141-206.sslip.io`의 전용 Nginx server.
- 비밀키: `shared/secrets/provider.json`, 파일 600·디렉터리 700. `DATA_GO_KR_SERVICE_KEY`만 저장하며 이미지·Git·로그에 포함하지 않습니다.
- 백업: `backups/migration/`의 SHA 고정 이전 묶음과 `backups/cas/`의 기존 partitioned-set 형식. 기존 로컬 원본·복구본은 보존합니다.
- API 읽기 전용, collector만 쓰기. UID 10001, cap drop, 읽기 전용 rootfs, 메모리·CPU·PID 상한, 로그 10MB×3, healthcheck, `unless-stopped`를 적용합니다.

```sh
cd /srv/services/korea-replay/current
docker compose --env-file deploy/vps/release.env -f deploy/vps/compose.yaml ps
curl --fail http://127.0.0.1:8437/health
curl --fail https://korea-replay.62-171-141-206.sslip.io/api/v1/property/acquisition
```

## 수집과 조회 계약

전환 전에 Windows 예약 실행을 비활성화하고 실행 중 작업의 정상 종료와 독점 파일 잠금을 확인합니다. `PROPERTY_COLLECTION_ENABLED=false`를 유지합니다. SQLite online backup을 포함한 최신 검증 recovery set의 전체 객체 closure를 SHA 검사해 이전합니다. 노트북 공간이 부족하면 `export-cas --stdout --receipt <새 영수증>`로 SSH에 직접 스트리밍하며, gzip 바이트의 송신 SHA와 VPS 수신 SHA를 대조합니다. 원본·과거 snapshot·장부 호출 기록을 모두 복원하고, 이전 CAS/legacy head는 삭제하지 않습니다. 복원은 새 디렉터리만 허용하며 경로·링크·중복·누락·크기·SHA·장부·참조를 검사합니다. 검증 영수증 `shared/data/migration-verified.json`이 없으면 서버 수집기가 실행되지 않습니다.

`collection-enabled` 파일이 있어야 수집합니다. Linux flock와 기존 SQLite lease를 함께 사용합니다. 경기·서울·인천·천안·아산·세종·청주·대전·부산, 아파트 241개월 계획과 기존 과거 자료를 유지합니다. 실행 500요청/64MiB, 서비스별 KST 일일 8,000회, 최소 간격 0.3초를 유지합니다. 기존 호출 장부도 이전하므로 한도가 초기화되지 않습니다. 소진된 원천은 건너뛰되 다른 원천의 한도를 초과하지 않습니다. 실패 재시도는 기존 안전 정책만 적용합니다.

한 실행 종료 후 전수 SHA·참조 백업을 수행하고 5분 후 이어갑니다. 인증·원천 차단·해시·저장 오류는 `collection-hold.json`에 안전 코드만 남겨 수집을 중단합니다. 원인 해결 후 이 프로젝트 hold만 명시적으로 옮겨 보존하고 재개합니다. 서버 80% 사용 또는 30GiB+5GiB 여유 미달에서는 검토가 필요하며, 무단 정리를 하지 않습니다. 보관량이 늘면 최대 20년 원본이 현재 디스크에 들어가는지 다시 계산합니다.

```sh
# 재시작해도 장부는 그대로 유지됩니다.
docker compose --env-file deploy/vps/release.env -f deploy/vps/compose.yaml restart collector
# 중단: service 하나만. 다른 프로젝트의 Compose/volume/prune은 금지합니다.
docker compose --env-file deploy/vps/release.env -f deploy/vps/compose.yaml stop collector
```

- `/health`: API와 장부 접근의 상태. 이력 완성 여부와 별개입니다.
- `/api/v1/property/acquisition`: KST 기준 완료 240개월·당월 잠정·원천 미제공·미확보·실제 빈 응답을 구분한 장부. 60초 캐시이며 원문 재파싱/게시 완료를 의미하지 않습니다.
- `/api/v1/property/transactions?regionCode=11710&month=202609&trade=sale&limit=50`: SHA 검사한 snapshot의 최대 100행. 다음 요청은 `offset`과 동일 `snapshot`을 함께 보내며 snapshot 변경은 409입니다. 미확보/실패의 `total`은 null, 실제 빈 응답만 0입니다.
- 16MiB 넘는 snapshot은 임의 삭제 대신 `requires_partitioning` 422를 반환합니다. 전체 JSON 응답 최대 1MiB, 동시 해석 2개. API는 쓰기·원본·비밀 파일 경로를 제공하지 않습니다.
- CORS는 대표 Pages와 불변 8자리 후보 주소에만 허용합니다. 서비스 Nginx는 GET만 허용하고 요청률·동시 연결을 제한합니다.

## 복원과 롤백

```sh
# 외부 보관 묶음 SHA를 별도로 확보한 상태에서, 반드시 새 복원 경로 사용
python -B -m pipeline.vps_transfer restore-cas --root /srv/services/korea-replay/shared/data/restore-<id> \
  --storage /srv/services/korea-replay/backups/restore-cas-<id> \
  --bundle /srv/services/korea-replay/backups/migration/collector-cas-cutover.tar.gz --sha256 <verified-sha256>
```

같은 디스크 CAS 백업은 VPS 디스크 고장 대비 외부 백업이 아닙니다. 이전 당시 원본과 이전 묶음은 노트북에도 보존합니다. 서버에서 새로 수집한 자료의 외부 복제는 별도 후속 조건입니다.

코드 롤백은 collector를 먼저 멈추고 이전 릴리스 Compose의 이미지 태그로 API·collector를 재생성합니다. `shared/data`를 과거 장부로 덮어쓰지 않습니다. 이전 코드가 현 snapshot decoder/장부와 호환되는지 먼저 검사하고, 서버 수집 이후에는 오래된 노트북 예약 수집을 그대로 재활성화하지 않습니다. 노트북 복귀는 서버의 최신 호출 기록·체크포인트·원본을 새 경로에 검증 복원한 뒤 소유자를 전환해야 합니다. 첫 VPS 릴리스에는 아직 이전 VPS 정상 릴리스가 없으므로 서버 코드 롤백 증명과 이전 외부 앱 복구 증명을 혼동하지 않습니다.

Nginx 설정 변경 전 해당 서비스 파일을 백업하고 `nginx -t` 성공 후 reload합니다. 기존 Hannun·hongong-learning·gateway의 건강 상태를 전후 확인합니다. 기존 `/srv/mirae`를 재생성하지 않습니다. 운영 등록은 `/srv/platform/SERVICES.md`에 새 항목만 추가합니다.

## 판정

구현·로컬 검사·서버 복원·수집 재개·공개 HTTPS·Git 병합을 각각 검증합니다. 실행 증거와 현재 확보표는 `docs/VPS_CUTOVER_20261002.md`에 기록합니다. 아직 20년 전체 확보, 단지 좌표 확정, 오피스텔 인증/공개, 모든 보조 공식 자료, 전체 백엔드 대체·자동 게시·경쟁 서비스 동등성은 완료가 아닙니다.
