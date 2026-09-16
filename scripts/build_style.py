"""
Script to safely merge the friend's reference CSS into static/css/style.css
"""
import os

with open('static/css/style.css', 'r', encoding='utf-8') as f:
    orig_css = f.read()

with open('scripts/reference/friend_style.css', 'r', encoding='utf-8') as f:
    friend_css = f.read()

friend_lines = friend_css.splitlines()

font_import = "@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Inter:wght@300;400;500;600;700&display=swap');\n"

# Extract --mt variables (lines 9 to 46 in friend_style.css)
mt_vars = '\n'.join('  ' + l.strip() for l in friend_lines[8:46] if l.strip())

# Extract brand badge (lines 83 to 114)
brand_css = '\n'.join(friend_lines[82:114])

# Extract landing page styles (lines 666 to 1602)
landing_css = '\n'.join(friend_lines[665:1602])

# Extract demo chips (lines 1702 to 1752)
demo_css = '\n'.join(friend_lines[1701:1752])

# Mobile styles
mobile_css = """
@media (max-width: 960px) {
  .mt-top-subbar { padding: 6px 16px; font-size: 0.75rem; }
  .mt-top-subbar-right { display: none; }
  .mt-society-nav-container { padding: 12px 16px; }
  .mt-society-nav-links { display: none; }
  .mt-society-hamburger { display: block; }
  .mt-society-hero-container { grid-template-columns: 1fr; min-height: auto; }
  .mt-society-hero-left { padding: 42px 20px 36px; gap: 18px; }
  .mt-society-hero-right { min-height: 320px; width: 100%; }
  .mt-hero-image-frame { min-height: 320px; }
  .mt-hero-image-overlay { width: 20%; }
  .mt-color-blocks-bar { grid-template-columns: repeat(2, 1fr); }
  .mt-about-container { grid-template-columns: 1fr; gap: 36px; }
  .mt-about-text-col { padding-right: 0; }
  .mt-society-about { padding: 56px 20px; }
}

@media (max-width: 600px) {
  .mt-hero-dots { display: none; }
  .mt-hero-society-left { padding: 30px 16px 28px; }
  .mt-hero-society-title { font-size: clamp(1.9rem, 7vw, 2.4rem); }
  .mt-hero-society-desc { font-size: 0.94rem; }
  .mt-btn-society-hero, .mt-btn-society-google { width: 100%; justify-content: center; }
  .mt-color-blocks-bar { grid-template-columns: 1fr; }
  .mt-block-col { padding: 26px 20px; }
  .mt-about-stats-row { grid-template-columns: 1fr; gap: 14px; }
  .mt-about-photo { height: 260px; }
  .mt-footer-grid { grid-template-columns: 1fr; }
}
"""

if 'family=Fraunces' not in orig_css:
    new_css = font_import + orig_css
else:
    new_css = orig_css

if '--mt-teal-900' not in new_css:
    new_css = new_css.replace(':root {', ':root {\n  /* MedTrack Clinical Design System Variables */\n' + mt_vars + '\n')

append_block = f"""
/* ==========================================================================
   MedTrack Reference Design System (Landing, Topbar, Hero, About & Footer)
   ========================================================================== */
{brand_css}

{landing_css}

{demo_css}

{mobile_css}
"""

new_css = new_css + '\n' + append_block

with open('static/css/style.css', 'w', encoding='utf-8') as f:
    f.write(new_css)

print('Success: static/css/style.css updated. Bytes:', len(new_css))
