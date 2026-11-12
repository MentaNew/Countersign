"""What the person sees in the agent's browser: a banner with the payment lock's state, a highlight on each
element the agent uses, and a page in place of a blocked payment step.

The banner lives in a closed shadow root on <html>, outside <body>: the agent's page snapshots and the guard's
cart reading never see it. It is hidden while the guard takes its evidence screenshot.
"""

import html
import re

# Text addressed to AI agents on a shop page (often hidden). Only a warning for the person and the agent:
# the gateway still checks the cart against the permission, whatever the page says.
AGENT_BAIT = re.compile(
    r"(ignore (all |any )?(previous|prior) instructions"
    r"|\b(assistant|ai (shopping )?(agent|assistant)s?)\b[^.]{0,120}\b(must|add|claim|enter|do not|don't))",
    re.IGNORECASE)

INIT_JS = """(() => {
  if (window.__guardRender) return;
  const COLORS = {locked: '#312e81', waiting: '#92400e', unlocked: '#166534', blocked: '#b91c1c',
                  warn: '#b45309', paid: '#166534'};
  let host, root, bar, box, tag;
  function ensure() {
    if (host && host.isConnected) return true;
    if (!document.documentElement) return false;
    host = document.createElement('guard-overlay');
    root = host.attachShadow({mode: 'closed'});
    root.innerHTML = `<style>
      .bar{position:fixed;top:0;left:0;right:0;z-index:2147483647;font:600 15px/1.3 -apple-system,system-ui,sans-serif;
        color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.25)}
      .row{padding:9px 16px;display:flex;gap:12px;align-items:baseline;transition:background .3s}
      .row small{font-weight:400;font-size:13px;opacity:.92}
      .notes{background:#dc2626;padding:6px 16px;display:flex;flex-wrap:wrap;gap:8px 18px;font-size:14px}
      .flash{animation:f .5s 3}
      @keyframes f{50%{filter:brightness(1.6)}}
      .box{position:fixed;z-index:2147483646;border:3px solid #6366f1;border-radius:6px;pointer-events:none;
        box-shadow:0 0 0 4px rgba(99,102,241,.25);transition:all .2s;display:none}
      .tag{position:fixed;z-index:2147483647;background:#4f46e5;color:#fff;font:600 13px system-ui,sans-serif;
        padding:4px 8px;border-radius:6px;pointer-events:none;display:none;white-space:nowrap}
    </style><div class="bar"></div><div class="box"></div><div class="tag"></div>`;
    bar = root.querySelector('.bar'); box = root.querySelector('.box'); tag = root.querySelector('.tag');
    document.documentElement.appendChild(host);
    return true;
  }
  window.__guardRender = (s) => {
    if (!s || !ensure()) return;
    host.style.display = s.hidden ? 'none' : '';
    bar.innerHTML = '';
    const row = document.createElement('div'); row.className = 'row';
    row.style.background = COLORS[s.mode] || COLORS.locked;
    const main = document.createElement('span'); main.textContent = s.text || ''; row.appendChild(main);
    if (s.sub) { const sm = document.createElement('small'); sm.textContent = s.sub; row.appendChild(sm); }
    bar.appendChild(row);
    const notes = (s.note || '').split('  ').filter(Boolean);
    if (notes.length) {
      const n = document.createElement('div'); n.className = 'notes';
      for (const t of notes) { const x = document.createElement('span'); x.textContent = t; n.appendChild(x); }
      bar.appendChild(n);
    }
    row.classList.remove('flash'); void row.offsetWidth; if (s.flash) row.classList.add('flash');
    // Push the shop's page down by the banner's height, so nothing is hidden under it.
    document.documentElement.style.setProperty('padding-top', (s.hidden ? 0 : bar.offsetHeight + 4) + 'px', 'important');
  };
  window.__guardHighlight = (ref, label) => {
    if (!ensure()) return;
    const el = document.querySelector(`[data-guard-ref="${ref}"]`);
    if (!el) return;
    el.scrollIntoView({block: 'center', behavior: 'instant'});
    const r = el.getBoundingClientRect();
    Object.assign(box.style, {display: 'block', left: (r.left - 6) + 'px', top: (r.top - 6) + 'px',
                              width: (r.width + 12) + 'px', height: (r.height + 12) + 'px'});
    tag.textContent = label;
    Object.assign(tag.style, {display: 'block', left: Math.max(4, r.left - 6) + 'px',
                              top: Math.max(44, r.top - 34) + 'px'});
    clearTimeout(window.__guardHideT);
    window.__guardHideT = setTimeout(() => { box.style.display = 'none'; tag.style.display = 'none'; }, 1600);
  };
  const boot = () => window.__guardState && window.__guardState().then(window.__guardRender).catch(() => {});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})()"""


def locked_page(target: str, total: str | None) -> str:
    """Shown instead of a payment page the agent tried to open before the person approved the cart."""
    where = html.escape(target.split("/")[2] if "//" in target else target)
    amount = f" ({html.escape(total)})" if total else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Payment locked</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;background:#f5f5f7;margin:0;display:grid;
place-items:center;min-height:100vh}} .c{{background:#fff;border-radius:16px;padding:36px 40px;max-width:520px;
box-shadow:0 8px 30px rgba(0,0,0,.12);text-align:center}} .i{{font-size:56px}} h1{{margin:8px 0;font-size:24px}}
p{{color:#444}} code{{background:#eef;padding:2px 6px;border-radius:4px}}</style></head><body><div class="c">
<div class="i">🔒</div><h1>Payment locked by Spend Guardrail</h1>
<p>Your AI agent tried to open the payment page (<code>{where}</code>) before you approved this cart{amount}.</p>
<p><strong>Approve the cart in Spend Guardrail</strong>: payment then unlocks for this exact cart only.</p>
<p style="color:#888;font-size:13px">Taking the agent back to the checkout…</p></div></body></html>"""
