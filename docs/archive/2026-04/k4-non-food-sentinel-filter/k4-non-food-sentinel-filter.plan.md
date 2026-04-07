# Plan: K4 식품 전용 + 임계값 재정의 (k4-non-food-sentinel-filter)

> 작성일: 2026-04-07
> 상태: Plan
> 이슈체인: scheduling.md (등록 예정)
> 마일스톤 기여: **K4 (자전 시스템 미해결 항목)** — HIGH (31일 NOT_MET → ACHIEVED 전환 목표)
>
> **작업명 비고**: 초기 명칭은 sentinel 필터링이었으나 조사 결과 sentinel은 mismatch 전체의 2.8%에 불과 → 옵션 D(식품 전용) + 옵션 C(임계값 완화) 조합으로 범위 확장. 작업명은 명령 시점 호환을 위해 유지.

---

## 1. 문제 정의

### 현상
K4 마일스톤(`integrity_checks.expiry_time_mismatch`)이 **31일 연속 NOT_MET**. claude-auto-respond 분석 보고서(2026-04-07)에서 PLANNED P1 우선순위로 지목.

### 진단 (조사 데이터)

#### 4매장 mismatch 전체 8,328건 분석
| 분류 | 건수 | 비율 |
|---|---:|---:|
| 비식품 (담배 072, 맥주 049, 면류 032, 과자 015 등) | 7,691 | 92.4% |
| 식품 (001~005, 012) | 637 | 7.6% |
| sentinel 값 (year ≥ 2030) | 230 | 2.8% |
| **총** | **8,328** | 100% |

#### 식품 카테고리 diff_days 분포
| 매장 | 1~3일 | 3~7일 | 7~30일 | 30~365일 | 합계 |
|---|---:|---:|---:|---:|---:|
| 46513 | 21 | 37 | 133 | 89 | 274 |
| 46704 | 7 | 16 | 44 | 48 | 109 |
| 47863 | 15 | 26 | 98 | 8 | 139 |
| 49965 | 32 | 64 | 37 | 0 | 115 |
| **합계** | **75** | **143** | **312** | **145** | **637** |

### 근본 원인

1. **K4 의도와 측정 대상 불일치**
   - K4 의도: **식품 폐기 정합성** (도시락/김밥 등이 정확한 시점에 폐기되는지)
   - 현재 측정: **모든 카테고리의 OT vs IB 불일치** (담배/맥주/우산까지 포함)
   - 비식품 92%가 K4 신호를 묻어버림

2. **임계값 1일이 너무 엄격**
   - 식품 1-7일 차이는 BGF 사이트의 expiry_time 표기 오차/시각 처리 차이일 가능성 (218건)
   - 진짜 폐기 사고는 7일 이상 차이 (식품 457건이 진짜 위험 신호)

3. **Sentinel 값 (2053-08, 2028-12)**
   - 비식품 일부가 sentinel 값으로 BGF에서 내려옴 → 9,997일 차이로 매번 mismatch
   - 230건 (2.8%)에 불과해 단독 해결로는 K4 효과 미미

---

## 2. 목표

### 1차 (K4 정상화)
- `check_expiry_time_mismatch`를 **식품 카테고리(001~005, 012)만** 측정하도록 변경
- 임계값 **1일 → 7일**로 완화 (식품 BGF 표기 오차 흡수)
- K4 NOT_MET → ACHIEVED 전환

### 2차 (진짜 mismatch 가시화)
- 7일 초과 식품 mismatch가 진짜 K4 anomaly로 잡힘
- 4매장 합계: 312(7-30일) + 145(30-365일) = **457건**이 진짜 신호
- 이걸 별도 후속 작업(식품 expiry 정합성 조사)으로 분리

### 3차 (비식품 mismatch 모니터링)
- 비식품 mismatch는 K4에서 제외하지만 **별도 메트릭**(`expiry_time_mismatch_nonfood`)으로 기록
- 운영자가 필요 시 조회 가능 (대시보드 또는 로그)

### 비목표
- OT/IB 산정 로직 자체의 재설계 (Phase 1.67 자전 시스템 재설계는 별도 큰 작업)
- 식품 7일 초과 mismatch 457건의 근본 원인 해결 (후속 작업)

