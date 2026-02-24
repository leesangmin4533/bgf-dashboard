/* BGF 발주 시스템 - 규칙 현황판 */
console.log('[RULES] rules.js loaded');

window._rulesLoaded = false;

// === 규칙 데이터 로드 (lazy load) ===
async function loadRulesData() {
    if (window._rulesLoaded) return;
    try {
        var data = await api('/api/rules/all?store_id=' + _currentStoreId);
        if (data.error) {
            console.error('[RULES] Load failed:', data.error);
            return;
        }
        renderRulesSummary(data.summary);
        renderRulesGroups(data.groups);
        window._rulesLoaded = true;
    } catch (e) {
        console.error('[RULES] Load error:', e);
    }
}

// === 요약 카드 렌더링 ===
function renderRulesSummary(summary) {
    document.getElementById('rulesTotalCount').textContent = summary.total;
    document.getElementById('rulesActiveCount').textContent = summary.active;
    document.getElementById('rulesInactiveCount').textContent = summary.inactive;
    document.getElementById('rulesGroupCount').textContent = summary.groups;
}

// === 그룹별 규칙 렌더링 ===
function renderRulesGroups(groups) {
    var container = document.getElementById('rulesGroupContainer');
    var html = '';

    var groupOrder = ['prediction', 'category', 'cost', 'eval', 'order', 'ml', 'params'];

    groupOrder.forEach(function(groupId) {
        var group = groups[groupId];
        if (!group) return;

        var isExpanded = groupId !== 'params'; // 보정 파라미터는 기본 접힘

        html += '<div class="rules-group-card">';
        html += '<div class="rules-group-header" onclick="toggleRulesGroup(\'' + groupId + '\')">';
        html += '<div class="rules-group-header-left">';
        html += '<span class="rules-group-chevron" id="chevron-' + groupId + '">' + (isExpanded ? '▼' : '▶') + '</span>';
        html += '<span class="rules-group-title">' + group.name + '</span>';
        html += '</div>';
        html += '<div class="rules-group-badges">';
        html += '<span class="rules-badge rules-badge-active">' + group.active_count + ' 활성</span>';
        if (group.total_count - group.active_count > 0) {
            html += '<span class="rules-badge rules-badge-inactive">' + (group.total_count - group.active_count) + ' 비활성</span>';
        }
        html += '</div>';
        html += '</div>';

        html += '<div class="rules-group-body" id="body-' + groupId + '" style="' + (isExpanded ? '' : 'display:none') + '">';
        html += '<table class="rules-table">';
        html += '<thead><tr>';
        html += '<th data-sort="text" style="width:60px">상태</th>';
        html += '<th data-sort="text">규칙명</th>';
        html += '<th data-sort="text">현재값</th>';
        html += '<th data-sort="text" class="rules-hide-mobile">소스 파일</th>';
        html += '<th data-sort="text" class="rules-hide-mobile">설명</th>';
        html += '</tr></thead><tbody>';

        group.rules.forEach(function(rule) {
            var statusClass = rule.enabled ? 'rules-status-on' : 'rules-status-off';
            var statusText = rule.enabled ? 'ON' : 'OFF';

            html += '<tr>';
            html += '<td><span class="' + statusClass + '">' + statusText + '</span></td>';
            html += '<td class="rules-rule-name">' + rule.name + '</td>';
            html += '<td class="rules-current-value">' + rule.current_value + '</td>';
            html += '<td class="rules-source rules-hide-mobile">' + rule.source_file + '</td>';
            html += '<td class="rules-desc rules-hide-mobile">' + rule.description + '</td>';
            html += '</tr>';
        });

        html += '</tbody></table>';
        html += '</div>';
        html += '</div>';
    });

    container.innerHTML = html;
}

// === 그룹 접기/펼치기 ===
function toggleRulesGroup(groupId) {
    var body = document.getElementById('body-' + groupId);
    var chevron = document.getElementById('chevron-' + groupId);
    if (!body) return;

    if (body.style.display === 'none') {
        body.style.display = '';
        if (chevron) chevron.textContent = '▼';
    } else {
        body.style.display = 'none';
        if (chevron) chevron.textContent = '▶';
    }
}

