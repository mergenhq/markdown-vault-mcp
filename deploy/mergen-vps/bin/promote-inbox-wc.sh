#!/usr/bin/env bash
# Faz 3 promote: inbox/wc/ taslaklarini review et, onaylanani gercek vault path'ine tasi.
# Out-of-band, Mergen-only. WC'ye expose EDILMEZ. Default DRY-RUN.
#
# WC yazilari uid 1000 (appuser) sahipli; gercek vault mergen(1001) sahipli. Bu yuzden
# dosya islemleri ROOT CONTAINER ile yapilir (ssh kullanicisi 1000-sahipli dosyalari
# silemez/tasiyamaz) ve promote'ta hedef vault sahipligine (1001:1001) chown edilir.
# Promote/reject sonrasi index'i MCP `reindex` araciyla yenile (karar #3) — script kendi
# MCP cagrisi yapmaz, hatirlatma basar.
#
# Kullanim:
#   promote-inbox-wc.sh list
#   promote-inbox-wc.sh promote <src> <hedef> [--apply]
#   promote-inbox-wc.sh reject  <src> [--apply]
set -euo pipefail
VPS=mergen-vps
INBOX=/home/mergen/services/vault-mcp/inbox-wc
VAULT=/home/mergen/mergen/docs/internal
VAULT_UID=1001
VAULT_GID=1001

_guard() {  # $1=etiket $2=yol — mutlak/traversal reddi (yerel, docker'dan once)
  case "$2" in
    /*|*..*) echo "RED: $1 mutlak/traversal: $2"; exit 1;;
  esac
}

case "${1:-}" in
  list)
    ssh "$VPS" "ls -la '$INBOX' 2>/dev/null || echo '(inbox bos/yok)'" ;;
  promote)
    src="${2:?src gerekli}"; dst="${3:?hedef gerekli}"; apply="${4:-}"
    _guard src "$src"; _guard hedef "$dst"
    if [ "$apply" = "--apply" ]; then
      ssh "$VPS" "docker run --rm -e SRC='$src' -e DST='$dst' -v '$INBOX':/inbox -v '$VAULT':/vault alpine sh -c 'set -e; test -f \"/inbox/\$SRC\"; mkdir -p \"\$(dirname \"/vault/\$DST\")\"; cp \"/inbox/\$SRC\" \"/vault/\$DST\"; chown $VAULT_UID:$VAULT_GID \"/vault/\$DST\"; rm -f \"/inbox/\$SRC\"'"
      echo "PROMOTED: $src -> $dst (sahip $VAULT_UID:$VAULT_GID)"
      echo "SONRAKI ADIM: index'i yenile — MCP 'reindex' aracini calistir (karar #3)."
    else
      echo "[DRY-RUN] $INBOX/$src -> $VAULT/$dst (root container: cp + chown $VAULT_UID:$VAULT_GID + rm; uygulamak icin --apply)"
    fi ;;
  reject)
    src="${2:?src gerekli}"; apply="${3:-}"
    _guard src "$src"
    if [ "$apply" = "--apply" ]; then
      ssh "$VPS" "docker run --rm -e SRC='$src' -v '$INBOX':/inbox alpine sh -c 'rm -f \"/inbox/\$SRC\"'"
      echo "REJECTED: $src"
    else
      echo "[DRY-RUN] rm $INBOX/$src (root container; uygulamak icin --apply)"
    fi ;;
  *) echo "kullanim: $0 {list|promote <src> <hedef> [--apply]|reject <src> [--apply]}"; exit 1;;
esac
