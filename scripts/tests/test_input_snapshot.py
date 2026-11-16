"""大构件按文件身份去重，但别名、元数据与最终内容校验都不可省略。"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from input_snapshot import InputSnapshot
from inventory import digest


class SnapshotTests(unittest.TestCase):
    def test_aliases_share_content_reads_and_small_sources_remain_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file';file.write_bytes(b'original')
            symbolic=Path(tmp)/'symbolic';symbolic.symlink_to(file)
            hard=Path(tmp)/'hard';os.link(file,hard)
            reads=[]
            def read(path):reads.append(path);return digest(path)
            snapshot=InputSnapshot(read)
            for path in [file,symbolic,hard]:snapshot.watch(path,snapshot.digest(path),bulk=True)
            for _ in range(11):self.assertEqual(snapshot.changes(),[])
            self.assertEqual(len(reads),1)
            self.assertEqual(snapshot.changes(full=True),[])
            self.assertEqual(len(reads),2)
            snapshot.watch(hard,snapshot.digest(hard))
            self.assertEqual(snapshot.changes(),[])
            self.assertEqual(len(reads),3)

    def test_final_hash_detects_change_even_when_metadata_appears_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file';file.write_bytes(b'original')
            snapshot=InputSnapshot(digest);snapshot.watch(file,snapshot.digest(file),bulk=True)
            initial=snapshot.state(file)
            file.write_bytes(b'modified')
            with patch.object(snapshot,'state',return_value=initial):
                self.assertEqual(snapshot.changes(),[])
                self.assertIn(str(file),snapshot.changes(full=True))

    def test_missing_files_and_replaced_inode_are_not_silently_rebound(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file';snapshot=InputSnapshot(digest)
            snapshot.watch(file,None)
            self.assertEqual(snapshot.changes(),[])
            file.write_bytes(b'created')
            self.assertIn(str(file),snapshot.changes())
            snapshot=InputSnapshot(digest);snapshot.watch(file,snapshot.digest(file),bulk=True)
            replacement=Path(tmp)/'new';replacement.write_bytes(b'created');replacement.replace(file)
            self.assertIn(str(file),snapshot.changes())

    def test_input_mutation_while_hashing_cannot_publish_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file';file.write_bytes(b'original')
            def read(path):
                sha=digest(path);path.write_bytes(b'modified');return sha
            snapshot=InputSnapshot(read)
            with self.assertRaisesRegex(ValueError,'changed while hashing'):snapshot.digest(file)
            self.assertEqual(snapshot.cache,{})

    def test_expected_byte_snapshot_is_not_replaced_by_later_disk_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'file';file.write_bytes(b'later version')
            snapshot=InputSnapshot(digest)
            snapshot.watch(file,hashlib.sha256(b'earlier version').hexdigest())
            self.assertIn(str(file),snapshot.changes())
