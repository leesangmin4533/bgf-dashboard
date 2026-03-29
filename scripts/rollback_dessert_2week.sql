-- Rollback: dessert-2week-evaluation (v2w)
-- 배포 후 문제 발견 시 실행
-- 대상: v2w 자동확정으로 인해 CONFIRMED_STOP된 상품 복구

-- 1. v2w 배포 이후 자동확정된 상품 복구 (operator_note에 [v2w] 식별자 포함)
UPDATE dessert_decisions
SET operator_action = NULL,
    operator_note = NULL,
    action_taken_at = NULL
WHERE operator_action = 'CONFIRMED_STOP'
  AND operator_note LIKE '%auto%'
  AND action_taken_at >= '2026-03-29';  -- 배포일 이후만

-- 2. 복구 확인 쿼리
SELECT store_id, COUNT(*) AS restored
FROM dessert_decisions
WHERE operator_action IS NULL
  AND judgment_period_end >= '2026-03-29'
GROUP BY store_id;

-- 3. 코드 롤백 후 constants.py에서:
--    DESSERT_2WEEK_EVALUATION_ENABLED = False
--    로 변경하면 기존 보호기간(4주/3주) 즉시 복구
