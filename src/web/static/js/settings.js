/* BGF 발주 시스템 - 설정 관리 */
console.log('[SET] settings.js loaded');

var _settingsLoaded = false;

async function loadSettingsData() {
    if (_settingsLoaded) return;
    console.log('[SET] loadSettingsData called');

    try {
        var results = await Promise.all([
            api('/api/settings/eval-params'),
            api('/api/settings/feature-flags'),
            api('/api/settings/audit-log?hours=168')
        ]);
        renderEvalParams(results[0]);
        renderFeatureFlags(results[1]);
        renderAuditLog(results[2]);
        _settingsLoaded = true;
    } catch (e) {
        console.error('[SET] load failed:', e);
    }
}

// === 예측 파라미터 렌더링 ===
function renderEvalParams(data) {
    var container = document.getElementById('evalParamsContainer');
    if (!container) return;

    var params = data.params || {};
    var groups = data.groups || {};
    var keys = Object.keys(params);
    if (keys.length === 0) {
        container.innerHTML = '<div class="hint">파라미터 없음</div>';
        return;
    }

    // 그룹별 분류
    var grouped = {};
    var ungrouped = [];

    for (var gName in groups) {
        grouped[gName] = [];
        var gKeys = groups[gName];
        for (var i = 0; i < gKeys.length; i++) {
            if (params[gKeys[i]]) grouped[gName].push(gKeys[i]);
        }
    }

    // 그룹에 속하지 않은 키
    var allGrouped = [];
    for (var g in groups) {
        allGrouped = allGrouped.concat(groups[g]);
    }
    for (var j = 0; j < keys.length; j++) {
        if (allGrouped.indexOf(keys[j]) === -1) ungrouped.push(keys[j]);
    }

    var groupLabels = {
        prediction: '예측 기본',
        calibration: '보정',
    };

    var html = '';

    // 그룹별 렌더
    for (var groupName in grouped) {
        var gParams = grouped[groupName];
        if (gParams.length === 0) continue;
        var label = groupLabels[groupName] || groupName;
        html += '<div class="param-group">';
        html += '<h4 style="margin:12px 0 8px; color:var(--text-secondary); font-size:0.85rem;">' + label + '</h4>';
        html += '<div class="param-grid">';
        for (var k = 0; k < gParams.length; k++) {
            html += _paramRow(gParams[k], params[gParams[k]]);
        }
        html += '</div></div>';
    }

    // 미분류
    if (ungrouped.length > 0) {
        html += '<div class="param-group">';
        html += '<h4 style="margin:12px 0 8px; color:var(--text-secondary); font-size:0.85rem;">기타</h4>';
        html += '<div class="param-grid">';
        for (var m = 0; m < ungrouped.length; m++) {
            html += _paramRow(ungrouped[m], params[ungrouped[m]]);
        }
        html += '</div></div>';
    }

    container.innerHTML = html;
}

function _paramRow(key, entry) {
    var desc = entry.description || key;
    var val = entry.value;
    var def = entry['default'];
    var min = entry.min;
    var max = entry.max;
    var changed = val !== def;
    var style = changed ? 'border-color:var(--warning)' : '';

    return '<div class="param-item" style="display:grid; grid-template-columns:1fr auto auto; gap:8px; align-items:center; padding:6px 0; border-bottom:1px solid var(--border-light);">'
        + '<div>'
        + '<div style="font-size:0.85rem; color:var(--text-primary);" title="' + key + '">' + desc + '</div>'
        + '<div style="font-size:0.7rem; color:var(--text-muted);">' + key + ' (기본: ' + def + ', 범위: ' + min + '~' + max + ')</div>'
        + '</div>'
        + '<input type="number" id="param_' + key + '" value="' + val + '" step="0.01" min="' + min + '" max="' + max + '" style="width:90px; padding:4px 8px; background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-sm); color:var(--text-primary); font-size:0.85rem; ' + style + '">'
        + '<button class="btn-sm btn-approve" onclick="saveEvalParam(\'' + key + '\')" style="font-size:0.75rem; padding:4px 10px;">저장</button>'
        + '</div>';
}

