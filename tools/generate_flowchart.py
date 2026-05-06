"""
generate_flowchart.py
---------------------
Generates the HMIS Analytics Bot product flowchart as a high-resolution PNG.
Run:   python generate_flowchart.py
Output: hmis_bot_flowchart.png
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
from matplotlib.patches import FancyBboxPatch

# ── Canvas ────────────────────────────────────────────────────────────────────
FIG_W, FIG_H = 16, 30
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=150)
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor("#FAFAFA")
ax.set_facecolor("#FAFAFA")

# ── Palette ───────────────────────────────────────────────────────────────────
C_TG  = "#1A6FA8"   # Telegram / user
C_AI  = "#C0550A"   # OpenAI
C_DB  = "#1E8449"   # BigQuery
C_OUT = "#7D3C98"   # Output
C_LOG = "#5D6D7E"   # Audit logging
C_DEC = "#B7770D"   # Decisions
C_ERR = "#943126"   # Error terminals
C_ALT = "#636E72"   # Alternative path

DARK  = "#1C2833"
WHITE = "#FFFFFF"

# ── Column x-centres ──────────────────────────────────────────────────────────
XM = 8.0    # main flow
XR = 13.2   # right side-branches
XL = 2.8    # left side-branches


# ── Drawing helpers ───────────────────────────────────────────────────────────

def rbox(cx, cy, text, color, w=5.8, h=0.78, fs=8.5):
    """Rounded rectangle."""
    p = FancyBboxPatch(
        (cx - w / 2, cy - h / 2), w, h,
        boxstyle="round,pad=0.13",
        facecolor=color, edgecolor=WHITE, linewidth=1.8, zorder=3,
    )
    ax.add_patch(p)
    ax.text(cx, cy, text, ha="center", va="center",
            fontsize=fs, color=WHITE, fontweight="bold",
            zorder=4, multialignment="center", linespacing=1.35)
    return cy - h / 2   # returns bottom y


def sbox(cx, cy, text, color, w=3.8, h=0.72, fs=7.8):
    """Smaller side box."""
    p = FancyBboxPatch(
        (cx - w / 2, cy - h / 2), w, h,
        boxstyle="round,pad=0.11",
        facecolor=color, edgecolor=WHITE, linewidth=1.4, zorder=3,
    )
    ax.add_patch(p)
    ax.text(cx, cy, text, ha="center", va="center",
            fontsize=fs, color=WHITE, fontweight="bold",
            zorder=4, multialignment="center", linespacing=1.3)


def diamond(cx, cy, text, w=5.0, h=1.15):
    """Decision diamond; returns edge midpoints."""
    pts = [(cx, cy + h / 2), (cx + w / 2, cy),
           (cx, cy - h / 2), (cx - w / 2, cy)]
    poly = plt.Polygon(pts, facecolor=C_DEC, edgecolor=WHITE,
                        linewidth=1.8, zorder=3)
    ax.add_patch(poly)
    ax.text(cx, cy, text, ha="center", va="center",
            fontsize=8, color=WHITE, fontweight="bold",
            zorder=4, multialignment="center", linespacing=1.3)
    return {
        "top":    (cx,            cy + h / 2),
        "bottom": (cx,            cy - h / 2),
        "left":   (cx - w / 2,   cy),
        "right":  (cx + w / 2,   cy),
    }


def straight(x1, y1, x2, y2, label="", ls="right", c=DARK):
    """Straight arrow + optional label."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=c, lw=1.6), zorder=2)
    if label:
        xoff = 0.15 if ls == "right" else -0.15
        ax.text((x1+x2)/2 + xoff, (y1+y2)/2, label,
                fontsize=7.5, color=c, ha="left" if ls=="right" else "right",
                va="center", style="italic", fontweight="bold", zorder=5)


def elbow(x1, y1, x2, y2, label="", c=DARK, label_yoff=0.12):
    """Right-angle path: horizontal from (x1,y1) then vertical to (x2,y2)."""
    ax.plot([x1, x2], [y1, y1], color=c, lw=1.6, zorder=2)
    ax.annotate("", xy=(x2, y2), xytext=(x2, y1),
                arrowprops=dict(arrowstyle="->", color=c, lw=1.6), zorder=2)
    if label:
        ax.text((x1+x2)/2, y1 + label_yoff, label,
                fontsize=7.5, color=c, ha="center",
                style="italic", fontweight="bold", zorder=5)


