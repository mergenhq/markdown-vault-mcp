#!/usr/bin/env bash
# Faz 3 promote: inbox/wc/ taslaklarini review et, onaylanani gercek vault path'ine tasi.
# Out-of-band, Mergen-only. WC'ye expose EDILMEZ. Default DRY-RUN.
# Promote/reject sonrasi index'i MCP `reindex` araciyla yenile (karar #3) — script kendi
# MCP cagrisi yapmaz, hatirlatma basar.
# Kullanim:
#   promote-inbox-wc.sh list
#   promote-inbox-wc.sh promote <src> <hedef> [--apply]
#   promote-inbox-wc.sh reject  <src> [--apply]
set -euo pipefail
VPS=mergen-vps
INBOX=/home/mergen/services/vault-mcp/inbox-wc
VAULT=/home/mergen/mergen/docs/internal

case "${1:-}" in
  list)
    ssh "$VPS" "ls -la '$INBOX' 2>/dev/null || echo '(inbox bos/yok)'" ;;
  promote)
    src="${2:?src gerekli}"; dst="${3:?hedef gerekli}"; apply="${4:-}"
    case "$src" in /*|*..*) echo "RED: src mutlak/traversal: $src"; exit 1;; esac
    case "$dst" in /*|*..*) echo "RED: hedef mutlak/traversal: $dst"; exit 1;; esac
    if [ "$apply" = "--apply" ]; then
      ssh "$VPS" "set -e; test -f '$INBOX/$src'; mkdir -p \"\$(dirname '$VAULT/$dst')\"; mv '$INBOX/$src' '$VAULT/$dst'"
      echo "PROMOTED: $src -> $dst"
      echo "SONRAKI ADIM: index'i yenile — MCP 'reindex' aracini calistir (karar #3)."
    else
      echo "[DRY-RUN] mv $INBOX/$src -> $VAULT/$dst  (uygulamak icin --apply)"
    fi ;;
  reject)
    src="${2:?src gerekli}"; apply="${3:-}"
    case "$src" in /*|*..*) echo "RED: src mutlak/traversal: $src"; exit 1;; esac
    if [ "$apply" = "--apply" ]; then ssh "$VPS" "rm -f '$INBOX/$src'"; echo "REJECTED: $src";
    else echo "[DRY-RUN] rm $INBOX/$src  (uygulamak icin --apply)"; fi ;;
  *) echo "kullanim: $0 {list|promote <src> <hedef> [--apply]|reject <src> [--apply]}"; exit 1;;
esac
