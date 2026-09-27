# 수도권·충청권 넓은 줌 후보

2026-09-27, 기존 상세 후보를 읽기 전용으로 검사한 뒤 넓은 줌 생성용 변환 허용 목록에 정확한 SHA 하나를 추가했습니다. 임의의 미래 변환을 허용하지 않습니다. 기존 원본·배포·3D 자료는 변경하지 않습니다.

- 원본 후보: `.local/regional-detail-20260926/capital-chungcheong-v1`
- 릴리스: `map2d-bafa61b25c500d34c8f7`
- 카탈로그 SHA: `f0f16afe19abadc0ce0e73ff9f9d8f7cae8a339035ac4af34a6c247c4848daff`
- 입력/DB fingerprint: `bafa61b25c500d34c8f784283a2f673166a3f4e0c65f5fcba8ae4d69dfdc6e8a`
- publication SHA: `889237f5fb0fe9629dfb53da8eeaceac565a0e8aa16e67dfe0d0d81e8dbe8ed3`
- 승인한 원본 변환 SHA: `75d5a69e3c24f5aa28440867ecf2e9d4dc6af1872bb28b1db6ae93ad6057b7a0`. 허용 목록 수정 전 실제 `pipeline/map_tiles_regional.py` 바이트의 SHA와 일치합니다.
- 렌더러 SHA: `4a9dc70d6e8cf083237c8eef2defa9ca4d1aefaa19d248859e21ae63be18807a`. 기존처럼 현재 렌더러와 일치해야만 진행합니다.

`load_baseline`의 게시 자산 검증을 통과했습니다. DB의 694개 원본 ID/해시는 입력 목록과 전부 일치하며 fingerprint도 일치합니다. 건물 1,809,945개, 도로 958,595개가 zoom 14에 모두 표현됐다는 기존 생성 감사를 확인했습니다. 기존 후보는 2,073파일·676,787,048바이트입니다. SGIS 2025-06-30 행정경계를 수집 범위로 쓰며 서울·인천·경기·대전·세종·충북·충남을 포함합니다. 법정동 경계나 검증된 단지 가격 좌표를 추가한 것은 아닙니다.

기존/신규 허용 변환 각각에 대해 형상·ID·기존 자산 보존과 재시작을 검사했습니다. 미승인 변환, 다른 버전, 다른 렌더러, 다른 zoom 입력은 출력 폴더 생성 전 거부됩니다. 관련 Python 테스트 11개 통과, 변경 파일 diff 검사 통과.

```powershell
.venv/Scripts/python.exe -X utf8 -B -m pipeline.map_tiles_regional --baseline .local/regional-detail-20260926/capital-chungcheong-v1 --output .local/regional-detail-20260927/capital-chungcheong-overview-v1 --extend-display
```

새 후보는 건물 고정 4분할 zoom 12~13과 상세 도로 zoom 13을 추가합니다. 기존 zoom 14 파일은 해시를 검사해 재사용하며, 원본 DB는 읽기 전용입니다. 파일 수 3,870 미만, 압축 파일/해제 타일 예산, 각 zoom의 전 ID 표현 검사는 그대로 유지됩니다. 실행 로그는 `.local/priority-detail-overview-build-20260927.log`입니다. 생성 시작은 완료·공개 배포·성능 인수를 뜻하지 않으며 최종 publication과 브라우저 검증은 별도로 확인해야 합니다.
