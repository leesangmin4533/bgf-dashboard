# Plan: security-hardening

> BGF 리테일 자동 발주 시스템 보안 강화

## 1. 개요

### 배경
bkit 보안 분석 결과 **35/100점** (Critical 3건, High 5건, Medium 4건, Low 3건).
Flask API 무인증, .env 평문 노출, subprocess 입력 미검증 등 심각한 취약점 발견.

### 목표
- 보안 점수 **35 -> 75+** 달성
- OWASP Top 10 주요 항목 대응 완료
- 운영 환경에서 안전한 서비스 제공

### 범위
| 포함 | 제외 |
|------|------|
| Flask API 인증/인가 | 전체 아키텍처 리팩터링 |
| 입력 검증 강화 | ML 모델 보안 |
| 시크릿 관리 개선 | 네트워크 방화벽 설정 |
| 보안 헤더 추가 | SSL/TLS 인증서 관리 |
| 에러 응답 정리 | 코드 서명 |

## 2. 현재 상태 (As-Is)

### Critical (즉시 조치 - 완료)
- [x] ~~`.gitignore` 미등록~~ -> 생성 완료
- [x] ~~Flask `host="0.0.0.0"` 바인딩~~ -> `127.0.0.1` 변경 완료
- [x] ~~`store_id` subprocess 미검증~~ -> 정규식 검증 추가 완료
- [x] ~~`SECRET_KEY` 하드코딩 기본값~~ -> `secrets.token_hex(32)` 변경 완료
- [x] ~~에러 응답에 `str(e)` 노출 (15곳)~~ -> 일반 메시지 + logger 변경 완료

### High (미완료 - 이번 PDCA 대상)
- [ ] CSRF 보호 미비
- [ ] DB `stores` 테이블 비밀번호 평문 저장
- [ ] `stores.json`에 인증 정보 기록

### Medium (미완료 - 이번 PDCA 대상)
- [ ] 보안 헤더 미설정 (X-Content-Type-Options, X-Frame-Options, CSP)
- [ ] Rate Limiting 미비
- [ ] 의존성 버전 미고정

### Low (미완료 - 이번 PDCA 대상)
- [ ] 웹 API 접근 로깅 미비

## 3. 목표 상태 (To-Be)

### 3.1 Flask 보안 미들웨어
```
요청 -> [보안 헤더] -> [Rate Limiter] -> [접근 로깅] -> 라우트 처리 -> 응답
```

### 3.2 비밀번호 해싱
- `stores` 테이블: `bgf_password` 컬럼 해싱 (bcrypt 또는 hashlib)
- `stores.json`: 비밀번호 필드 제거, 환경변수만 사용

### 3.3 CSRF 방어
- Flask-WTF 또는 수동 CSRF 토큰 (API 전용이므로 SameSite 쿠키 + Referer 검증)

### 3.4 보안 헤더
```python
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    return response
```

### 3.5 Rate Limiting
- 일반 API: 60 req/min
- 무거운 연산 (predict, run-script): 5 req/min

### 3.6 접근 로깅
- `@app.before_request`에서 IP, 메서드, 경로, timestamp 로깅

## 4. 구현 계획

### Phase A: CSRF + 보안 헤더 (Low Risk)
| 작업 | 파일 | 예상 |
|------|------|------|
| 보안 헤더 after_request 추가 | `src/web/app.py` | 10분 |
| SameSite 쿠키 설정 | `src/web/app.py` | 5분 |
| API Referer 검증 (선택) | `src/web/app.py` | 15분 |

### Phase B: 비밀번호 해싱 (Medium Risk)
| 작업 | 파일 | 예상 |
|------|------|------|
| 해싱 유틸리티 함수 | `src/utils/crypto.py` (신규) | 10분 |
| `store_service.py` 해싱 적용 | `src/application/services/store_service.py` | 15분 |
| `stores.json` 비밀번호 필드 제거 | `config/stores.json` | 5분 |
| DB 마이그레이션 (기존 평문 해싱) | `src/db/models.py` | 15분 |

### Phase C: Rate Limiting + 접근 로깅 (Low Risk)
| 작업 | 파일 | 예상 |
|------|------|------|
| 간단한 Rate Limiter 구현 | `src/web/middleware.py` (신규) | 20분 |
| 접근 로깅 before_request 추가 | `src/web/app.py` | 10분 |

### Phase D: 의존성 고정 + 테스트 (Low Risk)
| 작업 | 파일 | 예상 |
|------|------|------|
| requirements.txt 버전 고정 | `requirements.txt` | 10분 |
| 보안 테스트 케이스 추가 | `tests/test_web_security.py` (신규) | 30분 |

## 5. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 비밀번호 해싱 시 기존 로그인 플로우 깨짐 | 높음 | 마이그레이션 시 기존 평문 -> 해시 일괄 변환, 폴백 로직 |
| Rate Limiter가 정상 스케줄러 실행 차단 | 중간 | localhost 요청은 Rate Limit 제외 |
| 보안 헤더 CSP가 대시보드 JS/CSS 차단 | 낮음 | CDN 도메인 화이트리스트 추가 |

## 6. 성공 기준

- [ ] 보안 점수 **75점 이상** (security-architect 재분석)
- [ ] Critical/High 이슈 **0건**
- [ ] 1,520개 기존 테스트 전부 통과
- [ ] 보안 테스트 10건 이상 추가

## 7. 참고

- 기존 보안 분석 보고서: `docs/02-design/security-spec.md`
- Phase 1 긴급 조치 (이미 완료): `.gitignore`, Flask 바인딩, store_id 검증, SECRET_KEY, 에러 응답
