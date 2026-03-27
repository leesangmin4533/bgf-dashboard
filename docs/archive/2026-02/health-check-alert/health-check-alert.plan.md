# Plan: 헬스 체크 + 에러 알림 + 커스텀 예외 + DB 백업 확장

> **Feature**: health-check-alert
> **Created**: 2026-02-25
> **Status**: Approved

---

## 1. 배경

프로젝트 구조 감사에서 발견된 P1 Quick Win 4가지를 하나의 PDCA로 통합 구현:
1. 커스텀 예외 계층 없음 → `except Exception` 범용 사용
2. `/api/health` 미존재 → 서비스 다운 감지 불가
3. 에러 알림 없음 → 심각한 실패 수시간 미발견
4. 매장 DB 백업 미포함 → stores/46513.db 손상 시 데이터 영구 손실

## 2. 목표

| # | 항목 | 목표 |
|---|------|------|
| 1 | 커스텀 예외 | AppException 계층 정의, 주요 모듈에 적용 |
| 2 | 헬스 체크 | `/api/health` 엔드포인트 (DB, 스케줄러, 디스크, 최근 실행) |
| 3 | 에러 알림 | `AlertingHandler` 로깅 핸들러로 ERROR 발생 시 자동 알림 |
| 4 | DB 백업 | sync_to_cloud.py에 매장 DB 포함 + SHA256 무결성 검증 |

## 3. 범위

### In Scope
- `src/core/exceptions.py` 커스텀 예외 클래스 정의
- `src/web/routes/api_health.py` 헬스 체크 Blueprint
- `src/utils/alerting.py` 알림 핸들러 (로그 ERROR → Kakao/파일)
- `scripts/sync_to_cloud.py` 매장 DB 포함 + SHA256 검증
- 테스트

### Out of Scope
- 전체 코드베이스의 except Exception → 커스텀 예외 마이그레이션 (추후)
- 외부 모니터링 (Prometheus, Grafana)
- CI/CD 파이프라인
- 스테이징 환경

## 4. 예상 수정/신규 파일

| 파일 | 유형 | 내용 |
|------|------|------|
| `src/core/exceptions.py` | 신규 | 커스텀 예외 계층 |
| `src/web/routes/api_health.py` | 신규 | 헬스 체크 API |
| `src/utils/alerting.py` | 신규 | AlertingHandler + 알림 디스패처 |
| `src/web/routes/__init__.py` | 수정 | health_bp 등록 |
| `src/utils/logger.py` | 수정 | AlertingHandler 연결 |
| `scripts/sync_to_cloud.py` | 수정 | 매장 DB + SHA256 |
| `src/settings/constants.py` | 수정 | HEALTH_CHECK 상수 |

## 5. 의존성

- 기존 Kakao 알림 (`src/notification/kakao_notifier.py`)
- DashboardService (`src/application/services/dashboard_service.py`)
- DBRouter (`src/infrastructure/database/connection.py`)
- sync_to_cloud.py 기존 패턴
