# 지도 연결과 MCP 조사 · 2026-09-26

## 확인한 연결 경로

현재 세션의 도구 목록에 카카오·네이버 MCP가 없다는 사실과, 인터넷에 MCP가 존재하지 않는다는 주장은 다릅니다. 인터넷과 실제 공개 화면을 추가 조사했습니다.

| 후보 | 직접 확인한 기능 | 용도와 조건 |
|---|---|---|
| [카카오 공식 카카오맵 MCP](https://playmcp.kakao.com/mcp/3) | SearchPlaceByKeywordOpen, GetPublicTransitDirections, GetWalkDirections, GetBikeDirections. 공개 화면 Online, 4 tools | PlayMCP 도구함과 카카오 계정 인증을 거쳐 AI 도구로 연결. 로드뷰 표시 도구는 없음 |
| [카카오 공식 PlayMCP 도구함](https://www.kakaocorp.com/page/detail/11817) | 계정 인증 후 외부 MCP 클라이언트에 도구함 연결 | 사용자 인증 완료와 실제 tools/list·호출 성공은 별도 검증. 개인 계정 토큰을 공개 앱이나 저장소에 넣지 않음 |
| [카카오 Navigation 커뮤니티 MCP](https://github.com/cachij/kakao-navigation-mcp-server) | 주소·장소·자동차 경로 API wrapper | README만으로 도보 지원을 판단하지 않음. 로컬 stdio 서버이며 지도 UI·로드뷰 SDK가 아님 |
| [네이버 커뮤니티 MCP](https://github.com/flor3z-github/navermap-mcp-server) | 지오코딩·역지오코딩·자동차 경로·정적 지도·사용량 도구 | 네이버 자체 제작 MCP와 구분. API 인증 및 이용료 조건 확인 필요 |

카카오 [현행 API 안내](https://developers.kakao.com/docs/ko/kakaomap/common)는 2026-07-21 이후 개발자 계정의 첫 활성화 앱에만 무료 쿼터가 적용된다고 설명합니다. 오래된 MCP 설치 안내의 무료 조건을 그대로 신뢰하지 않습니다. 두 번째 앱 또는 쿼터 초과를 위해 비즈월렛·유료 API를 활성화하지 않습니다.

## 공개 웹에 추가한 연결

- 2D 지도 도구의 **거리뷰**: 현재 카메라 중심을 읽어 카카오 로드뷰·지도·목적지 길찾기 링크를 표시. 사용자 클릭 전 SDK 로딩이나 외부 조회를 하지 않음.
- 지도 중심 좌표를 표시하며, 단지 위치라고 부르지 않음. 잘못된 위경도·범위 밖 값은 차단. 위도/경도 순서를 회귀 검사.
- 단지정보·출퇴근: 시군구·법정동·지번·단지명으로 네이버/카카오 주소 검색. 주소가 불확실한 단지에 임의 지오코딩 좌표를 붙이지 않음.
- 공식 검증된 EPSG:4326 위치가 있고 신고 주소 충돌이 없는 단지만 직접 로드뷰/길찾기 링크 추가. 서울시 후보 점은 검증된 위치로 승격하지 않음.
- 링크는 새 창·noopener noreferrer. 키·계정·별도 유료 호출이 필요 없는 연결이며 MCP 연결 성공이나 앱 내부 거리뷰 임베딩으로 보고하지 않음.
- 공식 [카카오 URL 문서](https://apis.map.kakao.com/web/guide/)의 /link/roadview, /link/map, /link/to, /link/search 사용. 네이버 /p/search 경로는 공개 지도에서 직접 주소를 검색해 확인.
- 내부 영상 임베딩은 별도 공식 [카카오 Roadview SDK](https://apis.map.kakao.com/web/documentation/) 또는 [네이버 Panorama SDK](https://navermaps.github.io/maps.js.ncp/docs/tutorial-Panorama.html)의 등록 키·도메인·무료 적용을 확인한 뒤 연결해야 함.

## 실제 검증

로컬 프로덕션 빌드에서 헬리오시티 단지정보/출퇴근 연결, 현재 지도 중심 링크, Escape 닫기 및 도구 버튼 초점 복귀를 확인했습니다. 카카오 공식 링크에서 실제 삼성동 로드뷰 영상(화면 표기 촬영월 2026-06) 표시를 확인했습니다. 네이버 지도에서 주소 검색 결과가 표시되는 것도 확인했습니다. 이 관찰은 모든 위치의 촬영 영상 보유를 보증하지 않습니다.

공식 PlayMCP 가입·로그인과 카카오맵 도구함 추가를 완료했습니다. 공식 도구함 채팅에서 문촌마을12단지유승 검색과 주엽역까지 도보 조회를 실제 실행했습니다. 검색은 성공했고, 주소를 함께 넣은 첫 도보 호출은 실패했으나 정확한 장소명으로 다시 호출한 응답은 `isError: false`, 900m·828초였습니다. 이는 해당 시점·경로의 도구 응답이며 제품의 고정 통근시간이나 단지 확정 좌표로 저장하지 않았습니다.

Codex에 공식 `https://playmcp.kakao.com/mcp` 서버를 등록했지만 OAuth 동적 클라이언트 등록에서 HTTP 403 `ERR-PLAYAUTH-90403`(허용되지 않은 IP 대역)이 반환됐습니다. PlayMCP 안에서의 호출 성공과 외부 Codex 클라이언트 인증 성공은 별개입니다. 인증 우회·토큰 재사용은 하지 않았으며 Codex 직접 호출은 미연결 상태입니다. 계정 정보·토큰·접속 IP는 공개 저장소에 기록하지 않습니다.

커뮤니티 소스는 설치·실행 없이 확인했습니다. 카카오 후보 `a5452bd9f46c6b06fe49bd504986ddcdbd22da4e`는 자동차 경로 API wrapper로, README의 도보 표현을 실제 지원으로 채택하지 않았습니다. 네이버 후보 `9eba42934d29f00ea917f114dee24f44c813592c`는 별도 API 키와 호출료 관리가 필요하며 사용량 경고가 과금을 차단하지 않습니다. 두 후보 모두 로드뷰 UI 제공 도구가 아닙니다. 검증되지 않은 패키지 설치나 유료 API 활성화는 하지 않았습니다.
