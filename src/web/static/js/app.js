/* BGF 발주 시스템 - SPA 라우터 + 공통 유틸 + 테마 관리 + 글로벌 매장 선택 */

// === 글로벌 매장 ID ===
var _currentStoreId = '46513';

// === 현재 로그인 사용자 ===
var _currentUser = null;

// === 테마 관리 ===
var THEME_KEY = 'bgf-theme';
var MOON_PATH = 'M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z';
var SUN_PATH = 'M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41M12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12z';

function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
}

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_KEY, theme);
    updateThemeIcon(theme);
    updateChartTheme(theme);
}

function toggleTheme() {
    var current = getTheme();
    setTheme(current === 'dark' ? 'light' : 'dark');
}

function updateThemeIcon(theme) {
    var icon = document.getElementById('themeIcon');
    if (!icon) return;
    var path = icon.querySelector('path');
    if (!path) return;
    if (theme === 'light') {
        path.setAttribute('d', SUN_PATH);
    } else {
        path.setAttribute('d', MOON_PATH);
    }
}

function updateChartTheme(theme) {
    if (typeof Chart === 'undefined') return;
    var styles = getComputedStyle(document.documentElement);
    Chart.defaults.color = styles.getPropertyValue('--chart-text').trim();
    Chart.defaults.borderColor = styles.getPropertyValue('--chart-border').trim();

    // 모든 차트 업데이트
    Object.keys(_charts).forEach(function(key) {
        var chart = _charts[key];
        if (!chart) return;

        var gridColor = styles.getPropertyValue('--chart-grid').trim();
        var textColor = styles.getPropertyValue('--chart-text').trim();

        // 스케일 업데이트
        if (chart.options && chart.options.scales) {
            Object.keys(chart.options.scales).forEach(function(scaleKey) {
                var scale = chart.options.scales[scaleKey];
                if (scale.grid) scale.grid.color = gridColor;
                if (scale.ticks) scale.ticks.color = textColor;
                if (scale.angleLines) scale.angleLines.color = gridColor;
                if (scale.pointLabels) scale.pointLabels.color = textColor;
            });
        }

        chart.update('none');
    });
}

// === 글로벌 매장 선택 ===
async function loadGlobalStores() {
    var select = document.getElementById('globalStoreSelect');
    if (!select) return;

    try {
        var data = await api('/api/order/stores');
        var stores = data.stores || [];
        select.innerHTML = stores.map(function(s) {
            return '<option value="' + s.store_id + '"'
                + (s.store_id === _currentStoreId ? ' selected' : '')
                + '>' + s.store_name + ' (' + s.store_id + ')'
                + '</option>';
        }).join('');

        if (stores.length > 0) {
            var found = stores.some(function(s) { return s.store_id === _currentStoreId; });
            if (!found) {
                _currentStoreId = stores[0].store_id;
                select.value = _currentStoreId;
            }
        }
    } catch (e) {
        select.innerHTML = '<option value="46513">기본점포 (46513)</option>';
    }
}

function onGlobalStoreChange() {
    var select = document.getElementById('globalStoreSelect');
    if (!select) return;

    var newStoreId = select.value;
    if (newStoreId === _currentStoreId) return;

    _currentStoreId = newStoreId;

    // lazy-load 플래그 리셋 (탭 전환 시 새 매장 데이터로 다시 로드하도록)
    window._predAccuracyLoaded = false;
    window._predBlendingLoaded = false;
    window._weeklyLoaded = false;
    window._rulesLoaded = false;

    // 현재 활성 탭 데이터 재로드
    reloadActiveTab();
}

function getActiveTabName() {
    var active = document.querySelector('.nav-tab.active');
    return active ? active.dataset.tab : 'home';
}

