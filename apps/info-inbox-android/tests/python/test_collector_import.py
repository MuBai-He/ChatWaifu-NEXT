"""Execute the shipped collector SQL against synthetic plaintext source databases."""

import pathlib
import sqlite3
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class ImportedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = pathlib.Path(self.directory.name) / "source.db"
        self.cache = pathlib.Path(self.directory.name) / "cache.db"
        self.src = sqlite3.connect(self.source)
        self.columns = [
            "msgId",
            "msgSvrId",
            "type",
            "status",
            "isSend",
            "isShowTimer",
            "createTime",
            "talker",
            "content",
            "imgPath",
            "reserved",
            "lvbuffer",
            "transContent",
            "transBrandWording",
            "talkerId",
            "bizClientMsgId",
            "bizChatId",
            "bizChatUserId",
            "msgSeq",
            "flag",
            "solitaireFoldInfo",
            "historyId",
        ]
        self.src.execute(
            "CREATE TABLE message("
            + ",".join(x + (" INTEGER PRIMARY KEY" if x == "msgId" else "") for x in self.columns)
            + ")"
        )
        self.src.execute("CREATE TABLE rcontact(username,nickname,conRemark,type)")
        self.addCleanup(self.src.close)

    def add(self, key, text="合成资料", when=1700000000000):
        data = [None] * len(self.columns)
        for name, value in [
            ("msgId", key),
            ("msgSvrId", key + 10000),
            ("type", 1),
            ("isSend", 1),
            ("createTime", when),
            ("talker", "fixture-room"),
            ("content", text),
        ]:
            data[self.columns.index(name)] = value
        self.src.execute("INSERT INTO message VALUES(" + ",".join("?" for x in data) + ")", data)
        self.src.commit()

    def collect(self, full=False):
        script = (ROOT / "assets/collector.sh").read_text()
        sql = script.split('cat > "$SQL_FILE" <<SQL\n', 1)[1].split("\nSQL\n", 1)[0]
        sql = (
            sql.replace(" KEY '$DB_KEY'", "")
            .replace("$SOURCE_DB", str(self.source))
            .replace("$ACCOUNT_ID", "a" * 32)
            .replace("$FULL_RESCAN", "1" if full else "0")
        )
        sql = "\n".join(x for x in sql.splitlines() if not x.startswith("."))
        with sqlite3.connect(self.cache) as c:
            c.executescript(sql)

    def count(self):
        with sqlite3.connect(self.cache) as c:
            return c.execute("SELECT count(*) FROM messages").fetchone()[0]

    def test_large_restore_after_high_water_mark(self):
        self.add(1)
        self.add(2)
        self.collect()
        for key in range(3, 1503):
            self.add(key, "历史导入", 1600000000000 + key)
        self.collect()
        self.assertEqual(self.count(), 1502)
        self.collect()
        self.assertEqual(self.count(), 1502)

    def test_restore_finds_old_id_below_checkpoint_without_manual_rescan(self):
        self.add(1)
        self.add(100)
        self.collect()
        self.add(2, "旧消息", 1500000000000)
        self.collect()
        self.assertEqual(self.count(), 3)
        self.collect(full=True)
        self.assertEqual(self.count(), 3)

    def test_forced_rescan_refreshes_old_revision_with_unchanged_count(self):
        self.add(1, "旧版本")
        self.add(100)
        self.collect()
        self.src.execute("UPDATE message SET content='修订' WHERE msgId=1")
        self.src.commit()
        self.collect()
        with sqlite3.connect(self.cache) as c:
            self.assertEqual(
                c.execute("SELECT content FROM messages WHERE msg_id=1").fetchone()[0], "旧版本"
            )
        self.collect(full=True)
        with sqlite3.connect(self.cache) as c:
            self.assertEqual(
                c.execute("SELECT content FROM messages WHERE msg_id=1").fetchone()[0], "修订"
            )

    def test_rescan_preserves_archived_originals_and_updates_current_versions(self):
        self.add(1, "保留原文")
        self.add(2, "旧版本")
        self.collect()
        self.src.execute("DELETE FROM message WHERE msgId=1")
        self.src.execute("UPDATE message SET content='修订' WHERE msgId=2")
        self.src.commit()
        self.collect(full=True)
        with sqlite3.connect(self.cache) as c:
            self.assertEqual(
                c.execute("SELECT msg_id,content FROM messages ORDER BY msg_id").fetchall(),
                [(1, "保留原文"), (2, "修订")],
            )


if __name__ == "__main__":
    unittest.main()