def label_tag(x, y, text, c=DARK):
    """Small inline label (YES / NO etc.)."""
    ax.text(x, y, text, fontsize=7.5, color=c, fontweight="bold",
            ha="center", va="center", zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=c,
                       boxstyle="round,pad=0.13", linewidth=0.9, alpha=0.9))


def divider(y, label=""):
    """Thin horizontal rule for visual separation."""
    ax.plot([0.4, FIG_W - 0.4], [y, y], color="#D5D8DC", lw=0.8, zorder=1)
    if label:
        ax.text(0.6, y + 0.12, label, fontsize=7, color="#ABB2B9",
                ha="left", style="italic", zorder=2)


# ── Title block ───────────────────────────────────────────────────────────────
ax.text(XM, 29.45, "HMIS Analytics Bot",
        ha="center", va="center", fontsize=18, fontweight="bold", color=DARK)
ax.text(XM, 28.95, "Bihar Immunisation Programme  ·  Product Flow",
        ha="center", va="center", fontsize=10, color="#5D6D7E")
ax.plot([0.4, FIG_W - 0.4], [28.65, 28.65], color="#BDC3C7", lw=1.0)

# ── Legend ────────────────────────────────────────────────────────────────────
leg_items = [
    mlines.Line2D([], [], color=C_TG,  marker="s", ms=9, ls="None", label="Telegram (User)"),
    mlines.Line2D([], [], color=C_AI,  marker="s", ms=9, ls="None", label="OpenAI / AI"),
    mlines.Line2D([], [], color=C_DB,  marker="s", ms=9, ls="None", label="Google BigQuery"),
    mlines.Line2D([], [], color=C_OUT, marker="s", ms=9, ls="None", label="Output"),
    mlines.Line2D([], [], color=C_LOG, marker="s", ms=9, ls="None", label="Audit Logging"),
    mlines.Line2D([], [], color=C_DEC, marker="D", ms=9, ls="None", label="Decision"),
    mlines.Line2D([], [], color=C_ERR, marker="s", ms=9, ls="None", label="Error / End branch"),
    mlines.Line2D([], [], color=C_ALT, marker="s", ms=9, ls="None", label="Alternative path"),
]
ax.legend(handles=leg_items, loc="upper left", ncol=2, fontsize=7.8,
          framealpha=0.95, edgecolor="#BDC3C7", handlelength=1.2,
          bbox_to_anchor=(0.01, 0.975))


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN FLOW  (top → bottom, centre column XM)
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. User sends Telegram message ───────────────────────────────────────────
rbox(XM, 27.85, "① User sends a message\n(plain English or Hindi)", C_TG)
straight(XM, 27.46, XM, 26.73)

# ── 2. Extract geo entity ─────────────────────────────────────────────────────
rbox(XM, 26.42, "② Extract geographic entity\n(GPT-4o-mini)", C_AI)
straight(XM, 26.03, XM, 25.35)

# ── 3. Decision: location in message? ────────────────────────────────────────
d3 = diamond(XM, 24.77, "Location\nmentioned?")

# NO → right: use conversation history
elbow(d3["right"][0], d3["right"][1], XR, 23.50,
      label="NO", c=C_ALT)
sbox(XR, 23.15, "Use conversation\nhistory", C_ALT)
label_tag(d3["right"][0] + 0.35, d3["right"][1] + 0.15, "NO", c=C_ALT)

# YES → down
straight(*d3["bottom"], XM, 24.05)
label_tag(XM + 0.35, (d3["bottom"][1] + 24.05) / 2, "YES", c=C_DB)

# ── 4. BigQuery geo lookup ────────────────────────────────────────────────────
rbox(XM, 23.70, "③ BigQuery geo lookup\n(resolve name → numeric code)", C_DB)
straight(XM, 23.31, XM, 22.60)

# ── 5. Decision: match count ─────────────────────────────────────────────────
d5 = diamond(XM, 22.05, "Match\ncount?")

# 0 matches → left: not found
elbow(d5["left"][0], d5["left"][1], XL, 21.25,
      label="0  matches", c=C_ERR)
