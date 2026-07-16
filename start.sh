#!/bin/bash
# start.sh — সব সার্ভিস একসাথে চালু (Development)

echo "🕌 Smart Madrasa ERP v9.0 Starting..."
echo ""

# Check Redis
if command -v redis-cli &> /dev/null && redis-cli ping &> /dev/null; then
    echo "✅ Redis is running"
else
    echo "⚠️  Redis চালু নেই। চালু করুন: redis-server"
    echo "   অথবা: docker run -d -p 6379:6379 redis:alpine"
fi

echo ""

# FastAPI (background)
echo "🚀 FastAPI API server → http://localhost:8000"
echo "   Docs → http://localhost:8000/api/docs"
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2 --log-level warning &
API_PID=$!

sleep 2

# Celery Worker (background)
if python3 -c "import celery, redis" 2>/dev/null; then
    echo "⚙️  Celery Worker → 4 concurrent tasks"
    celery -A workers.celery_app worker --loglevel=warning --concurrency=4 &
    CELERY_PID=$!

    echo "📅 Celery Beat → scheduled tasks"
    celery -A workers.celery_app beat --loglevel=warning &
    BEAT_PID=$!
else
    echo "⚠️  Celery/Redis না থাকায় background jobs disabled"
fi

sleep 1

# Streamlit (foreground)
echo ""
echo "🎨 Streamlit UI → http://localhost:8501"
echo "   লগইন: admin / admin123"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false

# Cleanup on exit
trap "kill $API_PID $CELERY_PID $BEAT_PID 2>/dev/null" EXIT