function reloadActiveTab() {
    var tab = getActiveTabName();

    if (tab === 'home') {
        if (typeof resetHomeCards === 'function') resetHomeCards();
        if (typeof loadHomeData === 'function') loadHomeData();
    }
    if (tab === 'prediction') {
        if (typeof loadPredSummary === 'function') loadPredSummary();
        // 현재 활성 서브뷰에 따라 상세 데이터도 재로드
        if (typeof loadAccuracyDetail === 'function') loadAccuracyDetail();
        if (typeof loadBlendingDetail === 'function') loadBlendingDetail();
    }
    if (tab === 'order') {
        if (typeof loadPartialCategories === 'function') loadPartialCategories();
        if (typeof loadExclusions === 'function') loadExclusions();
    }
    if (tab === 'report') {
        // 현재 활성 리포트 서브뷰에 따라 재로드
        var activeReportBtn = document.querySelector('.report-type-btn.active');
        var activeReport = activeReportBtn ? activeReportBtn.dataset.report : 'daily';
        if (activeReport === 'daily' && typeof loadDailyReport === 'function') {
            loadDailyReport();
        } else if (activeReport === 'weekly' && typeof loadWeeklyReport === 'function') {
            loadWeeklyReport();
        }
        // 카테고리 목록도 새 매장으로 갱신
        if (typeof loadCategoryList === 'function') loadCategoryList();
    }
    if (tab === 'rules') {
        if (typeof loadRulesData === 'function') loadRulesData();
    }
    if (tab === 'users') {
        if (typeof loadUsersTab === 'function') loadUsersTab();
    }
}

// store_id 쿼리 파라미터를 URL에 붙이는 헬퍼
function storeParam(prefix) {
    // prefix: '?' 또는 '&' (URL에 이미 ?가 있으면 &)
    return (prefix || '?') + 'store_id=' + _currentStoreId;
}

// === 탭 전환 ===
document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', e => {
        e.preventDefault();
        const target = tab.dataset.tab;
        document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab-' + target).classList.add('active');

        // 홈 탭 활성화 시 데이터 갱신
        if (target === 'home' && typeof loadHomeData === 'function') {
            loadHomeData();
        }
        // 예측분석 탭 활성화 시 데이터 로드
        if (target === 'prediction' && typeof loadPredSummary === 'function') {
            loadPredSummary();
            if (!window._predAccuracyLoaded && typeof loadAccuracyDetail === 'function') loadAccuracyDetail();
        }
        // 규칙 현황판 탭 활성화 시 데이터 로드
        if (target === 'rules' && typeof loadRulesData === 'function') {
            loadRulesData();
        }
        // 사용자 관리 탭 활성화 시 데이터 로드
        if (target === 'users' && typeof loadUsersTab === 'function') {
            loadUsersTab();
        }
    });
});

// === 리포트 서브탭 전환 ===
document.querySelectorAll('.sub-tab').forEach(tab => {
    tab.addEventListener('click', e => {
        e.preventDefault();
        const target = tab.dataset.report;
        document.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.report-view').forEach(v => v.classList.remove('active'));
        document.getElementById('report-' + target).classList.add('active');

        // lazy load
        if (target === 'weekly' && !window._weeklyLoaded) {
            loadWeeklyReport();
        }
    });
});

// === Fetch 헬퍼 ===
async function api(url, options) {
    options = options || {};

    // viewer: store_id가 URL에 없으면 자동 주입
    if (_currentUser && _currentUser.role !== 'admin') {
        if (!url.includes('store_id=')) {
            var sep = url.includes('?') ? '&' : '?';
            url += sep + 'store_id=' + _currentUser.store_id;
        }
    }

    var fetchOpts = {
        headers: { 'Content-Type': 'application/json' },
    };
    if (options.method) fetchOpts.method = options.method;
    if (options.body) fetchOpts.body = JSON.stringify(options.body);
    var res = await fetch(url, fetchOpts);

    // 401 -> 로그인 페이지로 이동
    if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('Unauthorized');
    }

    return res.json();
}

// === 숫자 포맷 ===
function fmt(n) {
    if (n == null) return '0';
    return Number(n).toLocaleString('ko-KR');
}

