#!/bin/bash
# Cloudflare IP refresh script — managed by bastion
# Fetches current Cloudflare IP ranges and regenerates nginx snippets.
# Reloads nginx if configs changed.

set -euo pipefail

SNIPPET_DIR="${SNIPPET_DIR:-/etc/nginx/snippets}"
REALIP_CONF="$SNIPPET_DIR/cloudflare-realip.conf"
ALLOW_CONF="$SNIPPET_DIR/cloudflare-allow.conf"

CF_IPV4_URL="https://www.cloudflare.com/ips-v4/"
CF_IPV6_URL="https://www.cloudflare.com/ips-v6/"

# Fetch current IP ranges
IPV4=$(curl -sf "$CF_IPV4_URL") || { echo "Failed to fetch IPv4 ranges"; exit 1; }
IPV6=$(curl -sf "$CF_IPV6_URL") || { echo "Failed to fetch IPv6 ranges"; exit 1; }

if [ -z "$IPV4" ]; then
    echo "Empty IPv4 response from Cloudflare, aborting"
    exit 1
fi

# --- Generate realip config ---
REALIP_CONTENT="# Cloudflare Real IP restoration
# Managed by bastion — do not edit manually
# Last updated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

# Cloudflare IPv4 ranges"

while IFS= read -r ip; do
    [ -n "$ip" ] && REALIP_CONTENT="$REALIP_CONTENT
set_real_ip_from $ip;"
done <<< "$IPV4"

REALIP_CONTENT="$REALIP_CONTENT

# Cloudflare IPv6 ranges"

while IFS= read -r ip; do
    [ -n "$ip" ] && REALIP_CONTENT="$REALIP_CONTENT
set_real_ip_from $ip;"
done <<< "$IPV6"

REALIP_CONTENT="$REALIP_CONTENT

real_ip_header CF-Connecting-IP;"

# --- Generate allow config ---
ALLOW_CONTENT="# Cloudflare-only access control
# Managed by bastion — do not edit manually
# Last updated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

# Cloudflare IPv4 ranges"

while IFS= read -r ip; do
    [ -n "$ip" ] && ALLOW_CONTENT="$ALLOW_CONTENT
allow $ip;"
done <<< "$IPV4"

ALLOW_CONTENT="$ALLOW_CONTENT

# Cloudflare IPv6 ranges"

while IFS= read -r ip; do
    [ -n "$ip" ] && ALLOW_CONTENT="$ALLOW_CONTENT
allow $ip;"
done <<< "$IPV6"

ALLOW_CONTENT="$ALLOW_CONTENT

deny all;"

# --- Write configs if changed ---
CHANGED=0

write_if_changed() {
    local file="$1"
    local content="$2"
    local current=""
    [ -f "$file" ] && current=$(cat "$file")
    if [ "$current" != "$content" ]; then
        echo "$content" > "$file"
        echo "Updated: $file"
        CHANGED=1
    else
        echo "No changes: $file"
    fi
}

write_if_changed "$REALIP_CONF" "$REALIP_CONTENT"
write_if_changed "$ALLOW_CONF" "$ALLOW_CONTENT"

# Reload nginx if anything changed
if [ "$CHANGED" -eq 1 ]; then
    nginx -t 2>/dev/null && systemctl reload nginx
    echo "Nginx reloaded with updated Cloudflare IPs"
fi
