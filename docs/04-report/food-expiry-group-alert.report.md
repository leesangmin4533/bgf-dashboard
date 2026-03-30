# 완료 보고서: 동양점 단톡방 푸드 폐기 전용 알림

📅 완료일: 2026-03-30
📊 Match Rate: 97%
🔄 Iteration: 0 (토론 기반 수정 1회)

---

## 1. 개요

BGF 리테일 자동 발주 시스템에 **매장별 카카오톡 단톡방 폐기 알림** 기능을 추가.
기존 "나에게 보내기" 단일 채널에서, 매장별 단톡방 동시 발송으로 확장.

## 2. 구현 범위

### 2.1 완료 항목

| # | 항목 | 파일 | 커밋 |
|---|------|------|------|
| 1 | KakaoGroupSender (Win32API 단톡방 전송) | kakao_group_sender.py | de87552 |
| 2 | send_to_group(text, store_id) 공개 메서드 | kakao_notifier.py | 5c3fa11 |
| 3 | 매장별 rooms dict (enabled 개별 제어) | config/group_chat.json | 5c3fa11 |
| 4 | ExpiryChecker store_name + [매장명] 접두사 | expiry_checker.py | 5c3fa11 |
| 5 | step1 예고 알림 합류 (PRE_ALERT + EXPIRY_ALERT 통합) | run_scheduler.py | 5c3fa11 |
| 6 | step3 폐기전표 대조 + 미폐기 경고 | run_scheduler.py | 17e3be0 |
| 7 | EXPIRY_ALERT_SCHEDULE dead import 제거 | run_scheduler.py | 80d3a02 |
| 8 | 동양점(46704) is_active=true | config/stores.json | 5c3fa11 |

### 2.2 보류 항목 (운영 결과 후 결정)

| 항목 | 이유 |
|------|------|
| 복수 배치 qty 비교 | 실운영 데이터로 오판 빈도 확인 후 결정 |
| 호반점/마평로드/원삼휴게소 단톡방 활성화 | 동양점 테스트 완료 후 순차 활성화 |

## 3. 아키텍처 변경

### Before (5단계, BGF 2회 접속)
```
-60min  PRE_ALERT_COLLECTION    ← BGF 수집
-30min  EXPIRY_ALERT            ← 카카오 알림
-10min  step1 pre_collect       ← BGF 수집
  0min  step2 judge             ← 판정
+10min  step3 confirm           ← BGF 수집 + 확정
```

### After (3단계, BGF 접속 감소)
```
-10min  step1 pre_collect       ← BGF 수집 + 예고 알림 (나에게 + 단톡방)
  0min  step2 judge             ← 판정
+10min  step3 confirm           ← BGF 수집 + 폐기전표 DB 대조 + 컨펌 알림
```

### 알림 채널 분리
```
send_message(msg)                → 카카오 API (나에게, 전 매장)
send_to_group(msg, store_id)     → Win32API (해당 매장 단톡방만)
```

## 4. 수정 파일 목록

| 파일 | 변경 내용 |
|------|---------|
| src/notification/kakao_notifier.py | send_to_group 공개 메서드, _send_to_group 자동호출 제거, rooms dict 로드 |
| src/notification/kakao_group_sender.py | 전면 리팩토링: rooms dict 기반, store_id 라우팅, enabled 체크 |
| src/alert/expiry_checker.py | store_name 파라미터, [매장명] 접두사, send_to_group 호출 |
| run_scheduler.py | step1 합류, step3 폐기전표 대조, PRE_ALERT/EXPIRY_ALERT 제거 |
| config/group_chat.json | rooms dict (4매장, 동양점만 enabled) |
| config/stores.json | 동양점 is_active=true |

## 5. 검증 결과

### 5.1 Gap 분석: 97%
- Design 항목 33개 중 31개 일치
- Cosmetic 2건 (기능 무관)
- Gap 1건 (dead import) → 수정 완료

### 5.2 전문가 토론 (4명)
- 드라이버 NoneType 버그 발견 → run_optimized 복원으로 수정
- 13개 체크리스트 → 3개로 축소 (기존 서비스 활용)
- YAGNI 항목 삭제 (ctx 캐싱, 재시도, 확인불가 분기)

### 5.3 통합 테스트
| 테스트 | 결과 |
|--------|------|
| send_message → 단톡방 안 감 | PASS |
| send_to_group(46704) → CU동양점만 | PASS |
| send_to_group(46513) → enabled=false 스킵 | PASS |
| BGF 사이트 폐기전표 vs DB 정합성 | PASS (전표번호, 상품수, 원가 일치) |
| 스케줄 설정 일관성 (step1/step2/step3 동일 expiry_hour) | PASS |
| PRE_ALERT/EXPIRY_ALERT 제거 확인 | PASS |

## 6. config/group_chat.json 현재 상태

```json
{
  "enabled": true,
  "rooms": {
    "46704": {"name": "CU동양점", "enabled": true},
    "46513": {"name": "CU호반베르디움", "enabled": false},
    "47863": {"name": "CU마평로드", "enabled": false},
    "49965": {"name": "CU원삼휴게소", "enabled": false}
  }
}
```

## 7. 실운영 대기

다음 실운영 테스트: **2026-03-30 21:50** (22:00 폐기 step1)
- 동양점 단톡방에 예고 알림 발송 확인
- 22:10 step3에서 폐기전표 대조 + 컨펌 알림 확인

## 8. 참고 문서

| 문서 | 경로 |
|------|------|
| Plan | docs/01-plan/features/food-expiry-group-alert.plan.md |
| Design | docs/02-design/features/food-expiry-group-alert.design.md |
| Analysis | docs/03-analysis/food-expiry-group-alert.analysis.md |
| 토론 리포트 | data/discussions/20260330-expiry-confirm-verification/03-최종-리포트.md |
