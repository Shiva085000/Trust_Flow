#!/usr/bin/env bash
# scripts/demo_prep.sh
set -e

echo "=== 1. Generating Test PDFs ==="
# Determine python executable based on OS / availability
PYTHON_CMD="python"
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
fi
$PYTHON_CMD scripts/generate_test_docs.py

echo ""
echo "=== 2. LLM Preparation ==="
echo "If you want to run a local vLLM, open a separate terminal and run:"
echo "  vllm serve Qwen/Qwen2.5-7B-Instruct --port 8001"
echo ""
echo "[Fallback] If you are not running vLLM, ensure your backend/.env has your GROQ_API_KEY configured."

echo ""
echo "=== 3. Starting Phoenix ==="
echo "Starting Arize Phoenix in the background... (Available at http://localhost:6006)"
$PYTHON_CMD -m phoenix.server.main serve > phoenix.log 2>&1 &
PHOENIX_PID=$!

echo ""
echo "=== 4. Starting FastAPI Backend ==="
cd backend
echo "Starting Uvicorn backend... (Available at http://localhost:8000)"
# Since on Windows dot-sourcing in bash has different pathing, rely on the active env if activated, 
# otherwise assume dependencies are globally installed for demo script.
$PYTHON_CMD -m uvicorn main:app --reload --port 8000 > ../backend.log 2>&1 &
FASTAPI_PID=$!
cd ..

echo ""
echo "=== 5. Starting Frontend (Reminder) ==="
echo "Note: This script does not start the React frontend. Please run:"
echo "  cd frontend && npm run dev"
echo "in a separate terminal to kick off Vite."

echo ""
echo "Opening browser to http://localhost:5173/declaration ..."
sleep 3  # short wait so local dev server has time if you just started it
if which xdg-open > /dev/null; then
  xdg-open "http://localhost:5173/declaration"
elif which open > /dev/null; then
  open "http://localhost:5173/declaration"
elif which start > /dev/null; then
  start "http://localhost:5173/declaration"
else
  echo "(Please open http://localhost:5173/declaration manually in your browser)"
fi

echo ""
echo "✅ Demo ready — upload weight_conflict_set/ to trigger HITL flow"
echo "Press Ctrl+C to terminate Phoenix and FastAPI."

wait
