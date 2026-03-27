/* ============================================
   Beverage Dashboard — 음료 발주 유지/정지 판단
   DessertDashboard 구조 기반, 음료 전용 필드 반영
   ============================================ */

var BeverageDashboard = {
    _data: [],
    _summary: null,
    _selected: new Set(),
    _filter: { decision: 'all', category: 'all', search: '', hideProcessed: true },
    _searchTimer: null,
    _loaded: false,

    // 카테고리 라벨/색상
    _catLabels: {
        'A': 'A 유제품',
        'B': 'B 냉장중기',
        'C': 'C 상온장기',
        'D': 'D 생수/얼음'
    },
    _catColors: {
        'A': '#ef4444',
        'B': '#f59e0b',
        'C': '#3b82f6',
        'D': '#6b7280'
    },

    async init() {
        if (this._loaded) {
            await this.loadData();
            this.renderAll();
            return;
        }
        this._loaded = true;
        await this.loadData();
        this.render();
    },

    async loadData() {
        try {
            var sp = storeParam();
            var sep = sp ? '&' : '?';
            var results = await Promise.all([
                api('/api/beverage-decision/latest' + sp),
                api('/api/beverage-decision/summary' + sp + sep + 'history=8w')
            ]);
            this._data = (results[0] && results[0].data) || [];
            this._summary = (results[1] && results[1].data) || {};
        } catch (e) {
            this._data = [];
            this._summary = {};
        }
    },

    render() {
        var container = document.getElementById('beverageContent');
        if (!container) return;

        container.innerHTML =
            '<div id="beverageAlert"></div>' +
            '<div id="beverageSummary" class="dessert-summary-grid"></div>' +
            '<div id="beverageCharts" class="dessert-charts-row"></div>' +
            '<div id="beverageFilters" class="dessert-filter-bar"></div>' +
            '<div id="beverageTableWrap" class="dessert-table-wrapper"></div>';

        this.renderAlertBanner();
        this.renderSummaryCards();
        this.renderCharts();
        this.renderFilters();
        this.renderTable();
        this._ensureBatchBar();
        this._ensureModal();
    },

    renderAll() {
        this.renderAlertBanner();
        this.renderSummaryCards();
        this.renderTable();
        this.updateBatchBar();
    },

    // === 1. 알림 배너 ===
    renderAlertBanner() {
        var el = document.getElementById('beverageAlert');
        if (!el) return;
        var pending = this._getPendingCount();
        if (pending === 0) { el.innerHTML = ''; return; }
        el.innerHTML =
            '<div class="dessert-alert">' +
                '<span class="dessert-alert-icon">&#9888;</span>' +
                '<span class="dessert-alert-text">확인 대기 중인 음료 정지 권고 상품이 있습니다</span>' +
                '<span class="dessert-alert-count">' + pending + '건</span>' +
            '</div>';
    },

    // === 2. 요약 카드 ===
    renderSummaryCards() {
        var el = document.getElementById('beverageSummary');
        if (!el) return;

        var hideProc = this._filter.hideProcessed;
        var baseData = hideProc
            ? this._data.filter(function(d) { return !d.operator_action; })
            : this._data;

        var keepCount = baseData.filter(function(d) { return d.decision === 'KEEP'; }).length;
        var watchCount = baseData.filter(function(d) { return d.decision === 'WATCH'; }).length;
        var stopCount = baseData.filter(function(d) { return d.decision === 'STOP_RECOMMEND'; }).length;
        var skipCount = baseData.filter(function(d) { return !d.decision || d.decision === 'SKIP'; }).length;
        var totalDecided = keepCount + watchCount + stopCount;
        var pendingCount = this._getPendingCount();

        var processedCount = this._data.filter(function(d) { return !!d.operator_action; }).length;
        var allStopCount = this._data.filter(function(d) { return d.decision === 'STOP_RECOMMEND'; }).length;
        var processedStopCount = allStopCount - pendingCount;

        var totalSub = hideProc
            ? '전체 ' + this._data.length + '개 중 (' + processedCount + '개 처리됨)'
            : '전체 ' + (totalDecided + skipCount) + '개 중 (' + skipCount + '개 SKIP)';
        var stopSub = hideProc
            ? (processedStopCount > 0 ? '처리됨 ' + processedStopCount + '건' : '')
            : (pendingCount ? '미확인 ' + pendingCount + '건' : '전부 처리됨');

        var self = this;
        var cards = [
            { key: 'all', cls: 'dessert-card-total', value: totalDecided, label: '전체 판단', sub: totalSub },
            { key: 'KEEP', cls: 'dessert-card-keep', value: keepCount, label: 'KEEP',
              sub: totalDecided ? Math.round(keepCount / totalDecided * 100) + '%' : '0%' },
            { key: 'WATCH', cls: 'dessert-card-watch', value: watchCount, label: 'WATCH', sub: '' },
            { key: 'STOP_RECOMMEND', cls: 'dessert-card-stop', value: stopCount, label: 'STOP', sub: stopSub },
            { key: 'SKIP', cls: 'dessert-card-skip', value: skipCount, label: 'SKIP', sub: '판단 대상 제외' }
        ];

        el.innerHTML = cards.map(function(c) {
            var active = self._filter.decision === c.key ? ' active' : '';
            return '<div class="dessert-summary-card ' + c.cls + active +
                '" data-filter-decision="' + c.key + '">' +
                '<div class="dessert-card-value">' + fmt(c.value) + '</div>' +
                '<div class="dessert-card-label">' + c.label + '</div>' +
                (c.sub ? '<div class="dessert-card-sub">' + c.sub + '</div>' : '') +
            '</div>';
        }).join('');

        el.querySelectorAll('.dessert-summary-card').forEach(function(card) {
            card.addEventListener('click', function() {
                var key = this.dataset.filterDecision;
                self._filter.decision = (self._filter.decision === key) ? 'all' : key;
                self.renderSummaryCards();
                self.renderTable();
            });
        });
    },

    // === 3. 차트 ===
    renderCharts() {
        var el = document.getElementById('beverageCharts');
        if (!el) return;

        el.innerHTML =
            '<div class="dessert-chart-card">' +
                '<div class="dessert-chart-title">카테고리별 판단 분포</div>' +
                '<canvas id="beverageCatChart" class="dessert-chart-canvas"></canvas>' +
            '</div>' +
            '<div class="dessert-chart-card">' +
                '<div class="dessert-chart-title">주간 판단 추이</div>' +
                '<canvas id="beverageTrendChart" class="dessert-chart-canvas"></canvas>' +
            '</div>';

        this._renderCategoryChart();
        this._renderTrendChart();
    },

    _renderCategoryChart() {
        var byCat = (this._summary && this._summary.by_category) || {};
        var categories = ['A', 'B', 'C', 'D'];
        var self = this;
        var labels = categories.map(function(c) { return self._catLabels[c] || c; });

        var keepData = [], watchData = [], stopData = [];
        categories.forEach(function(cat) {
            var c = byCat[cat] || {};
            keepData.push(c.KEEP || 0);
            watchData.push(c.WATCH || 0);
            stopData.push(c.STOP_RECOMMEND || 0);
        });

        var colors = getChartColors();
        getOrCreateChart('beverageCatChart', {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: 'KEEP', data: keepData, backgroundColor: colors.greenA, borderColor: colors.green, borderWidth: 1 },
                    { label: 'WATCH', data: watchData, backgroundColor: colors.yellowA, borderColor: colors.yellow, borderWidth: 1 },
                    { label: 'STOP', data: stopData, backgroundColor: colors.redA, borderColor: colors.red, borderWidth: 1 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'top', labels: { boxWidth: 10, padding: 12, font: { size: 11 } } } },
                scales: {
                    x: { stacked: true, grid: { display: false } },
                    y: { stacked: true, beginAtZero: true, grid: { color: colors.grid }, ticks: { stepSize: 5 } }
                }
            }
        });
    },

    _renderTrendChart() {
        var trend = (this._summary && this._summary.weekly_trend) || [];
        if (trend.length === 0) {
            trend = [{ week: '-', KEEP: 0, WATCH: 0, STOP_RECOMMEND: 0 }];
        }

        var labels = trend.map(function(t) { return t.week; });
        var keepData = trend.map(function(t) { return t.KEEP || 0; });
        var watchData = trend.map(function(t) { return t.WATCH || 0; });
        var stopData = trend.map(function(t) { return t.STOP_RECOMMEND || 0; });

        var colors = getChartColors();
        getOrCreateChart('beverageTrendChart', {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    { label: 'KEEP', data: keepData, borderColor: colors.green, backgroundColor: colors.greenA, fill: true, tension: 0.3, pointRadius: 3 },
                    { label: 'WATCH', data: watchData, borderColor: colors.yellow, backgroundColor: colors.yellowA, fill: true, tension: 0.3, pointRadius: 3 },
                    { label: 'STOP', data: stopData, borderColor: colors.red, backgroundColor: colors.redA, fill: true, tension: 0.3, pointRadius: 3 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'top', labels: { boxWidth: 10, padding: 12, font: { size: 11 } } } },
                scales: {
                    x: { grid: { display: false } },
                    y: { beginAtZero: true, grid: { color: colors.grid }, ticks: { stepSize: 5 } }
                }
            }
        });
    },

    // === 4. 필터 바 ===
    renderFilters() {
        var el = document.getElementById('beverageFilters');
        if (!el) return;

        var self = this;
        var catBtns = [
            { key: 'all', label: '전체' },
            { key: 'A', label: 'A 유제품' },
            { key: 'B', label: 'B 냉장중기' },
            { key: 'C', label: 'C 상온장기' },
            { key: 'D', label: 'D 생수/얼음' }
        ];

        var processedCount = this._data.filter(function(d) { return !!d.operator_action; }).length;
        var toggleActive = !self._filter.hideProcessed ? ' active' : '';

        el.innerHTML = catBtns.map(function(b) {
            var active = self._filter.category === b.key ? ' active' : '';
            return '<button class="dessert-filter-btn' + active + '" data-cat="' + b.key + '">' + b.label + '</button>';
        }).join('') +
        '<button class="dessert-filter-btn dessert-filter-toggle' + toggleActive + '" id="beverageToggleProcessed">' +
            (self._filter.hideProcessed ? '&#128065; 처리됨 포함 (' + processedCount + ')' : '&#128064; 미확인만') +
        '</button>' +
        '<input type="text" class="dessert-search-input" id="beverageSearch" placeholder="상품명 검색..." value="' +
            (self._filter.search || '') + '">';

        el.querySelectorAll('.dessert-filter-btn[data-cat]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                self._filter.category = this.dataset.cat;
                self.renderFilters();
                self.renderTable();
            });
        });

        var toggleBtn = document.getElementById('beverageToggleProcessed');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function() {
                self._filter.hideProcessed = !self._filter.hideProcessed;
                self.renderSummaryCards();
                self.renderFilters();
                self.renderTable();
            });
        }

        var searchInput = document.getElementById('beverageSearch');
        if (searchInput) {
            searchInput.addEventListener('input', function() {
                var val = this.value;
                clearTimeout(self._searchTimer);
                self._searchTimer = setTimeout(function() {
                    self._filter.search = val;
                    self.renderTable();
                }, 300);
            });
        }
    },

    // === 5. 상품 테이블 (매대효율 컬럼, 행사보호/비수기 태그) ===
    renderTable() {
        var el = document.getElementById('beverageTableWrap');
        if (!el) return;

        var filtered = this._getFilteredData();

        if (filtered.length === 0) {
            el.innerHTML =
                '<div class="dessert-empty">' +
                    '<div class="dessert-empty-icon">&#127866;</div>' +
                    '<div class="dessert-empty-text">해당 조건의 상품이 없습니다</div>' +
                '</div>';
            return;
        }

        var self = this;
        var allCheckable = filtered.filter(function(d) {
            return d.decision === 'STOP_RECOMMEND' && !d.operator_action;
        });
        var visibleSelected = allCheckable.filter(function(d) { return self._selected.has(d.item_cd); });
        var headerChecked = allCheckable.length > 0 && visibleSelected.length === allCheckable.length;

        var html = '<table class="dessert-table" id="beverageProductTable">' +
            '<thead><tr>' +
                '<th class="dessert-cb-cell">' +
                    (allCheckable.length > 0
                        ? '<input type="checkbox" id="beverageSelectAll"' +
                          (headerChecked ? ' checked' : '') + '>'
                        : '') +
                '</th>' +
                '<th>상품</th>' +
                '<th>카테고리</th>' +
                '<th>생애주기</th>' +
                '<th>판매율</th>' +
                '<th>매대효율</th>' +
                '<th>주간추세</th>' +
                '<th>판단</th>' +
                '<th>사유</th>' +
                '<th>운영자 확인</th>' +
            '</tr></thead><tbody>';

        filtered.forEach(function(d) {
            var isCheckable = d.decision === 'STOP_RECOMMEND' && !d.operator_action;
            var isSelected = self._selected.has(d.item_cd);
            var rowCls = isSelected ? ' class="dessert-row-selected"' : '';

            html += '<tr' + rowCls + ' data-item-cd="' + d.item_cd + '">';

            // 체크박스
            html += '<td class="dessert-cb-cell">';
            if (isCheckable) {
                html += '<input type="checkbox" class="bev-item-cb" data-item-cd="' +
                    d.item_cd + '"' + (isSelected ? ' checked' : '') + '>';
            }
            html += '</td>';

            // 상품 + 태그
            var isNew = d.lifecycle_phase === 'new' || (d.weeks_since_intro && d.weeks_since_intro <= 4);
            var tags = '';
            if (isNew) tags += '<span class="dessert-tag-new">NEW</span>';
            html += '<td>' +
                '<div class="dessert-product-name">' + self._escHtml(d.item_nm || '') + tags + '</div>' +
                '<div class="dessert-product-code">' + (d.item_cd || '') + '</div>' +
            '</td>';

            // 카테고리
            var cat = d.dessert_category || '?';
            html += '<td><span class="dessert-cat-badge dessert-cat-' + cat + '">' + cat + '</span></td>';

            // 생애주기
            var phase = self._phaseLabel(d.lifecycle_phase);
            var weeks = d.weeks_since_intro || 0;
            html += '<td class="dessert-lifecycle">' + phase +
                '<span class="dessert-lifecycle-weeks">' + weeks + '주</span></td>';

            // 판매율
            var rate = Math.round((d.sale_rate || 0) * 100);
            var rateClass = rate >= 50 ? 'dessert-rate-high' : (rate >= 30 ? 'dessert-rate-mid' : 'dessert-rate-low');
            html += '<td><div class="dessert-rate-bar-wrapper ' + rateClass + '">' +
                '<div class="dessert-rate-bar"><div class="dessert-rate-bar-fill" style="width:' + Math.min(rate, 100) + '%"></div></div>' +
                '<span>' + rate + '%</span>' +
            '</div></td>';

            // 매대효율 (음료 전용)
            var shelfEff = self._calcShelfEfficiency(d);
            var shelfPct = Math.min(shelfEff * 100, 200);
            var shelfColor = shelfEff >= 1.0 ? 'var(--success, #22c55e)' :
                             shelfEff >= 0.2 ? 'var(--warning, #f59e0b)' : 'var(--danger, #ef4444)';
            html += '<td><div class="dessert-rate-bar-wrapper">' +
                '<div class="dessert-rate-bar"><div class="dessert-rate-bar-fill" style="width:' +
                    Math.min(shelfPct, 100) + '%;background:' + shelfColor + '"></div></div>' +
                '<span>' + shelfEff.toFixed(2) + '</span>' +
            '</div></td>';

            // 주간추세
            var trend = d.sale_trend_pct || 0;
            var trendRound = Math.round(trend);
            var trendCls = trend > 0 ? 'dessert-trend-up' : (trend < 0 ? 'dessert-trend-down' : 'dessert-trend-flat');
            var trendArrow = trend > 0 ? '&#9650;' : (trend < 0 ? '&#9660;' : '-');
            html += '<td class="' + trendCls + '">' + trendArrow + (trendRound !== 0 ? Math.abs(trendRound) + '%' : '') + '</td>';

            // 판단
            var decision = d.decision || 'SKIP';
            html += '<td><span class="dessert-decision-badge dessert-badge-' + decision + '">' +
                self._decisionLabel(decision) + '</span>';
            if (d.is_rapid_decline_warning) {
                html += '<span class="dessert-rapid-decline">&#9889; 급락</span>';
            }
            html += '</td>';

            // 사유
            html += '<td><span class="dessert-reason" title="' + self._escAttr(d.decision_reason || '') + '">' +
                self._escHtml(d.decision_reason || '') + '</span></td>';

            // 운영자 확인
            html += '<td>';
            if (d.decision === 'STOP_RECOMMEND' && !d.operator_action) {
                html += '<div class="dessert-action-btns">' +
                    '<button class="dessert-btn-stop" data-decision-id="' + d.id + '" data-action="CONFIRMED_STOP">정지확정</button>' +
                    '<button class="dessert-btn-keep" data-decision-id="' + d.id + '" data-action="OVERRIDE_KEEP">유지</button>' +
                '</div>';
            } else if (d.operator_action === 'CONFIRMED_STOP') {
                html += '<span class="dessert-action-done dessert-action-confirmed">&#128721; 정지됨</span>';
            } else if (d.operator_action === 'OVERRIDE_KEEP') {
                html += '<span class="dessert-action-done dessert-action-override">&#9989; 유지(재정)</span>';
            } else {
                html += '<span style="color:var(--text-muted)">-</span>';
            }
            html += '</td></tr>';
        });

        html += '</tbody></table>';
        el.innerHTML = html;

        // 이벤트 바인딩
        var selectAll = document.getElementById('beverageSelectAll');
        if (selectAll) {
            selectAll.addEventListener('change', function() {
                self.toggleSelectAll(this.checked);
            });
        }

        el.querySelectorAll('.bev-item-cb').forEach(function(cb) {
            cb.addEventListener('click', function(e) { e.stopPropagation(); });
            cb.addEventListener('change', function() {
                self.toggleSelectItem(this.dataset.itemCd, this.checked);
            });
        });

        el.querySelectorAll('.dessert-table tbody tr').forEach(function(row) {
            row.addEventListener('click', function(e) {
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
                var itemCd = this.dataset.itemCd;
                if (itemCd) self.openModal(itemCd);
            });
        });

        el.querySelectorAll('.dessert-btn-stop, .dessert-btn-keep').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                var decisionId = parseInt(this.dataset.decisionId);
                var action = this.dataset.action;
                self.singleAction(decisionId, action);
            });
        });
    },

    // === 6. 체크박스 ===
    toggleSelectAll(checked) {
        var self = this;
        this._getFilteredData().forEach(function(d) {
            if (d.decision === 'STOP_RECOMMEND' && !d.operator_action) {
                if (checked) self._selected.add(d.item_cd);
                else self._selected.delete(d.item_cd);
            }
        });
        this.renderTable();
        this.updateBatchBar();
    },

    toggleSelectItem(itemCd, checked) {
        if (checked) this._selected.add(itemCd);
        else this._selected.delete(itemCd);
        this.renderTable();
        this.updateBatchBar();
    },

    // === 7. 플로팅 액션 바 ===
    _ensureBatchBar() {
        if (document.getElementById('beverageBatchBar')) return;
        var bar = document.createElement('div');
        bar.id = 'beverageBatchBar';
        bar.className = 'dessert-batch-bar';
        bar.innerHTML =
            '<span class="dessert-batch-count" id="beverageBatchCount">선택 0건</span>' +
            '<button class="dessert-batch-btn dessert-batch-btn-stop" id="beverageBatchStop">&#128721; 일괄 정지확정</button>' +
            '<button class="dessert-batch-btn dessert-batch-btn-keep" id="beverageBatchKeep">&#9989; 일괄 유지(재정)</button>' +
            '<button class="dessert-batch-btn dessert-batch-btn-clear" id="beverageBatchClear">선택 해제</button>';
        document.body.appendChild(bar);

        var self = this;
        document.getElementById('beverageBatchStop').addEventListener('click', function() { self.batchAction('CONFIRMED_STOP'); });
        document.getElementById('beverageBatchKeep').addEventListener('click', function() { self.batchAction('OVERRIDE_KEEP'); });
        document.getElementById('beverageBatchClear').addEventListener('click', function() {
            self._selected.clear();
            self.renderTable();
            self.updateBatchBar();
        });
    },

    updateBatchBar() {
        var bar = document.getElementById('beverageBatchBar');
        if (!bar) return;
        var count = this._selected.size;
        document.getElementById('beverageBatchCount').textContent = '선택 ' + count + '건';
        if (count > 0) bar.classList.add('visible');
        else bar.classList.remove('visible');
    },

    // === 8. 일괄 처리 ===
    async batchAction(action) {
        var itemCds = Array.from(this._selected);
        if (itemCds.length === 0) return;

        var actionLabel = action === 'CONFIRMED_STOP' ? '정지확정' : '유지(재정)';
        if (!confirm(itemCds.length + '건을 ' + actionLabel + ' 처리하시겠습니까?')) return;

        try {
            var result = await api('/api/beverage-decision/action/batch' + storeParam(), {
                method: 'POST',
                body: { item_cds: itemCds, action: action }
            });

            if (result && result.success) {
                showToast(result.updated_count + '건 ' + actionLabel + ' 완료', 'success');
                this._selected.clear();
                await this.loadData();
                this.renderAll();
                this.renderCharts();
            } else {
                showToast((result && result.error) || '처리 실패', 'error');
            }
        } catch (e) {
            showToast('서버 오류가 발생했습니다', 'error');
        }
    },

    // === 9. 개별 처리 ===
    async singleAction(decisionId, action) {
        var actionLabel = action === 'CONFIRMED_STOP' ? '정지확정' : '유지(재정)';
        try {
            var result = await api('/api/beverage-decision/action/' + decisionId + storeParam(), {
                method: 'POST',
                body: { action: action }
            });

            if (result && result.success) {
                showToast(actionLabel + ' 완료', 'success');
                await this.loadData();
                this.renderAll();
            } else {
                showToast((result && result.error) || '처리 실패', 'error');
            }
        } catch (e) {
            showToast('서버 오류가 발생했습니다', 'error');
        }
    },

    // === 10. 상품 상세 모달 ===
    _ensureModal() {
        if (document.getElementById('beverageModalOverlay')) return;
        var overlay = document.createElement('div');
        overlay.id = 'beverageModalOverlay';
        overlay.className = 'dessert-modal-overlay';
        overlay.innerHTML =
            '<div class="dessert-modal" id="beverageModalContent">' +
                '<div class="dessert-modal-header">' +
                    '<span class="dessert-modal-title" id="beverageModalTitle"></span>' +
                    '<button class="dessert-modal-close" id="beverageModalClose">&times;</button>' +
                '</div>' +
                '<div class="dessert-modal-body" id="beverageModalBody"></div>' +
                '<div class="dessert-modal-footer" id="beverageModalFooter"></div>' +
            '</div>';
        document.body.appendChild(overlay);

        var self = this;
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) self.closeModal();
        });
        document.getElementById('beverageModalClose').addEventListener('click', function() {
            self.closeModal();
        });
    },

    async openModal(itemCd) {
        var item = this._data.find(function(d) { return d.item_cd === itemCd; });
        if (!item) return;

        var overlay = document.getElementById('beverageModalOverlay');
        var title = document.getElementById('beverageModalTitle');
        var body = document.getElementById('beverageModalBody');
        var footer = document.getElementById('beverageModalFooter');

        title.textContent = item.item_nm || itemCd;

        var cat = item.dessert_category || '?';
        var catLabel = this._catLabels[cat] || cat;
        var expDays = item.expiration_days || '-';
        var weeks = item.weeks_since_intro || 0;
        var smallNm = item.small_nm || '-';

        var metaHtml =
            '<div class="dessert-modal-meta">' +
                '<span class="dessert-meta-item">카테고리 ' + catLabel + '</span>' +
                '<span class="dessert-meta-item">유통기한 ' + expDays + '일</span>' +
                '<span class="dessert-meta-item">' + weeks + '주차</span>' +
                '<span class="dessert-meta-item">소분류: ' + this._escHtml(smallNm) + '</span>' +
            '</div>';

        var saleRate = Math.round((item.sale_rate || 0) * 100);
        var trendPct = Math.round(item.sale_trend_pct || 0);
        var shelfEff = this._calcShelfEfficiency(item);

        var perfHtml =
            '<div class="dessert-perf-grid">' +
                '<div class="dessert-perf-card"><div class="dessert-perf-value">' + fmt(item.total_sale_qty || 0) + '</div><div class="dessert-perf-label">판매수량</div></div>' +
                '<div class="dessert-perf-card"><div class="dessert-perf-value">' + fmt(item.total_disuse_qty || 0) + '</div><div class="dessert-perf-label">폐기수량</div></div>' +
                '<div class="dessert-perf-card"><div class="dessert-perf-value">' + saleRate + '%</div><div class="dessert-perf-label">판매율</div></div>' +
                '<div class="dessert-perf-card"><div class="dessert-perf-value">' + shelfEff.toFixed(2) + '</div><div class="dessert-perf-label">매대효율</div></div>' +
            '</div>';

        var chartHtml = '<div style="height:200px"><canvas id="beverageModalChart"></canvas></div>';
        var historyHtml = '<div class="dessert-history-title">판단 이력</div>' +
            '<div id="beverageHistoryList" class="dessert-loading"><div class="dessert-spinner"></div>로딩중...</div>';

        body.innerHTML = metaHtml + perfHtml + chartHtml + historyHtml;

        footer.innerHTML = '';
        if (item.decision === 'STOP_RECOMMEND' && !item.operator_action) {
            var self = this;
            footer.innerHTML =
                '<button class="dessert-btn-stop" id="beverageModalStop" style="padding:8px 16px;font-size:0.82rem;">&#128721; 정지확정</button>' +
                '<button class="dessert-btn-keep" id="beverageModalKeep" style="padding:8px 16px;font-size:0.82rem;">&#9989; 유지(재정)</button>';
            document.getElementById('beverageModalStop').addEventListener('click', function() {
                self.singleAction(item.id, 'CONFIRMED_STOP');
                self.closeModal();
            });
            document.getElementById('beverageModalKeep').addEventListener('click', function() {
                self.singleAction(item.id, 'OVERRIDE_KEEP');
                self.closeModal();
            });
        }

        overlay.classList.add('open');
        trapFocus(document.getElementById('beverageModalContent'));
        this._loadModalHistoryAndChart(itemCd);
    },

    closeModal() {
        var overlay = document.getElementById('beverageModalOverlay');
        if (overlay) overlay.classList.remove('open');
    },

    async _loadModalHistoryAndChart(itemCd) {
        var listEl = document.getElementById('beverageHistoryList');
        var history = [];

        try {
            var result = await api('/api/beverage-decision/history/' + itemCd + storeParam());
            history = (result && result.data) || [];

            if (listEl) {
                if (history.length === 0) {
                    listEl.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;">이력 없음</div>';
                } else {
                    var html = '<ul class="dessert-history-list">';
                    var self = this;
                    history.forEach(function(h) {
                        var date = (h.judgment_period_end || '').slice(5);
                        var decision = h.decision || 'SKIP';
                        var reason = h.decision_reason || '';
                        var actionStr = '';
                        if (h.operator_action === 'CONFIRMED_STOP') actionStr = ' <span class="dessert-action-confirmed">&#128721;</span>';
                        else if (h.operator_action === 'OVERRIDE_KEEP') actionStr = ' <span class="dessert-action-override">&#9989;</span>';

                        html += '<li class="dessert-history-item">' +
                            '<span class="dessert-history-date">' + date + '</span>' +
                            '<span class="dessert-decision-badge dessert-badge-' + decision + '">' +
                                self._decisionLabel(decision) + '</span>' + actionStr +
                            '<span class="dessert-history-reason">' + self._escHtml(reason) + '</span>' +
                        '</li>';
                    });
                    html += '</ul>';
                    listEl.innerHTML = html;
                }
            }
        } catch (e) {
            if (listEl) listEl.innerHTML = '<div style="color:var(--danger);font-size:0.8rem;">이력 로드 실패</div>';
        }

        this._renderModalChart(history);
    },

    _renderModalChart(history) {
        var canvas = document.getElementById('beverageModalChart');
        if (!canvas) return;

        var colors = getChartColors();
        var chartData = (history || []).slice(0, 8).reverse();

        if (chartData.length === 0) {
            getOrCreateChart('beverageModalChart', {
                type: 'bar',
                data: { labels: ['데이터 없음'], datasets: [
                    { label: '판매', data: [0], backgroundColor: colors.greenA, borderColor: colors.green, borderWidth: 1 },
                    { label: '폐기', data: [0], backgroundColor: colors.redA, borderColor: colors.red, borderWidth: 1 }
                ] },
                options: { responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { position: 'top', labels: { boxWidth: 10, font: { size: 11 } } } },
                    scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: colors.grid } } }
                }
            });
            return;
        }

        var labels = chartData.map(function(h) { return (h.judgment_period_end || '').slice(5); });
        var saleData = chartData.map(function(h) { return h.total_sale_qty || 0; });
        var disuseData = chartData.map(function(h) { return h.total_disuse_qty || 0; });

        getOrCreateChart('beverageModalChart', {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: '판매', data: saleData, backgroundColor: colors.greenA, borderColor: colors.green, borderWidth: 1 },
                    { label: '폐기', data: disuseData, backgroundColor: colors.redA, borderColor: colors.red, borderWidth: 1 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'top', labels: { boxWidth: 10, font: { size: 11 } } } },
                scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: colors.grid } } }
            }
        });
    },

    // === 헬퍼 ===
    _getPendingCount() {
        return this._data.filter(function(d) {
            return d.decision === 'STOP_RECOMMEND' && !d.operator_action;
        }).length;
    },

    _getFilteredData() {
        var self = this;
        return this._data.filter(function(d) {
            if (self._filter.hideProcessed && d.operator_action) return false;
            if (self._filter.decision !== 'all') {
                if (self._filter.decision === 'SKIP') {
                    if (d.decision && d.decision !== 'SKIP') return false;
                } else {
                    if (d.decision !== self._filter.decision) return false;
                }
            }
            if (self._filter.category !== 'all') {
                if (d.dessert_category !== self._filter.category) return false;
            }
            if (self._filter.search) {
                var q = self._filter.search.toLowerCase();
                var name = (d.item_nm || '').toLowerCase();
                var code = (d.item_cd || '').toLowerCase();
                if (!name.includes(q) && !code.includes(q)) return false;
            }
            return true;
        });
    },

    _calcShelfEfficiency(d) {
        // total_sale_qty / category_avg_sale_qty (0이면 0.0)
        var avg = d.category_avg_sale_qty || 0;
        if (avg <= 0) return 0.0;
        return (d.total_sale_qty || 0) / avg;
    },

    _decisionLabel(decision) {
        return { 'KEEP': 'KEEP', 'WATCH': 'WATCH', 'STOP_RECOMMEND': 'STOP', 'SKIP': 'SKIP' }[decision] || decision;
    },

    _phaseLabel(phase) {
        return { 'new': '신상품', 'growth_decline': '성장/하락', 'established': '정착기' }[phase] || (phase || '-');
    },

    _escHtml(s) {
        var div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    },

    _escAttr(s) {
        return s.replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
};
