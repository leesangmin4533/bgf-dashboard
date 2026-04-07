"""SrcWatcher 회귀 테스트 (scheduler-auto-reload)"""

import threading
import time

import pytest

from src.infrastructure.scheduler.src_watcher import SrcWatcher, src_signature


@pytest.fixture
def watch_dir(tmp_path):
    """tmp_path 안에 가짜 src/ 트리 생성"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("# initial\n")
    (src / "bar.py").write_text("# bar\n")
    sub = src / "sub"
    sub.mkdir()
    (sub / "baz.py").write_text("# baz\n")
    return [src]


class TestSrcSignature:
    def test_stable_when_unchanged(self, watch_dir):
        """변경 없으면 동일 시그니처"""
        sig1 = src_signature(watch_dir)
        sig2 = src_signature(watch_dir)
        assert sig1 == sig2
        assert sig1[0] > 0  # mtime
        assert sig1[1] > 0  # size

    def test_changes_on_file_modification(self, watch_dir):
        """파일 내용 변경 시 시그니처 달라짐"""
        sig1 = src_signature(watch_dir)
        time.sleep(1.1)  # mtime 해상도 보장
        (watch_dir[0] / "foo.py").write_text("# modified longer content\n")
        sig2 = src_signature(watch_dir)
        assert sig1 != sig2
        assert sig2[1] > sig1[1]  # size 증가

    def test_excludes_pycache(self, tmp_path):
        """__pycache__ 폴더는 제외"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "foo.py").write_text("ok")
        cache = src / "__pycache__"
        cache.mkdir()
        (cache / "foo.cpython-312.pyc.py").write_text("massive cache")
        sig = src_signature([src])
        # 캐시 파일 size가 포함되지 않아야 함
        assert sig[1] == len("ok")


class TestSrcWatcher:
    def test_sets_reload_event_on_change(self, watch_dir):
        """파일 변경 시 reload_event가 set됨"""
        event = threading.Event()
        watcher = SrcWatcher(event, interval_sec=1, watch_paths=watch_dir)
        watcher.start()

        time.sleep(0.3)
        assert not event.is_set()  # 아직 변경 없음

        time.sleep(1.0)  # mtime 해상도 보장
        (watch_dir[0] / "foo.py").write_text("# changed by test\n")

        # watcher가 다음 cycle(1초)에 감지
        time.sleep(2.0)
        assert event.is_set(), "변경 후 reload_event가 set되어야 함"

    def test_event_stays_clear_when_no_change(self, watch_dir):
        """변경 없으면 event는 set되지 않음"""
        event = threading.Event()
        watcher = SrcWatcher(event, interval_sec=1, watch_paths=watch_dir)
        watcher.start()

        time.sleep(2.5)  # 2 cycle 대기
        assert not event.is_set(), "변경 없으면 event가 set되지 않아야 함"
