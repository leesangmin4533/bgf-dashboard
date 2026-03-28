# 예측 파이프라인 안전장치 (pipeline-safety)

## 1. 개요

### 문제
10단계 예측 파이프라인에 새 로직을 추가할 때마다 순서가 엉켜 의도치 않은 결과 발생.
과거 3건의 버그가 동일 패턴: "뒤 단계가 앞 단계 의도를 덮어씀".

### 목표
1. 각 단계에 "경고 표지판" 설치 (주석 + docstring 계약)
2. 순서 변경 시 자동 감지하는 테스트 3개
3. 나중 구조 변경(Two-Pass)을 위한 데이터 수집 인프라

## 2. 범위

### 포함 (당장)
- Phase A/B/C 주석 추가 (파이프라인 호출부)
- 10개 _stage_* docstring에 pre/post/overwrites 추가
- StageIO dataclass + reads_from 선언
- raw_need_qty 스냅샷 기록
- diff+cap shadow 계산
- prediction_logs에 stage_trace 컬럼 추가
- Phase간 불변식 테스트 3개

### 제외
- Two-Pass 구조 변경 (나중에)
- ML 위치 이동 (나중에)
- Stage 리스트화/클래스 분리 (하지 않음)

## 3. 성공 기준
- [ ] 10개 stage docstring에 pre/post/overwrites 명시
- [ ] StageIO 기록으로 reads_from 확인 가능
- [ ] 불변식 테스트 3개 통과
- [ ] prediction_logs.stage_trace에 JSON 기록
- [ ] 기존 테스트 전부 통과
- [ ] 발주 결과에 영향 0 (shadow만 추가)

## 4. 제약 조건
- 기존 발주 흐름(result.qty) 일절 변경 금지
- shadow/metadata만 side-channel로 추가
- DB 마이그레이션: stage_trace TEXT 컬럼 1개 (ALTER TABLE)

## 5. 토론 결과 반영
- 실용주의: Phase A/B/C 구분 + StageIO.reads_from
- ML엔지니어: shadow 계산 + stage_trace 컬럼
- 상세: data/discussions/20260328-pipeline-safety-prep/

## 6. 예상 일정
- 구현: 2.5시간 (표지판 2시간 + 도로공사 준비 30분)
