import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename

from src.fetchers.trends_client import discover_trends
from src.processors.gemini_analyzer import (
    distill_search_terms,
    distill_theme_terms,
    get_client,
)
from src.integrations.printify_client import (
    build_client_from_env,
    create_and_publish_product,
    generate_listing_copy,
)
from src.utils.asset_engine import process_final_assets


load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = BASE_DIR / "output" / "final_assets"
DB_PATH = BASE_DIR / "output" / "review_queue.db"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS designs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                path                TEXT NOT NULL UNIQUE,
                status              TEXT NOT NULL DEFAULT 'pending',
                printify_product_id TEXT,
                error_message       TEXT,
                risk_level          TEXT NOT NULL DEFAULT 'LOW',
                risk_reason         TEXT,
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.execute("ALTER TABLE designs ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'LOW'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE designs ADD COLUMN risk_reason TEXT")
        except Exception:
            pass
        conn.commit()


def seed_assets() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    asset_paths = sorted(
        [*ASSETS_DIR.glob("*.png"), *ASSETS_DIR.glob("*.jpg"), *ASSETS_DIR.glob("*.jpeg")]
    )
    with get_db() as conn:
        for asset in asset_paths:
            conn.execute(
                "INSERT OR IGNORE INTO designs (path, status) VALUES (?, 'pending')",
                (str(asset),),
            )
        conn.commit()


