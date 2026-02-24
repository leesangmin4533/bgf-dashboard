# Changelog

BGF 자동 발주 시스템 전체 변경 기록.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2026-02-14] - 문서-코드 정합성 정리

### Removed (Dead Code)
- **auto_order.py**: `clear_old_inventory_data()` — 정의만 되고 호출되지 않는 dead code 제거
- **order_prep_collector.py**: `clear_old_data()` — 동일 이유
- **inventory_repo.py**: `clear_old()` — 위 2개의 기반 메서드, 외부 호출 없음
- **prediction_config.py**: `__main__` 테스트 블록 560줄 제거 (운영에서 미실행)

### Fixed
- **prediction_config.py**: deprecated 경고를 제거하고 역할 명확화
  - 원인: deprecated 표시했으면서 계절계수 등 신규 기능 계속 추가하는 모순
  - 수정: 경고 제거, 파일 역할(파라미터, 패턴분석, 계절/요일계수) 명시

### Changed (문서 업데이트)
- **bgf-database.md**: 스키마 버전 v18 → v27 업데이트, DB 경로 common.db+stores/ 반영, 마이그레이션 이력 v19~v27 추가
- **web-dashboard.md**: 파일명 수정 (home.py → api_home.py 등), api_prediction.py 추가
- **bgf-order-flow.md**: 적응형 블렌딩(4-0), 계절계수(6-1) 섹션 추가, ML Feature 25개 목록 추가
- **CLAUDE.md**: 핵심 플로우 설명 보강 (4단계 → WMA→블렌딩→계절→트렌드→ML앙상블)

---

## [2026-02-14] - 기간대비 예측 개선 (Phase A+B)

### Added
- **prediction_config.py**: 7개 카테고리 그룹별 월간 계절 계수 테이블 (`SEASONAL_COEFFICIENTS`)
  - beverage(여름 1.30/겨울 0.80), frozen(여름 1.50/겨울 0.60), food(안정 0.95~1.05)
  - beer(여름 1.35), soju(겨울 1.15), ramen(겨울 1.20), snack(겨울 1.08)
  - `get_seasonal_coefficient(mid_cd, month)` 함수
- **feature_builder.py**: ML Feature 22개 → 25개 확장
  - `lag_7`: 7일 전 판매량 (일평균 대비 비율 정규화)
  - `lag_28`: 28일 전 판매량 (일평균 대비 비율 정규화)
  - `week_over_week`: 전주 대비 변화율 (클리핑 -1.0~3.0)
- **trainer.py**: 학습 데이터에 lag 계산 로직 (date_to_idx 맵 기반)
- **improved_predictor.py**: predict() 흐름에 3개 단계 추가
  - `4-0. [기간대비]`: WMA + FeatureCalculator(EWM+동요일평균) 블렌딩 (품질별 10~40%)
  - `6-1. [계절계수]`: 카테고리별 월간 계절 계수 적용
  - `6-2. [트렌드조정]`: ±8~15% 트렌드 계수 적용 (7일 vs 28일 비교)

### Fixed
- **improved_predictor.py**: `feat_result` 변수 미초기화 → `feat_result = None` 초기화 추가
  - 원인: try 블록 내에서만 정의되어 except 시 ML 앙상블 단계에서 NameError 가능
- **improved_predictor.py**: `mid_cd` UnboundLocalError
  - 원인: 계절계수에서 `mid_cd` 사용했으나, 해당 변수는 함수 후반부에서 정의
  - 수정: `product["mid_cd"]`로 직접 참조

### Changed
- **test_ml_predictor.py**: feature shape 22 → 25 반영 (5개소)

### Verified (시뮬레이션 검증)
- 38개 활성 상품 대상, 2026-02-15(일요일) 예측 비교 (WMA only vs 블렌딩+계절+트렌드)
- **맥주(049)**: 평균 -22.3% (겨울 계절계수 0.78 반영)
- **탄산음료(044)**: 평균 -16.5% (겨울 계절계수 0.82 반영)
- **라면/면류(032)**: 평균 +7.6% (겨울 계절계수 1.10 반영)
- **캔디(020)**: 평균 +13.5% (겨울 1.05 + 강한 상승트렌드)
- 최대 변화: 팔리아멘트아쿠아5mg +35.7% (strong_up), 카스라이트캔500ml -34.6% (계절+strong_down)
- 계절계수가 가장 큰 영향, 트렌드 조정은 ±8~15% 범위 내 적용 확인

