# BGF 리테일 사이트 분석 문서

2026-02-27 실시한 BGF 리테일 점포관리 사이트 전체 탐색 결과.

## 문서 목록

| 파일 | 내용 |
|------|------|
| [01-menu-tree.md](01-menu-tree.md) | 전체 메뉴 트리 (272개), 카테고리별 화면 목록, 활용 가능성 평가 |
| [02-api-endpoints.md](02-api-endpoints.md) | 화면별 API 엔드포인트, Direct API 전환 후보, URL 패턴 |
| [03-improvement-priority.md](03-improvement-priority.md) | 개선 우선순위 Top 15, Tier 분류, 구현 로드맵 |
| [04-nexacro-navigation-tips.md](04-nexacro-navigation-tips.md) | 넥사크로 네비게이션 기술 노하우, 시행착오 기록 |

## 데이터 파일

| 파일 | 내용 |
|------|------|
| `data/bgf_topframe_datasets.json` | TopFrame 전체 데이터셋 (ds_orgMenu 272행 포함) |

## 핵심 수치

- **전체 화면**: ~180개 (리프 메뉴)
- **현재 사용**: 8개 (4.4%)
- **즉시 활용 가능 (Tier 1)**: 5개 (재고/입고예정/시간대매출/유통기한/재고추이)
- **발주 최적화 (Tier 2)**: 5개 (발주정지/품절/카렌더/대체/스마트발주)
- **운영 인텔리전스 (Tier 3)**: 5개 (가격변동/행사/CUT상품/필수품목)
