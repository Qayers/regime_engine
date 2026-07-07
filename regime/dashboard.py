"""Generator statycznego dashboardu (index.html + history.json + .htaccess). — ETAP E5.

Bez frameworków i procesu serwera. Chart.js z CDN, dane inline z history.json.
Meta robots noindex; basic-auth przez .htaccess/.htpasswd (jeśli moduły Apache dostępne).
"""
from __future__ import annotations

# TODO(E5): render_dashboard() → PUBLIC_DIR/index.html, history.json, .htaccess.