def add_design_file(
    file_path: Path,
    risk_level: str = "LOW",
    risk_reason: Optional[str] = None,
) -> None:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM designs WHERE path = ?", (str(file_path),)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE designs
                SET status = 'pending',
                    printify_product_id = NULL,
                    error_message = NULL,
                    risk_level = ?,
                    risk_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE path = ?
                """,
                (risk_level, risk_reason, str(file_path)),
            )
        else:
            conn.execute(
                """
                INSERT INTO designs (path, status, risk_level, risk_reason)
                VALUES (?, 'pending', ?, ?)
                """,
                (str(file_path), risk_level, risk_reason),
            )
        conn.commit()


def get_next_design() -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, path, status, printify_product_id, error_message,
                   risk_level, risk_reason
            FROM designs
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["name"] = Path(d["path"]).name
    return d


def get_queue_counts() -> Dict[str, int]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM designs GROUP BY status"
        ).fetchall()
    counts = {"pending": 0, "published": 0, "rejected": 0, "failed": 0, "flagged": 0}
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] = row["count"]
    return counts



# ---------------------------------------------------------------------------
# Design generation
# ---------------------------------------------------------------------------

def generate_top_designs(theme: str = "") -> Dict[str, object]:
    project_id = os.getenv("VERTEX_PROJECT_ID", "").strip()
    location = os.getenv("VERTEX_LOCATION", "us-central1").strip()
    if not project_id:
        raise RuntimeError("Set VERTEX_PROJECT_ID in .env to generate designs.")

    client = get_client()

    if theme:
        trend_data = distill_theme_terms(client=client, theme=theme)
    else:
        # Primary: Google Trends + curated fallback (no more Reddit news)
        reddit_terms = discover_trends(gemini_client=client, limit=40)
        if not reddit_terms:
            return {"count": 0, "paths": []}

        trend_data = distill_search_terms(
            client=client,
            bq_context=reddit_terms,  # field name kept for interface compatibility
        )

    if not trend_data:
        return {"count": 0, "paths": []}

    asset_results = process_final_assets(
        trend_data=trend_data,
        project_id=project_id,
        location=location,
        gemini_client=client,
        is_theme_mode=bool(theme),
    )

    fresh_results = sorted(
        asset_results,
        key=lambda r: Path(r["path"]).stat().st_mtime if Path(r["path"]).exists() else 0,
        reverse=True,
    )[:5]

    for result in reversed(fresh_results):
        file_path = Path(result["path"])
        if file_path.exists():
            add_design_file(
                file_path,
                risk_level=result.get("risk_level", "LOW"),
                risk_reason=result.get("risk_reason"),
            )

    return {
        "count": len(fresh_results),
        "paths": [r["path"] for r in fresh_results],
    }


def update_design_status(
    design_id: int,
    status: str,
    printify_product_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE designs
            SET status = ?, printify_product_id = ?, error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, printify_product_id, error_message, design_id),
        )
        conn.commit()


def _get_printify_shop_id(client) -> str:
    shop_id = os.getenv("PRINTIFY_SHOP_ID", "").strip()
    if not shop_id:
        shops = client.get_shops()
        if not shops:
            raise RuntimeError("No Printify shops found for this API token.")
        shop_id = str(shops[0]["id"])
    return shop_id


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.get("/")
def index():
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>TrendThread Review</title>
    <style>
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        color: #f4f7ff;
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
        background: radial-gradient(circle at 20% 20%, #233567 0%, #0d111b 45%, #080b12 100%);
        min-height: 100vh;
      }
      .wrap { max-width: 720px; margin: 0 auto; padding: 18px 16px 28px; }
      .title { font-size: 32px; font-weight: 800; margin: 6px 0 4px; letter-spacing: -.02em; }
      .subtitle { color: #a7b6d8; margin: 0 0 14px; }
      .toolbar {
        display: grid;
        grid-template-columns: 1fr auto auto;
        gap: 8px;
        background: rgba(12, 18, 34, .7);
        border: 1px solid rgba(128, 149, 204, .2);
        border-radius: 14px;
        padding: 10px;
        backdrop-filter: blur(8px);
      }
      .toolbar input {
        width: 100%;
        border: 1px solid #2a395f;
        border-radius: 10px;
        padding: 10px 12px;
        background: #111a30;
        color: #f4f7ff;
      }
      .toolbar button {
        border: 0;
        border-radius: 10px;
        padding: 10px 12px;
        color: #fff;
        font-weight: 700;
        cursor: pointer;
      }
      .generate-btn { background: linear-gradient(135deg, #5b7dff, #7a53f5); }
      .upload-btn { background: #23448f; }
      .stats { margin: 10px 2px 14px; color: #c2cdee; font-size: 14px; }
      .card-shell { position: relative; max-width: 560px; margin: 0 auto; }
      .card-shadow {
        position: absolute; inset: 10px -6px -10px 6px;
        border-radius: 22px; background: #121a2b; opacity: .8; transform: scale(.97);
      }
      .card {
        position: relative;
        background: linear-gradient(180deg, rgba(35,47,77,.92) 0%, rgba(22,31,52,.96) 100%);
        border: 1px solid rgba(156,178,231,.24);
        border-radius: 22px;
        padding: 12px;
        box-shadow: 0 24px 60px rgba(0,0,0,.45);
        touch-action: pan-y;
      }
      img.design-img {
        width: 100%;
        min-height: 420px;
        max-height: 620px;
        object-fit: contain;
        border-radius: 16px;
        background: #0f1422;
      }
      .risk-banner {
        display: none;
        margin-top: 8px;
        border-radius: 10px;
        padding: 8px 12px;
        font-size: 13px;
        font-weight: 600;
        line-height: 1.4;
      }
      .risk-banner.medium {
        display: block;
        background: rgba(200, 140, 0, 0.18);
        border: 1px solid rgba(200, 140, 0, 0.5);
        color: #f5cc60;
      }
      .risk-banner.high {
        display: block;
        background: rgba(200, 40, 40, 0.18);
        border: 1px solid rgba(200, 40, 40, 0.5);
        color: #f56060;
      }
      .meta {
        margin-top: 10px;
        display: flex;
        justify-content: space-between;
        font-size: 14px;
        color: #ccd7f4;
      }
      .actions { display: flex; gap: 10px; margin-top: 12px; }
      .actions button {
        flex: 1;
        padding: 14px;
        border: 0;
        border-radius: 999px;
        color: #fff;
        font-size: 15px;
        font-weight: 800;
        cursor: pointer;
      }
      .reject  { background: linear-gradient(135deg, #d74444, #9d1f1f); }
      .approve { background: linear-gradient(135deg, #21b867, #168d4c); }
      .regen-wrap { display: flex; margin-top: 10px; }
      .regen-btn {
        width: calc(50% - 5px);
        margin: 0 auto;
        background: linear-gradient(135deg, #b38600, #d4a000);
        color: #fff;
        border: 0;
        border-radius: 999px;
        padding: 14px;
        font-size: 15px;
        font-weight: 800;
        cursor: pointer;
      }

      /* Regen modal */
      .regen-modal {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,.65);
        backdrop-filter: blur(4px);
        z-index: 100;
        align-items: center;
        justify-content: center;
      }
      .regen-modal.open { display: flex; }
      .regen-box {
        background: #131d34;
        border: 1px solid rgba(156,178,231,.3);
        border-radius: 18px;
        padding: 24px 20px 18px;
        width: min(400px, 90vw);
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .regen-box h3 { margin: 0; font-size: 17px; color: #f4f7ff; }
      .regen-box p  { margin: 0; font-size: 13px; color: #8fa3cc; }
      .regen-box input {
        border: 1px solid #2a395f;
        border-radius: 10px;
        padding: 10px 12px;
        background: #0f1828;
        color: #f4f7ff;
        font-size: 14px;
        width: 100%;
      }
      .regen-box-actions { display: flex; gap: 8px; }
      .regen-box-actions button {
        flex: 1; padding: 11px; border: 0; border-radius: 999px;
        font-weight: 700; font-size: 14px; cursor: pointer;
      }
      .regen-cancel  { background: #1e2d4a; color: #a7b6d8; }
      .regen-confirm { background: linear-gradient(135deg, #b38600, #d4a000); color: #fff; }

      /* Loading overlay */
      .loading-overlay {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(8, 11, 18, 0.82);
        backdrop-filter: blur(6px);
        z-index: 300;
        align-items: center;
        justify-content: center;
        flex-direction: column;
        gap: 18px;
      }
      .loading-overlay.open { display: flex; }
      .loading-spinner {
        width: 48px;
        height: 48px;
        border: 4px solid rgba(91, 125, 255, 0.2);
        border-top-color: #5b7dff;
        border-radius: 50%;
        animation: spin 0.9s linear infinite;
      }
      @keyframes spin { to { transform: rotate(360deg); } }
      .loading-text {
        color: #a7b6d8;
        font-size: 15px;
        font-weight: 600;
        text-align: center;
        max-width: 300px;
        line-height: 1.5;
        transition: opacity 0.3s;
      }

      /* Preview modal */
      .preview-modal {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,.75);
        backdrop-filter: blur(6px);
        z-index: 200;
        align-items: flex-start;
        justify-content: center;
        overflow-y: auto;
        padding: 24px 16px;
      }
      .preview-modal.open { display: flex; }
      .preview-box {
        background: #131d34;
        border: 1px solid rgba(156,178,231,.3);
        border-radius: 18px;
        padding: 24px 20px 20px;
        width: min(560px, 100%);
        display: flex;
        flex-direction: column;
        gap: 14px;
        margin: auto;
      }
      .preview-box h3 { margin: 0; font-size: 18px; color: #f4f7ff; }
      .preview-label {
        font-size: 12px;
        font-weight: 700;
        color: #7a90c2;
        text-transform: uppercase;
        letter-spacing: .06em;
        margin-bottom: 4px;
      }
      .preview-box input[type="text"],
      .preview-box textarea {
        width: 100%;
        border: 1px solid #2a395f;
        border-radius: 10px;
        padding: 10px 12px;
        background: #0f1828;
        color: #f4f7ff;
        font-size: 14px;
        font-family: inherit;
        resize: vertical;
      }
      .preview-box textarea { min-height: 140px; }
      .preview-actions { display: flex; gap: 8px; margin-top: 4px; }
      .preview-actions button {
        flex: 1; padding: 13px; border: 0; border-radius: 999px;
        font-weight: 700; font-size: 14px; cursor: pointer;
      }
      .preview-cancel  { background: #1e2d4a; color: #a7b6d8; }
      .preview-confirm { background: linear-gradient(135deg, #21b867, #168d4c); color: #fff; }
      .preview-loading { color: #a7b6d8; font-size: 14px; text-align: center; padding: 28px 0; }

      .status {
        margin: 12px auto 0;
        max-width: 560px;
        min-height: 22px;
        color: #cbd6f6;
        font-size: 14px;
      }
      @media (max-width: 560px) {
        .title { font-size: 27px; }
        .toolbar { grid-template-columns: 1fr; }
        img.design-img { min-height: 320px; }
      }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="title">TrendThread Swipe Studio</div>
      <p class="subtitle">Generate top 5 designs, swipe left to reject, right to publish.</p>
      <div class="toolbar">
        <input id="theme-input" placeholder="Optional theme: e.g. anime absurdist, cyber y2k" />
        <button class="generate-btn" onclick="generateDesigns()">Generate Top 5</button>
        <button class="upload-btn" onclick="triggerUpload()">Upload</button>
        <input id="file-input" style="display:none" type="file" accept="image/png,image/jpeg,image/jpg,image/webp" />
      </div>
      <div class="stats" id="stats"></div>
      <div class="card-shell">
        <div class="card-shadow"></div>
        <div class="card" id="swipe-card">
          <img class="design-img" id="design-image" src="" alt="design" />
          <div class="risk-banner" id="risk-banner"></div>
          <div class="meta">
            <span id="design-meta">Loading...</span>
            <span>← Reject / Publish →</span>
          </div>
          <div class="actions">
            <button class="reject"  onclick="decide('reject')">Dislike</button>
            <button class="approve" onclick="openPreview()">Like + Publish</button>
          </div>
          <div class="regen-wrap">
            <button class="regen-btn" onclick="openRegenModal()">⟳ Regenerate</button>
          </div>
        </div>
      </div>
      <div class="status" id="status"></div>
    </div>

    <!-- Loading overlay -->
    <div class="loading-overlay" id="loading-overlay">
      <div class="loading-spinner"></div>
      <div class="loading-text" id="loading-text">Starting…</div>
    </div>

    <!-- Regen modal -->
    <div class="regen-modal" id="regen-modal">
      <div class="regen-box">
        <h3>Regenerate Design</h3>
        <p>Optionally describe what you want. Leave blank to regenerate from the current motif.</p>
        <input id="regen-prompt" placeholder="e.g. more minimal, darker palette, chibi style…" />
        <div class="regen-box-actions">
          <button class="regen-cancel"  onclick="closeRegenModal()">Cancel</button>
          <button class="regen-confirm" onclick="confirmRegen()">Regenerate</button>
        </div>
      </div>
    </div>

    <!-- Preview modal -->
    <div class="preview-modal" id="preview-modal">
      <div class="preview-box">
        <h3>Preview Before Publishing</h3>
        <div id="preview-loading" class="preview-loading">Generating listing copy and mockups…</div>
        <div id="preview-content" style="display:none; flex-direction:column; gap:14px;">
          <div>
            <div class="preview-label">Design</div>
            <img id="preview-design-img" src="" alt="design preview"
              style="width:100%; border-radius:12px; background:#0f1422; object-fit:contain; max-height:300px;" />
          </div>
          <div>
            <div class="preview-label">Title</div>
            <input type="text" id="preview-title" maxlength="140" />
          </div>
          <div>
            <div class="preview-label">Description</div>
            <textarea id="preview-description"></textarea>
          </div>
          <div class="preview-actions">
            <button class="preview-cancel"  onclick="closePreview()">Cancel</button>
            <button class="preview-confirm" onclick="confirmPublish()">Publish Now</button>
          </div>
        </div>
      </div>
    </div>

    <script>
      let current = null;
      let startX = 0;
      let deltaX = 0;
      const card = document.getElementById('swipe-card');

      function showRiskBanner(riskLevel, riskReason) {
        const banner = document.getElementById('risk-banner');
        banner.className = 'risk-banner';
        if (riskLevel === 'HIGH') {
          banner.classList.add('high');
          banner.innerText = '⚠ HIGH IP RISK — ' + (riskReason || 'Possible copyright issue. Review before publishing.');
        } else if (riskLevel === 'MEDIUM') {
          banner.classList.add('medium');
          banner.innerText = '⚠ Review flagged — ' + (riskReason || 'Potential IP concern. Use judgement before publishing.');
        }
      }

      async function loadNext() {
        const resp = await fetch('/api/next');
        const data = await resp.json();
        if (data.counts) {
          const c = data.counts;
          document.getElementById('stats').innerText =
            `Pending: ${c.pending} | Published: ${c.published} | Rejected: ${c.rejected} | Failed: ${c.failed} | Flagged: ${c.flagged}`;
        }
        if (!data.design) {
          current = null;
          document.getElementById('design-image').style.display = 'none';
          document.getElementById('design-meta').innerText = 'No pending designs. Click "Generate Top 5".';
          document.getElementById('risk-banner').className = 'risk-banner';
          return;
        }
        current = data.design;
        document.getElementById('design-image').style.display = 'block';
        document.getElementById('design-image').src = '/api/image/' + current.id;
        document.getElementById('design-meta').innerText = `#${current.id} - ${current.name}`;
        document.getElementById('status').innerText = '';
        showRiskBanner(current.risk_level || 'LOW', current.risk_reason || '');
      }

      async function decide(action) {
        if (!current) {
          document.getElementById('status').innerText = 'No active design. Upload one first.';
          return;
        }
        document.getElementById('status').innerText = 'Processing...';
        const resp = await fetch('/api/decision', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({id: current.id, action})
        });
        const data = await resp.json();
        document.getElementById('status').innerText = data.message;
        await loadNext();
      }

      const GENERATION_STEPS = [
        "Fetching trending signals…",
        "Distilling top motifs from trend data…",
        "Pulling reference images…",
        "Analysing visual strategy…",
        "Generating designs with Gemini 3 Pro Image…",
        "Screening for IP risks…",
        "Finalising assets…",
      ];

      let loadingInterval = null;

      function showLoadingOverlay() {
        const overlay = document.getElementById('loading-overlay');
        const text = document.getElementById('loading-text');
        overlay.classList.add('open');
        let step = 0;
        text.innerText = GENERATION_STEPS[0];
        loadingInterval = setInterval(() => {
          step = Math.min(step + 1, GENERATION_STEPS.length - 1);
          text.style.opacity = '0';
          setTimeout(() => {
            text.innerText = GENERATION_STEPS[step];
            text.style.opacity = '1';
          }, 300);
        }, 12000); // ~12s per stage
      }

      function hideLoadingOverlay() {
        clearInterval(loadingInterval);
        loadingInterval = null;
        document.getElementById('loading-overlay').classList.remove('open');
      }

      async function generateDesigns() {
        const theme = document.getElementById('theme-input').value.trim();
        showLoadingOverlay();
        try {
          const resp = await fetch('/api/generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({theme})
          });
          const data = await resp.json();
          hideLoadingOverlay();
          document.getElementById('status').innerText = data.message;
          await loadNext();
        } catch (err) {
          hideLoadingOverlay();
          document.getElementById('status').innerText = 'Generation request failed.';
        }
      }

      function triggerUpload() {
        document.getElementById('file-input').click();
      }

      async function uploadDesign() {
        const input = document.getElementById('file-input');
        if (!input.files || !input.files.length) {
          document.getElementById('status').innerText = 'Pick an image first.';
          return;
        }
        const form = new FormData();
        form.append('image', input.files[0]);
        document.getElementById('status').innerText = 'Uploading design...';
        const resp = await fetch('/api/upload', { method: 'POST', body: form });
        const data = await resp.json();
        document.getElementById('status').innerText = data.message;
        input.value = '';
        await loadNext();
      }

      document.getElementById('file-input').addEventListener('change', uploadDesign);

      // ---- Regen modal ----
      function openRegenModal() {
        if (!current) {
          document.getElementById('status').innerText = 'No active design to regenerate.';
          return;
        }
        document.getElementById('regen-prompt').value = '';
        document.getElementById('regen-modal').classList.add('open');
      }
      function closeRegenModal() {
        document.getElementById('regen-modal').classList.remove('open');
      }
      async function confirmRegen() {
        closeRegenModal();
        const prompt = document.getElementById('regen-prompt').value.trim();
        document.getElementById('status').innerText = 'Regenerating image…';
        const img = document.getElementById('design-image');
        img.style.opacity = '0.3';
        try {
          const resp = await fetch('/api/regenerate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: current.id, prompt })
          });
          const data = await resp.json();
          if (resp.ok) {
            img.src = '/api/image/' + current.id + '?t=' + Date.now();
            img.style.opacity = '1';
            document.getElementById('status').innerText = data.message;
            if (data.risk_level) {
              showRiskBanner(data.risk_level, data.risk_reason || '');
            }
          } else {
            img.style.opacity = '1';
            document.getElementById('status').innerText = data.message;
          }
        } catch (err) {
          img.style.opacity = '1';
          document.getElementById('status').innerText = 'Regeneration request failed.';
        }
      }

      // ---- Preview modal ----
      async function openPreview() {
        if (!current) {
          document.getElementById('status').innerText = 'No active design.';
          return;
        }
        // Reset and open modal in loading state
        document.getElementById('preview-loading').style.display = 'block';
        document.getElementById('preview-content').style.display = 'none';
        document.getElementById('preview-modal').classList.add('open');
        document.getElementById('status').innerText = 'Generating listing copy…';

        try {
          const resp = await fetch('/api/preview', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: current.id })
          });
          const data = await resp.json();

          if (!resp.ok) {
            closePreview();
            document.getElementById('status').innerText = data.message || 'Preview failed.';
            return;
          }

          // Show the design image
          document.getElementById('preview-design-img').src = data.design_image_url + '?t=' + Date.now();

          // Populate editable fields
          document.getElementById('preview-title').value = data.title || '';
          document.getElementById('preview-description').value = data.description || '';

          document.getElementById('preview-loading').style.display = 'none';
          document.getElementById('preview-content').style.display = 'flex';
          document.getElementById('status').innerText = '';

        } catch (err) {
          closePreview();
          document.getElementById('status').innerText = 'Preview request failed.';
        }
      }

      function closePreview() {
        document.getElementById('preview-modal').classList.remove('open');
      }

      async function confirmPublish() {
        const title = document.getElementById('preview-title').value.trim();
        const description = document.getElementById('preview-description').value.trim();
        closePreview();
        document.getElementById('status').innerText = 'Publishing…';
        try {
          const resp = await fetch('/api/confirm', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: current.id, title, description })
          });
          const data = await resp.json();
          document.getElementById('status').innerText = data.message;
          await loadNext();
        } catch (err) {
          document.getElementById('status').innerText = 'Publish request failed.';
        }
      }

      // Touch swipe
      card.addEventListener('touchstart', (e) => { startX = e.touches[0].clientX; });
      card.addEventListener('touchmove',  (e) => { deltaX = e.touches[0].clientX - startX; });
      card.addEventListener('touchend', async () => {
        if (Math.abs(deltaX) > 80) {
          if (deltaX > 0) await openPreview();
          if (deltaX < 0) await decide('reject');
        }
        startX = 0;
        deltaX = 0;
      });

      // Keyboard shortcuts
      window.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft')  decide('reject');
        if (e.key === 'ArrowRight') openPreview();
      });

      loadNext();
    </script>
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/next")
def api_next():
    return jsonify({"design": get_next_design(), "counts": get_queue_counts()})


@app.get("/api/image/<int:design_id>")
def api_image(design_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT path FROM designs WHERE id = ?", (design_id,)).fetchone()
    if not row:
        return jsonify({"error": "Design not found"}), 404
    return send_file(row["path"])


@app.post("/api/decision")
def api_decision():
    payload = request.get_json(silent=True) or {}
    design_id = payload.get("id")
    action = payload.get("action")

    if not design_id or action not in {"approve", "reject"}:
        return jsonify({"message": "Invalid request"}), 400

    with get_db() as conn:
        row = conn.execute("SELECT path FROM designs WHERE id = ?", (design_id,)).fetchone()
    if not row:
        return jsonify({"message": "Design not found"}), 404

    if action == "reject":
        update_design_status(design_id, "rejected")
        return jsonify({"message": "Rejected."})

    # approve goes through /api/preview + /api/confirm now,
    # but keep this path as a direct fallback if needed
    try:
        image_path = row["path"]
        stem = Path(image_path).stem.replace("_final", "").replace("_", " ").strip()
        gemini_client = get_client()
        copy = generate_listing_copy(
            gemini_client=gemini_client,
            term=stem, subject=stem,
            context=f"A trending graphic tee design: {stem}",
            topic_type="GENERAL",
        )
        printify = build_client_from_env()
        shop_id = _get_printify_shop_id(printify)
        product = create_and_publish_product(
            client=printify,
            shop_id=shop_id,
            image_path=image_path,
            title=copy["title"],
            description=copy["description"],
            tags=copy["tags"],
        )
        product_id = str(product["id"])
        update_design_status(design_id, "published", printify_product_id=product_id)
        return jsonify({"message": f"Published to Etsy. Product ID: {product_id}"})
    except Exception as exc:
        update_design_status(design_id, "failed", error_message=str(exc))
        return jsonify({"message": f"Publish failed: {exc}"}), 500


@app.post("/api/preview")
def api_preview():
    """
    Generates listing copy only — no Printify product is created yet.
    The design image URL is returned so the preview modal can show it.
    Nothing is created or charged until the user clicks Publish Now.
    """
    payload = request.get_json(silent=True) or {}
    design_id = payload.get("id")

    if not design_id:
        return jsonify({"message": "Missing design id"}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, path FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
    if not row:
        return jsonify({"message": "Design not found"}), 404

    try:
        image_path = row["path"]
        stem = Path(image_path).stem.replace("_final", "").replace("_", " ").strip()

        gemini_client = get_client()
        copy = generate_listing_copy(
            gemini_client=gemini_client,
            term=stem, subject=stem,
            context=f"A trending graphic tee design: {stem}",
            topic_type="GENERAL",
        )

        return jsonify({
            "design_image_url": f"/api/image/{design_id}",
            "title": copy["title"],
            "description": copy["description"],
            "tags": copy["tags"],
        })

    except Exception as exc:
        return jsonify({"message": f"Preview failed: {exc}"}), 500


@app.post("/api/confirm")
def api_confirm():
    """
    Creates the Printify product and publishes it in one step,
    using the title/description the user confirmed (or edited) in the preview modal.
    """
    payload = request.get_json(silent=True) or {}
    design_id = payload.get("id")
    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip()

    if not design_id:
        return jsonify({"message": "Missing design id"}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT path FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
    if not row:
        return jsonify({"message": "Design not found"}), 404

    try:
        image_path = row["path"]
        stem = Path(image_path).stem.replace("_final", "").replace("_", " ").strip()

        # Use the user's edited copy; fall back to stem-based title if somehow empty
        copy = {
            "title": title or f"{stem.title()} Unisex Tee",
            "description": description or f"Original TrendThread design: {stem}.",
            "tags": ["trend", "meme", "streetwear", "gift", "graphic tee"],
        }

        printify = build_client_from_env()
        shop_id = _get_printify_shop_id(printify)
        product = create_and_publish_product(
            client=printify,
            shop_id=shop_id,
            image_path=image_path,
            title=copy["title"],
            description=copy["description"],
            tags=copy["tags"],
        )
        product_id = str(product["id"])

        update_design_status(design_id, "published", printify_product_id=product_id)
        return jsonify({"message": f"Published. Product ID: {product_id}"})

    except Exception as exc:
        update_design_status(design_id, "failed", error_message=str(exc))
        return jsonify({"message": f"Publish failed: {exc}"}), 500


@app.post("/api/upload")
def api_upload():
    uploaded = request.files.get("image")
    if not uploaded or not uploaded.filename:
        return jsonify({"message": "No file uploaded"}), 400

    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    filename = secure_filename(uploaded.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        return jsonify({"message": "Unsupported file type"}), 400

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    target = ASSETS_DIR / filename
    uploaded.save(target)
    add_design_file(target)
    return jsonify({"message": f"Uploaded: {filename}"})


@app.post("/api/generate")
def api_generate():
    payload = request.get_json(silent=True) or {}
    theme = (payload.get("theme") or "").strip()
    try:
        result = generate_top_designs(theme=theme)
        if result["count"] == 0:
            return jsonify({"message": "No designs generated (safety filter may have blocked outputs)."}), 400
        return jsonify({"message": f"Generated {result['count']} designs and queued them for review."})
    except Exception as exc:
        return jsonify({"message": f"Generation failed: {exc}"}), 500


@app.post("/api/regenerate")
def api_regenerate():
    payload = request.get_json(silent=True) or {}
    design_id = payload.get("id")
    user_prompt = (payload.get("prompt") or "").strip()

    if not design_id:
        return jsonify({"message": "Missing design id"}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, path FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
    if not row:
        return jsonify({"message": "Design not found"}), 404

    project_id = os.getenv("VERTEX_PROJECT_ID", "").strip()
    location = os.getenv("VERTEX_LOCATION", "us-central1").strip()
    if not project_id:
        return jsonify({"message": "VERTEX_PROJECT_ID not set"}), 500

    try:
        from src.fetchers.market_research import fetch_reference_images, MIN_VALID_REFERENCES
        from src.processors.image_generator import generate_shirt_design
        from src.processors.ip_screener import screen_generated_image
        import shutil

        gemini_client = get_client()
        image_path = row["path"]
        stem = Path(image_path).stem.replace("_final", "").replace("_", " ").strip()

        # Use term as search key — same flow as initial generation.
        # If user_prompt was provided, append it to the query for refinement.
        search_term = f"{stem} {user_prompt}".strip() if user_prompt else stem

        references = fetch_reference_images(term=search_term, max_images=5)
        if len(references) < MIN_VALID_REFERENCES:
            return jsonify({
                "message": f"Insufficient reference images for '{search_term}' — try a different term"
            }), 400

        # Generate to a temp path, then move into place on success
        tmp_path = str(ASSETS_DIR / f"_regen_{design_id}.png")
        gen_path = generate_shirt_design(
            reference_images=references,
            out_path=tmp_path,
            term=search_term,
        )
        if not gen_path or not Path(gen_path).exists():
            return jsonify({"message": "Image generation produced no output"}), 500

        shutil.move(gen_path, image_path)

        screen = screen_generated_image(gemini_client, image_path)
        risk_level = screen.get("risk_level", "LOW")
        risk_reason = screen.get("risk_reason")

        with get_db() as conn:
            conn.execute(
                """
                UPDATE designs
                SET risk_level = ?, risk_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (risk_level, risk_reason, design_id),
            )
            conn.commit()

        return jsonify({
            "message": "Regenerated successfully.",
            "risk_level": risk_level,
            "risk_reason": risk_reason,
        })

    except Exception as exc:
        return jsonify({"message": f"Regeneration failed: {exc}"}), 500


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap() -> None:
    init_db()
    seed_assets()


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=8080, debug=True)