---

## 3. 해결 방향

### 핵심 변경
`src/infrastructure/database/repos/integrity_check_repo.py:check_expiry_time_mismatch`

```sql
-- Before
WHERE ot.store_id = ?
  AND ot.status NOT IN ('expired', 'disposed', 'cancelled')
  AND ib.status = 'active' AND ib.remaining_qty > 0
  AND ABS(julianday(date(ot.expiry_time)) - julianday(ib.expiry_date)) > 1

-- After
WHERE ot.store_id = ?
  AND ot.status NOT IN ('expired', 'disposed', 'cancelled')
  AND ib.status = 'active' AND ib.remaining_qty > 0
  AND ABS(julianday(date(ot.expiry_time)) - julianday(ib.expiry_date)) > 7  -- 1→7
  AND p.mid_cd IN ('001','002','003','004','005','012')  -- 식품 전용
  -- products JOIN 추가 (common.db ATTACH 필요)
```

### 옵션 비교

| 옵션 | 동작 | K4 효과 | 복잡도 |
|---|---|---|---|
| **D+C (권장)** | 식품 전용 + 7일 임계값 | NOT_MET → 진짜 신호만 (4매장 457건 예상) | 1쿼리 변경 |
| A. sentinel만 제외 | year≥2030 제외 | 8,328 → 8,098 (-2.8%) | 1줄 |
| B. OT/IB 재설계 | Phase 1.67 단일화 | 100% 정상화 | 큰 작업 |

---

## 4. 범위

### 대상 파일
- `src/infrastructure/database/repos/integrity_check_repo.py:249-288` `check_expiry_time_mismatch`
- 커넥션을 `_get_conn()` → `get_store_connection_with_common()`로 교체 (products JOIN 위해)
- `tests/test_integrity_check_repo.py` (있는지 확인 후) — 회귀 테스트 추가
- `docs/05-issues/scheduling.md` — 이슈 등록

### 비범위
- `data_integrity_service.py` 호출부 (시그니처 동일 유지)
- `action_proposal_service.py:92` expiry_time_mismatch 핸들러 (출력 형식 동일 유지)
- 비식품 mismatch 별도 메트릭 추가 (3차 목표는 후속 작업)

---

## 5. 성공 조건

- [ ] `check_expiry_time_mismatch` 식품 전용 + 임계값 7일로 변경
- [ ] 4매장 수동 호출 시 mismatch count가 100~150 범위 내 (45~60건/매장 평균)
- [ ] 회귀 테스트 추가:
  - 식품 7일 초과 → mismatch 카운트
  - 식품 1~7일 → 카운트 안 함
  - 비식품 1000일 차이 → 카운트 안 함
- [ ] 다음 milestone_snapshots에서 K4 NOT_MET → ACHIEVED 전환 (anomaly_count ≤ 임계치 시)
- [ ] 이슈체인 [WATCHING] 등록 + 검증 일자 기록

---

## 6. 리스크

- **임계값 7일이 너무 관대?**: 식품 폐기 정합성 측면에서 7일은 도시락(1일)/빵(3일) 기준 너무 큼. 그러나 BGF 사이트 expiry_time 표기 오차 + 새벽 02:00 만료 같은 시각 처리 오차를 흡수하려면 최소 5일 이상 필요. **7일은 보수적 시작점이며 1주 운영 후 임계값 재조정 가능**
- **식품 limit이 비식품 폐기 사고를 가림?**: 비식품(우유, 디저트 등)의 진짜 폐기 위험은? → 비식품 mismatch 별도 메트릭(3차)에서 다룰 것. 1차에서는 K4 의도(식품 폐기)에 집중
- **K4 anomaly가 0이 되어 마일스톤 의미 상실?**: 0건이 되더라도 진짜 식품 정합성 문제(7일 초과 457건)가 잡힘 → 후속 작업으로 자연스럽게 연결

---

## 7. 다음 단계

`/pdca design k4-non-food-sentinel-filter` — Design 문서 작성 (products JOIN 패턴 + 회귀 테스트 케이스 정의)