---

## [2026-02-14] - CUT 필터 순서 버그 수정

### Fixed
- **auto_order.py**: CUT 상품이 발주 목록에서 제외되지 않는 버그
  - 원인: `_exclude_filtered_items()` (메인 CUT 필터)가 `prefetch_pending_quantities()` (BGF 사이트 실시간 CUT 감지) **이전**에 실행됨
  - 수정: prefetch 이후 `[CUT 재필터]` 블록 추가 (Path A + Path B 양쪽)
  - 영향: 스케줄 실행 전 단품발주 화면에서 CUT 감지된 상품이 발주에 포함되던 문제 해결
- **sales_repo.py**: `_upsert_daily_sale()`에서 `is_cut_item` 명시적 처리
  - 원인: INSERT 시 `is_cut_item` 컬럼 미포함 → 기본값 0으로 삽입, ON CONFLICT에서 덮어쓰기 가능
  - 수정: INSERT에 `is_cut_item=0` 명시, ON CONFLICT에서 `is_cut_item` 업데이트 제외

---

## [2026-02-14] - 폐기추적 모듈 store_id 누락 수정

### Fixed
- **receiving_collector.py**: `update_order_tracking()` 내 2건 store_id 누락
  - `get_receiving_by_date()` 호출에 `store_id=self.store_id` 추가
  - `update_order_tracking_receiving()` 호출에 `store_id=self.store_id` 추가
  - 원인: 직접 SQL은 store_filter 적용했으나 Repository 위임 호출 시 전달 누락
  - 영향: 멀티매장 환경에서 다른 매장 입고 데이터와 혼합될 수 있었음

---

## [2026-02-04] - flow-tab 완료

### Added
- **흐름도 탭 (flow-tab)**: 대시보드에 "흐름도" 탭 추가
  - 7개 Phase 세로 타임라인 레이아웃
  - Phase 0: 07:00 스케줄러 트리거
  - Phase 1: 데이터 수집 (로그인, 판매데이터, DB저장)
  - Phase 1.5: 평가 보정 (자동보정, 리포트)
  - 카카오톡 수집 리포트 발송
  - Phase 2: 자동 발주 (예측, 카테고리별 로직, 사전평가, 미입고, 발주실행)
  - Phase 3: 실패 사유 수집 (조건부: fail_count > 0)
  - 결과 출력 (카카오 알림, 대시보드)

- **flow.js**: Step 호버 시 툴팁 표시
  - `initFlowTooltips()` 함수로 동적 생성
  - `data-file` (파일 경로) / `data-desc` (설명) / `data-time` (스케줄) 지원
  - `escapeHtml()` XSS 방지 함수 포함

- **CSS flow-* 클래스** (232줄 추가):
  - `.flow-timeline`: 세로 타임라인 컨테이너
  - `.flow-phase`: Phase 카드 + 좌측 4px 색상바
  - `.flow-phase-header`: Phase 제목 + 아이콘
  - `.flow-phase-trigger`: Phase 0 (트리거) 별도 색상
  - `.flow-step`: 세부 단계
  - `.flow-step-sub`: Phase 2 하위 단계 들여쓰기
  - `.flow-connector`: 연결선 + 화살표 (6개)
  - `.flow-condition-diamond`: 조건부 분기 (다이아몬드)
  - 색상 체계: Gray(트리거) / Blue(수집) / Indigo(보정) / Green(발주) / Orange(카카오) / Red(실패) / Purple(결과)

- **web-dashboard.md**: 프론트엔드 아키텍처 기술 문서 (350줄)
  - 탭 구조 및 SPA 패턴
  - CSS 네이밍 규칙 및 변수 매핑
  - JS 파일 역할 분담
  - API 엔드포인트 20개 정리
  - 새 탭 추가 체크리스트
  - 모듈별 색상 배정표