async function saveEvalParam(key) {
    var input = document.getElementById('param_' + key);
    if (!input) return;
    var val = parseFloat(input.value);
    if (isNaN(val)) { alert('숫자만 입력 가능합니다'); return; }

    try {
        var resp = await api('/api/settings/eval-params', {
            method: 'POST',
            body: { key: key, value: val }
        });
        if (resp.error) {
            alert(resp.error);
        } else {
            input.style.borderColor = 'var(--success)';
            setTimeout(function() { input.style.borderColor = ''; }, 2000);
            _settingsLoaded = false;
            loadSettingsData();
        }
    } catch (e) {
        alert('저장 실패: ' + e.message);
    }
}

async function resetAllEvalParams() {
    if (!confirm('모든 파라미터를 기본값으로 복원하시겠습니까?')) return;
    try {
        var resp = await api('/api/settings/eval-params/reset', {
            method: 'POST',
            body: { key: '__all__' }
        });
        if (resp.error) {
            alert(resp.error);
        } else {
            alert(resp.reset_count + '개 파라미터 리셋 완료');
            _settingsLoaded = false;
            loadSettingsData();
        }
    } catch (e) {
        alert('리셋 실패: ' + e.message);
    }
}

// === 기능 토글 렌더링 ===
function renderFeatureFlags(data) {
    var container = document.getElementById('featureFlagsContainer');
    if (!container) return;

    var flags = data.flags || [];
    if (flags.length === 0) {
        container.innerHTML = '<div class="hint">기능 토글 없음</div>';
        return;
    }

    var html = '';
    for (var i = 0; i < flags.length; i++) {
        var f = flags[i];
        var checked = f.value ? 'checked' : '';
        html += '<div style="display:flex; align-items:center; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border-light);">'
            + '<div>'
            + '<div style="font-size:0.85rem; color:var(--text-primary);">' + f.description + '</div>'
            + '<div style="font-size:0.7rem; color:var(--text-muted);">' + f.key + '</div>'
            + '</div>'
            + '<label class="toggle-switch" style="position:relative; display:inline-block; width:44px; height:24px;">'
            + '<input type="checkbox" ' + checked + ' onchange="toggleFlag(\'' + f.key + '\', this.checked)" style="opacity:0; width:0; height:0;">'
            + '<span style="position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background:' + (f.value ? 'var(--success)' : 'var(--border)') + '; border-radius:24px; transition:0.3s;"></span>'
            + '<span style="position:absolute; content:\'\'; height:18px; width:18px; left:' + (f.value ? '22px' : '3px') + '; bottom:3px; background:white; border-radius:50%; transition:0.3s;"></span>'
            + '</label>'
            + '</div>';
    }
    container.innerHTML = html;
}

async function toggleFlag(key, value) {
    try {
        var resp = await api('/api/settings/feature-flags', {
            method: 'POST',
            body: { key: key, value: value }
        });
        if (resp.error) {
            alert(resp.error);
            _settingsLoaded = false;
            loadSettingsData();
        } else {
            _settingsLoaded = false;
            loadSettingsData();
        }
    } catch (e) {
        alert('토글 실패: ' + e.message);
    }
}

// === 변경 이력 렌더링 ===
function renderAuditLog(data) {
    var tbody = document.getElementById('auditLogBody');
    var empty = document.getElementById('auditLogEmpty');
    var table = document.getElementById('auditLogTable');
    if (!tbody) return;

    var logs = data.logs || [];
    if (logs.length === 0) {
        if (table) table.style.display = 'none';
        if (empty) empty.style.display = '';
        return;
    }

    if (table) table.style.display = '';
    if (empty) empty.style.display = 'none';

    var html = '';
    for (var i = 0; i < logs.length; i++) {
        var log = logs[i];
        var time = log.changed_at ? log.changed_at.substring(5, 16).replace('T', ' ') : '';
        html += '<tr>'
            + '<td>' + (log.setting_key || '') + '</td>'
            + '<td style="font-size:0.8rem;">' + (log.old_value || '') + '</td>'
            + '<td style="font-size:0.8rem; color:var(--primary);">' + (log.new_value || '') + '</td>'
            + '<td>' + (log.changed_by || '') + '</td>'
            + '<td style="font-size:0.8rem;">' + time + '</td>'
            + '<td><span class="status-badge-sm">' + (log.category || '') + '</span></td>'
            + '</tr>';
    }
    tbody.innerHTML = html;
}
