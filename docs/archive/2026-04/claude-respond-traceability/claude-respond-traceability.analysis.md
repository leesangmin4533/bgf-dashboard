# Gap Analysis: claude-respond-traceability

> 관련: Plan `docs/01-plan/features/claude-respond-traceability.plan.md`, Design `docs/02-design/features/claude-respond-traceability.design.md`
> 분석 일자: 2026-04-07

## 요약

| 항목 | 값 |
|---|---|
| **Match Rate** | **95%** |
| FR 항목 수 | 5 |
| FR 완전 구현 | 5 |
| NFR 항목 수 | 3 |
| NFR 완전 구현 | 3 |
| Minor Gap | 2 (단위 테스트 미작성, 운영 검증 미실시) |
| Critical Gap | 0 |

설계 대비 코드 구현은 1:1 매칭됨. 감점은 **설계에 포함된 단위 테스트(5개)가 작성되지 않았다**는 점과 **04-08 운영 검증이 아직 미수행**인 점에서 발생.

## FR 매칭 상세

### FR-1 입력 스냅샷 보존 ✅
| Design | Implementation |
|---|---|
| `data/auto_respond/{date}_input.json` | `claude_responder.py:155` `path = _OUTPUT_DIR / f"{today}_input.json"` |
| 중복 시 HHMMSS suffix | `claude_responder.py:156-158` `f"{today}_input_{ts}.json"` |
| 실패 시 None 무해화 | `claude_responder.py:165-167` `except Exception → logger.debug + return None` |
| **상태** | **완전 일치** |

### FR-2 소요시간 기록 ✅
| Design | Implementation |
|---|---|
| `started_at` | `run_scheduler.py` `started_at = datetime.now().isoformat()` |
| `duration_sec` (성공/실패/스킵 모두) | 5개 경로 모두 `t_start = time.monotonic()` + `round(time.monotonic() - t_start, 2)` |
| claude_responder 내부 duration | `claude_responder.py:96,102,119` `t0 = time.monotonic()` 성공/실패 모두 계산 |
| **상태** | **완전 일치** |

### FR-3 응답 유효성 검증 ✅
| Design | Implementation |
|---|---|
| `_MIN_RESPONSE_LEN = 50` | `claude_responder.py:30` 동일 |
| 빈/짧은 응답 → RuntimeError | `claude_responder.py` `_call_claude` returncode=0 분기 이후 `len(output.strip()) < _MIN_RESPONSE_LEN → RuntimeError` |
| debug 로그 생성 | `_dump_failure_context(cmd, result, env)` 호출 |
| **상태** | **완전 일치** |

### FR-4 스킵 상태 구분 ✅
| Design | Implementation |
|---|---|
| `record_step(..., status=None)` | `pipeline_status.py:27-33` 시그니처 확장 |
| status 값 "ok"/"skipped"/"failed" | `run_scheduler.py` 5개 경로 각각 명시적 전달 |
| 하위호환 `success` 유지 | `pipeline_status.py` `data["steps"][step_name]`에 `success`, `status` 둘 다 저장 |
| 스킵/성공 분리 | 선행실패=skipped, 비활성=skipped, pending없음=skipped, 정상=ok, 에러=failed |
| **상태** | **완전 일치** + 추가 개선 (skipped는 카카오 알림 미발송 — design에는 없던 안전장치) |

### FR-5 성공 경로 로그 상세화 ✅
| Design | Implementation |
|---|---|
| `[AutoRespond] 분석 완료: duration=Xs, anomalies=N, prompt=N chars, response=N chars` | `claude_responder.py:133-137` 동일 패턴 + `path=...` 추가 |
| **상태** | **완전 일치** |

## NFR 매칭 상세

### NFR-1 하위호환 ✅
- `pipeline_status.json`의 `success`/`summary`/`details`/`completed_at` 유지
- `status`/`duration_sec`는 신규 추가 (기존 파서 영향 없음)
- `record_step`의 `status` 파라미터 기본값 `None` → 기존 호출부(`ops_detect`, `chain_report`) 수정 불필요

### NFR-2 실패 무해화 ✅
- `_snapshot_input()` 내부 `try/except Exception → logger.debug → return None`
- `respond_if_needed` 호출부에서 snapshot 실패 검사 없음 (None이면 snapshot_str도 None으로 자연 전파)

### NFR-3 디스크 사용 ✅
- `data/auto_respond/` 하위 저장. 로테이션은 범위 외(향후 작업)로 명시

## 수락 기준 매칭

| # | 기준 | 상태 | 근거 |
|:--:|---|:---:|---|
| 1 | `data/auto_respond/{date}_input.json` 존재 | ⏳ | 로직 구현 완료. 04-08 23:58 실행 필요 |
| 2 | `details`에 `started_at`, `duration_sec`, `status` 필드 포함 | ✅ | 코드 레벨 확인. 실행 검증은 04-08 |
| 3 | status "ok"/"skipped"/"failed" 구분 | ✅ | 5경로 매핑 완료 |
| 4 | 빈 응답 → failed + debug 로그 | ✅ | `_call_claude`에 len 검증 + `_dump_failure_context` |
| 5 | INFO 로그 duration/anomalies/prompt/response 포함 | ✅ | `claude_responder.py:133-137` |
| 6 | 하위호환 (DailyChainReport 영향 없음) | ✅ | `success` 필드 유지, 신규 키는 details 하위 |

## Gap 목록

### G1 [Minor] 단위 테스트 미작성
- **설계**: `tests/test_claude_responder_traceability.py`에 5개 테스트 정의
- **구현**: 미작성
- **영향**: 회귀 방지 가드 부재. 향후 수정 시 잠재 리스크
- **권장**: `/pdca iterate` 또는 별도로 테스트 추가

### G2 [Minor] 운영 검증 미실시
- **설계**: 04-08 23:58 실행 후 4개 체크리스트 확인
- **구현**: 04-07 현재 코드 수정만 완료, 실제 실행 대기
- **영향**: 실측 duration(max_turns=30) 미확인
- **권장**: 04-08 23:58 이후 이슈 체인 `scheduling.md` 갱신

## 판정

**Match Rate 95% ≥ 90%** → Report 단계로 진행 가능.

단, G1(단위 테스트)은 코드 품질 관점에서 작성을 권장. G2는 일정상 자연 해소 예정.

## 다음 단계

- `/pdca iterate claude-respond-traceability` — G1 테스트 추가 원할 시
- `/pdca report claude-respond-traceability` — 현 상태로 완료 리포트 생성 (권장)
