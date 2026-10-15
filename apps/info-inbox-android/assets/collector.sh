#!/system/bin/sh
# Root-only, local WeChat message collector. No network or source DB writes.
set -eu
umask 077
BASE=${WECHAT_COLLECTOR_HOME:-/data/adb/wechat-collector}
SELF=$BASE/collector.sh
SQLCIPHER=$BASE/bin/sqlcipher
export LD_LIBRARY_PATH=$BASE/lib

die() { printf '%s\n' "$1" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die 'Root is required.'
mkdir -p "$BASE"
chmod 700 "$BASE"

running() {
    [ -f "$BASE/watch.pid" ] || return 1
    PID=$(cat "$BASE/watch.pid")
    case "$PID" in ''|*[!0-9]*) return 1 ;; esac
    [ -r "/proc/$PID/cmdline" ] || return 1
    tr '\000' ' ' < "/proc/$PID/cmdline" | grep -Fq "$SELF watch"
}

acquire_lock() {
    LOCK_DIR=$BASE/$1.lock
    LOCK_COMMAND="$SELF $2"
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        # A killed process or reboot must not leave the collector permanently stuck.
        [ -f "$LOCK_DIR/pid" ] || sleep 1
        OWNER=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
        case "$OWNER" in
            ''|*[!0-9]*) ;;
            *)
                if [ -r "/proc/$OWNER/cmdline" ] && tr '\000' ' ' < "/proc/$OWNER/cmdline" | grep -Fq "$SELF "; then
                    die 'Another collector command is in progress.'
                fi
                ;;
        esac
        rm -f "$LOCK_DIR/pid"
        rmdir "$LOCK_DIR" 2>/dev/null || die 'Cannot recover the previous command lock.'
        mkdir "$LOCK_DIR" 2>/dev/null || die 'Another collector command is starting.'
    fi
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
}

collect_once() {
    FULL_RESCAN=${1:-0}
    [ -f "$BASE/config.sh" ] || die 'Missing private config.sh.'
    . "$BASE/config.sh"
    case "$DB_KEY" in ''|*[!0-9a-f]*) die 'Invalid key format.' ;; esac
    [ "${#DB_KEY}" = 7 ] || die 'Expected the verified seven-character key.'
    case "$ACCOUNT_ID" in ''|*[!0-9a-f]*) die 'Invalid account identifier.' ;; esac
    [ "$SOURCE_DB" = "/data/user/0/com.tencent.mm/MicroMsg/$ACCOUNT_ID/EnMicroMsg.db" ] || die 'Unexpected source database path.'
    [ -f "$SOURCE_DB" ] || die 'Source database is unavailable.'
    acquire_lock cycle once
    SQL_FILE=$BASE/cycle.$$.sql
    trap 'rm -f "$SQL_FILE" "$BASE/cycle.lock/pid"; rmdir "$BASE/cycle.lock" 2>/dev/null || true' EXIT
    # SQL goes through stdin; keys never appear in process arguments or console logs.
    cat > "$SQL_FILE" <<SQL
.bail on
.timeout 5000
.output /dev/null
PRAGMA cipher_log=OFF;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA cipher_default_compatibility=3;
PRAGMA cipher_default_page_size=1024;
PRAGMA cipher_default_kdf_iter=4000;
PRAGMA cipher_default_use_hmac=OFF;
ATTACH DATABASE 'file:$SOURCE_DB?mode=ro' AS source KEY '$DB_KEY';
PRAGMA source.cipher_page_size=1024;
PRAGMA source.kdf_iter=4000;
PRAGMA source.cipher_use_hmac=OFF;
CREATE TABLE IF NOT EXISTS collection_state (
 account_id TEXT PRIMARY KEY, last_msg_id INTEGER NOT NULL,
 source_count INTEGER NOT NULL, last_success INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 account_id TEXT NOT NULL, msg_id INTEGER NOT NULL, server_id TEXT,
 type INTEGER, status INTEGER, is_send INTEGER, is_show_timer INTEGER, created_at INTEGER,
 talker TEXT, content TEXT, img_path TEXT, reserved TEXT, lvbuffer BLOB,
 trans_content TEXT, trans_brand_wording TEXT, talker_id INTEGER,
 biz_client_msg_id TEXT, biz_chat_id INTEGER, biz_chat_user_id TEXT,
 msg_seq INTEGER, flag INTEGER, solitaire_fold_info BLOB, history_id TEXT,
 collected_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(account_id,msg_id)
);
CREATE INDEX IF NOT EXISTS messages_talker_time ON messages(account_id,talker,created_at,msg_id);
CREATE INDEX IF NOT EXISTS messages_type_time ON messages(type,created_at DESC,msg_id DESC);
CREATE INDEX IF NOT EXISTS messages_server_id ON messages(account_id,server_id);
CREATE TABLE IF NOT EXISTS contacts (
 account_id TEXT NOT NULL, username TEXT NOT NULL, nickname TEXT,
 remark TEXT, type INTEGER, PRIMARY KEY(account_id,username)
);
BEGIN;
CREATE TEMP TABLE source_state AS SELECT count(*) AS n,coalesce(max(msgId),0) AS max_id FROM source.message;
CREATE TEMP TABLE batch AS SELECT b.* FROM source.message b WHERE b.msgId >= (
 SELECT CASE WHEN $FULL_RESCAN=1 OR s.n < coalesce(p.source_count,0) OR s.max_id < coalesce(p.last_msg_id,0)
 THEN 0 ELSE max(0,coalesce(p.last_msg_id,0)-50) END
 FROM source_state s LEFT JOIN collection_state p ON p.account_id='$ACCOUNT_ID'
) OR (
 (SELECT s.n > coalesce(p.source_count,0) FROM source_state s
  LEFT JOIN collection_state p ON p.account_id='$ACCOUNT_ID')
 AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.account_id='$ACCOUNT_ID' AND m.msg_id=b.msgId)
);
CREATE TEMP TABLE stats AS SELECT count(*) AS new_messages FROM batch b
 WHERE NOT EXISTS(SELECT 1 FROM messages m WHERE m.account_id='$ACCOUNT_ID' AND m.msg_id=b.msgId);
