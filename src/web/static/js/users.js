/* BGF 발주 시스템 - 설정 탭 (admin 전용) */

var _usersLoaded = false;

// === 설정 탭 전체 로드 ===
async function loadUsersTab() {
    try {
        await Promise.all([loadUsersList(), loadSettingsSignupRequests(), loadSettingsScheduler()]);
        _usersLoaded = true;
    } catch (e) {
        console.error('설정 탭 로드 실패:', e);
    }
}

// === 등록된 사용자 목록 ===
async function loadUsersList() {
    try {
        var data = await api('/api/auth/users');
        var users = data.users || [];
        var tbody = document.getElementById('usersTableBody');
        if (!tbody) return;

        tbody.innerHTML = users.map(function(u) {
            var isMe = _currentUser && u.id === _currentUser.id;
            var activeLabel = u.is_active ? '<span style="color:var(--success)">Y</span>' : '<span style="color:var(--danger)">N</span>';
            var lastLogin = u.last_login_at ? u.last_login_at.slice(0, 16).replace('T', ' ') : '-';

            var actions = '';
            if (!isMe) {
                actions =
                    '<button class="btn-sm btn-approve" onclick="resetUserPassword(' + u.id + ')" title="비밀번호 변경">PW</button> ' +
                    '<button class="btn-sm" onclick="toggleUserRole(' + u.id + ', \'' + u.role + '\')" title="역할 전환" style="background:var(--primary);color:#fff;">' +
                        (u.role === 'admin' ? 'A' : 'V') +
                    '</button> ' +
                    '<button class="btn-sm" onclick="toggleUserActive(' + u.id + ', ' + (u.is_active ? 'false' : 'true') + ')" title="' + (u.is_active ? '비활성화' : '활성화') + '" style="background:var(--chart-yellow);color:#000;">' +
                        (u.is_active ? 'OFF' : 'ON') +
                    '</button> ' +
                    '<button class="btn-sm btn-reject" onclick="deleteUser(' + u.id + ', \'' + u.username + '\')" title="삭제">X</button>';
            } else {
                actions = '<span style="color:var(--text-secondary);font-size:0.8rem;">(me)</span>';
            }

            return '<tr>' +
                '<td>' + u.id + '</td>' +
                '<td><strong>' + u.username + '</strong></td>' +
                '<td>' + u.store_id + '</td>' +
                '<td><span class="badge ' + (u.role === 'admin' ? 'badge-high' : 'badge-medium') + '">' + u.role + '</span></td>' +
                '<td>' + (u.full_name || '-') + '</td>' +
                '<td>' + (u.phone || '-') + '</td>' +
                '<td>' + activeLabel + '</td>' +
                '<td style="font-size:0.8rem;color:var(--text-secondary);">' + lastLogin + '</td>' +
                '<td style="white-space:nowrap;">' + actions + '</td>' +
            '</tr>';
        }).join('');

        initTableSearch('usersSearch', 'usersTable');
        initTableSort('usersTable');
    } catch (e) {
        console.error('사용자 목록 로드 실패:', e);
    }
}

// === 가입 요청 목록 (설정 탭 내) ===
async function loadSettingsSignupRequests() {
    if (!_currentUser || _currentUser.role !== 'admin') return;
    try {
        var data = await api('/api/auth/signup-requests');
        var requests = data.requests || [];
        var badge = document.getElementById('settingsSignupCount');
        var tbody = document.getElementById('settingsSignupTableBody');
        var table = document.getElementById('settingsSignupTable');
        var empty = document.getElementById('settingsSignupEmpty');
        if (!badge || !tbody) return;

        badge.textContent = requests.length;
        if (requests.length === 0) {
            tbody.innerHTML = '';
            if (table) table.style.display = 'none';
            if (empty) empty.style.display = '';
        } else {
            if (table) table.style.display = '';
            if (empty) empty.style.display = 'none';
            tbody.innerHTML = requests.map(function(r) {
                return '<tr>' +
                    '<td><strong>' + r.store_id + '</strong></td>' +
                    '<td>' + r.phone + '</td>' +
                    '<td>' + (r.created_at || '').slice(0, 10) + '</td>' +
                    '<td style="white-space:nowrap;">' +
                        '<button class="btn-sm btn-approve" onclick="approveSignupFromSettings(' + r.id + ')">승인</button> ' +
                        '<button class="btn-sm btn-reject" onclick="rejectSignupFromSettings(' + r.id + ')">거절</button>' +
                    '</td>' +
                '</tr>';
            }).join('');
        }
    } catch (e) { /* ignore */ }
}

async function approveSignupFromSettings(id) {
    if (!confirm('이 가입 요청을 승인하시겠습니까?')) return;
    try {
        var data = await api('/api/auth/signup-requests/' + id + '/approve', { method: 'POST' });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('승인 완료', 'success');
        loadUsersTab();
        if (typeof loadSignupRequests === 'function') loadSignupRequests();
    } catch (e) { showToast('승인 처리 중 오류', 'danger'); }
}

async function rejectSignupFromSettings(id) {
    var reason = prompt('거절 사유를 입력하세요 (선택사항):');
    if (reason === null) return;
    try {
        var body = {};
        if (reason) body.reason = reason;
        var data = await api('/api/auth/signup-requests/' + id + '/reject', { method: 'POST', body: body });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('거절 완료', 'success');
        loadSettingsSignupRequests();
        if (typeof loadSignupRequests === 'function') loadSignupRequests();
    } catch (e) { showToast('거절 처리 중 오류', 'danger'); }
}

