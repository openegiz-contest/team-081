#!/usr/bin/env bash
# Print where the Aktogay twin can be seen.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
set -a; . "$ROOT/deploy/compose/.env"; set +a
H="${OPENEGIZ_PUBLIC_HOST:-localhost}"
cat <<TXT

Aktogay digital twin is running.

  3D Stand (scene, dispatcher, what-if)  http://$H:${AKTOGAY_PORT:-8070}
  Grafana dashboard                      http://$H:$GRAFANA_PORT/d/aktogay-haulage   admin / $GRAFANA_ADMIN_PASSWORD
  Twins in Ditto                         http://$H:$DITTO_PORT/api/2/search/things?namespaces=kz.aktogay   ditto / $DITTO_PASSWORD

End-to-end check with what-if scenarios: make aktogay-verify
TXT
