#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH UV_NO_SYNC
python3 scripts/check_open_boundary.py src pyproject.toml
sync_options=()
run_options=()
export_options=()
fixture_wheels=()
contract_only=0
for option in "$@"; do
case "$option" in
  --fixtures)
    fixture_paths=$(python3 scripts/verify_dependency_fixtures.py "$PWD")
    mapfile -t fixture_wheels <<< "$fixture_paths"
    run_options=(--no-sync)
    sync_options+=(--no-install-package controlplane-api)
    export_options+=(--no-emit-package controlplane-api)
    sync_options+=(--no-install-package controlplane-tenancy)
    export_options+=(--no-emit-package controlplane-tenancy)
    sync_options+=(--no-install-package controlplane-identity)
    export_options+=(--no-emit-package controlplane-identity)
    sync_options+=(--no-install-package controlplane-db)
    export_options+=(--no-emit-package controlplane-db)
    sync_options+=(--no-install-package controlplane-events)
    export_options+=(--no-emit-package controlplane-events)
    sync_options+=(--no-install-package operator-ban-store)
    export_options+=(--no-emit-package operator-ban-store)
    ;;
  --contracts) contract_only=1 ;;
  *) echo 'Usage: bash scripts/check-package.sh [--fixtures] [--contracts]' >&2; exit 2 ;;
esac
done
if (( ${#fixture_wheels[@]} == 0 )); then uv lock --check; fi
sync_environment() {
  uv sync --frozen --python 3.13 "${sync_options[@]}"
  if (( ${#fixture_wheels[@]} )); then
    uv pip install --python .venv/bin/python --no-deps --reinstall "${fixture_wheels[@]}"
  fi
}
pytest_options=()
if (( contract_only )); then
  pytest_options+=(--ignore=tests/test_oidc_platform_subject.py
    --ignore=tests/test_storage_provider_database.py
    --deselect=tests/test_review_regressions.py::test_celery_repeated_event_loops)
else
  : "${TEST_DATABASE_URL:?Disposable PostgreSQL is required}"
fi
sync_environment
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
python3 scripts/prepare-storage-schema.py tests_artifacts/storage-schema "$scratch/storage-schema"
uv run --frozen "${run_options[@]}" pytest "${pytest_options[@]}" --junitxml="$scratch/source.xml"
uv build --out-dir "$scratch/dist"
uv run --frozen "${run_options[@]}" python scripts/verify_wheel.py "$scratch/dist"
uv run --frozen "${run_options[@]}" pytest -q tests_tools
uv run --frozen "${run_options[@]}" python scripts/export_release_contract.py "$scratch"/dist/*.whl --output "$scratch/contract"
for qualified_manifest in releases/candidate/release.json; do
  if [[ -f "$qualified_manifest" ]]; then
    uv run --frozen "${run_options[@]}" python scripts/verify_release_candidate.py "$qualified_manifest" "$scratch/contract/contract.json" "$scratch/contract/evidence.json" controlplane-core
  fi
done
uv export --frozen "${export_options[@]}" --no-emit-project --format requirements-txt --output-file "$scratch/requirements.txt" >/dev/null
uv venv --python 3.13 "$scratch/venv"
uv pip sync --python "$scratch/venv/bin/python" "$scratch/requirements.txt"
if (( ${#fixture_wheels[@]} )); then
  uv pip install --python "$scratch/venv/bin/python" --no-deps "${fixture_wheels[@]}" "$scratch"/dist/*.whl
else
uv pip uninstall --python "$scratch/venv/bin/python" controlplane-api controlplane-tenancy controlplane-identity controlplane-db controlplane-events operator-ban-store
uv pip install --python "$scratch/venv/bin/python" --constraint "$scratch/requirements.txt" "$scratch"/dist/*.whl
fi
cp -R tests "$scratch/"
(cd "$scratch" && "$scratch/venv/bin/python" -I -m pytest -c /dev/null --rootdir="$scratch" tests "${pytest_options[@]}" --junitxml="$scratch/wheel.xml")
# Rebuild from the sdist; a checkout must not supply omitted package files.
uv run --frozen "${run_options[@]}" python - "$scratch" <<'PY'
import pathlib, sys, tarfile
root = pathlib.Path(sys.argv[1])
with tarfile.open(next((root / 'dist').glob('*.tar.gz'))) as archive:
    archive.extractall(root / 'sdist', filter='data')
PY
uv build --wheel "$scratch"/sdist/* --out-dir "$scratch/rebuilt"
uv run --frozen "${run_options[@]}" python scripts/verify_wheel.py "$scratch/rebuilt"
uv run --frozen "${run_options[@]}" python scripts/export_release_contract.py "$scratch"/rebuilt/*.whl --output "$scratch/rebuilt-contract"
cmp "$scratch/contract/contract.json" "$scratch/rebuilt-contract/contract.json"
if (( ${#fixture_wheels[@]} )); then
  uv pip install --python "$scratch/venv/bin/python" --no-deps --reinstall "$scratch"/rebuilt/*.whl
else
  uv pip install --python "$scratch/venv/bin/python" --constraint "$scratch/requirements.txt" --reinstall "$scratch"/rebuilt/*.whl
  uv pip check --python "$scratch/venv/bin/python"
fi
(cd "$scratch" && "$scratch/venv/bin/python" -I -m pytest -c /dev/null --rootdir="$scratch" tests "${pytest_options[@]}" --junitxml="$scratch/sdist.xml")

# Retain the exact validated bytes for the release channel; never rebuild them.
if [[ -n "${RELEASE_ARTIFACT_DIR:-}" ]]; then
  mkdir -p "$RELEASE_ARTIFACT_DIR"
  cp "$scratch/dist"/* "$RELEASE_ARTIFACT_DIR/"
fi

python3 scripts/verify-test-results.py "$scratch/source.xml" "$scratch/wheel.xml" "$scratch/sdist.xml"
if [[ -n "${TEST_EVIDENCE_DIR:-}" ]]; then
  mkdir -p "$TEST_EVIDENCE_DIR"
  cp "$scratch"/*.xml "$TEST_EVIDENCE_DIR/"
fi
