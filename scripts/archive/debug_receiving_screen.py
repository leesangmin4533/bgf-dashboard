"""
센터매입 조회/확정 화면 데이터셋 구조 분석
- 화면의 모든 데이터셋 목록 조회
- 각 데이터셋의 컬럼 정보 조회
- 실제 데이터 샘플 출력
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sales_analyzer import SalesAnalyzer
from src.collectors.receiving_collector import ReceivingCollector
from src.config.ui_config import FRAME_IDS
from src.utils.logger import get_logger

logger = get_logger(__name__)


def analyze_receiving_screen(driver):
    """센터매입 화면 데이터셋 분석"""

    frame_id = FRAME_IDS["RECEIVING"]

    print("=" * 80)
    print("센터매입 조회/확정 화면 데이터셋 분석")
    print("=" * 80)

    # 1. 화면의 모든 데이터셋 나열
    print("\n[1] 화면의 모든 데이터셋 목록")
    print("-" * 80)

    result = driver.execute_script(f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            const datasets = [];

            // form 레벨 데이터셋
            for (let key in form) {{
                if (key.startsWith('ds') && form[key] && typeof form[key].getRowCount === 'function') {{
                    datasets.push({{
                        level: 'form',
                        name: key,
                        rowCount: form[key].getRowCount()
                    }});
                }}
            }}

            // workForm 레벨 데이터셋
            if (wf) {{
                for (let key in wf) {{
                    if (key.startsWith('ds') && wf[key] && typeof wf[key].getRowCount === 'function') {{
                        datasets.push({{
                            level: 'workForm',
                            name: key,
                            rowCount: wf[key].getRowCount()
                        }});
                    }}
                }}
            }}

            return {{success: true, datasets: datasets}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)

    if result.get('success'):
        for ds in result['datasets']:
            print(f"  - [{ds['level']}] {ds['name']}: {ds['rowCount']}행")
    else:
        print(f"  오류: {result.get('error')}")

    # 2. 주요 데이터셋 상세 분석
    print("\n[2] 주요 데이터셋 상세 정보")
    print("-" * 80)

    datasets_to_analyze = ['dsAcpYmd', 'dsListPopup', 'dsList', 'dsDetail']

    for ds_name in datasets_to_analyze:
        print(f"\n### {ds_name}")

        result = driver.execute_script(f"""
            try {{
                const app = nexacro.getApplication();
                const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
                const wf = form.div_workForm?.form;

                let ds = wf?.{ds_name} || form.{ds_name};

                if (!ds) {{
                    return {{error: 'dataset not found'}};
                }}

                // 컬럼 정보
                const columns = [];
                for (let i = 0; i < ds.getColCount(); i++) {{
                    columns.push({{
                        index: i,
                        name: ds.getColID(i),
                        type: ds.getColType(i)
                    }});
                }}

                // 첫 3행 데이터 샘플
                const samples = [];
                const maxRows = Math.min(3, ds.getRowCount());
                for (let row = 0; row < maxRows; row++) {{
                    const rowData = {{}};
                    for (let col = 0; col < ds.getColCount(); col++) {{
                        const colName = ds.getColID(col);
                        let value = ds.getColumn(row, colName);

                        // Decimal 타입 처리
                        if (value && typeof value === 'object' && value.hi !== undefined) {{
                            value = value.hi;
                        }}

                        rowData[colName] = value;
                    }}
                    samples.push(rowData);
                }}

                return {{
                    success: true,
                    rowCount: ds.getRowCount(),
                    colCount: ds.getColCount(),
                    columns: columns,
                    samples: samples
                }};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        """)

        if result.get('success'):
            print(f"  총 행 수: {result['rowCount']}")
            print(f"  총 컬럼 수: {result['colCount']}")
            print(f"\n  컬럼 목록:")
            for col in result['columns']:
                print(f"    [{col['index']}] {col['name']} ({col['type']})")

            if result['samples']:
                print(f"\n  데이터 샘플 (최대 3행):")
                for i, sample in enumerate(result['samples'], 1):
                    print(f"\n  행 {i}:")
                    for key, value in sample.items():
                        if value:  # 빈 값은 생략
                            print(f"    - {key}: {value}")
        else:
            print(f"  오류: {result.get('error')}")

    # 3. 그리드 정보 분석
    print("\n[3] 그리드 정보")
    print("-" * 80)

    result = driver.execute_script(f"""
        try {{
            const app = nexacro.getApplication();
            const form = app.mainframe.HFrameSet00.VFrameSet00.FrameSet.{frame_id}.form;
            const wf = form.div_workForm?.form;

            const grids = [];

            // workForm 레벨 그리드
            if (wf) {{
                for (let key in wf) {{
                    if ((key.startsWith('gd') || key.startsWith('Grid')) &&
                        wf[key] && wf[key]._binddataset) {{
                        const bindDs = wf[key]._binddataset;
                        grids.push({{
                            name: key,
                            bindDataset: bindDs.name,
                            rowCount: bindDs.getRowCount()
                        }});
                    }}
                }}
            }}

            return {{success: true, grids: grids}};
        }} catch(e) {{
            return {{error: e.message}};
        }}
    """)

    if result.get('success'):
        for grid in result['grids']:
            print(f"  - {grid['name']}")
            print(f"    바인드 데이터셋: {grid['bindDataset']}")
            print(f"    행 수: {grid['rowCount']}")
    else:
        print(f"  오류: {result.get('error')}")

    print("\n" + "=" * 80)
    print("분석 완료")
    print("=" * 80)


def main():
    """메인 실행"""
    analyzer = SalesAnalyzer()

    try:
        # 드라이버 설정
        logger.info("크롬 드라이버 설정 중...")
        analyzer.setup_driver()

        # BGF 사이트 접속
        logger.info("BGF 사이트 접속 중...")
        analyzer.connect()

        # 로그인
        logger.info("로그인 중...")
        if not analyzer.do_login():
            logger.error("로그인 실패")
            return 1

        logger.info("로그인 성공")

        # 센터매입 화면으로 이동
        logger.info("센터매입 조회/확정 화면 이동 중...")
        collector = ReceivingCollector(analyzer.driver)

        if not collector.navigate_to_receiving_menu():
            logger.error("메뉴 이동 실패")
            return 1

        logger.info("화면 이동 완료")
        time.sleep(3)  # 데이터 로딩 대기

        # 화면 분석
        analyze_receiving_screen(analyzer.driver)

        # 결과 대기
        input("\nEnter 키를 누르면 종료합니다...")

        return 0

    except Exception as e:
        logger.error(f"오류 발생: {e}", exc_info=True)
        return 1

    finally:
        try:
            analyzer.close()
            logger.info("브라우저 종료")
        except Exception as e:
            logger.debug(f"브라우저 종료 실패: {e}")


if __name__ == "__main__":
    exit(main())