INSERT INTO messages(account_id,msg_id,server_id,type,status,is_send,is_show_timer,created_at,talker,content,
 img_path,reserved,lvbuffer,trans_content,trans_brand_wording,talker_id,biz_client_msg_id,
 biz_chat_id,biz_chat_user_id,msg_seq,flag,solitaire_fold_info,history_id,collected_at,updated_at)
 SELECT '$ACCOUNT_ID',msgId,CAST(msgSvrId AS TEXT),type,status,isSend,isShowTimer,createTime,talker,content,
 imgPath,reserved,lvbuffer,transContent,transBrandWording,talkerId,bizClientMsgId,
 bizChatId,bizChatUserId,msgSeq,flag,solitaireFoldInfo,historyId,unixepoch(),unixepoch()
 FROM batch WHERE 1
 ON CONFLICT(account_id,msg_id) DO UPDATE SET
 server_id=excluded.server_id,type=excluded.type,status=excluded.status,is_send=excluded.is_send,is_show_timer=excluded.is_show_timer,
 created_at=excluded.created_at,talker=excluded.talker,content=excluded.content,img_path=excluded.img_path,
 reserved=excluded.reserved,lvbuffer=excluded.lvbuffer,trans_content=excluded.trans_content,
 trans_brand_wording=excluded.trans_brand_wording,talker_id=excluded.talker_id,
 biz_client_msg_id=excluded.biz_client_msg_id,biz_chat_id=excluded.biz_chat_id,
 biz_chat_user_id=excluded.biz_chat_user_id,msg_seq=excluded.msg_seq,flag=excluded.flag,
 solitaire_fold_info=excluded.solitaire_fold_info,history_id=excluded.history_id,updated_at=unixepoch()
 WHERE messages.server_id IS NOT excluded.server_id OR messages.type IS NOT excluded.type
 OR messages.status IS NOT excluded.status OR messages.is_send IS NOT excluded.is_send OR messages.is_show_timer IS NOT excluded.is_show_timer
 OR messages.created_at IS NOT excluded.created_at OR messages.talker IS NOT excluded.talker
 OR messages.content IS NOT excluded.content OR messages.img_path IS NOT excluded.img_path
 OR messages.reserved IS NOT excluded.reserved OR messages.lvbuffer IS NOT excluded.lvbuffer
 OR messages.trans_content IS NOT excluded.trans_content OR messages.trans_brand_wording IS NOT excluded.trans_brand_wording
 OR messages.talker_id IS NOT excluded.talker_id OR messages.biz_client_msg_id IS NOT excluded.biz_client_msg_id
 OR messages.biz_chat_id IS NOT excluded.biz_chat_id OR messages.biz_chat_user_id IS NOT excluded.biz_chat_user_id
 OR messages.msg_seq IS NOT excluded.msg_seq OR messages.flag IS NOT excluded.flag
 OR messages.solitaire_fold_info IS NOT excluded.solitaire_fold_info OR messages.history_id IS NOT excluded.history_id;
ALTER TABLE stats ADD COLUMN changed_messages INTEGER;
UPDATE stats SET changed_messages=changes();
INSERT INTO contacts SELECT '$ACCOUNT_ID',username,nickname,conRemark,type FROM source.rcontact WHERE 1
 ON CONFLICT(account_id,username) DO UPDATE SET nickname=excluded.nickname,remark=excluded.remark,type=excluded.type
 WHERE contacts.nickname IS NOT excluded.nickname OR contacts.remark IS NOT excluded.remark OR contacts.type IS NOT excluded.type;
