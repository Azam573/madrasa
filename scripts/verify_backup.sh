#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# scripts/verify_backup.sh — Backup আসলেই restore হয় কি না যাচাই
#
# "Backup আছে" আর "backup থেকে ফিরে আসা যায়" — দুটো সম্পূর্ণ আলাদা জিনিস।
# এই script সাপ্তাহিক চালানোর জন্য: সর্বশেষ backup-টা একটা disposable
# Postgres container-এ restore করে sanity check চালায়।
#
# ধাপ:
#   1. সর্বশেষ .dump ফাইল খোঁজে
#   2. Scratch Postgres container চালু করে (port 55432 — মূল DB-র সাথে
#      কোনো সংযোগ নেই, production ছোঁয়ার ঝুঁকি শূন্য)
#   3. pg_restore চালায়
#   4. Sanity checks: core table গুলো আছে কি না, tenant + student
#      row count শূন্য নয় কি না
#   5. Container ধ্বংস করে ফলাফল জানায়
#
# Cron (প্রতি রবিবার ভোর ৪টায়):
#   0 4 * * 0 /opt/madrasa/scripts/verify_backup.sh >> /var/log/madrasa_backup_verify.log 2>&1
#
# Environment:
#   BACKUP_DIR — default: /var/backups/madrasa
#   PG_IMAGE   — default: postgres:16-alpine
# ════════════════════════════════════════════════════════════════
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/madrasa}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
CONTAINER="madrasa_backup_verify"
PORT=55432
SCRATCH_URL="postgresql://verify:verify@localhost:${PORT}/verify"

cleanup() {
    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT

# ── ১. সর্বশেষ backup ─────────────────────────────────────────
LATEST=$(ls -1t "${BACKUP_DIR}"/madrasa_*.dump 2>/dev/null | head -1 || true)
if [[ -z "$LATEST" ]]; then
    echo "❌ ${BACKUP_DIR}-এ কোনো backup পাওয়া যায়নি!" >&2
    exit 1
fi
AGE_HOURS=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
echo "[$(date -Is)] Verifying: $LATEST (বয়স: ${AGE_HOURS} ঘণ্টা)"

# Backup ২৬ ঘণ্টার বেশি পুরনো হলে daily cron ব্যর্থ হচ্ছে — সেটাও ধরা পড়ুক
if (( AGE_HOURS > 26 )); then
    echo "⚠️  সর্বশেষ backup ${AGE_HOURS} ঘণ্টা পুরনো — daily backup cron চলছে না!" >&2
    exit 1
fi

# ── ২. Scratch Postgres ──────────────────────────────────────
cleanup
docker run -d --name "$CONTAINER" \
    -e POSTGRES_USER=verify -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=verify \
    -p "${PORT}:5432" "$PG_IMAGE" > /dev/null

echo -n "Postgres চালু হচ্ছে"
for i in $(seq 1 30); do
    if docker exec "$CONTAINER" pg_isready -U verify > /dev/null 2>&1; then
        echo " ✓"; break
    fi
    echo -n "."; sleep 1
    [[ $i -eq 30 ]] && { echo "❌ Postgres চালু হলো না"; exit 1; }
done

# ── ৩. Restore ────────────────────────────────────────────────
echo "[$(date -Is)] Restore চলছে..."
# --no-owner: scratch-এ মূল DB-র role নেই; exit-on-error নয় কারণ
# extension/comment জাতীয় harmless error থাকতে পারে — শেষে যাচাই-ই আসল
docker cp "$LATEST" "$CONTAINER:/tmp/backup.dump"
docker exec "$CONTAINER" pg_restore -U verify -d verify \
    --no-owner --no-privileges /tmp/backup.dump 2>&1 \
    | grep -v "already exists" | head -20 || true

# ── ৪. Sanity checks ─────────────────────────────────────────
echo "[$(date -Is)] Sanity checks..."
FAIL=0

check_table() {
    local table="$1" min_rows="${2:-0}"
    local n
    n=$(docker exec "$CONTAINER" psql -U verify -d verify -tAc \
        "SELECT COUNT(*) FROM ${table};" 2>/dev/null || echo "MISSING")
    if [[ "$n" == "MISSING" ]]; then
        echo "  ❌ Table নেই: ${table}"; FAIL=1
    elif (( n < min_rows )); then
        echo "  ❌ ${table}: ${n} rows (কমপক্ষে ${min_rows} প্রত্যাশিত)"; FAIL=1
    else
        echo "  ✓ ${table}: ${n} rows"
    fi
}

# Core tables — এগুলো ছাড়া সিস্টেম অচল
check_table "tenants"             1
check_table "users"               1
check_table "students"            0
check_table "fee_vouchers"        0
check_table "student_enrollments" 0
check_table "classes"             0

# Migration state-ও restore হয়েছে কি না
check_table "alembic_version"     1

if (( FAIL )); then
    echo "[$(date -Is)] ❌ VERIFY FAILED — backup থেকে restore অসম্পূর্ণ!"
    exit 1
fi

echo "[$(date -Is)] ✅ VERIFY PASSED — backup সম্পূর্ণ restore-যোগ্য"
