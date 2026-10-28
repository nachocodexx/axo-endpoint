#!/usr/bin/env bash
# Deploys the axo platform (docker-compose.yml): the Control Plane, axo-storage,
# Xolo and axo-ui. The VEs themselves are created later by the Control Plane.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose
  --project-directory "$SCRIPT_DIR"
  --file "$SCRIPT_DIR/docker-compose.yml"
  --env-file "$SCRIPT_DIR/deploy/env/compose.env"
)

usage() {
  cat <<EOF
Usage: $0 <command> [options]

Commands:
  up [--build]     Start the platform (--build: rebuild every image first,
                   including the VEM / endpoint / runner images the Control
                   Plane starts for each VE)
  build            Build every image, start nothing
  down [--ves]     Stop the platform (--ves: also remove every VE container
                   and network the Control Plane created; data volumes stay)
  restart [svc]    Restart all services, or one
  logs [svc]       Follow logs (all services, or one)
  ps               The platform's containers, and the VEs' ones
  urls             Where everything is
EOF
}

build_images() {
  "${COMPOSE[@]}" --profile images build axo-vem-image axo-endpoint-image axo-runner-image
  "${COMPOSE[@]}" build
}

urls() {
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/deploy/env/compose.env"
  cat <<EOF

  axo-ui             http://localhost:${AXO_UI_PUBLIC_PORT:-3080}
  Control Plane API  http://localhost:${AXO_CP_PUBLIC_PORT:-9000}/docs
  Xolo admin UI      http://127.0.0.1:${XOLO_UI_PUBLIC_PORT:-3001}

  First sign-in: the bootstrap Platform Admin (AXO_CP_ADMIN_USERNAME /
  AXO_CP_ADMIN_PASSWORD in deploy/env/axo.control-plane.prod.env); you are
  asked to change the password first. Then sign up a normal account to buy VEs.
EOF
}

wait_for_cp() {
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/deploy/env/compose.env"
  local url="http://localhost:${AXO_CP_PUBLIC_PORT:-9000}/errors"
  echo -n "Waiting for the Control Plane"
  for _ in $(seq 1 90); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      echo " ready."
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo " not answering yet -- see: $0 logs axo-control-plane"
  return 1
}

remove_ves() {
  local containers networks
  containers=$(docker ps -aq --filter label=axo.managed-by=axo-control-plane)
  runners=$(docker ps -aq --filter label=axo.role=runner)
  endpoints=$(docker ps -aq --filter label=axo.role=endpoint)
  for id in $containers $runners $endpoints; do docker rm -f "$id" >/dev/null || true; done
  networks=$(docker network ls -q --filter label=axo.managed-by=axo-control-plane)
  for id in $networks; do docker network rm "$id" >/dev/null || true; done
  echo "VE containers and networks removed (volumes kept: docker volume ls | grep '^ve-')."
}

CMD="${1:-up}"
shift || true

case "$CMD" in
  up)
    if [[ " $* " == *" --build "* ]]; then
      build_images
    fi
    "${COMPOSE[@]}" up -d "$@"
    wait_for_cp || true
    urls
    ;;
  build)
    build_images
    ;;
  down)
    if [[ " $* " == *" --ves "* ]]; then
      remove_ves
    fi
    args=()
    for a in "$@"; do [[ "$a" == "--ves" ]] || args+=("$a"); done
    "${COMPOSE[@]}" down "${args[@]}"
    ;;
  restart)
    "${COMPOSE[@]}" restart "$@"
    ;;
  logs)
    "${COMPOSE[@]}" logs -f "$@"
    ;;
  ps)
    "${COMPOSE[@]}" ps
    echo
    echo "VE containers:"
    docker ps -a --filter label=axo.ve --format 'table {{.Names}}\t{{.Label "axo.role"}}\t{{.Status}}'
    ;;
  urls)
    urls
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    echo "Unknown command: $CMD"
    usage
    exit 1
    ;;
esac