sbox(XL, 20.90, "\"Location not found\"\nerror reply → user", C_ERR, w=3.6)
label_tag(d5["left"][0] - 0.4, d5["left"][1] + 0.15, "0", c=C_ERR)

# 2+ matches → right: clarify
elbow(d5["right"][0], d5["right"][1], XR, 21.25,
      label="2+ matches", c=C_ALT)
sbox(XR, 20.90, "Ask user to clarify\n(numbered list)", C_ALT)
# loop-back arrow up (user replies → restarts)
ax.plot([XR + 1.8, XR + 1.8], [20.54, 27.85], color=C_ALT, lw=1.3,
        linestyle="dashed", zorder=1)
ax.annotate("", xy=(XM + 2.9, 27.85), xytext=(XR + 1.8, 27.85),
            arrowprops=dict(arrowstyle="->", color=C_ALT, lw=1.3,
                             linestyle="dashed"), zorder=1)
ax.text(XR + 2.05, 24.4, "User replies\n→ restart", fontsize=7,
        color=C_ALT, ha="left", style="italic", zorder=5)
label_tag(d5["right"][0] + 0.45, d5["right"][1] + 0.15, "2+", c=C_ALT)

# 1 match → down
straight(*d5["bottom"], XM, 21.28)
label_tag(XM + 0.4, (d5["bottom"][1] + 21.28) / 2, "1  match", c=C_DB)

# ── 6. Reconnect "use history" path ──────────────────────────────────────────
# Draw the line from XR, 22.78 (bottom of sbox) down to XM level
ax.plot([XR, XR], [22.78, 20.60], color=C_ALT, lw=1.3, linestyle="dashed", zorder=1)
ax.plot([XR, XM + 2.9], [20.60, 20.60], color=C_ALT, lw=1.3, linestyle="dashed", zorder=1)

divider(20.20, "── geo resolution complete ──")

# ── 7. Generate SQL ───────────────────────────────────────────────────────────
rbox(XM, 19.85, "④ Generate BigQuery SQL\n(GPT-4o  ·  all HMIS rules applied)", C_AI)
straight(XM, 19.46, XM, 18.78)

# ── 8. Decision: SQL generated or conversational? ────────────────────────────
d8 = diamond(XM, 18.20, "SQL needed?")

# NO → right: conversational reply
elbow(d8["right"][0], d8["right"][1], XR, 17.45, c=C_ALT)
sbox(XR, 17.10, "Conversational reply\n(GPT-4o-mini, from history)", C_ALT)
ax.plot([XR + 1.9, XR + 1.9], [16.73, 10.4], color=C_ALT, lw=1.3,
        linestyle="dashed", zorder=1)
ax.annotate("", xy=(XM + 2.9, 10.4), xytext=(XR + 1.9, 10.4),
            arrowprops=dict(arrowstyle="->", color=C_ALT, lw=1.3,
                             linestyle="dashed"), zorder=1)
label_tag(d8["right"][0] + 0.45, d8["right"][1] + 0.15, "NO", c=C_ALT)

# YES → down
straight(*d8["bottom"], XM, 17.48)
label_tag(XM + 0.38, (d8["bottom"][1] + 17.48) / 2, "YES", c=C_DB)

# ── 9. Run BigQuery query ─────────────────────────────────────────────────────
rbox(XM, 17.12, "⑤ Execute BigQuery query", C_DB)
straight(XM, 16.73, XM, 16.02)

# ── 10. Decision: 400 error? ─────────────────────────────────────────────────
d10 = diamond(XM, 15.45, "400 error?")

# YES → right: auto-fix and retry
elbow(d10["right"][0], d10["right"][1], XR, 14.72, c=C_AI)
sbox(XR, 14.37, "fix_sql() →\nauto-retry (max 3×)", C_AI)
# retry loop back up to Execute
ax.plot([XR - 1.9, XR - 1.9], [13.99, 17.12], color=C_AI, lw=1.3,
        linestyle="dashed", zorder=1)
ax.plot([XR - 1.9, XM + 2.9], [17.12, 17.12], color=C_AI, lw=1.3,
        linestyle="dashed", zorder=1)
ax.text(XR - 2.35, 15.6, "retry", fontsize=7.5, color=C_AI,
        style="italic", fontweight="bold", rotation=90, zorder=5)