- **반응형 디자인**: 모바일 (max-width 768px)에서 타임 표시 숨김
- **다크/라이트 모드**: CSS 변수 활용 (하드코딩 없음)

### Changed
- **index.html**: nav-tabs에 "흐름도" 탭 추가 (line 29)
  ```html
  <a href="#" class="nav-tab" data-tab="flow">흐름도</a>
  ```

- **dashboard.css**: flow 관련 CSS 232줄 추가 (lines 1599-1830)

### Quality Metrics
- **Design Match Rate**: 97% (PASS 기준: 90%)
- **HTML 검증**: 100% (nav-tab 추가, 7개 phase 렌더링)
- **CSS 검증**: 100% (180줄+, flow- prefix, 색상 체계)
- **JS 검증**: 87% (경미한 gap: .flow-tooltip-title CSS 미사용)
- **반응형/다크모드**: 100%

### Files Modified/Created
```
+ src/web/static/js/flow.js                    # 신규 (48줄)
~ src/web/templates/index.html                 # 수정 (+207줄)
~ src/web/static/css/dashboard.css             # 수정 (+232줄)
+ .claude/skills/web-dashboard.md              # 신규 (350줄)
+ docs/03-analysis/flow-tab.analysis.md        # 신규 분석 문서
+ docs/04-report/flow-tab.report.md            # 신규 완료 보고서
```

**Total LOC**: 837줄

### PDCA Completion
- ✅ Plan: 구두 요청 (별도 문서 없음)
- ✅ Design: web-dashboard.design.md 참조
- ✅ Do: 전체 구현 완료
- ✅ Check: Gap Analysis 97% Match
- ✅ Act: Completion Report 작성

---

## Future Releases

### v1.1 (계획)
- [ ] flow-tab.plan.md 추가
- [ ] flow-tab.design.md 분리 (web-dashboard에서 독립)
- [ ] CSS/JS 모듈 분리 (dashboard.css 대규모 리팩토링)
- [ ] 자동 탭 검증 도구 (lint-flow-tabs.js)
- [ ] 색상 팔레트 config.json 중앙화

### v2.0 (계획)
- [ ] Vue/React 컴포넌트화
- [ ] 동적 Phase 추가/편집 UI
- [ ] 실시간 Phase 상태 업데이트 (WebSocket)
- [ ] 흐름도 내보내기 (PNG/SVG)

---

## [2026-02-02] - web-dashboard 기본 구현

### Added
- Flask 기반 웹 대시보드 서버
- 발주 컨트롤 탭 (파라미터 조정, 예측 실행, 결과 테이블)
- 리포트 탭 (일일/주간/카테고리/영향도)
- REST API 엔드포인트 (발주 5개, 리포트 5개)
- 다크 테마 CSS (base.html 기반)
- Chart.js 차트 통합

### Files Added
```
+ src/web/
+ src/web/__init__.py
+ src/web/app.py
+ src/web/routes/
+ src/web/routes/__init__.py
+ src/web/routes/pages.py
+ src/web/routes/api_order.py
+ src/web/routes/api_report.py
+ src/web/templates/index.html
+ src/web/static/css/dashboard.css
+ src/web/static/js/app.js
+ src/web/static/js/order.js
+ src/web/static/js/report.js
+ scripts/run_dashboard.pyw
```

### API Endpoints
- GET `/` - 메인 대시보드
- GET/POST `/api/order/params` - 파라미터 조회/저장
- POST `/api/order/predict` - 예측 실행
- POST `/api/order/adjust` - 발주량 수동 조정
- GET `/api/order/categories` - 카테고리 목록
- GET `/api/report/daily` - 일일 발주 데이터
- GET `/api/report/weekly` - 주간 트렌드
- GET `/api/report/category/<mid_cd>` - 카테고리 분석
- GET `/api/report/impact` - 영향도 비교
- POST `/api/report/baseline` - Baseline 저장

---

## Notes

- 모든 변경사항은 PDCA 사이클에 따라 문서화됨
- Design Match Rate는 Check phase의 Gap Analysis 결과
- 추가 개선사항은 "Future Releases"에서 추적
