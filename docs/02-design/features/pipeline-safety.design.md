# 파이프라인 안전장치 설계서

## 1. StageIO dataclass

```python
@dataclass(frozen=True)
class StageIO:
    stage: str
    input_qty: int
    output_qty: int
    reads_from: str  # "pipe" | "prev" | "pipe_and_prev"
```

각 stage 실행 시 ctx["_stage_io"]에 append.

## 2. Phase A/B/C 분류

파이프라인 호출부(improved_predictor.py ~1799행)에 주석:

```
Phase A (독립 제안 가능): rule, rop
Phase B (변환/보정, 이전 결과 의존): promo, ml, new_product, diff, promo_floor, dessert_sub
Phase C (하드 제약, 순서 무관): cap, round
```

## 3. docstring 계약

각 _stage_* 메서드에 추가:
- pre: 이 단계가 필요로 하는 선행 조건
- post: 이 단계가 보장하는 사후 조건
- overwrites: 이 단계가 깰 수 있는 앞 단계 의도

## 4. shadow 계산 (diff + cap)

기존 로직에 영향 0. 원본(pipe["need_qty"]) 기준으로 "만약이었으면?" 계산.

```python
# _stage_diff에서
shadow_qty = max(1, int(pipe["need_qty"] * penalty))
result = result.with_shadow("diff_from_raw", shadow_qty)
```

## 5. prediction_logs.stage_trace

```sql
ALTER TABLE prediction_logs ADD COLUMN stage_trace TEXT;
```

값: JSON 직렬화된 _snapshot_stages + shadow dict.

## 6. 불변식 테스트 3개

1. 행사 중 발주량 >= 1 (promo_floor 불변식)
2. 카테고리 상한 초과 불가 (cap 불변식)
3. 발주 단위 정렬 (round 불변식)

형태: 순서 무관 (Two-Pass 전환 후에도 유효)