// === 카드 생성 헬퍼 ===
function renderCards(containerId, cards) {
    var el = document.getElementById(containerId);
    el.innerHTML = cards.map(function(c) {
        return '<div class="card ' + (c.type || '') + '">' +
            '<div class="value">' + c.value + '</div>' +
            '<div class="label">' + c.label + '</div>' +
        '</div>';
    }).join('');
    el.style.display = '';
}

// === 테이블 검색 ===
function initTableSearch(inputId, tableId) {
    var input = document.getElementById(inputId);
    if (!input) return;
    input.addEventListener('input', function() {
        var filter = this.value.toLowerCase();
        var rows = document.querySelectorAll('#' + tableId + ' tbody tr');
        rows.forEach(function(row) {
            var text = row.textContent.toLowerCase();
            row.style.display = text.includes(filter) ? '' : 'none';
        });
    });
}

// === 테이블 정렬 ===
function initTableSort(tableId) {
    var table = document.getElementById(tableId);
    if (!table) return;
    var headers = table.querySelectorAll('thead th[data-sort]');
    headers.forEach(function(th, colIdx) {
        th.addEventListener('click', function() {
            var tbody = table.querySelector('tbody');
            var rows = Array.from(tbody.querySelectorAll('tr'));
            var type = th.dataset.sort;
            var asc = !th.classList.contains('sorted-asc');

            headers.forEach(function(h) { h.classList.remove('sorted-asc', 'sorted-desc'); });
            th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');

            rows.sort(function(a, b) {
                var va = a.cells[colIdx].textContent.trim();
                var vb = b.cells[colIdx].textContent.trim();
                if (type === 'num') {
                    va = parseFloat(va.replace(/,/g, '')) || 0;
                    vb = parseFloat(vb.replace(/,/g, '')) || 0;
                    return asc ? va - vb : vb - va;
                }
                return asc ? va.localeCompare(vb, 'ko') : vb.localeCompare(va, 'ko');
            });
            rows.forEach(function(r) { tbody.appendChild(r); });
        });
    });
}

// === 신뢰도 배지 ===
function badgeHtml(confidence) {
    var cls = 'badge-info';
    var label = confidence;
    if (confidence === 'high') { cls = 'badge-high'; label = '높음'; }
    else if (confidence === 'medium') { cls = 'badge-medium'; label = '보통'; }
    else if (confidence === 'low') { cls = 'badge-low'; label = '낮음'; }
    return '<span class="badge ' + cls + '">' + label + '</span>';
}

// === 차트 색상 CSS 변수 읽기 ===
function getChartColors() {
    var s = getComputedStyle(document.documentElement);
    return {
        blue:   s.getPropertyValue('--chart-blue').trim(),
        blueA:  s.getPropertyValue('--chart-blue-a').trim(),
        red:    s.getPropertyValue('--chart-red').trim(),
        redA:   s.getPropertyValue('--chart-red-a').trim(),
        green:  s.getPropertyValue('--chart-green').trim(),
        greenA: s.getPropertyValue('--chart-green-a').trim(),
        yellow: s.getPropertyValue('--chart-yellow').trim(),
        yellowA:s.getPropertyValue('--chart-yellow-a').trim(),
        purple: s.getPropertyValue('--chart-purple').trim(),
        purpleA:s.getPropertyValue('--chart-purple-a').trim(),
        orange: s.getPropertyValue('--chart-orange').trim(),
        orangeA:s.getPropertyValue('--chart-orange-a').trim(),
        cyan:   s.getPropertyValue('--chart-cyan').trim(),
        cyanA:  s.getPropertyValue('--chart-cyan-a').trim(),
        slate:  s.getPropertyValue('--chart-slate').trim(),
        slateA: s.getPropertyValue('--chart-slate-a').trim(),
        pink:   s.getPropertyValue('--chart-pink').trim(),
        lime:   s.getPropertyValue('--chart-lime').trim(),
        grid:   s.getPropertyValue('--chart-grid').trim(),
    };
}
function getChartPalette() {
    var s = getComputedStyle(document.documentElement);
    return [
        s.getPropertyValue('--chart-blue').trim(),
        s.getPropertyValue('--chart-red').trim(),
        s.getPropertyValue('--chart-green').trim(),
        s.getPropertyValue('--chart-yellow').trim(),
        s.getPropertyValue('--chart-purple').trim(),
        s.getPropertyValue('--chart-orange').trim(),
        s.getPropertyValue('--chart-cyan').trim(),
        s.getPropertyValue('--chart-lime').trim(),
        s.getPropertyValue('--chart-pink').trim(),
        s.getPropertyValue('--chart-slate').trim(),
    ];
}

