#!/bin/bash
# fortress-start.sh — Start or reconnect to the persistent Fortress Claude Code session
#
# WiFi drop? SSH closed? Just run this script again and you're back exactly where you left off.
# The tmux session keeps Claude Code running on the server even when your connection drops.
#
# Usage:
#   bash /opt/fortress/fortress-start.sh
#
# From the wetty web terminal (http://46.225.110.190:3000), just type:
#   fortress
#
# Or set up the alias permanently (already done if you ran this once):
#   alias fortress='bash /opt/fortress/fortress-start.sh'

SESSION="fortress"

cd /opt/fortress || exit 1

# Check if a fortress tmux session already exists
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo ""
    echo "  Reconnecting to existing Fortress session..."
    echo "  (Claude Code is still running — picking up where you left off)"
    echo ""
    tmux attach-session -t "$SESSION"
else
    echo ""
    echo "  Starting new Fortress Claude Code session..."
    echo ""
    # Create new detached session, then attach
    tmux new-session -d -s "$SESSION" -x 220 -y 50
    # Set a useful status bar
    tmux set-option -t "$SESSION" status-right "#[fg=green]Fortress VPS#[default] | %H:%M UTC"
    tmux set-option -t "$SESSION" status-right-length 40
    # Start Claude Code in the session.
    # --continue resumes the MOST RECENT conversation (same chat, full context)
    # instead of starting a blank one — so even a VPS reboot doesn't lose the chat.
    # Falls back to a fresh session only if there is nothing to resume.
    # cd /root: conversations are stored per-directory; the Fortress chat lives
    # in the /root project (memory at /root/.claude/projects/-root/).
    tmux send-keys -t "$SESSION" "cd /root && (claude --continue || claude)" Enter
    tmux attach-session -t "$SESSION"
fi
