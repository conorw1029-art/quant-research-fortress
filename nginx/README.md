# Fortress nginx config

Proxies port 80 → Flask webhook on 8765, so TradingView can POST to port 80.

Deploy:
  cp fortress-tv.conf /etc/nginx/sites-available/fortress-tv
  ln -sf /etc/nginx/sites-available/fortress-tv /etc/nginx/sites-enabled/fortress-tv
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  ufw allow 80/tcp