// === Toast 알림 시스템 ===
function showToast(message, type, duration) {
    type = type || 'info';
    duration = duration || 3000;
    var container = document.getElementById('toastContainer');
    if (!container) return;

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.textContent = message;
    container.appendChild(toast);

    // 트리거 리플로우 후 show
    requestAnimationFrame(function() {
        toast.classList.add('toast-show');
    });

    setTimeout(function() {
        toast.classList.remove('toast-show');
        toast.classList.add('toast-hide');
        setTimeout(function() {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
    }, duration);
}

// === 숫자 카운터 애니메이션 ===
function animateValue(el, end, duration, suffix) {
    if (!el) return;
    duration = duration || 600;
    suffix = suffix || '';
    var start = 0;
    var startTime = null;

    function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

    function step(ts) {
        if (!startTime) startTime = ts;
        var progress = Math.min((ts - startTime) / duration, 1);
        var val = Math.round(easeOutCubic(progress) * end);
        el.textContent = fmt(val) + suffix;
        if (progress < 1) requestAnimationFrame(step);
    }
    if (end === 0) { el.textContent = '0' + suffix; return; }
    requestAnimationFrame(step);
}

// === 전역 ESC 핸들러 (모든 모달) ===
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var modals = document.querySelectorAll('.modal');
        modals.forEach(function(modal) {
            if (modal.style.display !== 'none' && modal.style.display !== '') {
                modal.style.display = 'none';
            }
        });
    }
});

// === 모달 Focus Trap ===
function trapFocus(modalEl) {
    var focusable = modalEl.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (focusable.length === 0) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    first.focus();
    modalEl._focusTrap = function(e) {
        if (e.key !== 'Tab') return;
        if (e.shiftKey) {
            if (document.activeElement === first) { e.preventDefault(); last.focus(); }
        } else {
            if (document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    };
    modalEl.addEventListener('keydown', modalEl._focusTrap);
}

// === 테이블 페이지네이션 ===
function initPagination(tableId, pageSize) {
    pageSize = pageSize || 25;
    var table = document.getElementById(tableId);
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;

    var wrapper = table.closest('.report-table-wrapper') || table.parentElement;
    var existingPag = wrapper.querySelector('.table-pagination');
    if (existingPag) existingPag.remove();

    var rows = Array.from(tbody.querySelectorAll('tr'));
    if (rows.length <= pageSize) return;

    var totalPages = Math.ceil(rows.length / pageSize);
    var currentPage = 1;

    var pagEl = document.createElement('div');
    pagEl.className = 'table-pagination';
    wrapper.appendChild(pagEl);

    function showPage(page) {
        currentPage = page;
        rows.forEach(function(row, i) {
            row.style.display = (i >= (page - 1) * pageSize && i < page * pageSize) ? '' : 'none';
        });
        renderPagButtons();
    }

    function renderPagButtons() {
        var html = '<button class="pagination-btn" ' + (currentPage === 1 ? 'disabled' : '') + ' data-page="' + (currentPage - 1) + '">&lsaquo;</button>';

        // Ellipsis 패턴: 1 ... 4 5 [6] 7 8 ... 62
        var maxVisible = 7;
        var pages = [];
        if (totalPages <= maxVisible) {
            for (var i = 1; i <= totalPages; i++) pages.push(i);
        } else {
            pages.push(1);
            var start = Math.max(2, currentPage - 2);
            var end = Math.min(totalPages - 1, currentPage + 2);
            if (start > 2) pages.push('...');
            for (var i = start; i <= end; i++) pages.push(i);
            if (end < totalPages - 1) pages.push('...');
            pages.push(totalPages);
        }

        pages.forEach(function(p) {
            if (p === '...') {
                html += '<span class="pagination-ellipsis">&hellip;</span>';
            } else {
                html += '<button class="pagination-btn' + (p === currentPage ? ' active' : '') + '" data-page="' + p + '">' + p + '</button>';
            }
        });

        html += '<button class="pagination-btn" ' + (currentPage === totalPages ? 'disabled' : '') + ' data-page="' + (currentPage + 1) + '">&rsaquo;</button>';
        pagEl.innerHTML = html;
        pagEl.querySelectorAll('.pagination-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var pg = parseInt(this.dataset.page);
                if (pg >= 1 && pg <= totalPages) showPage(pg);
            });
        });
    }
    showPage(1);
}

