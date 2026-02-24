# 대시보드 스케줄러 UI 개선 설계

## 📋 개요

**목표**: 스케줄러 제어 버튼을 크게 만들고, 스케줄 표시 방식을 개선하여 사용성 향상

**변경 범위**:
- `src/web/templates/index.html` - 모달 구조 수정
- `src/web/static/js/home.js` - 스케줄 렌더링 로직 수정
- `src/web/static/css/dashboard.css` - 버튼 스타일 추가

---

## 🎯 요구사항

### 1. 시작 버튼 크기 확대
- **현재**: 카드 내부에 작은 텍스트 링크 (`<a>` 태그)
- **변경**: 대형 독립 버튼으로 변경

### 2. 스케줄 표시 개선
- **현재**: 모든 스케줄을 등록 순서대로 표시
- **변경**: 오늘 스케줄만 00:00~23:59 시간 순으로 정렬

---

## 📐 설계

### 1. 스케줄러 모달 레이아웃 변경

#### 현재 구조 (index.html:174-184)
```html
<div id="schedulerModal" class="modal-overlay">
    <div class="modal-content">
        <div class="modal-header">
            <h2>스케줄러 작업 목록</h2>
            <button class="modal-close">&times;</button>
        </div>
        <div class="modal-body" id="schedulerModalBody">
            <!-- 동적 콘텐츠 -->
        </div>
    </div>
</div>
```

#### 변경 후 구조
```html
<div id="schedulerModal" class="modal-overlay">
    <div class="modal-content scheduler-modal">
        <div class="modal-header">
            <h2>오늘의 스케줄</h2>
            <button class="modal-close">&times;</button>
        </div>

        <!-- 상태 및 제어 영역 -->
        <div class="scheduler-control-panel">
            <div class="scheduler-status">
                <span id="schedulerStatusBadge" class="status-badge">확인중</span>
                <span id="schedulerPidText">-</span>
            </div>
            <div class="scheduler-actions">
                <button id="btnSchedulerStart" class="btn-scheduler-start" onclick="schedulerStart()">
                    <svg>...</svg>
                    <span>스케줄러 시작</span>
                </button>
                <button id="btnSchedulerStop" class="btn-scheduler-stop" onclick="schedulerStop()" style="display:none">
                    <svg>...</svg>
                    <span>스케줄러 중지</span>
                </button>
            </div>
        </div>

        <!-- 스케줄 타임라인 -->
        <div class="modal-body">
            <div class="schedule-timeline" id="schedulerTimeline">
                <!-- 동적 콘텐츠: 00시부터 24시 순 -->
            </div>
        </div>
    </div>
</div>
```

---

### 2. CSS 스타일 추가 (dashboard.css)

```css
/* 스케줄러 모달 전용 */
.scheduler-modal .modal-content {
    max-width: 700px;
    min-height: 600px;
}

/* 제어 패널 */
.scheduler-control-panel {
    padding: 24px;
    background: var(--card-bg);
    border-bottom: 1px solid var(--border-color);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 20px;
}

.scheduler-status {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.status-badge {
    display: inline-block;
    padding: 6px 12px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 600;
}

.status-badge.running {
    background: var(--success-bg);
    color: var(--success);
}

.status-badge.stopped {
    background: var(--danger-bg);
    color: var(--danger);
}

/* 대형 제어 버튼 */
.scheduler-actions {
    display: flex;
    gap: 12px;
}

.btn-scheduler-start,
.btn-scheduler-stop {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 16px 32px;
    font-size: 16px;
    font-weight: 600;
    border: none;
    border-radius: 12px;
    cursor: pointer;
    transition: all 0.2s;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.btn-scheduler-start {
    background: linear-gradient(135deg, var(--success), #28a745);
    color: white;
}

.btn-scheduler-start:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(40, 167, 69, 0.3);
}

.btn-scheduler-stop {
    background: linear-gradient(135deg, var(--danger), #dc3545);
    color: white;
}

.btn-scheduler-stop:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(220, 53, 69, 0.3);
}

.btn-scheduler-start svg,
.btn-scheduler-stop svg {
    width: 20px;
    height: 20px;
    fill: currentColor;
}

/* 스케줄 타임라인 */
.schedule-timeline {
    padding: 24px;
    max-height: 500px;
    overflow-y: auto;
}

.schedule-item {
    display: flex;
    align-items: flex-start;
    gap: 20px;
    padding: 16px;
    margin-bottom: 12px;
    background: var(--panel-bg);
    border-left: 4px solid var(--border-color);
    border-radius: 8px;
    transition: all 0.2s;
}

.schedule-item:hover {
    background: var(--card-bg);
    border-left-color: var(--primary);
}

.schedule-item.done {
    opacity: 0.5;
    border-left-color: var(--muted);
}

.schedule-item.upcoming {
    border-left-color: var(--success);
}

.schedule-time {
    flex-shrink: 0;
    width: 60px;
    font-size: 18px;
    font-weight: 700;
    color: var(--primary);
}

.schedule-info {
    flex: 1;
}

.schedule-name {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 4px;
}

.schedule-desc {
    font-size: 13px;
    color: var(--muted);
}

.schedule-freq {
    flex-shrink: 0;
    padding: 4px 10px;
    background: var(--bg-secondary);
    border-radius: 12px;
    font-size: 12px;
    color: var(--muted);
}
```

