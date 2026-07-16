#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# scripts/backup_db.sh — সম্পূর্ণ ডাটাবেজের automated backup
#
# যা করে:
#   1. pg_dump (custom format — compressed, selective restore সম্ভব)
#   2. gzip integrity check
#   3. পুরনো backup rotation (default: শেষ ১৪টি রাখে)
#   4. ব্যর্থ হলে non-zero exit — cron mail/alert-এ ধরা পড়বে
#
# Cron setup (প্রতিদিন রাত ৩টায়):
#   crontab -e
#   0 3 * * * /opt/madrasa/scripts/backup_db.sh >> /var/log/madrasa_backup.log 2>&1
#
# Environment:
#   DATABASE_URL     — postgres connection string (আবশ্যক)
#   BACKUP_DIR       — default: /var/backups/madrasa
#   BACKUP_KEEP      — কয়টি backup রাখা হবে, default: 14
#
# ⚠️ Off-site copy: এই script লোকাল ডিস্কে রাখে। সার্ভার নষ্ট হলে
# backup-ও যাবে — তাই rclone/rsync দিয়ে অন্য মেশিন বা object storage-এ
# সিঙ্ক করা অত্যন্ত জরুরি। উদাহরণ (cron-এ backup-এর ৩০ মিনিট পরে):
#   30 3 * * * rclone sync /var/backups/madrasa remote:madrasa-backups
# ════════════════════════════════════════════════════════════════
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/madrasa}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTFILE="${BACKUP_DIR}/madrasa_${TIMESTAMP}.dump"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "❌ DATABASE_URL সেট করা নেই।" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "[$(date -Is)] Backup শুরু → $OUTFILE"

# Custom format (-Fc): compressed + pg_restore দিয়ে selective restore সম্ভব
pg_dump "$DATABASE_URL" -Fc --no-owner --no-privileges -f "$OUTFILE"

# Integrity check: pg_restore -l দিয়ে TOC পড়া যায় কি না
if ! pg_restore -l "$OUTFILE" > /dev/null 2>&1; then
    echo "❌ Backup ফাইল corrupt — pg_restore TOC পড়তে পারছে না!" >&2
    rm -f "$OUTFILE"
    exit 1
fi

SIZE=$(du -h "$OUTFILE" | cut -f1)
echo "[$(date -Is)] ✅ Backup সফল (${SIZE})"

# Rotation — নতুনগুলো রেখে বাকি মুছে ফেলা
ls -1t "${BACKUP_DIR}"/madrasa_*.dump 2>/dev/null \
    | tail -n +$((BACKUP_KEEP + 1)) \
    | xargs -r rm -f

COUNT=$(ls -1 "${BACKUP_DIR}"/madrasa_*.dump 2>/dev/null | wc -l)
echo "[$(date -Is)] Rotation সম্পন্ন — ${COUNT}টি backup সংরক্ষিত"
