#!/usr/bin/env bash
# Deploy OCR Image Reader em uma VPS (Docker Swarm + Traefik).
# Uso: ./scripts/deploy-vps.sh sp|ams
#
# Requer no ambiente:
#   OCR_API_KEY (obrigatório)
#   OPENAI_API_KEYS_BY_TENANT (obrigatório — JSON tenantId -> sk-...)
# Opcionais: OCR_ENGINE, OCR_STRUCTURE_ENABLED, OCR_REPO_DIR

set -euo pipefail

TARGET="${1:-}"
if [[ "$TARGET" != "sp" && "$TARGET" != "ams" ]]; then
  echo "Uso: $0 sp|ams" >&2
  exit 1
fi

REPO_DIR="${OCR_REPO_DIR:-/root/ocr-image-reader}"
OCR_ENGINE="${OCR_ENGINE:-auto}"
OCR_STRUCTURE_ENABLED="${OCR_STRUCTURE_ENABLED:-true}"

if [[ -z "${OCR_API_KEY:-}" ]]; then
  echo "OCR_API_KEY não definida" >&2
  exit 1
fi

if [[ -z "${OPENAI_API_KEYS_BY_TENANT:-}" ]]; then
  echo "OPENAI_API_KEYS_BY_TENANT não definida" >&2
  exit 1
fi

if [[ "$TARGET" == "sp" ]]; then
  COMPOSE_FILE="docker-compose.ocr.yml"
  HEALTH_URL="https://ocr.integrapedidos.com.br/health"
else
  COMPOSE_FILE="docker-compose.ocr2.yml"
  HEALTH_URL="https://ocr2.integrapedidos.com.br/health"
fi

echo "=== Deploy OCR ($TARGET) $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Repositório não encontrado em $REPO_DIR (faça o clone inicial na VPS)." >&2
  exit 1
fi

cd "$REPO_DIR"

echo "=== Atualizando código (origin/main) ==="
git fetch origin main
git reset --hard origin/main
chmod +x scripts/deploy-vps.sh

echo "=== Build da imagem ==="
docker build --no-cache -t ocr-image-reader:latest .

echo "=== Stack deploy ($COMPOSE_FILE) ==="
export OCR_API_KEY
export OPENAI_API_KEYS_BY_TENANT
export OCR_ENGINE
export OCR_STRUCTURE_ENABLED

docker stack deploy -c "$COMPOSE_FILE" ocr
docker service update --force ocr_ocr

echo "=== Aguardando serviço ==="
sleep 8
# Evita exit 141 (SIGPIPE) com pipefail: não use head/sed em pipe com docker
docker service ps ocr_ocr --no-trunc > /tmp/ocr-service-ps.txt
sed -n '1,5p' /tmp/ocr-service-ps.txt

echo "=== Health check ==="
for i in 1 2 3 4 5 6; do
  if curl -sf "$HEALTH_URL" -o /tmp/ocr-health.json; then
    cat /tmp/ocr-health.json
    echo
    if grep -q '"openai_configured"[[:space:]]*:[[:space:]]*true' /tmp/ocr-health.json; then
      echo "=== Deploy $TARGET concluído com sucesso ==="
      exit 0
    fi
    echo "Health OK, mas openai_configured ainda não é true (tentativa $i/6)"
  else
    echo "Health ainda indisponível (tentativa $i/6)"
  fi
  sleep 5
done

echo "Health check falhou ou openai_configured != true após deploy" >&2
exit 1