---

### 3. JavaScript 로직 수정 (home.js)

#### 3.1 스케줄러 모달 열기 함수 수정

**현재 위치**: home.js 438번 줄 근처

```javascript
async function openSchedulerModal() {
    var modal = document.getElementById('schedulerModal');
    var timeline = document.getElementById('schedulerTimeline');
    var statusBadge = document.getElementById('schedulerStatusBadge');
    var pidText = document.getElementById('schedulerPidText');
    var btnStart = document.getElementById('btnSchedulerStart');
    var btnStop = document.getElementById('btnSchedulerStop');

    modal.style.display = 'flex';
    timeline.innerHTML = '<div class="hint" style="text-align:center;padding:32px 0">로딩중...</div>';

    try {
        var data = await api('/api/home/scheduler/jobs');

        // 상태 업데이트
        if (data.running) {
            statusBadge.textContent = '동작중';
            statusBadge.className = 'status-badge running';
            pidText.textContent = 'PID: ' + data.pid;
            btnStart.style.display = 'none';
            btnStop.style.display = 'flex';
        } else {
            statusBadge.textContent = '정지됨';
            statusBadge.className = 'status-badge stopped';
            pidText.textContent = '스케줄러가 정지되어 있습니다';
            btnStart.style.display = 'flex';
            btnStop.style.display = 'none';
        }

        // 스케줄 렌더링 (오늘 스케줄만, 시간순)
        renderScheduleTimeline(data.jobs);

    } catch (e) {
        timeline.innerHTML = '<div class="hint error">데이터 로드 실패</div>';
        console.error('Scheduler modal error:', e);
    }
}
```

#### 3.2 스케줄 타임라인 렌더링 함수 (신규)

```javascript
function renderScheduleTimeline(jobs) {
    var timeline = document.getElementById('schedulerTimeline');

    if (!jobs || jobs.length === 0) {
        timeline.innerHTML = '<div class="hint">등록된 스케줄이 없습니다</div>';
        return;
    }

    // 현재 시각
    var now = new Date();
    var nowHM = now.getHours() * 60 + now.getMinutes(); // 분 단위 변환

    // 오늘 실행 스케줄만 필터링 (매일 + 오늘이 월요일이면 주간)
    var isMonday = now.getDay() === 1;
    var todayJobs = jobs.filter(j => {
        if (j.freq === '매일') return true;
        if (j.freq === '매주 월' && isMonday) return true;
        return false;
    });

    // 시간순 정렬 (00:00 ~ 23:59)
    todayJobs.sort((a, b) => {
        var aTime = parseTime(a.time);
        var bTime = parseTime(b.time);
        return aTime - bTime;
    });

    // HTML 생성
    var html = '';
    todayJobs.forEach(job => {
        var jobTime = parseTime(job.time);
        var isDone = jobTime < nowHM;
        var statusClass = isDone ? 'done' : 'upcoming';

        html += `
            <div class="schedule-item ${statusClass}">
                <div class="schedule-time">${job.time}</div>
                <div class="schedule-info">
                    <div class="schedule-name">${job.name}</div>
                    <div class="schedule-desc">${job.desc}</div>
                </div>
                <div class="schedule-freq">${job.freq}</div>
            </div>
        `;
    });

    timeline.innerHTML = html;
}

// 시간 파싱 헬퍼 (HH:MM → 분 단위)
function parseTime(timeStr) {
    var parts = timeStr.split(':');
    return parseInt(parts[0]) * 60 + parseInt(parts[1]);
}
```

---

### 4. 백엔드 API 수정 (선택사항)

현재 API (`/api/home/scheduler/jobs`)는 모든 스케줄을 반환합니다.
프론트엔드에서 필터링하므로 **백엔드 수정 불필요**하지만,
성능 최적화를 원한다면 다음과 같이 수정 가능합니다.