// === 설정 탭: 스케줄러 관리 ===
async function loadSettingsScheduler() {
    var badge = document.getElementById('settingsSchedulerBadge');
    var pidText = document.getElementById('settingsSchedulerPid');
    var btnStart = document.getElementById('btnSettingsSchedulerStart');
    var btnStop = document.getElementById('btnSettingsSchedulerStop');
    var timeline = document.getElementById('settingsTimeline');
    if (!badge || !timeline) return;

    try {
        var data = await api('/api/home/scheduler/jobs');
        if (data.running) {
            badge.textContent = '동작중';
            badge.className = 'status-badge running';
            if (pidText) pidText.textContent = 'PID: ' + data.pid;
            if (btnStart) btnStart.style.display = 'none';
            if (btnStop) btnStop.style.display = 'flex';
        } else {
            badge.textContent = '정지됨';
            badge.className = 'status-badge stopped';
            if (pidText) pidText.textContent = '스케줄러가 정지되어 있습니다';
            if (btnStart) btnStart.style.display = 'flex';
            if (btnStop) btnStop.style.display = 'none';
        }
        if (typeof renderScheduleTimeline === 'function') {
            // settingsTimeline에 렌더 (home.js의 함수 재사용, 타겟 ID 다름)
            var jobs = data.jobs || [];
            renderSettingsTimeline(jobs, timeline);
        }
    } catch (e) {
        timeline.innerHTML = '<div class="hint">스케줄 로드 실패</div>';
    }
}

function renderSettingsTimeline(jobs, container) {
    if (!jobs || jobs.length === 0) {
        container.innerHTML = '<div class="hint">등록된 스케줄이 없습니다</div>';
        return;
    }
    var now = new Date();
    var nowHM = now.getHours() * 60 + now.getMinutes();
    var isMonday = now.getDay() === 1;
    var todayJobs = jobs.filter(function(j) {
        if (j.freq === '매일') return true;
        if (j.freq === '매주 월' && isMonday) return true;
        return false;
    });
    todayJobs.sort(function(a, b) {
        var aP = a.time.split(':'), bP = b.time.split(':');
        return (parseInt(aP[0]) * 60 + parseInt(aP[1])) - (parseInt(bP[0]) * 60 + parseInt(bP[1]));
    });
    var html = '';
    todayJobs.forEach(function(job) {
        var parts = job.time.split(':');
        var jobTime = parseInt(parts[0]) * 60 + parseInt(parts[1]);
        var isDone = jobTime < nowHM;
        html += '<div class="schedule-item ' + (isDone ? 'done' : 'upcoming') + '">'
            + '<div class="schedule-time">' + job.time + '</div>'
            + '<div class="schedule-info">'
            + '<div class="schedule-name">' + job.name + '</div>'
            + '<div class="schedule-desc">' + job.desc + '</div>'
            + '</div>'
            + '<div class="schedule-freq">' + job.freq + '</div>'
            + '</div>';
    });
    container.innerHTML = html;
}

// === 사용자 추가 폼 ===
function showCreateUserForm() {
    var form = document.getElementById('createUserForm');
    if (form) {
        form.style.display = '';
        document.getElementById('newUsername').focus();
    }
}

function hideCreateUserForm() {
    var form = document.getElementById('createUserForm');
    if (form) {
        form.style.display = 'none';
        document.getElementById('newUsername').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('newStoreId').value = '';
        document.getElementById('newRole').value = 'viewer';
    }
}

async function createUser() {
    var username = document.getElementById('newUsername').value.trim();
    var password = document.getElementById('newPassword').value;
    var storeId = document.getElementById('newStoreId').value.trim();
    var role = document.getElementById('newRole').value;

    if (!username || !password || !storeId) {
        showToast('모든 필드를 입력하세요.', 'warning');
        return;
    }

    try {
        var data = await api('/api/auth/users', {
            method: 'POST',
            body: { username: username, password: password, store_id: storeId, role: role }
        });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('사용자 "' + username + '" 생성 완료', 'success');
        hideCreateUserForm();
        loadUsersList();
    } catch (e) { showToast('사용자 생성 실패', 'danger'); }
}

// === 사용자 관리 액션 ===
async function resetUserPassword(userId) {
    var newPw = prompt('새 비밀번호를 입력하세요 (4자 이상):');
    if (!newPw) return;
    if (newPw.length < 4) { showToast('비밀번호는 4자 이상이어야 합니다.', 'warning'); return; }

    try {
        var data = await api('/api/auth/users/' + userId, {
            method: 'PUT',
            body: { password: newPw }
        });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('비밀번호 변경 완료', 'success');
    } catch (e) { showToast('비밀번호 변경 실패', 'danger'); }
}

async function toggleUserRole(userId, currentRole) {
    var newRole = currentRole === 'admin' ? 'viewer' : 'admin';
    if (!confirm('역할을 "' + newRole + '"(으)로 변경하시겠습니까?')) return;

    try {
        var data = await api('/api/auth/users/' + userId, {
            method: 'PUT',
            body: { role: newRole }
        });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('역할 변경: ' + newRole, 'success');
        loadUsersList();
    } catch (e) { showToast('역할 변경 실패', 'danger'); }
}

async function toggleUserActive(userId, active) {
    var action = active ? '활성화' : '비활성화';
    if (!confirm('이 사용자를 ' + action + '하시겠습니까?')) return;

    try {
        var data = await api('/api/auth/users/' + userId, {
            method: 'PUT',
            body: { is_active: active }
        });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast(action + ' 완료', 'success');
        loadUsersList();
    } catch (e) { showToast(action + ' 실패', 'danger'); }
}

async function deleteUser(userId, username) {
    if (!confirm('"' + username + '" 사용자를 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.')) return;

    try {
        var data = await api('/api/auth/users/' + userId, { method: 'DELETE' });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('"' + username + '" 삭제 완료', 'success');
        loadUsersList();
    } catch (e) { showToast('삭제 실패', 'danger'); }
}
