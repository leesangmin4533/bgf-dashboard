# Plan: 동양점 단톡방 푸드 폐기 전용 알림

## 1. 개요

### 문제
현재 `send_message()`에 단톡방 전송이 투명 통합되어 있어 **모든 알림**(일일 리포트, 행사, 이상탐지 등)이 단톡방에 전송됨. 동양점 단톡방에는 **동양점 푸드 폐기 알림만** 보내야 함.

### 목표
1. `send_message()`에서 단톡방 자동 전송 제거 (방안 B)
2. 폐기 알림 전용 `send_to_group()` 메서드로 **명시적 호출**
3. 폐기 시간 기준 3단계 알림: **30분 전 → 정각 확정 → 누락건 후속**

### 대상 매장
- **구현+테스트**: 동양점(46704) 단톡방만 `enabled: true`
- **설계만**: 호반점/마평로드/원삼휴게소는 `enabled: false` (추후 활성화)
- **호반점(46513)**: 기존 "나에게 보내기"만 유지, 단톡방 전송 안 함

### 시간대
- 전 시간대 발송 (02:00, 10:00, 14:00, 22:00, 00:00)

## 2. 현행 폐기 스케줄 분석

### 폐기 시간별 대상

| 폐기시간 | 대상 | 배송차수 |
|---------|------|---------|
| 02:00 | 도시락/주먹밥/김밥 (001-003) | 1차 |
| 10:00 | 샌드위치/햄버거 (004-005) | 2차 |
| 14:00 | 도시락/주먹밥/김밥 (001-003) | 2차 |
| 22:00 | 샌드위치/햄버거 (004-005) | 1차 |
| 00:00 | 빵 (012) | - |

### 현행 스케줄 (run_scheduler.py)

```
PRE_ALERT_COLLECTION (알림 45분 전)
  └── 판매현황 수집 (최신 stock 확보)

EXPIRY_ALERT_SCHEDULE (30분 전)
  └── send_expiry_alert(expiry_hour) → notifier.send_message()
      └── 카카오 API "나에게 보내기" + (현재) 단톡방 동시 전송

EXPIRY_CONFIRM_SCHEDULE (정밀 폐기 3단계)
  ├── pre_collect (10분 전): 판매 수집
  ├── judge (정각): 만료 배치 목록 + stock 스냅샷 저장
  └── confirm (10분 후): 수집 + stock 비교 → 폐기 확정
```

## 3. 변경 설계

### 3.1 send_message에서 단톡방 제거

**현재**: `send_message()` → 카카오 API + 단톡방 (모든 메시지)
**변경**: `send_message()` → 카카오 API만 (기존 복원)

단톡방 전송은 `send_to_group(text)` 공개 메서드로 분리하여 호출부에서 명시적으로 사용.

### 3.2 폐기 알림 3단계 플로우

```
[30분 전] 예고 알림
  └── ExpiryChecker.send_expiry_alert(expiry_hour)
      ├── notifier.send_message(msg)       ← 나에게 보내기 (기존)
      └── notifier.send_to_group(msg)      ← 단톡방 (신규, 명시적)

[정각+10분] confirm 단계에 통합: 폐기 확정 + 컨펌 알림
  └── expiry_confirm_wrapper(expiry_hour)
      ├── 판매 수집 (10분간 변동)
      ├── 판정 vs 현재 stock 비교 → 폐기 확정
      ├── 통합전표 기반 실제 폐기 목록 생성
      └── 단톡방 컨펌 메시지 발송
          ├── 폐기 확정 목록 (판정 시 있었고 + 아직 재고 있는 것)
          └── 누락건 (30분 전 알림에 없었지만 confirm에서 새로 발견된 것)

[정리]
  └── 나에게 보내기: 30분 전 + 컨펌 모두 발송
  └── 단톡방: 30분 전 예고 + 컨펌 확정 모두 발송
```

### 3.3 컨펌 메시지 포맷

```
[CU동양점] 14:00 폐기 확정 (03/30 14:10)

[폐기 확정] 5개
  - 도시락류
    삼각김밥참치마요1  2개
    도시락불고기2      1개
  - 김밥류
    김밥야채2          3개

[누락건 추가] 2개 (30분 전 미포함)
  - 주먹밥새우2        1개
  - 김밥참치2          1개

총 7개 상품 폐기 처리 완료
```

### 3.4 매장별 단톡방 매핑

- `config/group_chat.json`에 `rooms` dict: `{ store_id: 채팅방이름 }`
- 각 매장의 폐기 알림은 **해당 매장의 단톡방에만** 전송
- 매장이 추가되면 rooms에 엔트리만 추가

```json
{
  "enabled": true,
  "rooms": {
    "46704": {"name": "CU동양점", "enabled": true},
    "46513": {"name": "CU호반베르디움", "enabled": false},
    "47863": {"name": "CU마평로드", "enabled": false},
    "49965": {"name": "CU원삼휴게소", "enabled": false}
  },
  "alert_types": ["food_expiry"]
}
```

## 4. 수정 대상 파일

| 파일 | 변경 내용 |
|------|---------|
| `src/notification/kakao_notifier.py` | `_send_to_group` 자동호출 제거, `send_to_group()` 공개 메서드 추가 |
| `src/alert/expiry_checker.py` | `send_expiry_alert()`에 단톡방 전송 추가, 컨펌 메시지 생성 함수 추가 |
| `run_scheduler.py` | `expiry_confirm_wrapper()`에 컨펌 알림 로직 통합 |
| `config/group_chat.json` | `store_filter`, `alert_types` 필드 추가 |

## 5. 구현 순서

1. `kakao_notifier.py`: `send_message()`에서 `_send_to_group` 제거 → `send_to_group()` 공개 메서드
2. `config/group_chat.json`: `store_filter` 추가
3. `expiry_checker.py`: `send_expiry_alert()`에서 매장 필터 확인 후 `send_to_group()` 호출
4. `expiry_checker.py`: `generate_confirm_message()` 신규 함수 (확정 목록 + 누락건)
5. `run_scheduler.py`: `expiry_confirm_wrapper()`에 컨펌 알림 발송 통합
6. 통합 테스트

## 6. 리스크

| 리스크 | 대응 |
|--------|------|
| 카카오톡 PC 창이 닫혀있으면 전송 불가 | 실패 시 로그 경고, 나에게 보내기는 정상 동작 |
| 동양점이 is_active: false | 호반점(46513) 폐기 데이터를 단톡방에 전송 (테스트), 추후 active 전환 시 store_filter 변경 |
| Win32API 포그라운드 전환 시 BGF 작업 간섭 | 전송 후 0.5초 대기, 카카오톡 최소화 복원 |