// === 상품별 규칙 추적 ===
document.addEventListener('DOMContentLoaded', function() {
    var traceBtn = document.getElementById('btnRulesTrace');
    var traceInput = document.getElementById('rulesTraceInput');

    if (traceBtn) {
        traceBtn.addEventListener('click', function() {
            runRulesTrace();
        });
    }

    if (traceInput) {
        traceInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                runRulesTrace();
            }
        });
    }
});

async function runRulesTrace() {
    var itemCd = document.getElementById('rulesTraceInput').value.trim();
    if (!itemCd) {
        alert('상품코드를 입력해주세요.');
        return;
    }

    var resultDiv = document.getElementById('rulesTraceResult');
    resultDiv.style.display = '';
    resultDiv.innerHTML = '<div class="rules-trace-loading">조회 중...</div>';

    console.log('[RULES] Trace request: item_cd=' + itemCd);

    try {
        var resp = await fetch('/api/rules/trace?item_cd=' + encodeURIComponent(itemCd) + '&store_id=' + _currentStoreId);
        console.log('[RULES] Trace response status:', resp.status);

        if (!resp.ok) {
            var errText = await resp.text();
            console.error('[RULES] Trace error response:', errText);
            resultDiv.innerHTML = '<div class="rules-trace-error">서버 오류 (' + resp.status + '): 서버를 재시작해주세요.</div>';
            return;
        }

        var data = await resp.json();
        if (data.error) {
            resultDiv.innerHTML = '<div class="rules-trace-error">' + data.error + '</div>';
            return;
        }
        renderRulesTrace(data);
    } catch (e) {
        console.error('[RULES] Trace exception:', e);
        resultDiv.innerHTML = '<div class="rules-trace-error">조회 실패: ' + e.message + '</div>';
    }
}

function renderRulesTrace(data) {
    var resultDiv = document.getElementById('rulesTraceResult');
    var html = '';

    // 상품 정보 헤더
    html += '<div class="rules-trace-product">';
    html += '<div class="rules-trace-product-info">';
    html += '<span class="rules-trace-product-name">' + (data.item_nm || '상품명 없음') + '</span>';
    html += '<span class="rules-trace-product-code">' + data.item_cd + '</span>';
    html += '<span class="rules-trace-product-cat">' + (data.mid_nm || data.mid_cd || '-') + ' (' + (data.mid_cd || '-') + ')</span>';
    html += '</div>';
    html += '<div class="rules-trace-product-stats">';
    html += '<span class="rules-badge rules-badge-active">' + data.applied_count + '개 적용</span>';
    html += '<span class="rules-badge rules-badge-neutral">' + (data.total_count - data.applied_count) + '개 미적용</span>';
    html += '</div>';
    html += '</div>';

    // 규칙 목록
    html += '<table class="rules-table rules-trace-table">';
    html += '<thead><tr>';
    html += '<th style="width:50px">적용</th>';
    html += '<th>그룹</th>';
    html += '<th>규칙명</th>';
    html += '<th class="rules-hide-mobile">사유</th>';
    html += '<th class="rules-hide-mobile">현재값</th>';
    html += '</tr></thead><tbody>';

    data.rules.forEach(function(rule) {
        var appliedClass = rule.applied ? 'rules-trace-applied' : 'rules-trace-skipped';
        var appliedIcon = rule.applied ? '✅' : '➖';

        html += '<tr class="' + appliedClass + '">';
        html += '<td>' + appliedIcon + '</td>';
        html += '<td class="rules-trace-group-name">' + rule.group_name + '</td>';
        html += '<td>' + rule.name + '</td>';
        html += '<td class="rules-hide-mobile">' + rule.reason + '</td>';
        html += '<td class="rules-hide-mobile rules-current-value">' + rule.current_value + '</td>';
        html += '</tr>';
    });

    html += '</tbody></table>';

    resultDiv.innerHTML = html;
}
