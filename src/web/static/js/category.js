/* ============================================
   Category Dashboard — 서브탭 컨트롤러
   디저트 + 음료 서브탭 전환 관리
   ============================================ */

var CategoryDashboard = {
    _activeSubTab: 'dessert',
    _pendingCounts: { dessert: 0, beverage: 0 },

    async init() {
        await this.loadPendingCounts();
        this.renderSubTabs();
        this.switchSubTab(this._activeSubTab);
    },

    async loadPendingCounts() {
        try {
            var result = await api('/api/category-decision/pending-count' + storeParam());
            if (result && result.data) {
                this._pendingCounts = result.data;
            }
        } catch (e) {
            // 실패 시 기본값 유지
        }
        this.updateMainBadge();
    },

    renderSubTabs() {
        var container = document.getElementById('categorySubTabs');
        if (!container) return;

        var self = this;
        container.innerHTML =
            '<button class="cat-subtab' + (this._activeSubTab === 'dessert' ? ' active' : '') +
                '" data-sub="dessert">' +
                '\uD83C\uDF70 디저트' + this._badge('dessert') +
            '</button>' +
            '<button class="cat-subtab' + (this._activeSubTab === 'beverage' ? ' active' : '') +
                '" data-sub="beverage">' +
                '\uD83E\uDD64 음료' + this._badge('beverage') +
            '</button>';

        container.querySelectorAll('.cat-subtab').forEach(function(btn) {
            btn.addEventListener('click', function() {
                self.switchSubTab(this.dataset.sub);
            });
        });
    },

    switchSubTab(sub) {
        this._activeSubTab = sub;

        document.querySelectorAll('.cat-subtab').forEach(function(btn) {
            btn.classList.toggle('active', btn.dataset.sub === sub);
        });

        var dessertEl = document.getElementById('dessertContent');
        var beverageEl = document.getElementById('beverageContent');

        if (sub === 'dessert') {
            if (dessertEl) dessertEl.style.display = '';
            if (beverageEl) beverageEl.style.display = 'none';
            if (typeof DessertDashboard !== 'undefined') DessertDashboard.init();
        } else {
            if (dessertEl) dessertEl.style.display = 'none';
            if (beverageEl) beverageEl.style.display = '';
            if (typeof BeverageDashboard !== 'undefined') BeverageDashboard.init();
        }
    },

    updateMainBadge() {
        var total = (this._pendingCounts.dessert || 0) + (this._pendingCounts.beverage || 0);
        var badge = document.getElementById('categoryBadge');
        if (badge) {
            badge.textContent = total;
            badge.style.display = total > 0 ? 'inline-flex' : 'none';
        }
    },

    _badge(type) {
        var n = this._pendingCounts[type] || 0;
        return n > 0 ? '<span class="cat-subtab-badge">' + n + '</span>' : '';
    }
};