label_tag(d10["right"][0] + 0.45, d10["right"][1] + 0.15, "YES", c=C_AI)

# after 3 attempts → left: error reply
elbow(d10["left"][0], d10["left"][1], XL, 14.72, c=C_ERR)
sbox(XL, 14.37, "Error reply\n(after 3 attempts)", C_ERR, w=3.6)
label_tag(d10["left"][0] - 0.5, d10["left"][1] + 0.15, "3× fail", c=C_ERR)

# NO → down
straight(*d10["bottom"], XM, 14.68)
label_tag(XM + 0.35, (d10["bottom"][1] + 14.68) / 2, "NO", c=C_DB)

# ── 11. Decision: results found? ─────────────────────────────────────────────
d11 = diamond(XM, 14.10, "Results\nfound?")

# NO → left: no data message
elbow(d11["left"][0], d11["left"][1], XL, 13.35, c=C_ERR)
sbox(XL, 13.00, "\"No data found\"\nmessage → user", C_ERR, w=3.6)
label_tag(d11["left"][0] - 0.4, d11["left"][1] + 0.15, "NO", c=C_ERR)

# YES → down
straight(*d11["bottom"], XM, 13.33)
label_tag(XM + 0.35, (d11["bottom"][1] + 13.33) / 2, "YES", c=C_DB)

divider(12.90, "── data retrieved ──")

# ── 12. Summarise + Chart spec (parallel) ────────────────────────────────────
# Draw two parallel boxes side by side
rbox(XM - 1.65, 12.52, "⑥a  Summarise results\n(GPT-4o-mini)", C_AI, w=3.5, h=0.75)
rbox(XM + 1.65, 12.52, "⑥b  Decide chart type\n(GPT-4o-mini)", C_AI, w=3.5, h=0.75)

# "Parallel" label above the two boxes
ax.text(XM, 13.08, "⬡  Two AI calls run in parallel", ha="center", va="center",
        fontsize=7.5, color=C_AI, style="italic", fontweight="bold",
        bbox=dict(facecolor="#FEF9E7", edgecolor=C_AI,
                   boxstyle="round,pad=0.12", linewidth=0.8), zorder=5)

# join arrows into one
ax.plot([XM - 1.65, XM - 1.65], [12.14, 11.85], color=DARK, lw=1.4, zorder=2)
ax.plot([XM + 1.65, XM + 1.65], [12.14, 11.85], color=DARK, lw=1.4, zorder=2)
ax.plot([XM - 1.65, XM + 1.65], [11.85, 11.85], color=DARK, lw=1.4, zorder=2)
straight(XM, 11.85, XM, 11.25)

# ── 13. Generate chart ────────────────────────────────────────────────────────
rbox(XM, 10.90, "⑦  Generate chart image\n(matplotlib  ·  gauge / line / bar / horizontal bar)", C_OUT)
straight(XM, 10.51, XM, 9.85)

# ── 14. Send response + chart ─────────────────────────────────────────────────
rbox(XM, 9.50, "⑧  Send text response + chart image\nto user via Telegram", C_TG)
straight(XM, 9.11, XM, 8.48)

divider(8.20, "── response delivered ──")

# ── 15. Audit log (background) ────────────────────────────────────────────────
rbox(XM, 7.85, "⑨  Audit log  (background thread)\nGoogle Sheets  +  audit_log.jsonl", C_LOG)
straight(XM, 7.46, XM, 6.82)

# ── 16. END ───────────────────────────────────────────────────────────────────
end_box = FancyBboxPatch((XM - 1.3, 6.45), 2.6, 0.6,
                          boxstyle="round,pad=0.18",
                          facecolor=DARK, edgecolor=WHITE, linewidth=1.5, zorder=3)
ax.add_patch(end_box)
ax.text(XM, 6.75, "END", ha="center", va="center",
        fontsize=11, color=WHITE, fontweight="bold", zorder=4)


# ═══════════════════════════════════════════════════════════════════════════════
#  RESPONSE FORMAT CALLOUT  (bottom right)
# ═══════════════════════════════════════════════════════════════════════════════
callout_x, callout_y = 13.2, 8.6
cw, ch = 5.4, 3.2
callout = FancyBboxPatch((callout_x - cw/2, callout_y - ch/2), cw, ch,
                          boxstyle="round,pad=0.2",
                          facecolor="#EBF5FB", edgecolor="#2980B9",
                          linewidth=1.2, zorder=3)