// === Chart.js 테마 초기화 ===
(function initChartDefaults() {
    var styles = getComputedStyle(document.documentElement);
    Chart.defaults.color = styles.getPropertyValue('--chart-text').trim() || '#8e8ea8';
    Chart.defaults.borderColor = styles.getPropertyValue('--chart-border').trim() || 'rgba(255,255,255,0.06)';
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.scale = Chart.defaults.scale || {};
    Chart.defaults.font = Chart.defaults.font || {};
    Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
})();

// === 차트 인스턴스 관리 (재생성 방지) ===
var _charts = {};
function getOrCreateChart(canvasId, config) {
    if (_charts[canvasId]) {
        _charts[canvasId].destroy();
    }
    var ctx = document.getElementById(canvasId).getContext('2d');

    // 현재 테마에 맞는 차트 그리드 색상 적용
    var styles = getComputedStyle(document.documentElement);
    var gridColor = styles.getPropertyValue('--chart-grid').trim();
    if (config.options && config.options.scales) {
        Object.keys(config.options.scales).forEach(function(key) {
            var scale = config.options.scales[key];
            if (scale.grid && scale.grid.color) {
                scale.grid.color = gridColor;
            }
            if (scale.angleLines && scale.angleLines.color) {
                scale.angleLines.color = gridColor;
            }
        });
    }

    _charts[canvasId] = new Chart(ctx, config);
    return _charts[canvasId];
}

// === 세션 확인 + 사용자 UI 초기화 ===
async function checkSession() {
    try {
        var res = await fetch('/api/auth/session');
        if (!res.ok) {
            window.location.href = '/login';
            return;
        }
        var data = await res.json();
        _currentUser = data.user;

        // 네비게이션 사용자 정보 표시
        var userInfo = document.getElementById('navUserInfo');
        if (userInfo) userInfo.style.display = 'flex';
        var navUsername = document.getElementById('navUsername');
        if (navUsername) navUsername.textContent = _currentUser.full_name || _currentUser.username;
        var navBadge = document.getElementById('navStoreBadge');
        if (navBadge) navBadge.textContent = _currentUser.store_id;

        if (_currentUser.role !== 'admin') {
            // viewer: 매장 선택 숨기고, 매장 ID 고정
            var storePicker = document.getElementById('navStorePicker');
            if (storePicker) storePicker.style.display = 'none';
            _currentStoreId = _currentUser.store_id;

            // viewer: 민감 기능 숨기기
            hideAdminOnlyElements();
        } else {
            // admin: 매장 선택 표시
            var storePicker = document.getElementById('navStorePicker');
            if (storePicker) storePicker.style.display = '';
            // admin: 사용자 관리 탭 표시
            var usersTab = document.getElementById('navTabUsers');
            if (usersTab) usersTab.style.display = '';
            // admin: 가입 요청 로드
            loadSignupRequests();
        }
    } catch (e) {
        window.location.href = '/login';
    }
}