INSERT INTO collection_state SELECT '$ACCOUNT_ID',max_id,n,unixepoch() FROM source_state WHERE 1
 ON CONFLICT(account_id) DO UPDATE SET last_msg_id=excluded.last_msg_id,source_count=excluded.source_count,last_success=excluded.last_success;
COMMIT;
.output stdout
SELECT json_object('ok',json('true'),'new_messages',new_messages,'changed_messages',changed_messages,
 'source_messages',(SELECT n FROM source_state),'collected_messages',(SELECT count(*) FROM messages),
 'conversations',(SELECT count(DISTINCT talker) FROM messages),'contacts',(SELECT count(*) FROM contacts),
 'last_success',(SELECT last_success FROM collection_state WHERE account_id='$ACCOUNT_ID')) FROM stats;
SQL
    if "$SQLCIPHER" -batch -bail "$BASE/messages.db" < "$SQL_FILE" > "$BASE/status.next" 2> "$BASE/last-error.log"; then
        mv "$BASE/status.next" "$BASE/status.json"
        chmod 600 "$BASE/messages.db" "$BASE/status.json"
        cat "$BASE/status.json"
        # Explicit signature-protected metadata signal. No message text or key crosses IPC.
        if ! grep -q '"new_messages":0,"changed_messages":0' "$BASE/status.json"; then
            /system/bin/am broadcast --user 0 -a com.mubai.infoinbox.COLLECTED \
                -n com.mubai.infoinbox/.CollectorReceiver >/dev/null 2>&1 || true
        fi
    else
        rm -f "$BASE/status.next"
        die 'Collection failed; see the private last-error.log.'
    fi
}

case "${1:-status}" in
 once) collect_once 0 ;;
 rescan) collect_once 1 ;;
 watch)
    trap 'rm -f "$BASE/watch.pid"; exit 0' TERM INT
    printf '%s\n' "$$" > "$BASE/watch.pid"
    while :; do
        if /system/bin/sh "$SELF" once >/dev/null 2>"$BASE/watch-error.log"; then
            date +%s > "$BASE/watch.heartbeat"
        fi
        sleep 10
    done
    ;;
 start)
    acquire_lock start start
    trap 'rm -f "$BASE/start.lock/pid"; rmdir "$BASE/start.lock" 2>/dev/null || true' EXIT
    if running; then printf '%s\n' 'Already running.'; exit 0; fi
    setsid /system/bin/sh "$SELF" watch </dev/null > "$BASE/watch.log" 2>&1 &
    sleep 1
    running || die 'Background process did not start.'
    printf '%s\n' 'Started: phone-local collection every 10 seconds.'
    ;;
 stop)
    if running; then
        kill -TERM "$PID"
        n=0
        while running && [ "$n" -lt 15 ]; do sleep 1; n=$((n+1)); done
        running && die 'Process is still stopping.'
    fi
    n=0
    while [ -d "$BASE/cycle.lock" ] && [ "$n" -lt 10 ]; do sleep 1; n=$((n+1)); done
    [ ! -d "$BASE/cycle.lock" ] || die 'A collection is still in progress.'
    printf '%s\n' 'Stopped.'
    ;;
 status)
    if running; then printf '%s\n' 'running=true'; else printf '%s\n' 'running=false'; fi
    if running && [ -f "$BASE/watch.heartbeat" ]; then
        NOW=$(date +%s); LAST=$(cat "$BASE/watch.heartbeat")
        case "$LAST" in ''|*[!0-9]*) printf '%s\n' 'healthy=false' ;; *)
            AGE=$((NOW-LAST))
            if [ "$AGE" -ge 0 ] && [ "$AGE" -le 180 ]; then printf '%s\n' 'healthy=true'; else printf '%s\n' 'healthy=false'; fi ;;
        esac
    else printf '%s\n' 'healthy=false'; fi
    [ ! -f "$BASE/status.json" ] || cat "$BASE/status.json"
    [ ! -s "$BASE/last-error.log" ] || printf '%s\n' 'last_cycle_error=true'
    [ ! -s "$BASE/watch-error.log" ] || printf '%s\n' 'last_cycle_error=true'
    ;;
 export)
    [ -f "$BASE/messages.db" ] || die 'No collection database.'
    OUT=$BASE/export.json
    "$SQLCIPHER" -readonly -batch -bail -json "$BASE/messages.db" 'SELECT account_id,msg_id,server_id,type,is_send,created_at,talker,content,img_path,trans_content FROM messages ORDER BY created_at,msg_id;' > "$OUT.next"
    mv "$OUT.next" "$OUT"
    chmod 600 "$OUT"
    printf '%s\n' "Exported to $OUT"
    ;;
 *) die 'Usage: collector.sh once|rescan|start|stop|status|export' ;;
esac
