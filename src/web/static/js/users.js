/* BGF 발주 시스템 - 사용자 관리 탭 (admin 전용) */

var _usersLoaded = false;

// === 사용자 목록 로드 ===
async function loadUsersTab() {
    try {
        await Promise.all([loadUsersList(), loadUsersSignupRequests()]);
        _usersLoaded = true;
    } catch (e) {
        console.error('사용자 탭 로드 실패:', e);
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

// === 가입 요청 목록 (사용자 관리 탭 내) ===
async function loadUsersSignupRequests() {
    if (!_currentUser || _currentUser.role !== 'admin') return;
    try {
        var data = await api('/api/auth/signup-requests');
        var requests = data.requests || [];
        var badge = document.getElementById('usersSignupCount');
        var tbody = document.getElementById('usersSignupTableBody');
        var table = document.getElementById('usersSignupTable');
        var empty = document.getElementById('usersSignupEmpty');
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
                        '<button class="btn-sm btn-approve" onclick="approveSignupFromUsers(' + r.id + ')">승인</button> ' +
                        '<button class="btn-sm btn-reject" onclick="rejectSignupFromUsers(' + r.id + ')">거절</button>' +
                    '</td>' +
                '</tr>';
            }).join('');
        }
    } catch (e) { /* ignore */ }
}

async function approveSignupFromUsers(id) {
    if (!confirm('이 가입 요청을 승인하시겠습니까?')) return;
    try {
        var data = await api('/api/auth/signup-requests/' + id + '/approve', { method: 'POST' });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('승인 완료', 'success');
        loadUsersTab(); // 전체 새로고침 (사용자 목록 + 가입 요청)
        if (typeof loadSignupRequests === 'function') loadSignupRequests(); // 홈탭도 갱신
    } catch (e) { showToast('승인 처리 중 오류', 'danger'); }
}

async function rejectSignupFromUsers(id) {
    var reason = prompt('거절 사유를 입력하세요 (선택사항):');
    if (reason === null) return;
    try {
        var body = {};
        if (reason) body.reason = reason;
        var data = await api('/api/auth/signup-requests/' + id + '/reject', { method: 'POST', body: body });
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('거절 완료', 'success');
        loadUsersSignupRequests();
        if (typeof loadSignupRequests === 'function') loadSignupRequests();
    } catch (e) { showToast('거절 처리 중 오류', 'danger'); }
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
