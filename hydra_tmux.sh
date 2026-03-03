#!/bin/bash
# ================================================
# HYDRA TRADING SYSTEM - PERSISTENT TMUX LAUNCHER
# ================================================
# This script runs HYDRA inside a tmux session that survives:
#   - Terminal close
#   - Mac sleep/wake
#   - SSH disconnects
#
# Usage:
#   ./hydra_tmux.sh          Start or reattach to HYDRA
#   ./hydra_tmux.sh stop     Gracefully stop HYDRA
#   ./hydra_tmux.sh status   Check if HYDRA is running
#   ./hydra_tmux.sh logs     Tail live logs
#   ./hydra_tmux.sh restart  Stop then start
#
# To detach (leave running): Ctrl+B then D
# To reattach later:         tmux attach -t hydra
# ================================================

SESSION_NAME="hydra"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

case "${1:-start}" in
    start)
        # Check if session already exists
        if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
            echo "╔══════════════════════════════════════════╗"
            echo "║  HYDRA is already running in tmux!       ║"
            echo "║  Reattaching...                          ║"
            echo "╚══════════════════════════════════════════╝"
            tmux attach -t "$SESSION_NAME"
            exit 0
        fi

        echo "╔══════════════════════════════════════════╗"
        echo "║    HYDRA TRADING SYSTEM v2.0             ║"
        echo "║    Starting in persistent tmux session    ║"
        echo "║    'Survive. Compound. Dominate.'        ║"
        echo "╚══════════════════════════════════════════╝"
        echo ""

        # Create tmux session and run HYDRA
        tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_DIR" \
            "source '$VENV_DIR/bin/activate' && python3 main.py; echo ''; echo 'HYDRA stopped. Press Enter to close.'; read"

        echo "[OK] HYDRA started in tmux session '$SESSION_NAME'"
        echo ""
        echo "Commands:"
        echo "  Attach to session:   tmux attach -t $SESSION_NAME"
        echo "  Detach (keep alive): Ctrl+B then D"
        echo "  Check status:        ./hydra_tmux.sh status"
        echo "  View live logs:      ./hydra_tmux.sh logs"
        echo "  Stop gracefully:     ./hydra_tmux.sh stop"
        echo ""

        # Auto-attach
        sleep 1
        tmux attach -t "$SESSION_NAME"
        ;;

    stop)
        if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
            echo "HYDRA is not running."
            exit 0
        fi

        echo "Sending graceful shutdown signal to HYDRA..."
        # Send Ctrl+C to the tmux session (SIGINT → graceful shutdown)
        tmux send-keys -t "$SESSION_NAME" C-c
        echo "[OK] Shutdown signal sent. HYDRA will save state and exit."
        echo "     Check with: ./hydra_tmux.sh status"
        ;;

    restart)
        echo "Restarting HYDRA..."
        "$0" stop
        echo "Waiting 10s for clean shutdown..."
        sleep 10
        "$0" start
        ;;

    status)
        if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
            echo "╔══════════════════════════════════════════╗"
            echo "║  HYDRA: RUNNING in tmux                  ║"
            echo "╚══════════════════════════════════════════╝"

            # Check if python process is alive inside the session
            PID=$(tmux list-panes -t "$SESSION_NAME" -F '#{pane_pid}' 2>/dev/null | head -1)
            if [ -n "$PID" ]; then
                CHILD=$(pgrep -P "$PID" -f "python3 main.py" 2>/dev/null)
                if [ -n "$CHILD" ]; then
                    echo "  PID: $CHILD"
                    # Get uptime
                    ELAPSED=$(ps -o etime= -p "$CHILD" 2>/dev/null | xargs)
                    echo "  Uptime: $ELAPSED"
                else
                    echo "  WARNING: tmux session exists but python not running"
                    echo "  The bot may have crashed. Check: tmux attach -t $SESSION_NAME"
                fi
            fi

            # Show last log line
            LATEST_LOG=$(ls -t "$PROJECT_DIR/logs/hydra_"*.log 2>/dev/null | head -1)
            if [ -n "$LATEST_LOG" ]; then
                echo "  Last log: $(tail -1 "$LATEST_LOG" | cut -c1-80)"
            fi
        else
            echo "╔══════════════════════════════════════════╗"
            echo "║  HYDRA: NOT RUNNING                      ║"
            echo "╚══════════════════════════════════════════╝"
            echo "  Start with: ./hydra_tmux.sh"
        fi
        ;;

    logs)
        LATEST_LOG=$(ls -t "$PROJECT_DIR/logs/hydra_"*.log 2>/dev/null | head -1)
        if [ -n "$LATEST_LOG" ]; then
            echo "Tailing: $LATEST_LOG"
            echo "Press Ctrl+C to stop watching"
            echo "---"
            tail -f "$LATEST_LOG"
        else
            echo "No log files found."
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
