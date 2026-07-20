#!/bin/sh
set -eu

case "${AUTO_BUILD_VECTOR_DB:-true}" in
    0|[Ff][Aa][Ll][Ss][Ee]|[Nn][Oo])
        echo "[INFO] Automatic guideline indexing is disabled."
        ;;
    *)
        echo "[INFO] Ensuring the guideline vector index is up to date."
        python -m scripts.build_vector_db
        ;;
esac

exec "$@"