**api_home.py:150-182** 수정안:
```python
@home_bp.route("/scheduler/jobs", methods=["GET"])
def scheduler_jobs():
    """오늘 실행될 스케줄만 반환"""
    project_root = current_app.config["PROJECT_ROOT"]
    status = _get_scheduler_status(project_root)

    now = datetime.now()
    is_monday = now.weekday() == 0

    # 오늘 실행될 스케줄만 필터링
    today_jobs = [
        j for j in _SCHEDULER_JOBS
        if j["freq"] == "매일" or (j["freq"] == "매주 월" and is_monday)
    ]

    # 시간순 정렬
    today_jobs.sort(key=lambda x: x["time"])

    return jsonify({
        "running": status["running"],
        "pid": status["pid"],
        "total_jobs": len(today_jobs),
        "jobs": today_jobs,
    })
```

---

## 🔄 변경 파일 요약

| 파일 | 변경 내용 | 우선순위 |
|------|---------|---------|
| `src/web/templates/index.html` | 스케줄러 모달 HTML 구조 변경 | 필수 |
| `src/web/static/css/dashboard.css` | 대형 버튼 스타일 추가 | 필수 |
| `src/web/static/js/home.js` | 모달 렌더링 로직 수정 | 필수 |
| `src/web/routes/api_home.py` | API 필터링 로직 추가 | 선택 |

---

## ✅ 예상 결과

### Before (현재)
```
┌─────────────────────────┐
│  스케줄러 작업 목록      │
├─────────────────────────┤
│ [작은 링크] 시작         │
│ 06:30 매일 토큰 갱신     │
│ 07:00 매일 데이터 수집   │
│ 08:00 매주 월 주간 리포트 │ ← 화요일에도 표시됨
│ ...                     │
└─────────────────────────┘
```

### After (변경 후)
```
┌─────────────────────────────────┐
│  오늘의 스케줄                   │
├─────────────────────────────────┤
│ [정지됨] -                       │
│ ┌─────────────────────────────┐ │
│ │ ▶ 스케줄러 시작 (대형 버튼)  │ │
│ └─────────────────────────────┘ │
├─────────────────────────────────┤
│ 00:00  폐기 전 수집  [매일]     │ (완료 - 반투명)
│ 06:30  토큰 갱신     [매일]     │ (완료 - 반투명)
│ 07:00  데이터 수집   [매일]     │ (완료 - 반투명)
│ 09:00  폐기 전 수집  [매일]     │ (다음 실행 - 강조)
│ 11:00  상품 상세 수집 [매일]    │
│ ...                             │
└─────────────────────────────────┘
```

---

## 📝 구현 순서

1. ✅ **CSS 스타일 추가** (dashboard.css)
   - 버튼, 타임라인 스타일 정의

2. ✅ **HTML 구조 변경** (index.html)
   - 모달 레이아웃 재구성
   - 대형 버튼 추가

3. ✅ **JavaScript 로직 수정** (home.js)
   - `openSchedulerModal()` 함수 수정
   - `renderScheduleTimeline()` 함수 추가
   - 시간 파싱 헬퍼 추가

4. ⚪ **테스트**
   - 브라우저에서 동작 확인
   - 시간순 정렬 검증
   - 월요일 주간 스케줄 표시 확인

5. ⚪ **선택: 백엔드 최적화** (api_home.py)
   - API 필터링 로직 추가

---

## 🎨 디자인 가이드

### 색상 활용
- **시작 버튼**: 그라데이션 녹색 (`#10b981` → `#28a745`)
- **중지 버튼**: 그라데이션 빨강 (`#ef4444` → `#dc3545`)
- **완료된 스케줄**: 반투명 (`opacity: 0.5`)
- **다음 스케줄**: 좌측 보더 녹색 (`border-left: 4px solid var(--success)`)

### 아이콘 (SVG)
**시작 버튼 아이콘** (Play):
```svg
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M8 5v14l11-7z"/>
</svg>
```

**중지 버튼 아이콘** (Stop):
```svg
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <rect x="6" y="6" width="12" height="12"/>
</svg>
```

---

## 🚀 마이그레이션 노트

- 기존 `schedulerStart()`, `schedulerStop()` 함수는 유지 (재사용)
- 카드 내부 작은 링크는 그대로 유지 (호환성)
- 모달에서만 대형 버튼 추가 (점진적 개선)

---

**작성일**: 2026-02-05
**작성자**: Claude Code
**버전**: v1.0