// === 가입 요청 관리 (admin only) ===
async function loadSignupRequests() {
    if (!_currentUser || _currentUser.role !== 'admin') return;
    try {
        var data = await api('/api/auth/signup-requests');
        var requests = data.requests || [];
        var panel = document.getElementById('homeSignupPanel');
        var badge = document.getElementById('homeSignupCount');
        var tbody = document.getElementById('homeSignupTableBody');
        var emptyHint = document.getElementById('homeSignupEmpty');
        if (!panel) return;

        badge.textContent = requests.length;
        if (requests.length === 0) {
            tbody.innerHTML = '';
            emptyHint.style.display = '';
            document.querySelector('#homeSignupTable').style.display = 'none';
        } else {
            emptyHint.style.display = 'none';
            document.querySelector('#homeSignupTable').style.display = '';
            tbody.innerHTML = requests.map(function(r) {
                return '<tr>' +
                    '<td>' + r.store_id + '</td>' +
                    '<td>' + r.phone + '</td>' +
                    '<td>' + (r.created_at || '').slice(0, 10) + '</td>' +
                    '<td style="white-space:nowrap;">' +
                        '<button class="btn-sm btn-approve" onclick="approveSignup(' + r.id + ')">승인</button> ' +
                        '<button class="btn-sm btn-reject" onclick="rejectSignup(' + r.id + ')">거절</button>' +
                    '</td>' +
                '</tr>';
            }).join('');
        }
        panel.style.display = '';
    } catch (e) { /* ignore */ }
}

async function approveSignup(id) {
    if (!confirm('이 가입 요청을 승인하시겠습니까?')) return;
    try {
        var data = await api('/api/auth/signup-requests/' + id + '/approve', { method: 'POST' });
        if (data.error) { alert(data.error); return; }
        alert('승인 완료: ' + (data.username || ''));
        loadSignupRequests();
    } catch (e) { alert('승인 처리 중 오류가 발생했습니다.'); }
}

async function rejectSignup(id) {
    var reason = prompt('거절 사유를 입력하세요 (선택사항):');
    if (reason === null) return; // 취소
    try {
        var body = {};
        if (reason) body.reason = reason;
        var data = await api('/api/auth/signup-requests/' + id + '/reject', { method: 'POST', body: body });
        if (data.error) { alert(data.error); return; }
        alert('거절 완료');
        loadSignupRequests();
    } catch (e) { alert('거절 처리 중 오류가 발생했습니다.'); }
}

function hideAdminOnlyElements() {
    // 발주 실행 버튼, 스케줄러 제어 등을 숨김
    document.querySelectorAll('[data-admin-only]').forEach(function(el) {
        el.style.display = 'none';
    });
}

// === DOMContentLoaded: 세션 + 테마 + 글로벌 매장 초기화 ===
document.addEventListener('DOMContentLoaded', function() {
    // 세션 확인 (첫 번째로 실행)
    checkSession();

    // 초기 아이콘 설정
    updateThemeIcon(getTheme());

    // 토글 버튼 이벤트
    var toggleBtn = document.getElementById('themeToggle');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleTheme);
    }

    // 글로벌 매장 드롭다운 로드
    loadGlobalStores();

    // 매장 변경 이벤트
    var globalSelect = document.getElementById('globalStoreSelect');
    if (globalSelect) {
        globalSelect.addEventListener('change', onGlobalStoreChange);
    }

    // 로그아웃 버튼
    var logoutBtn = document.getElementById('btnLogout');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', function() {
            fetch('/api/auth/logout', { method: 'POST' }).then(function() {
                window.location.href = '/login';
            });
        });
    }
});
