
if (typeof window.automation !== 'object') {
    window.automation = {};
}

/**
 * [MODIFIED] IIFE to a named async function returning a Promise.
 * 상품 코드를 기반으로 Nexacro 화면에서 상품 상세 정보를 비동기적으로 조회합니다.
 * @param {string} productCode - 조회할 상품의 코드 (바코드 등)
 * @param {object} [OPT={}] - (선택) 상세 동작을 제어하는 옵션 객체
 * @returns {Promise<object>} 성공 시 {success: true, data: {...}}, 실패 시 {success: false, ...} 객체를 담은 Promise
 */
window.automation.getProductDetails = async function(productCode, OPT = {}) {
    // This function is now async and returns a promise, which fits the python script's call structure.
    // The watchdog and exit logic is simplified by the async/await structure.

    // 팝업에서 데이터를 정규화하는 함수 (참고 코드 기반)
    function normalizeFromPopup() {
        const app = nexacro.getApplication?.();
        const f =
            app?.popupframes?.CallItemDetailPopup?.form ||
            window.newChild?.form ||
            window.__lastPopupForm ||
            (function() {
                if (!app?.popupframes) return null;
                const ids = Object.keys(app.popupframes);
                for (let i = ids.length - 1; i >= 0; i--) {
                    const cf = app.popupframes[ids[i]];
                    if (cf?.form) return cf.form;
                }
                return null;
            })();
        if (!f) {
            console.warn("❌ 팝업 form 없음");
            return null;
        }

        const d1 = f.objects?.dsItemDetail;
        const d2 = f.objects?.dsItemDetailOrd || d1;
        const r = 0;

        function gv(ds, col) {
            try {
                const v = ds?.getRowCount?.() > 0 ? ds.getColumn(r, col) : null;
                return (v == null) ? null : (typeof v === "object" && v.toString) ? v.toString() : String(v);
            } catch (e) {
                return null;
            }
        }

        // 발주 가능 요일 - 데이터셋에서 조회
        let orderableDay = gv(d2, "ORD_ADAY") || gv(d1, "ORD_ADAY") ||
                          gv(d2, "ORD_DAY") || gv(d1, "ORD_DAY") ||
                          gv(d2, "ORD_PSS_YO") || gv(d1, "ORD_PSS_YO") || "";

        // 데이터셋에 없으면 넥사크로 정적 텍스트에서 조회
        if (!orderableDay) {
            try {
                // divInfo07 내의 stOrdPssYo
                const div07 = f.divInfo07;
                if (div07?.form?.stOrdPssYo) {
                    orderableDay = div07.form.stOrdPssYo.text || "";
                }
            } catch (e) { console.warn("stOrdPssYo access error:", e); }
        }

        // DOM에서 직접 조회 (ID 패턴: *stOrdPssYo*text)
        if (!orderableDay) {
            try {
                const domEl = document.querySelector("[id$='stOrdPssYo:text']");
                if (domEl) {
                    orderableDay = (domEl.innerText || domEl.textContent || "").trim();
                }
            } catch (e) { console.warn("DOM stOrdPssYo error:", e); }
        }

        // 추가: CallItemDetailPopup에서 직접 접근
        if (!orderableDay) {
            try {
                const popup = app?.popupframes?.CallItemDetailPopup;
                const stEl = popup?.form?.divInfo07?.form?.stOrdPssYo;
                if (stEl) {
                    orderableDay = stEl.text || "";
                }
            } catch (e) { console.warn("popup direct access error:", e); }
        }

        const out = {
          product_name: gv(d1, "ITEM_NM"),
          expiration_days: gv(d1, "EXPIRE_DAY"),
          orderable_day: orderableDay,
          orderable_status: gv(d2, "ORD_PSS_ID_NM") || gv(d2, "ORD_STAT_NM") || gv(d2, "ORD_GB") || gv(d2, "ORD_STAT") || "",
          order_unit_name: gv(d1, "ORD_UNIT_NM") || gv(d2, "ORD_UNIT_NM") || "",
          order_unit_qty: Number(gv(d1, "ORD_UNIT_QTY") || gv(d2, "ORD_UNIT_QTY") || 0),
          case_unit_qty: Number(gv(d1, "CASE_UNIT_QTY") || gv(d2, "CASE_UNIT_QTY") || 0),
        };

        // 디버깅: 한 건 수집 직후에 한번만
        console.debug("[product_info]", out);
        return out;
    }

    try {
        const effectiveProductCode = String(productCode ?? "").trim();
        const TIMEOUT_OPEN = OPT.timeoutOpen ?? 5000;
        const TIMEOUT_FILL = OPT.timeoutFill ?? 3000;
        const STEP = OPT.step ?? 100;

        const app = (window.nexacro && (nexacro.getApplication?.() || window.application)) || null;
        if (!app) return { success: false, stage: "init", message: "nexacro app not found" };

        let main = null;
        const possiblePaths = [
            app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.WorkFrame?.form,
            app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STMB011_M0?.form,
            app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.form,
            app?.mainframe?.form,
            app?.mainframe?.HFrameSet00?.form,
            app?.mainframe?.HFrameSet00?.VFrameSet00?.form,
            app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.WorkFrame?.div_workForm?.form,
            app?.mainframe?.HFrameSet00?.VFrameSet00?.FrameSet?.STMB011_M0?.div_workForm?.form
        ];
        for (const path of possiblePaths) {
            if (path) {
                main = path;
                console.log("[get_product_details] 메인 폼을 찾았습니다:", path);
                break;
            }
        }
        if (!main) return { success: false, stage: "init", message: "main form not found" };
        if (!effectiveProductCode) return { success: false, stage: "input", message: "empty productCode" };

        // 기존 팝업 모두 닫기
        try {
            const pf = app.popupframes;
            if (pf && typeof pf === "object") {
                const ids = Object.keys(pf);
                for (const id of ids) {
                    try { pf[id]?.close?.(); } catch (e) {}
                }
            }
            try { window.newChild?.close?.(); } catch (e) {}
            await new Promise(r => setTimeout(r, 500)); // 팝업 닫힘 대기
        } catch (e) {
            console.warn("[get_product_details] 팝업 닫기 실패:", e);
        }

        let searchField = null;
        const possibleSearchFields = [
            main?.edt_pluSearch, main?.components?.edt_pluSearch, main?.edt_search, main?.components?.edt_search,
            main?.edt_product, main?.components?.edt_product, main?.edt_item, main?.components?.edt_item,
            main?.edt_plu, main?.components?.edt_plu, main?.edt_code, main?.components?.edt_code,
            main?.div_workForm?.form?.edt_pluSearch, main?.div_workForm?.form?.edt_search,
            main?.div_workForm?.form?.edt_product, main?.div_workForm?.form?.edt_item, main?.div_workForm?.form?.edt_plu
        ];
        for (const field of possibleSearchFields) {
            if (field && typeof field.set_value === 'function') {
                searchField = field;
                break;
            }
        }
        if (searchField) {
            try {
                searchField.setFocus?.();
                searchField.set_value(effectiveProductCode);
                searchField.text = effectiveProductCode;
                try { searchField.on_fire_onchanged?.(searchField, searchField.pretext, searchField.text); } catch (e) {}
                try { searchField.on_fire_onkeyup?.(searchField, 13, false, false, false); } catch (e) {}
                try { searchField.on_fire_onkillfocus?.(searchField); } catch (e) {}
            } catch (e) { console.warn("[get_product_details] 검색값 주입 실패:", e); }
        } else { console.warn("[get_product_details] 검색 필드를 찾을 수 없습니다."); }

        let searchButton = null;
        const possibleButtons = [
            main?.btn_search, main?.components?.btn_search, main?.btnSearch, main?.components?.btnSearch,
            main?.btn_find, main?.components?.btn_find, main?.btn_search_plu, main?.components?.btn_search_plu,
            main?.btn_ok, main?.components?.btn_ok, main?.btn_query, main?.components?.btn_query,
            main?.btn_confirm, main?.components?.btn_confirm, main?.div_workForm?.form?.btn_search,
            main?.div_workForm?.form?.btnSearch, main?.div_workForm?.form?.btn_find,
            main?.div_workForm?.form?.btn_ok, main?.div_workForm?.form?.btn_query
        ];
        for (const btn of possibleButtons) {
            if (btn && (typeof btn.click === "function" || typeof btn.on_fire_onclick === "function")) {
                searchButton = btn;
                break;
            }
        }
        if (searchButton) {
            try {
                if (typeof searchButton.click === "function") searchButton.click();
                else if (typeof searchButton.on_fire_onclick === "function") searchButton.on_fire_onclick(searchButton);
            } catch (e) { console.warn("[get_product_details] 검색 버튼 클릭 실패:", e); }
        } else { console.warn("[get_product_details] 검색 버튼을 찾을 수 없습니다."); }

        function readRow(form){
          const out = {};
          const objs = form?.objects || {};
          for (const k of Object.keys(objs)){
            const ds = objs[k];
            if (!ds || typeof ds.getRowCount !== "function" || ds.getRowCount()<=0) continue;
            const pick = (c)=>{ try{ return ds.getColumn(0,c); }catch(e){ return null; } };
            if (out.item_cd == null && pick("ITEM_CD")!=null) out.item_cd = String(pick("ITEM_CD"));
            if (out.item_nm == null && pick("ITEM_NM")!=null) out.item_nm = String(pick("ITEM_NM"));
            if (out.class_nm== null && pick("CLASS_NM")!=null) out.class_nm= String(pick("CLASS_NM"));
            if (out.store_cd== null && pick("STORE_CD")!=null) out.store_cd = String(pick("STORE_CD"));
            if (out.expire_days==null && pick("EXPIRE_DAY")!=null) out.expire_days = Number(pick("EXPIRE_DAY"));
            if (out.order_unit_nm==null && pick("ORD_UNIT_NM")!=null) out.order_unit_nm = String(pick("ORD_UNIT_NM"));
            if (out.order_unit_qty==null && pick("ORD_UNIT_QTY")!=null) out.order_unit_qty = Number(pick("ORD_UNIT_QTY"));
            if (out.case_unit_qty==null && pick("CASE_UNIT_QTY")!=null) out.case_unit_qty = Number(pick("CASE_UNIT_QTY"));
            if (out.order_mult_llmt==null && pick("ORD_MULT_LLMT")!=null) out.order_mult_llmt = Number(pick("ORD_MULT_LLMT"));
            if (out.order_mult_ulmt==null && pick("ORD_MULT_ULMT")!=null) out.order_mult_ulmt = Number(pick("ORD_MULT_ULMT"));
          }
          return out;
        }

        const t0 = performance.now();
        let popupForm = null;
        while (performance.now() - t0 < TIMEOUT_OPEN) {
            try {
                const pf = app.popupframes;
                if (pf && typeof pf === "object") {
                    const ids = Object.keys(pf);
                    const last = ids.length ? pf[ids[ids.length - 1]] : null;
                    if (last?.form) {
                        popupForm = last.form;
                        break;
                    }
                }
                if (window.newChild?.form) {
                    popupForm = window.newChild.form;
                    break;
                }
            } catch (e) {}
            await new Promise(r => setTimeout(r, STEP));
        }

        const t1 = performance.now();
        let data = {};
        while (performance.now() - t1 < TIMEOUT_FILL) {
            if (popupForm) {
                const normalizedData = normalizeFromPopup();
                if (normalizedData && normalizedData.product_name) {
                    data = normalizedData; // Use the object with the correct keys directly
                    break;
                } else {
                    data = readRow(popupForm);
                }
            }
            if (!data.product_name) {
                data = readRow(main);
            }
            if (data.product_name) break;
            await new Promise(r => setTimeout(r, STEP));
        }

        // DOM 렌더링 대기 후 추가 정보 조회
        await new Promise(r => setTimeout(r, 500));

        // 발주가능요일 - DOM에서 직접 조회 (팝업 닫기 전)
        if (!data.orderable_day) {
            try {
                const domEl = document.querySelector("[id$='stOrdPssYo:text']");
                if (domEl) {
                    data.orderable_day = (domEl.innerText || domEl.textContent || "").trim();
                }
            } catch (e) {}
        }

        // 발주상태 - DOM에서 직접 조회
        if (!data.orderable_status) {
            try {
                const statusEl = document.querySelector("[id$='stOrdPssIdNm:text']") ||
                                document.querySelector("[id$='stOrdStatNm:text']");
                if (statusEl) {
                    data.orderable_status = (statusEl.innerText || statusEl.textContent || "").trim();
                }
            } catch (e) {}
        }

        try { popupForm?.getOwnerFrame?.().close?.(); } catch (e) {}
        if (!data.product_name) {
            return { success: false, stage: "fill", message: "dataset empty" };
        }

        const nm = (data.order_unit_nm || "").trim();
        const ord = Number(data.order_unit_qty || 1);
        const cpk = Number(data.case_unit_qty || 0);
        const llm = Number(data.order_mult_llmt || 1);
        data.calc_pack_qty = (nm === "묶음") ? (cpk > 0 ? cpk : Math.max(ord, 1)) : Math.max(ord, llm, 1);
        data.snapshot_ymd = new Date().toISOString().slice(0, 10).replace(/-/g, "");
        data.source_dataset = popupForm ? "popup" : "main";

        return { success: true, data };
    } catch (err) {
        console.error("[get_product_details] 오류 발생:", err);
        return { success: false, stage: "fatal", message: String(err?.message || err) };
    }
};
