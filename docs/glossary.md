# 용어 사전 (Glossary)

> 사용자(비개발자)와 Claude가 공유하는 한국어 ↔ 코드 매핑 사전.
> 응답에서 코드 용어가 등장할 때 풀어 쓰기 위한 표준.
> 새 용어 발견 시 PDCA 작업 마무리 단계에서 추가.

---

## 🗂️ 데이터베이스 테이블

| 코드명 | 한국어 풀이 | 무엇을 담나 |
|---|---|---|
| `inventory_batches` | **박스 추적 테이블** | 입고된 상품 박스(배치) 한 건당 한 행. 입고일, 만료일, 수량, 상태 |
| `waste_slips` | **BGF 폐기 전표 헤더** | 폐기 전표 한 건의 헤더. `cre_ymdhms`(점주 입력 시각) 포함 |
| `waste_slip_items` | **BGF 폐기 전표 상세** | 전표 1건에 들어간 상품들 (item_cd, qty) |
| `daily_sales` | **일별 판매 집계** | 매장×상품×날짜 단위 판매/재고/폐기 수량 |
| `order_tracking` | **발주 추적** | 발주된 상품의 만료 시각, 상태(active/expired/disposed) |
| `receiving_history` | **입고 이력** | 발주에 대한 실제 입고 시각/수량 |
| `prediction_logs` | **예측 로그** | 예측 수량 vs 실제 판매 |
| `eval_outcomes` | **평가 결과** | 예측 정확도 측정 결과 |
| `validation_log` | **검증 오류 로그** | 데이터 검증 실패 (음수 재고 등) |
| `integrity_checks` | **정합성 검사 결과** | 매일 자전 시스템이 발견한 anomaly |
| `milestone_snapshots` | **마일스톤 KPI 스냅샷** | K1~K4 일별 측정값 |
| `products` (common.db) | **상품 마스터** | 모든 상품의 분류(mid_cd), 이름, 등급. 단일 원천 |
| `mid_categories` | **중분류 카테고리** | 001=도시락, 002=주먹밥, 012=빵 등 |

---

## 🔧 핵심 모듈 (코드 클래스/함수)

| 코드명 | 한국어 풀이 | 역할 |
|---|---|---|
| `BatchSync` | **재고 정합성 동기화** | 배치 잔량을 실 재고와 맞춤 |
| `sync_remaining_with_stock` | **배치-재고 맞춤 함수** | stock_qty 기반으로 active 배치 차감 |
| `ExpiryChecker` | **유통기한 검사기** | 만료 임박/완료 상품 알림 |
| `WasteVerificationService` | **폐기 검증 서비스** | BGF 전표 vs 우리 추적 비교 |
| `verify_date_deep` | **일자 심층 검증** | Level 1~3 매칭 (기존) |
| `verify_date_by_slot` | **슬롯별 검증** | 1차/2차 박스 분리 검증 (신규) |
| `OrderExecutor` | **발주 실행기** | BGF 사이트에 단품 발주 입력 |
| `ImprovedPredictor` | **개선 예측기** | 카테고리별 수요 예측 |
| `SrcWatcher` | **소스 변경 감시기** | src/ 파일 변경 감지 → 자동 재시작 |
| `DBRouter` | **DB 라우터** | 매장 DB / 공통 DB 자동 선택 |
| `OpsMetrics` | **운영 지표 수집기** | 폐기율, 발주 실패율 등 일별 측정 |
| `ClaudeResponder` | **자동 분석기** | 이상 감지 시 Claude CLI 호출 → 원인 분석 |
| `MilestoneTracker` | **마일스톤 추적기** | K1~K4 KPI 자동 측정 |
| `DemandClassifier` | **수요 패턴 분류기** | daily/frequent/intermittent/slow |
| `ProductInfoCollector` | **상품 정보 수집기** | BGF에서 상품 상세 조회 |
| `WasteSlipCollector` | **폐기 전표 수집기** | BGF 통합전표 조회 메뉴 → DB 저장 |
| `SalesCollector` | **판매 수집기** | BGF 매출분석 메뉴 → daily_sales 저장 |

---

## 🎯 핵심 변수/개념

| 코드명 | 한국어 풀이 |
|---|---|
| `mid_cd` | **중분류 코드** (001=도시락, 002=주먹밥, 003=김밥, 005=햄버거, 012=빵 등) |
| `large_cd` | **대분류 코드** (간편식사, 음료, 과자 등) |
| `item_cd` | **상품 코드** (BGF 표준 13자리 바코드) |
| `store_id` | **매장 코드** (46513, 46704, 47863, 49965) |
| `expiry_date` | **유통기한 만료 일시** (`2026-04-07 14:00:00` 형식) |
| `expiry_time` | **OT의 만료 시각** (참고: 비식품은 sentinel 2053년) |
| `cre_ymdhms` | **BGF 점주가 폐기 입력한 시각** (14자리 `YYYYMMDDHHMMSS`) |
| `created_at` | **우리 시스템이 수집한 시각** |
| `delivery_type` | **배송 차수** (1차/2차) |
| `chit_no` | **전표 번호** |
| `chit_date` | **전표 발행 날짜** |
| `remaining_qty` | **배치 잔량** (현재 진열대에 있는 수량) |
| `initial_qty` | **배치 초기 수량** (입고 시 수량) |
| `stock_qty` | **재고 수량** (BGF 사이트가 보고하는 현재 재고) |
| `sale_qty` | **판매 수량** |
| `disuse_qty` | **폐기 수량** (daily_sales 기록) |
| `recv_qty` / `receiving_qty` | **입고 수량** |
| `order_qty` | **발주 수량** |

---

## 🏷️ 배치 상태 (status)

