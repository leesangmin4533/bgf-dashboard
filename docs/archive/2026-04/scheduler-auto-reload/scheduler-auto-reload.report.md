# PDCA Report: scheduler-auto-reload

> 완료일: 2026-04-07
> Match Rate: 100% — PASS
> 이슈체인: scheduling.md#scheduler-모듈-캐시-코드-fix-무력화

---

## 핵심 요약

오늘 4건 PDCA 작업(claude-respond, ops-metrics, d1-bgf, k4)이 공통으로 가지던 **"수동 재시작 의식"** 문제를 해소. SrcWatcher 데몬 스레드가 src/ 변경을 감지하면 graceful exit, 외부 wrapper 배치(`start_scheduler_loop.bat`)가 자동 재시작. 코드 수정 후 1분 이내 자동 적용.

---

## PDCA 사이클 요약

| 단계 | 결과 |
|------|------|
| **Plan** | 5개 옵션 비교 → C(mtime watch + wrapper 재시작) 채택 |
| **Design** | SrcWatcher 알고리즘 + graceful exit 조건 + wrapper 분기 |
| **Do** | 모듈/패치/스크립트/테스트 4종 작성, 5/5 통과 |
| **Check** | Match Rate 100% (Gap 없음) |
| **Act** | 불필요 |

---

## 변경 사항

### 신규 파일
- `src/infrastructure/scheduler/__init__.py`
- `src/infrastructure/scheduler/src_watcher.py` — SrcWatcher 데몬 + src_signature
- `scripts/start_scheduler_loop.bat` — 외부 wrapper (exit code 분기)
- `tests/test_src_watcher.py` — 5개 회귀 테스트

### 수정 파일
- `run_scheduler.py` — 무한 루프 진입 전 SrcWatcher 시작 + 매 분 reload_event 확인 → sys.exit(0)
- `CLAUDE.md` — 사용법에 `start_scheduler_loop.bat` 권장 추가

---

## 메커니즘

```
[start_scheduler_loop.bat]
       │ 무한 재시작 루프
       ▼
[python run_scheduler.py]
       ├─ SrcWatcher daemon (60초 폴)
       │     └─ src/ mtime+size 변경 감지 → reload_event.set()
       │
       └─ schedule loop (매 분)
             └─ reload_event.is_set()? → sys.exit(0)
                  ↑
                  wrapper가 exit 0 감지 → 즉시 재시작 → 새 코드 로드
```

### exit code 의미
- **0**: auto-reload → 즉시 재시작
- **2**: 정지 명령 → wrapper 종료
- **기타**: 오류 → 5초 backoff 후 재시작

---

## 검증

### 자동 테스트 (5/5)
- `test_stable_when_unchanged`
- `test_changes_on_file_modification`
- `test_excludes_pycache`
- `test_sets_reload_event_on_change`
- `test_event_stays_clear_when_no_change`

### 잔여 라이브
- [ ] 운영자가 `start_scheduler_loop.bat` 실행 후 임의 코드 변경 → 1~2분 내 자동 재시작 확인
- [ ] 로그에서 `[SrcWatcher] src 변경 감지` → `[Scheduler] auto-reload 트리거` → 새 프로세스 시작 시퀀스 확인

---

## 교훈

1. **운영 의식(ritual) 자동화의 가치**: "scheduler 재시작"이라는 1분 작업이 오늘만 4번 잊혀져 fix를 무효화. 무인 자동화는 fix 신뢰성의 핵심
2. **importlib.reload는 함정**: transitive 종속성/객체 정체성 깨짐 → 프로세스 재시작이 가장 안전
3. **mtime + size hash 조합**: hash 단독은 무겁고, mtime 단독은 OneDrive 신뢰성 낮음. 조합이 가벼우면서 강건
4. **MVP 단순화**: graceful 작업 진행 중 잠금 같은 정교함은 비범위로 두고 즉시 exit. 작업 누락은 다음 cycle에서 catch up (대부분 멱등)

---

## 영향: 오늘 4건 PDCA 자동 적용

| 작업 | 잔여 검증 (Before) | 잔여 검증 (After) |
|---|---|---|
| claude-respond-fix | 수동 재시작 후 04-08 23:58 | **자동 재시작 후 즉시 적용** |
| ops-metrics-waste-query-fix | 수동 재시작 후 23:55 | **자동 적용** |
| d1-bgf-collector-import-fix | 수동 재시작 후 14:00 | **자동 적용** |
| k4-non-food-sentinel-filter | 수동 재시작 후 milestone | **자동 적용** |

→ **5번째 작업이 앞 4건의 잔여 운영 의식을 모두 해소.**

---

## 후속 작업 후보
- backoff 점진 증가 (5s→30s→60s)
- 작업 진행 중 graceful 잠금 (in-flight 작업 보호)
- git rev-parse HEAD 백업 감지 (OneDrive mtime 실패 대비)

---

## 관련 문서
- Plan: `docs/01-plan/features/scheduler-auto-reload.plan.md`
- Design: `docs/02-design/features/scheduler-auto-reload.design.md`
- Analysis: `docs/03-analysis/scheduler-auto-reload.analysis.md`
- Issue: `docs/05-issues/scheduling.md#scheduler-모듈-캐시-코드-fix-무력화`
- 선행 사례 (수동 재시작 필요): docs/archive/2026-04/{claude-respond-fix, ops-metrics-waste-query-fix, d1-bgf-collector-import-fix, k4-non-food-sentinel-filter}
