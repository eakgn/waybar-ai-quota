"""Modern Catppuccin Mocha / Adwaita karanlık tema CSS tanımları."""

from __future__ import annotations

DASHBOARD_CSS = """
window.an-window {
    background-color: rgba(24, 24, 37, 0.97);
    color: #cdd6f4;
    border-radius: 16px;
    border: 1px solid rgba(137, 180, 250, 0.22);
}

.an-header {
    padding: 14px 16px 10px 16px;
    border-bottom: 1px solid rgba(205, 214, 244, 0.08);
}

.an-title {
    font-size: 15px;
    font-weight: 700;
    color: #cdd6f4;
}

.an-subtitle {
    font-size: 11px;
    color: #a6adc8;
}

.an-section-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
    color: #9399b2;
    margin-top: 6px;
    margin-bottom: 4px;
}

.an-card {
    background-color: #1e1e2e;
    border: 1px solid rgba(205, 214, 244, 0.08);
    border-radius: 12px;
    padding: 12px 14px;
}

.an-card-waiting {
    border-color: rgba(249, 226, 175, 0.65);
    background-color: rgba(249, 226, 175, 0.07);
}

.an-card-running {
    border-color: rgba(137, 180, 250, 0.45);
}

.an-card-done {
    border-color: rgba(166, 227, 161, 0.45);
}

.an-card-error {
    border-color: rgba(243, 139, 168, 0.65);
    background-color: rgba(243, 139, 168, 0.07);
}

.an-session-title {
    font-size: 13px;
    font-weight: 700;
    color: #cdd6f4;
}

.an-meta {
    font-size: 11px;
    color: #a6adc8;
}

.an-message {
    font-size: 11.5px;
    color: #f9e2af;
    margin-top: 4px;
}

.an-badge {
    font-size: 10.5px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
}

.an-badge-waiting {
    background-color: rgba(249, 226, 175, 0.22);
    color: #f9e2af;
}

.an-badge-running {
    background-color: rgba(137, 180, 250, 0.22);
    color: #89b4fa;
}

.an-badge-done {
    background-color: rgba(166, 227, 161, 0.22);
    color: #a6e3a1;
}

.an-badge-error {
    background-color: rgba(243, 139, 168, 0.22);
    color: #f38ba8;
}

.an-badge-idle {
    background-color: rgba(147, 153, 178, 0.18);
    color: #bac2de;
}

.an-harness-pill {
    font-size: 10px;
    font-weight: 600;
    padding: 1px 7px;
    border-radius: 6px;
    background-color: rgba(180, 190, 254, 0.14);
    color: #b4befe;
}

.an-subagent-tree {
    margin-top: 8px;
    margin-left: 8px;
    padding-left: 12px;
    border-left: 2px solid rgba(137, 180, 250, 0.28);
}

.an-subagent-row {
    background-color: rgba(49, 50, 68, 0.55);
    border-radius: 8px;
    padding: 6px 10px;
}

.an-subagent-label {
    font-size: 11.5px;
    font-weight: 600;
    color: #bac2de;
}

.an-progress trough {
    min-height: 7px;
    border-radius: 999px;
    background-color: #313244;
}

.an-progress progress {
    min-height: 7px;
    border-radius: 999px;
}

.an-bar-green progress { background-color: #a6e3a1; }
.an-bar-yellow progress { background-color: #f9e2af; }
.an-bar-red progress { background-color: #f38ba8; }
.an-bar-blue progress { background-color: #89b4fa; }

.an-icon-btn {
    min-width: 28px;
    min-height: 28px;
    padding: 4px 8px;
    border-radius: 8px;
    background-color: rgba(49, 50, 68, 0.8);
    color: #cdd6f4;
}

.an-icon-btn:hover {
    background-color: rgba(69, 71, 90, 0.95);
}

.an-empty {
    padding: 18px 12px;
    color: #9399b2;
    font-size: 12px;
}
"""
