// BGF 단품별 발주 화면에서 행사 데이터 대량 수집
// Chrome 콘솔에서 실행하거나, Claude MCP를 통해 주입
//
// 사전조건: /stbj030/selSearch 요청이 window._capturedRequests에 캡처되어 있어야 함
// 실행: promoScan.start(barcodes) -> promoScan.getResults()

window.promoScan = (function() {
    var state = {
        results: [],     // 행사가 있는 상품만
        noPromo: 0,      // 행사 없는 상품 수
        errors: [],
        processed: 0,
        total: 0,
        running: false,
        startTime: null
    };

    var requestTemplate = null;

    function init() {
        if (!window._capturedRequests || window._capturedRequests.length === 0) {
            throw new Error('No captured requests. Enter a barcode manually first.');
        }
        requestTemplate = window._capturedRequests[0].body;
    }

    function parseResponse(text, itemCd) {
        try {
            var records = text.split('\u001e');
            var colRecord = null;
            var dataRecord = null;

            for (var i = 0; i < records.length; i++) {
                if (records[i].includes('_RowType_') && records[i].includes('MONTH_EVT')) {
                    colRecord = records[i];
                    if (i + 1 < records.length) dataRecord = records[i + 1];
                    break;
                }
            }

            if (!colRecord || !dataRecord || dataRecord.trim() === '') return null;

            var cols = colRecord.split('\u001f');
            var vals = dataRecord.split('\u001f');

            var colMap = {};
            for (var j = 0; j < cols.length; j++) {
                colMap[cols[j].split(':')[0]] = j;
            }

            var me = colMap['MONTH_EVT'] !== undefined ? (vals[colMap['MONTH_EVT']] || '') : '';
            var nme = colMap['NEXT_MONTH_EVT'] !== undefined ? (vals[colMap['NEXT_MONTH_EVT']] || '') : '';
            var nm = colMap['ITEM_NM'] !== undefined ? (vals[colMap['ITEM_NM']] || '') : '';

            me = me.replace(/\r\n/g, ' ').replace(/\r/g, ' ').replace(/\n/g, ' ').trim();
            nme = nme.replace(/\r\n/g, ' ').replace(/\r/g, ' ').replace(/\n/g, ' ').trim();

            if (me || nme) {
                return { item_cd: itemCd, item_nm: nm, month_evt: me, next_month_evt: nme };
            }
            return null;
        } catch(e) {
            return { item_cd: itemCd, error: e.message };
        }
    }

    async function scanOne(barcode) {
        var body = requestTemplate.replace(/strItemCd=[^\u001e]*/, 'strItemCd=' + barcode);
        var resp = await fetch('/stbj030/selSearch', {
            method: 'POST',
            headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
            body: body
        });
        return await resp.text();
    }

    async function start(barcodes) {
        init();
        state.total = barcodes.length;
        state.processed = 0;
        state.results = [];
        state.noPromo = 0;
        state.errors = [];
        state.running = true;
        state.startTime = Date.now();

        console.log('[PromoScan] Starting scan of ' + barcodes.length + ' items...');

        for (var i = 0; i < barcodes.length; i++) {
            if (!state.running) {
                console.log('[PromoScan] Stopped at ' + i);
                break;
            }

            try {
                var text = await scanOne(barcodes[i]);
                var parsed = parseResponse(text, barcodes[i]);
                if (parsed && !parsed.error) {
                    state.results.push(parsed);
                } else if (parsed && parsed.error) {
                    state.errors.push(parsed);
                } else {
                    state.noPromo++;
                }
            } catch(e) {
                state.errors.push({ item_cd: barcodes[i], error: e.message });
            }

            state.processed++;

            // 진행 로그 (100개마다)
            if (state.processed % 100 === 0) {
                var elapsed = ((Date.now() - state.startTime) / 1000).toFixed(0);
                var rate = (state.processed / elapsed * 60).toFixed(0);
                console.log('[PromoScan] ' + state.processed + '/' + state.total +
                    ' (' + state.results.length + ' promos found, ' +
                    elapsed + 's, ~' + rate + '/min)');
            }

            // 50ms 딜레이
            await new Promise(r => setTimeout(r, 50));
        }

        state.running = false;
        var totalTime = ((Date.now() - state.startTime) / 1000).toFixed(1);
        console.log('[PromoScan] Done! ' + state.results.length + ' promos found in ' + totalTime + 's');
        return state.results;
    }

    function stop() { state.running = false; }
    function getStatus() {
        var elapsed = state.startTime ? ((Date.now() - state.startTime) / 1000).toFixed(0) : 0;
        return {
            processed: state.processed,
            total: state.total,
            promoFound: state.results.length,
            noPromo: state.noPromo,
            errors: state.errors.length,
            running: state.running,
            elapsedSec: elapsed
        };
    }
    function getResults() { return state.results; }
    function getErrors() { return state.errors; }

    return { start: start, stop: stop, getStatus: getStatus, getResults: getResults, getErrors: getErrors };
})();

console.log('[PromoScan] Module loaded. Use promoScan.start(barcodes) to begin.');