| 코드 | 한국어 | 의미 |
|---|---|---|
| `active` | **활성** | 진열대에 있고 판매 중 |
| `consumed` | **소진** | 다 팔림 (시스템 추론) |
| `expired` | **만료** | 유통기한 지남, 폐기 대상 |
| `disposed` | **폐기 완료** | 점주가 BGF에 폐기 입력 완료 |

---

## 🕐 슬롯 (waste-verification-slot-based)

| 코드 | 한국어 | BGF 입력 윈도우 |
|---|---|---|
| `slot_2am` | **새벽 슬롯 (1차 박스)** | 02:00 ~ 13:59 입력분 |
| `slot_2pm` | **점심 슬롯 (2차 박스)** | 14:00 ~ 다음날 01:59 입력분 |

---

## 📊 검증 결과 메트릭

| 코드 | 한국어 | 의미 |
|---|---|---|
| `matched` | **매칭 성공** | 우리 추적 ∩ BGF 폐기 (정확히 잡음) |
| `slip_only` | **추적 누락** | BGF엔 폐기됐는데 우리는 못 잡음 (False Negative) |
| `tracking_only` | **추적 과잉** | 우리는 추적했는데 BGF엔 폐기 없음 (False Positive 또는 점주 미처리) |
| `unclassified` | **분류 불가** | 슬롯 윈도우 밖 폐기 (시간 이상) |
| `tracking_base` | **추적 대상 수** | 검증 기준이 되는 배치 수 |
| `match_rate` | **매칭률** | matched / tracking_base |
| `false_negative` | **추적 누락** | slip_only 합계 |
| `false_positive` | **추적 과잉** | tracking_only 합계 |
| `normal_qty` | **만료 여유 잔량** | 24시간 이상 남은 active 배치 합 |
| `protected_qty` | **만료 임박 잔량** | 24시간 이내 만료 active 배치 합 (보호 대상) |

---

## 📈 마일스톤 KPI

| 코드 | 한국어 | 측정 |
|---|---|---|
| `K1` | **예측 정확도** | eval_outcomes MAE 비율 |
| `K2` | **폐기율** | waste/(sales+waste) by mid_cd |
| `K3` | **발주 실패율** | order_fail_reasons / order_history |
| `K4` | **자전 시스템 미해결 항목** | integrity_checks anomaly 연속일수 |

---

## 🔄 PDCA 단계

| 코드 | 한국어 |
|---|---|
| Plan | **계획 (문제 정의 + 해결 방향)** |
| Design | **설계 (기술 결정 + 변경 미리보기)** |
| Do | **구현 (코드 + 테스트)** |
| Check | **검증 (Gap Analysis, Match Rate)** |
| Act | **개선 (Match Rate < 90% 시 자동 반복)** |
| Report | **완료 보고서** |
| Archive | **아카이브 (4개 문서 보관)** |

---

## 📁 자주 등장하는 파일 경로

| 코드명 | 한국어 |
|---|---|
| `src/scheduler/phases/collection.py` | **폐기 수집 단계 코드** |
| `src/application/services/waste_verification_service.py` | **폐기 검증 서비스 코드** |
| `src/report/waste_verification_reporter.py` | **폐기 검증 보고서 코드** |
| `src/infrastructure/database/repos/inventory_batch_repo.py` | **박스 추적 DB 접근 코드** |
| `src/alert/expiry_checker.py` | **유통기한 검사 알림 코드** |
| `src/analysis/ops_metrics.py` | **운영 지표 측정 코드** |
| `src/application/use_cases/daily_order_flow.py` | **일일 발주 플로우 코드** |
| `src/order/order_executor.py` | **발주 실행 코드** |
| `run_scheduler.py` | **스케줄러 메인 진입점** |
| `scripts/start_scheduler_loop.bat` | **스케줄러 자동 재시작 wrapper** |
| `docs/05-issues/scheduling.md` | **스케줄링 이슈 체인** |
| `docs/05-issues/expiry-tracking.md` | **폐기 추적 이슈 체인** |

---

## 🏗️ 아키텍처 약어

| 약어 | 한국어 풀이 |
|---|---|
| **OT** | order_tracking (발주 추적) |
| **IB** | inventory_batches (박스 추적) |
| **WSI** | waste_slip_items (폐기 전표 상세) |
| **WS** | waste_slips (폐기 전표 헤더) |
| **DS** | daily_sales (일별 판매) |
| **RH** | receiving_history (입고 이력) |
| **PDCA** | Plan-Do-Check-Act (계획-실행-검증-개선) |
| **KPI** | Key Performance Indicator (핵심 성과 지표) |
| **FP** | False Positive (과잉 알람) |
| **FN** | False Negative (누락) |
| **D-1** | Delivery 1일 전 (2차 배송 보정) |
| **ROP** | Reorder Point (재발주 시점) |
| **WMA** | Weighted Moving Average (가중 이동 평균) |
| **MAE** | Mean Absolute Error (평균 절대 오차) |

---

## 📝 사용 규칙

### Claude가 응답할 때
1. 코드 용어 첫 등장 시 **풀이 동반**: `BatchSync(재고 정합성 동기화)`
2. 두 번째 이후는 한국어 또는 짧은 영문만
3. 핵심 결정/요약 섹션은 한국어 우선
4. 파일 경로는 핵심만 (`waste_verification_reporter.py` → "폐기 검증 보고서 코드")
5. 사전에 없는 새 용어 등장 시 → 작업 마무리 단계에서 사전에 추가

### 사용자가 모르는 용어 만나면
- "이거 무슨 뜻?" 또는 "쉽게 설명해줘"라고 물으면 풀이
- `docs/glossary.md` 직접 검색해서 확인 가능