ax.add_patch(callout)
ax.text(callout_x, callout_y + ch/2 - 0.28, "Every response includes:",
        ha="center", va="center", fontsize=8, color=DARK,
        fontweight="bold", zorder=4)
callout_lines = [
    "[1]  Section 1 — Direct answer",
    "         (coverage %, numerator, denominator)",
    "[2]  Section 2 — Public health analysis",
    "         (vs UIP 90% target, gaps flagged)",
    "[3]  Section 3 — Suggested next queries",
    "         (context-aware follow-ups)",
    "[*]  Chart  (gauge / line / bar / h-bar)",
]
for i, line in enumerate(callout_lines):
    ax.text(callout_x - 2.4, callout_y + 1.05 - i * 0.36, line,
            fontsize=7.2, color=DARK, va="center", zorder=4)


# ═══════════════════════════════════════════════════════════════════════════════
#  AUDIT LOG CALLOUT  (bottom right, below response format)
# ═══════════════════════════════════════════════════════════════════════════════
audit_x, audit_y = 13.2, 5.25
aw, ah = 5.4, 2.0
audit_box = FancyBboxPatch((audit_x - aw/2, audit_y - ah/2), aw, ah,
                            boxstyle="round,pad=0.2",
                            facecolor="#F2F3F4", edgecolor="#808B96",
                            linewidth=1.2, zorder=3)
ax.add_patch(audit_box)
ax.text(audit_x, audit_y + ah/2 - 0.28, "Audit log captures:",
        ha="center", va="center", fontsize=8, color=DARK,
        fontweight="bold", zorder=4)
audit_lines = [
    "·  Timestamp  ·  User ID  ·  User message",
    "·  Geo entity  ·  Generated SQL  ·  Attempts",
    "·  BQ row count  ·  Response text  ·  Chart type",
    "·  Success/fail  ·  Processing time (ms)",
    "·  Fine-tuning stubs (SQL + response examples)",
]
for i, line in enumerate(audit_lines):
    ax.text(audit_x - 2.4, audit_y + 0.48 - i * 0.33, line,
            fontsize=7.2, color=DARK, va="center", zorder=4)


# ═══════════════════════════════════════════════════════════════════════════════
#  SYSTEM COMPONENTS STRIP  (very bottom)
# ═══════════════════════════════════════════════════════════════════════════════
ax.plot([0.4, FIG_W - 0.4], [2.55, 2.55], color="#BDC3C7", lw=0.8)
ax.text(XM, 2.30, "System Components", ha="center", va="center",
        fontsize=8.5, fontweight="bold", color=DARK)

components = [
    (2.2,  1.65, "Telegram\nBot API",          C_TG),
    (5.3,  1.65, "OpenAI\ngpt-5.4  /  gpt-5.4-mini", C_AI),
    (8.8,  1.65, "Google\nBigQuery",            C_DB),
    (11.8, 1.65, "Google\nSheets",              C_LOG),
    (14.2, 1.65, "matplotlib\ncharts",          C_OUT),
]
for cx, cy, text, color in components:
    p = FancyBboxPatch((cx - 1.35, cy - 0.38), 2.7, 0.76,
                        boxstyle="round,pad=0.1",
                        facecolor=color, edgecolor=WHITE,
                        linewidth=1.2, alpha=0.88, zorder=3)
    ax.add_patch(p)
    ax.text(cx, cy, text, ha="center", va="center",
            fontsize=7.2, color=WHITE, fontweight="bold",
            zorder=4, multialignment="center", linespacing=1.25)


# ── Footer ────────────────────────────────────────────────────────────────────
ax.text(XM, 0.55, "HMIS Telegram Analytics Agent  ·  Bihar National Health Mission  ·  2026",
        ha="center", va="center", fontsize=7.5, color="#ABB2B9")

# ── Save ──────────────────────────────────────────────────────────────────────
out = "hmis_bot_flowchart.png"
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="#FAFAFA")
plt.close(fig)
print(f"Saved -> {out}")
